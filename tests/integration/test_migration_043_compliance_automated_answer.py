"""Migration 043 on a real database at 042 (plan item 121, Q3 a):
compliance_entries gains automated_status, automated_reasoning_summary and
decided_by, backfilled from the override audit events.

An entry with a compliance_override event later than its last_evaluated_at
was decided by a reviewer, and the earliest such event's `before` is what the
analysis had left; every other entry's own status and reasoning are the
analysis's.

Built like test_migration_030_readiness_report_content.py: a scratch
database upgraded to 042 with real Alembic, seeded with raw SQL with foreign
keys off (session_replication_role=replica), so no tender or requirement rows
are needed. Helpers are copied, this codebase's convention for tests.

Slower than a typical test: each one runs the whole migration history.
"""

import json
import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from tenderos_backend.core.config import settings

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BEFORE = "042_agent_trace_rate_limits"
AFTER = "043_compliance_automated_answer"
EVALUATED = "2026-11-25 12:00:00+00"


def _sync_dsn(async_dsn: str, database: str | None = None) -> str:
    dsn = async_dsn.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    if database is not None:
        dsn = re.sub(r"/[^/?]+(\?.*)?$", f"/{database}", dsn)
    return dsn


def _execute_as_admin(admin_dsn: str, statement: str) -> None:
    engine = create_engine(admin_dsn, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(statement))
    finally:
        engine.dispose()


@dataclass
class Scratch:
    dsn: str
    cfg: Config

    def version(self) -> str:
        return self.rows("SELECT version_num FROM alembic_version")[0]["version_num"]

    def rows(self, sql: str, params: dict | None = None) -> list[dict]:
        engine = create_engine(self.dsn)
        try:
            with engine.connect() as conn:
                return [dict(r) for r in conn.execute(text(sql), params or {}).mappings()]
        finally:
            engine.dispose()

    def execute(self, sql: str, params: dict | None = None) -> None:
        engine = create_engine(self.dsn)
        try:
            with engine.begin() as conn:
                conn.execute(text("SET LOCAL session_replication_role = replica"))
                conn.execute(text(sql), params or {})
        finally:
            engine.dispose()


