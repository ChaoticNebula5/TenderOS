"""Migration 047 on a real database at 046 (plan item 124): a reanalysis
claim can end failed, with failed_at and error_code set exactly then and
reaped_rule only then; the exporter's view; and a round trip whose
downgrade puts failed claims back to running.

Built like test_migration_046_requirement_keys.py: a scratch database
upgraded to 046 with real Alembic, with helpers copied, this codebase's
convention for tests. Rows are seeded with their real parents, foreign keys
on.

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
BEFORE = "046_requirement_keys"
AFTER = "047_reanalysis_claim_failed"


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
                conn.execute(text(sql), params or {})
        finally:
            engine.dispose()


@pytest.fixture
def scratch(monkeypatch) -> Iterator[Scratch]:
    name = f"migration_047_{uuid.uuid4().hex[:8]}"
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


def _tender(db: Scratch) -> uuid.UUID:
    org, tender = uuid.uuid4(), uuid.uuid4()
    db.execute("INSERT INTO organizations (id, name) VALUES (:id, 'Org')", {"id": org})
    db.execute(
        "INSERT INTO tenders (id, organization_id, name) VALUES (:id, :org, 'Bid')",
        {"id": tender, "org": org},
    )
    return tender


def _running_claim(db: Scratch, tender: uuid.UUID, state_version: int, lapsed: bool) -> uuid.UUID:
    claim = uuid.uuid4()
    db.execute(
        "INSERT INTO reanalysis_claims (id, tender_id, knowledge_item_id, state_version, status, "
        "claim_token, locked_until) VALUES (:id, :tender, :item, :sv, 'running', :token, "
        f"now() {'-' if lapsed else '+'} interval '1 day')",
        {
            "id": claim,
            "tender": tender,
            "item": uuid.uuid4(),
            "sv": state_version,
            "token": uuid.uuid4(),
        },
    )
    return claim


def _status(db: Scratch, claim: uuid.UUID) -> str:
    return db.rows("SELECT status FROM reanalysis_claims WHERE id = :id", {"id": claim})[0][
        "status"
    ]


def test_upgrade_keeps_running_claims_and_allows_a_failed_ending(scratch: Scratch):
    tender = _tender(scratch)
    claim = _running_claim(scratch, tender, 1, lapsed=True)

    command.upgrade(scratch.cfg, AFTER)

    assert _status(scratch, claim) == "running"
    scratch.execute(
        "UPDATE reanalysis_claims SET status = 'failed', failed_at = now(), "
        "error_code = 'WORKER_LOST', reaped_rule = 'lease' WHERE id = :id",
        {"id": claim},
    )
    assert _status(scratch, claim) == "failed"


@pytest.mark.parametrize(
    "values",
    [
        "status = 'failed', error_code = 'GRAPH_STALE'",
        "status = 'failed', failed_at = now()",
        "failed_at = now(), error_code = 'GRAPH_STALE'",
        "reaped_rule = 'lease'",
        "status = 'abandoned'",
    ],
    ids=[
        "failed without failed_at",
        "failed without error_code",
        "an ending on a running claim",
        "a rule on a running claim",
        "an unknown status",
    ],
)
def test_the_checks_refuse_a_half_written_failure(scratch: Scratch, values: str):
    command.upgrade(scratch.cfg, AFTER)
    claim = _running_claim(scratch, _tender(scratch), 1, lapsed=False)

    with pytest.raises(IntegrityError):
        scratch.execute(f"UPDATE reanalysis_claims SET {values} WHERE id = :id", {"id": claim})

    assert _status(scratch, claim) == "running"


def test_the_exporters_view_says_which_running_claims_lapsed(scratch: Scratch):
    command.upgrade(scratch.cfg, AFTER)
    tender = _tender(scratch)
    _running_claim(scratch, tender, 1, lapsed=True)
    _running_claim(scratch, tender, 2, lapsed=False)
    failed = _running_claim(scratch, tender, 3, lapsed=True)
    scratch.execute(
        "UPDATE reanalysis_claims SET status = 'failed', failed_at = now(), "
        "error_code = 'GRAPH_STALE' WHERE id = :id",
        {"id": failed},
    )

    rows = scratch.rows(
        "SELECT status, error_code, reaped_rule, lease_lapsed FROM observability.reanalysis_claims "
        "ORDER BY status, lease_lapsed"
    )

    assert rows == [
        {
            "status": "failed",
            "error_code": "GRAPH_STALE",
            "reaped_rule": None,
            "lease_lapsed": False,
        },
        {"status": "running", "error_code": None, "reaped_rule": None, "lease_lapsed": False},
        {"status": "running", "error_code": None, "reaped_rule": None, "lease_lapsed": True},
    ]


def test_downgrade_puts_failed_claims_back_to_running_and_upgrade_runs_again(scratch: Scratch):
    tender = _tender(scratch)
    claim = _running_claim(scratch, tender, 1, lapsed=True)
    command.upgrade(scratch.cfg, AFTER)
    scratch.execute(
        "UPDATE reanalysis_claims SET status = 'failed', failed_at = now(), "
        "error_code = 'WORKER_LOST', reaped_rule = 'unknown' WHERE id = :id",
        {"id": claim},
    )

    command.downgrade(scratch.cfg, BEFORE)

    assert scratch.version() == BEFORE
    assert _status(scratch, claim) == "running"
    assert scratch.rows(
        "SELECT count(*) AS n FROM information_schema.columns WHERE table_name = "
        "'reanalysis_claims' AND column_name IN ('failed_at', 'error_code', 'reaped_rule')"
    ) == [{"n": 0}]
    assert scratch.rows(
        "SELECT count(*) AS n FROM information_schema.views "
        "WHERE table_schema = 'observability' AND table_name = 'reanalysis_claims'"
    ) == [{"n": 0}]
    with pytest.raises(IntegrityError):
        scratch.execute(
            "UPDATE reanalysis_claims SET status = 'failed' WHERE id = :id", {"id": claim}
        )

    command.upgrade(scratch.cfg, AFTER)

    assert scratch.version() == AFTER
    assert _status(scratch, claim) == "running"
