"""Migration 025 on a real database at 024 (phase 4 plan item 35).

Each test builds a scratch database with a real `alembic upgrade` to 024, as
test_schema_diff.py does, because the session's test database comes from the
models and has no migration history. Its helpers are copied rather than
imported, this codebase's convention for test files.

The seed rows are raw SQL against the schema at 024, not the ORM: this test
must keep working when the models move on from 024. Foreign keys are off for
the seed (session_replication_role=replica, which needs a superuser, as the
test server's user is), so it plants only the columns the migration looks at.
The CHECK constraints still apply.

Slower than a typical test: each one runs the whole migration history.
"""

import io
import logging
import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError
from tenderos_backend.core.config import settings
from tenderos_backend.domain.services.redteam_service import finding_fingerprint

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BEFORE = "024_workflow_run_current_job"
AFTER = "025_redteam_target_type"
DESELECTED = "deselected_fact_in_use"
REMOVED = "removed_employee_committed"


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
    tender: uuid.UUID = field(default_factory=uuid.uuid4)
    entry: uuid.UUID = field(default_factory=uuid.uuid4)
    claim: uuid.UUID = field(default_factory=uuid.uuid4)
    role: uuid.UUID = field(default_factory=uuid.uuid4)

    def version(self) -> str:
        return self.scalar("SELECT version_num FROM alembic_version")

    def scalar(self, sql: str):
        engine = create_engine(self.dsn)
        try:
            with engine.connect() as conn:
                return conn.execute(text(sql)).scalar_one()
        finally:
            engine.dispose()

    def findings(self) -> dict[uuid.UUID, dict]:
        """Every column of every finding, as the database renders it, so two
        snapshots compare byte for byte."""
        engine = create_engine(self.dsn)
        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT id, to_jsonb(f) AS full_row, target_type, target_id, "
                        "content_fingerprint, finding_category, violated_condition "
                        "FROM redteam_findings f"
                    )
                ).mappings()
                return {row["id"]: dict(row) for row in rows}
        finally:
            engine.dispose()


