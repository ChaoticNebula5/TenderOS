"""Migration 045 on a real database at 044 (plan item 125): the
requirement_citations table, with every existing requirement's
source_chunk_id copied in as its primary citation, both foreign keys
cascading, and a round trip.

Built like test_migration_044_readiness_report_generation.py: a scratch
database upgraded to 044 with real Alembic, with helpers copied, this
codebase's convention for tests. Rows are seeded with their real parents
(organization, tender, document, chunk), foreign keys on: the backfill's
INSERT ... SELECT checks its new foreign keys, as it would on real data.

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
BEFORE = "044_readiness_report_generation"
AFTER = "045_requirement_citations"


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
    name = f"migration_045_{uuid.uuid4().hex[:8]}"
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


def _bid(db: Scratch) -> tuple[uuid.UUID, uuid.UUID]:
    """An organization, a tender and one document. Returns (tender, document)."""
    org, tender, document = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db.execute("INSERT INTO organizations (id, name) VALUES (:id, 'Org')", {"id": org})
    db.execute(
        "INSERT INTO tenders (id, organization_id, name) VALUES (:id, :org, 'Bid')",
        {"id": tender, "org": org},
    )
    db.execute(
        "INSERT INTO documents (id, tender_id, side, doc_type, filename, storage_key, sha256, "
        "mime_type, file_size_bytes) VALUES (:id, :tender, 'client', 'rfp', 'rfp.pdf', :key, :sha, "
        "'application/pdf', 1)",
        {"id": document, "tender": tender, "key": uuid.uuid4().hex, "sha": uuid.uuid4().hex},
    )
    return tender, document


def _chunk(db: Scratch, document: uuid.UUID, index: int) -> uuid.UUID:
    chunk = uuid.uuid4()
    db.execute(
        "INSERT INTO document_chunks (id, document_id, chunk_index, chunk_text, normalized_text, "
        "content_hash) VALUES (:id, :doc, :i, 'Text.', 'Text.', :h)",
        {"id": chunk, "doc": document, "i": index, "h": uuid.uuid4().hex},
    )
    return chunk


def _requirement(db: Scratch, tender: uuid.UUID, chunk: uuid.UUID) -> uuid.UUID:
    requirement_id = uuid.uuid4()
    db.execute(
        "INSERT INTO requirements (id, tender_id, display_code, requirement_text, normalized_text, "
        "category, mandatory, source_chunk_id, content_fingerprint) VALUES "
        "(:id, :tender, 'REQ-001', 'Text.', 'Text.', 'other', true, :chunk, :fp)",
        {"id": requirement_id, "tender": tender, "chunk": chunk, "fp": uuid.uuid4().hex},
    )
    return requirement_id


def _citations(db: Scratch) -> set[tuple[uuid.UUID, uuid.UUID]]:
    return {
        (row["requirement_id"], row["chunk_id"])
        for row in db.rows("SELECT requirement_id, chunk_id FROM requirement_citations")
    }


def test_upgrade_copies_every_requirements_primary_citation(scratch: Scratch):
    tender, document = _bid(scratch)
    first_chunk, second_chunk = _chunk(scratch, document, 0), _chunk(scratch, document, 1)
    first = _requirement(scratch, tender, first_chunk)
    second = _requirement(scratch, tender, second_chunk)
    also_first_chunk = _requirement(scratch, tender, first_chunk)

    command.upgrade(scratch.cfg, AFTER)

    assert scratch.version() == AFTER
    assert _citations(scratch) == {
        (first, first_chunk),
        (second, second_chunk),
        (also_first_chunk, first_chunk),
    }
    with pytest.raises(IntegrityError, match="requirement_citations_pkey"):
        scratch.execute(
            "INSERT INTO requirement_citations (requirement_id, chunk_id) VALUES (:r, :c)",
            {"r": first, "c": first_chunk},
        )


def test_a_citation_goes_with_its_requirement_and_with_its_chunk(scratch: Scratch):
    command.upgrade(scratch.cfg, AFTER)
    tender, document = _bid(scratch)
    chunks = [_chunk(scratch, document, index) for index in range(3)]
    kept = _requirement(scratch, tender, chunks[0])
    removed = _requirement(scratch, tender, chunks[0])
    for requirement_id, chunk in ((kept, chunks[1]), (removed, chunks[2])):
        scratch.execute(
            "INSERT INTO requirement_citations (requirement_id, chunk_id) VALUES (:r, :c)",
            {"r": requirement_id, "c": chunk},
        )
    assert len(_citations(scratch)) == 2, "only the rows inserted here: the table starts empty"

    scratch.execute("DELETE FROM requirements WHERE id = :id", {"id": removed})
    scratch.execute("DELETE FROM document_chunks WHERE id = :id", {"id": chunks[1]})

    assert _citations(scratch) == set()


def test_downgrade_drops_the_table_and_upgrade_runs_again(scratch: Scratch):
    tender, document = _bid(scratch)
    chunk = _chunk(scratch, document, 0)
    requirement_id = _requirement(scratch, tender, chunk)
    command.upgrade(scratch.cfg, AFTER)

    command.downgrade(scratch.cfg, BEFORE)

    assert scratch.version() == BEFORE
    assert scratch.rows(
        "SELECT count(*) AS n FROM information_schema.tables "
        "WHERE table_name = 'requirement_citations'"
    ) == [{"n": 0}]
    assert [
        row["source_chunk_id"] for row in scratch.rows("SELECT source_chunk_id FROM requirements")
    ] == [chunk]

    command.upgrade(scratch.cfg, AFTER)

    assert _citations(scratch) == {(requirement_id, chunk)}
