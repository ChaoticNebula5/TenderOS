"""Migration 029 on a real database at 028 (phase 4 plan item 71, decision
0093): the run status check gains 'changes_requested', and the recovery
UPDATE moves exactly the runs a Request changes decision left stuck.

Built like test_migration_027_revoke_live_refresh_tokens.py: a scratch
database upgraded to 028 with real Alembic, seeded with raw SQL with foreign
keys off (session_replication_role=replica), so no tender or user rows are
needed. The one-active-run index still applies, so each seeded run gets its
own tender id. Helpers are copied, this codebase's convention for tests.

The seeds are the shapes the recovery must tell apart. Only one moves: a
waiting run whose LATEST gate is changes_requested and which has no pending
gate. Every run waiting at a pending gate, and every run in another status,
must come out exactly as it went in.

Slower than a typical test: each one runs the whole migration history.
"""

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
BEFORE = "028_refresh_token_resent_at"
AFTER = "029_run_status_changes_requested"


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

    def runs(self) -> dict[uuid.UUID, dict]:
        """Every run's state, timestamps as the database renders them, so
        two snapshots compare exactly."""
        return {
            row["id"]: row
            for row in self.rows(
                "SELECT id, status, completed_at::text AS completed_at, error_code "
                "FROM workflow_runs"
            )
        }

    def status_counts(self) -> dict[str, int]:
        return {
            row["status"]: row["n"]
            for row in self.rows("SELECT status, count(*) AS n FROM workflow_runs GROUP BY status")
        }


@pytest.fixture
def scratch(monkeypatch) -> Iterator[Scratch]:
    name = f"migration_029_{uuid.uuid4().hex[:8]}"
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


# (name, run status, gates oldest first). A gate is (status, decided);
# requested_at steps one hour per gate, so "oldest first" is the order.
_SEEDS: list[tuple[str, str, list[str]]] = [
    # The one shape that moves.
    (
        "stuck: latest changes_requested, nothing pending",
        "waiting_for_human",
        ["approved", "changes_requested"],
    ),
    ("stuck: only gate changes_requested", "waiting_for_human", ["changes_requested"]),
    # Legitimately waiting at a pending gate.
    ("waiting at a pending gate", "waiting_for_human", ["pending"]),
    (
        "waiting at a pending gate after approvals",
        "waiting_for_human",
        ["approved", "approved", "pending"],
    ),
    (
        "waiting at a pending gate after an older changes_requested",
        "waiting_for_human",
        ["changes_requested", "pending"],
    ),
    # Latest changes_requested, but a gate still pending: not the stuck shape.
    (
        "latest changes_requested with an older gate pending",
        "waiting_for_human",
        ["pending", "changes_requested"],
    ),
    # Decided, resume not run yet: the reaper's case, not this migration's.
    ("latest approved, resume pending", "waiting_for_human", ["changes_requested", "approved"]),
    ("waiting with no gate at all", "waiting_for_human", []),
    # Not waiting: never touched, whatever their gates say.
    ("failed with a changes_requested gate", "failed", ["changes_requested"]),
    ("cancelled, gate closed by the cancel", "cancelled", ["rejected"]),
    ("completed", "completed", ["approved"]),
    ("running", "running", ["approved"]),
    ("queued", "queued", []),
]
_MOVES = {
    "stuck: latest changes_requested, nothing pending",
    "stuck: only gate changes_requested",
}


def _seed(db: Scratch) -> tuple[dict[str, uuid.UUID], dict[str, str]]:
    """Returns each seed's run id, and, for the stuck ones, the latest
    gate's decided_at as the database renders it."""
    ids: dict[str, uuid.UUID] = {}
    engine = create_engine(db.dsn)
    try:
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL session_replication_role = replica"))
            for name, run_status, gates in _SEEDS:
                run_id, tender_id = uuid.uuid4(), uuid.uuid4()
                ids[name] = run_id
                conn.execute(
                    text(
                        "INSERT INTO workflow_runs (id, tender_id, langgraph_thread_id, status, "
                        "prompt_bundle_version, model_config, completed_at) VALUES "
                        "(:id, :tender, :thread, :status, 'v1', '{}'::jsonb, "
                        "CASE WHEN :status IN ('failed','cancelled','completed') "
                        "THEN now() - interval '3 days' END)"
                    ),
                    {
                        "id": run_id,
                        "tender": tender_id,
                        "thread": str(run_id),
                        "status": run_status,
                    },
                )
                for step, gate_status in enumerate(gates):
                    conn.execute(
                        text(
                            "INSERT INTO approval_gates (id, tender_id, workflow_run_id, gate_key, "
                            "gate_type, status, request_payload, decision_payload, requested_at, "
                            "decided_at) VALUES (gen_random_uuid(), :tender, :run, :key, "
                            "'requirement_review', :status, '{}'::jsonb, "
                            "CASE WHEN :status <> 'pending' "
                            "THEN jsonb_build_object('notes', 'seeded') END, "
                            "now() - interval '2 days' + make_interval(hours => :step), "
                            "CASE WHEN :status <> 'pending' "
                            "THEN now() - interval '2 days' + make_interval(hours => :step, mins => 30) "
                            "END)"
                        ),
                        {
                            "tender": tender_id,
                            "run": run_id,
                            "key": f"gate-{step}",
                            "status": gate_status,
                            "step": step,
                        },
                    )
    finally:
        engine.dispose()
    decided = {
        name: db.rows(
            "SELECT decided_at::text AS d FROM approval_gates WHERE workflow_run_id = :run "
            "ORDER BY requested_at DESC, id DESC LIMIT 1",
            {"run": ids[name]},
        )[0]["d"]
        for name in _MOVES
    }
    return ids, decided