@pytest.fixture
def scratch(monkeypatch) -> Iterator[Scratch]:
    name = f"migration_025_{uuid.uuid4().hex[:8]}"
    admin_dsn = _sync_dsn(settings.postgres_dsn)
    _execute_as_admin(admin_dsn, f'CREATE DATABASE "{name}"')
    try:
        # env.py reads settings.postgres_dsn and builds an async engine.
        monkeypatch.setattr(
            settings, "postgres_dsn", re.sub(r"/[^/?]+(\?.*)?$", f"/{name}", settings.postgres_dsn)
        )
        cfg = Config(str(REPO_ROOT / "alembic.ini"))
        command.upgrade(cfg, BEFORE)
        built = Scratch(dsn=_sync_dsn(admin_dsn, name), cfg=cfg)
        _seed_targets(built)
        yield built
    finally:
        _execute_as_admin(admin_dsn, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _seed_targets(db: Scratch) -> None:
    engine = create_engine(db.dsn)
    try:
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL session_replication_role = replica"))
            conn.execute(
                text(
                    "INSERT INTO compliance_entries (id, requirement_id, status) "
                    "VALUES (:id, gen_random_uuid(), 'fully_compliant')"
                ),
                {"id": db.entry},
            )
            conn.execute(
                text(
                    "INSERT INTO proposal_claims "
                    "(id, section_id, claim_text, claim_type, content_fingerprint) "
                    "VALUES (:id, gen_random_uuid(), 'c', 'team', 'f')"
                ),
                {"id": db.claim},
            )
            conn.execute(
                text(
                    "INSERT INTO role_requirements (id, tender_id, role_name, content_fingerprint) "
                    "VALUES (:id, :tender, 'r', 'f')"
                ),
                {"id": db.role, "tender": db.tender},
            )
    finally:
        engine.dispose()


def _plant(
    conn: Connection,
    db: Scratch,
    category: str,
    label: str,
    target: uuid.UUID | None,
    *,
    resolved: bool = False,
    tender: uuid.UUID | None = None,
    fingerprint: str | None = None,
) -> uuid.UUID:
    """One finding as the old code wrote it: fingerprint from the label given,
    by the app's own formula, unless one is given."""
    finding_id = uuid.uuid4()
    tender_id = tender or db.tender
    violated = f"category={category}; node_id={target}"
    conn.execute(
        text(
            "INSERT INTO redteam_findings (id, tender_id, target_type, target_id, "
            "finding_category, severity, finding_text, violated_condition, "
            "content_fingerprint, resolved, resolution_note, resolution_source) "
            "VALUES (:id, :tender, :label, :target, :category, 'critical', 'text', "
            ":violated, :fingerprint, :resolved, :note, :source)"
        ),
        {
            "id": finding_id,
            "tender": tender_id,
            "label": label,
            "target": target,
            "category": category,
            "violated": violated,
            "fingerprint": fingerprint
            or finding_fingerprint(tender_id, label, target, category, violated),
            "resolved": resolved,
            "note": "accepted by a human" if resolved else None,
            "source": "human" if resolved else None,
        },
    )
    return finding_id


def _plant_all(db: Scratch, plants: list[tuple]) -> list[uuid.UUID]:
    engine = create_engine(db.dsn)
    try:
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL session_replication_role = replica"))
            return [_plant(conn, db, *args, **kwargs) for args, kwargs in plants]
    finally:
        engine.dispose()


def _assert_fingerprint_is_the_apps(row: dict) -> None:
    full = row["full_row"]
    assert row["content_fingerprint"] == finding_fingerprint(
        uuid.UUID(full["tender_id"]),
        row["target_type"],
        row["target_id"],
        row["finding_category"],
        row["violated_condition"],
    ), f"{row['finding_category']} / {row['target_type']}: not what finding_fingerprint returns"


def test_upgrade_relabels_by_table_and_downgrade_restores_every_byte(scratch, monkeypatch):
    nowhere = uuid.uuid4()
    ids = _plant_all(
        scratch,
        [
            ((DESELECTED, "evidence", scratch.entry), {}),
            ((DESELECTED, "evidence", scratch.claim), {"tender": uuid.uuid4()}),
            ((DESELECTED, "evidence", nowhere), {}),
            ((DESELECTED, "evidence", None), {}),
            ((REMOVED, "proposal_claim", scratch.role), {"resolved": True}),
            ((REMOVED, "proposal_claim", scratch.claim), {}),
            # A stored fingerprint no formula produces: this row already has
            # its old label, so neither direction may rewrite it.
            ((REMOVED, "proposal_claim", nowhere), {"fingerprint": "not-from-the-formula"}),
            # A different category pointing at the same tables: not this bug.
            (("unsupported_claim", "proposal_claim", scratch.role), {}),
            (("expired_certification", "evidence", scratch.entry), {}),
        ],
    )
    (
        to_entry,
        to_claim,
        unmatched,
        null_target,
        to_role,
        already_right,
        removed_unmatched,
        other,
        other2,
    ) = ids
    before = scratch.findings()

    logged: list[str] = []
    logger = logging.getLogger("alembic.runtime.migration")
    original_info = logger.info

    def recording_info(msg, *args, **kwargs):
        logged.append(msg % args if args else msg)
        original_info(msg, *args, **kwargs)

    monkeypatch.setattr(logger, "info", recording_info)
    command.upgrade(scratch.cfg, AFTER)
    after = scratch.findings()

    assert scratch.version() == AFTER
    assert after.keys() == before.keys(), "no finding may appear or go: ids are held elsewhere"

    assert after[to_entry]["target_type"] == "compliance_entry"
    assert after[to_claim]["target_type"] == "proposal_claim"
    assert after[to_role]["target_type"] == "role_requirement"
    for changed in (to_entry, to_claim, to_role):
        assert after[changed]["content_fingerprint"] != before[changed]["content_fingerprint"]
        _assert_fingerprint_is_the_apps(after[changed])

    for untouched in (unmatched, null_target, already_right, removed_unmatched, other, other2):
        assert after[untouched]["full_row"] == before[untouched]["full_row"]

    # Only the label and fingerprint move; a human's resolution stays.
    changed_columns = {
        key
        for key in before[to_role]["full_row"]
        if before[to_role]["full_row"][key] != after[to_role]["full_row"][key]
    }
    assert changed_columns == {"target_type", "content_fingerprint"}
    assert after[to_role]["full_row"]["resolved"] is True
    assert after[to_role]["full_row"]["resolution_source"] == "human"
    assert after[to_role]["full_row"]["resolution_note"] == "accepted by a human"

    assert any(
        "025: 3 red-team finding(s) relabelled; 3 left as they were" in line for line in logged
    ), f"the output must count relabelled and unmatched rows, got: {logged}"

    command.downgrade(scratch.cfg, BEFORE)
    restored = scratch.findings()
    assert scratch.version() == BEFORE
    assert {key: value["full_row"] for key, value in restored.items()} == {
        key: value["full_row"] for key, value in before.items()
    }, "the downgrade must restore every column of every row exactly"


def test_downgrade_gives_a_row_the_fixed_code_wrote_what_the_old_code_would_have(scratch):
    command.upgrade(scratch.cfg, AFTER)
    # Written after 025 by the fixed code, so nothing to restore it from: one
    # of each new label, including the node-without-an-id case, where the
    # app's formula hashes the string 'None'.
    ids = _plant_all(
        scratch,
        [
            ((DESELECTED, "compliance_entry", scratch.entry), {}),
            ((DESELECTED, "proposal_claim", scratch.claim), {}),
            ((DESELECTED, "compliance_entry", None), {}),
            ((REMOVED, "role_requirement", scratch.role), {}),
        ],
    )

    command.downgrade(scratch.cfg, BEFORE)
    rows = scratch.findings()

    for finding_id, label in zip(
        ids, ("evidence", "evidence", "evidence", "proposal_claim"), strict=True
    ):
        assert rows[finding_id]["target_type"] == label
        _assert_fingerprint_is_the_apps(rows[finding_id])


def test_a_collision_rolls_the_whole_migration_back(scratch):
    """The fixed code ran before the migration: a row already has the new
    label for the same target. Nothing is merged and nothing is deleted."""
    ids = _plant_all(
        scratch,
        [
            # Relabelled by an earlier statement, which succeeds on its own.
            ((DESELECTED, "evidence", scratch.claim), {}),
            # This pair collides in the role statement, which runs last.
            ((REMOVED, "proposal_claim", scratch.role), {}),
            ((REMOVED, "role_requirement", scratch.role), {}),
        ],
    )
    before = scratch.findings()

    with pytest.raises(IntegrityError, match="uq_redteam_finding_fingerprint"):
        command.upgrade(scratch.cfg, AFTER)

    after = scratch.findings()
    assert scratch.version() == BEFORE, "the migration must not count as applied"
    assert after.keys() == set(ids), "no finding may be deleted"
    assert {key: value["full_row"] for key, value in after.items()} == {
        key: value["full_row"] for key, value in before.items()
    }, "every statement must roll back, the ones that had succeeded included"


def test_offline_mode_writes_the_statements_without_a_database():
    """`alembic upgrade --sql` has no connection to count rows on."""
    output = io.StringIO()
    cfg = Config(str(REPO_ROOT / "alembic.ini"), output_buffer=output)

    command.upgrade(cfg, f"{BEFORE}:{AFTER}", sql=True)

    assert output.getvalue().count("UPDATE redteam_findings") == 3