@pytest.fixture
def scratch(monkeypatch) -> Iterator[Scratch]:
    name = f"migration_043_{uuid.uuid4().hex[:8]}"
    admin_dsn = _sync_dsn(settings.postgres_dsn)
    _execute_as_admin(admin_dsn, f'CREATE DATABASE "{name}"')
    try:
        monkeypatch.setattr(
            settings, "postgres_dsn", re.sub(r"/[^/?]+(\?.*)?$", f"/{name}", settings.postgres_dsn)
        )
        cfg = Config(str(REPO_ROOT / "alembic.ini"))
        command.upgrade(cfg, BEFORE)
        yield Scratch(dsn=_sync_dsn(admin_dsn, name), cfg=cfg)
    finally:
        _execute_as_admin(admin_dsn, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _entry(db: Scratch, status: str, reasoning: str | None, approved: bool = True) -> uuid.UUID:
    entry_id = uuid.uuid4()
    db.execute(
        "INSERT INTO compliance_entries "
        "(id, requirement_id, status, reasoning_summary, human_approved, last_evaluated_at) "
        "VALUES (:id, :requirement, :status, :reasoning, :approved, :evaluated)",
        {
            "id": entry_id,
            "requirement": uuid.uuid4(),
            "status": status,
            "reasoning": reasoning,
            "approved": approved,
            "evaluated": EVALUATED,
        },
    )
    return entry_id


def _event(db: Scratch, entry_id: uuid.UUID, at: str, before: dict, action: str) -> None:
    db.execute(
        "INSERT INTO audit_events "
        "(id, organization_id, actor_type, actor_id, action, target_type, target_id, before, "
        "created_at) VALUES (:id, :org, 'user', 'reviewer', :action, 'compliance_entry', "
        ":target, CAST(:before AS jsonb), :at)",
        {
            "id": uuid.uuid4(),
            "org": uuid.uuid4(),
            "action": action,
            "target": entry_id,
            "before": json.dumps(before),
            "at": at,
        },
    )


def _override(db: Scratch, entry_id: uuid.UUID, at: str, status: str, reasoning) -> None:
    _event(
        db,
        entry_id,
        at,
        {"status": status, "reasoning_summary": reasoning, "human_approved": False},
        "compliance_override",
    )


def _backfilled(db: Scratch) -> dict[uuid.UUID, tuple]:
    return {
        row["id"]: (row["decided_by"], row["automated_status"], row["automated_reasoning_summary"])
        for row in db.rows(
            "SELECT id, decided_by, automated_status, automated_reasoning_summary "
            "FROM compliance_entries"
        )
    }


def test_upgrade_backfills_each_entry_from_its_overrides_since_the_last_evaluation(
    scratch: Scratch,
):
    never_overridden = _entry(scratch, "partially_compliant", "The analysis's words.")

    overridden_since = _entry(scratch, "fully_compliant", "Reviewer: held on paper.")
    _override(
        scratch, overridden_since, "2026-11-25 12:14:00+00", "evidence_unavailable", "None found."
    )

    # The override came before the last evaluation, which wrote the status
    # back: today's behaviour, so the status is the analysis's.
    overridden_before = _entry(scratch, "evidence_unavailable", "Re-evaluated.")
    _override(scratch, overridden_before, "2026-11-25 11:00:00+00", "evidence_unavailable", "Old.")

    # Two overrides since: the earliest's `before` is the analysis's answer;
    # the later one's is the reviewer's own first decision.
    overridden_twice = _entry(scratch, "non_compliant", "Reviewer: second thoughts.")
    _override(
        scratch, overridden_twice, "2026-11-25 12:30:00+00", "fully_compliant", "Reviewer first."
    )
    _override(
        scratch, overridden_twice, "2026-11-25 12:20:00+00", "clarification_required", "Ask them."
    )

    # A `before` whose reasoning is a JSON null reads as SQL NULL.
    null_reasoning = _entry(scratch, "fully_compliant", "Reviewer: yes.")
    _override(scratch, null_reasoning, "2026-11-25 12:14:00+00", "not_reviewed", None)

    # Another action after the last evaluation is not an override.
    cleared_only = _entry(scratch, "non_compliant", "Changed.", approved=False)
    _event(
        scratch,
        cleared_only,
        "2026-11-25 12:14:00+00",
        {"status": "fully_compliant", "human_approved": True},
        "compliance_approval_cleared_on_reevaluation",
    )

    command.upgrade(scratch.cfg, AFTER)

    assert scratch.version() == AFTER
    assert _backfilled(scratch) == {
        never_overridden: ("analysis", "partially_compliant", "The analysis's words."),
        overridden_since: ("reviewer", "evidence_unavailable", "None found."),
        overridden_before: ("analysis", "evidence_unavailable", "Re-evaluated."),
        overridden_twice: ("reviewer", "clarification_required", "Ask them."),
        null_reasoning: ("reviewer", "not_reviewed", None),
        cleared_only: ("analysis", "non_compliant", "Changed."),
    }
    statuses = {
        row["id"]: row["status"]
        for row in scratch.rows("SELECT id, status FROM compliance_entries")
    }
    assert statuses[overridden_since] == "fully_compliant", "the decision in effect is unchanged"


def test_the_new_columns_are_checked(scratch: Scratch):
    command.upgrade(scratch.cfg, AFTER)

    with pytest.raises(IntegrityError, match="automated_status"):
        scratch.execute(
            "INSERT INTO compliance_entries (id, requirement_id, status) "
            "VALUES (:id, :requirement, 'fully_compliant')",
            {"id": uuid.uuid4(), "requirement": uuid.uuid4()},
        )
    with pytest.raises(IntegrityError, match="ck_compliance_entry_automated_status"):
        scratch.execute(
            "INSERT INTO compliance_entries (id, requirement_id, status, automated_status) "
            "VALUES (:id, :requirement, 'fully_compliant', 'probably_fine')",
            {"id": uuid.uuid4(), "requirement": uuid.uuid4()},
        )
    with pytest.raises(IntegrityError, match="ck_compliance_entry_decided_by"):
        scratch.execute(
            "INSERT INTO compliance_entries "
            "(id, requirement_id, status, automated_status, decided_by) "
            "VALUES (:id, :requirement, 'fully_compliant', 'fully_compliant', 'the_board')",
            {"id": uuid.uuid4(), "requirement": uuid.uuid4()},
        )


def test_downgrade_drops_the_columns_and_keeps_the_decision_and_upgrade_runs_again(
    scratch: Scratch,
):
    entry = _entry(scratch, "fully_compliant", "Reviewer: held on paper.")
    _override(scratch, entry, "2026-11-25 12:14:00+00", "evidence_unavailable", "None found.")
    command.upgrade(scratch.cfg, AFTER)

    command.downgrade(scratch.cfg, BEFORE)

    assert scratch.version() == BEFORE
    columns = {
        row["column_name"]
        for row in scratch.rows(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'compliance_entries'"
        )
    }
    assert not columns & {"automated_status", "automated_reasoning_summary", "decided_by"}
    assert scratch.rows("SELECT status FROM compliance_entries") == [{"status": "fully_compliant"}]

    command.upgrade(scratch.cfg, AFTER)

    assert _backfilled(scratch) == {entry: ("reviewer", "evidence_unavailable", "None found.")}