def test_upgrade_moves_only_runs_stuck_by_request_changes(scratch: Scratch):
    ids, decided = _seed(scratch)
    before = scratch.runs()
    counts_before = scratch.status_counts()

    command.upgrade(scratch.cfg, AFTER)

    assert scratch.version() == AFTER
    after = scratch.runs()
    for name in _MOVES:
        run_id = ids[name]
        assert after[run_id]["status"] == "changes_requested", name
        # When the run really ended: its deciding gate's decided_at.
        assert after[run_id]["completed_at"] == decided[name], name
        assert after[run_id]["error_code"] is None, name
    for name, run_id in ids.items():
        if name not in _MOVES:
            assert after[run_id] == before[run_id], name
    assert counts_before == {
        "waiting_for_human": 8,
        "failed": 1,
        "cancelled": 1,
        "completed": 1,
        "running": 1,
        "queued": 1,
    }
    assert scratch.status_counts() == {
        "waiting_for_human": 6,
        "changes_requested": 2,
        "failed": 1,
        "cancelled": 1,
        "completed": 1,
        "running": 1,
        "queued": 1,
    }


def test_after_upgrade_the_status_is_allowed_and_frees_the_tender(scratch: Scratch):
    """The check allows the new value, refuses an unknown one, and a
    changes_requested run is outside the one-active-run index: its tender
    can hold a new active run."""
    ids, _ = _seed(scratch)
    command.upgrade(scratch.cfg, AFTER)
    stuck = ids["stuck: only gate changes_requested"]
    tender = scratch.rows("SELECT tender_id FROM workflow_runs WHERE id = :id", {"id": stuck})[0][
        "tender_id"
    ]

    engine = create_engine(scratch.dsn)
    try:
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL session_replication_role = replica"))
            conn.execute(
                text(
                    "INSERT INTO workflow_runs (id, tender_id, langgraph_thread_id, status, "
                    "prompt_bundle_version, model_config) VALUES "
                    "(gen_random_uuid(), :tender, :thread, 'queued', 'v1', '{}'::jsonb)"
                ),
                {"tender": tender, "thread": uuid.uuid4().hex},
            )
        with pytest.raises(IntegrityError, match="ck_workflow_run_status"), engine.begin() as conn:
            conn.execute(
                text("UPDATE workflow_runs SET status = 'paused' WHERE id = :id"), {"id": stuck}
            )
    finally:
        engine.dispose()


def test_upgrade_on_an_empty_table_is_a_no_op(scratch: Scratch):
    command.upgrade(scratch.cfg, AFTER)

    assert scratch.version() == AFTER
    assert scratch.runs() == {}


def test_downgrade_maps_changes_requested_to_failed_and_restores_the_check(scratch: Scratch):
    ids, _ = _seed(scratch)
    command.upgrade(scratch.cfg, AFTER)
    upgraded = scratch.runs()

    command.downgrade(scratch.cfg, BEFORE)

    assert scratch.version() == BEFORE
    downgraded = scratch.runs()
    for name, run_id in ids.items():
        if name in _MOVES:
            # failed, not waiting_for_human: see the migration's docstring.
            # completed_at stays: the run did end then.
            assert downgraded[run_id]["status"] == "failed", name
            assert downgraded[run_id]["completed_at"] == upgraded[run_id]["completed_at"], name
        else:
            assert downgraded[run_id] == upgraded[run_id], name
    # The gates still say what happened.
    gate_statuses = {
        row["status"]
        for row in scratch.rows(
            "SELECT g.status FROM approval_gates g WHERE g.workflow_run_id = ANY(:ids)",
            {"ids": [ids[name] for name in _MOVES]},
        )
    }
    assert "changes_requested" in gate_statuses
    engine = create_engine(scratch.dsn)
    try:
        with (
            pytest.raises(IntegrityError, match="ck_workflow_run_status"),
            engine.begin() as conn,
        ):
            conn.execute(
                text("UPDATE workflow_runs SET status = 'changes_requested' WHERE id = :id"),
                {"id": ids["queued"]},
            )
    finally:
        engine.dispose()
