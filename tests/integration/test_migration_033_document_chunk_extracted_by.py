"""Migration 033 (plan item 104) against a scratch database: upgrade adds
document_chunks.extracted_by_task_id (nullable, a foreign key to
workflow_tasks with ON DELETE SET NULL) and the partial index of unread
parsed chunks; a chunk that existed before stays unread (no backfill, user
decision Q5); downgrade drops both and keeps the chunk; upgrade again adds
them back. test_schema_diff.py separately checks the upgraded schema matches
the models.

Helpers copied from test_migration_032_redteam_passes.py rather than
imported, this codebase's convention for test files. The chunk is raw SQL
with foreign keys off (session_replication_role=replica), so no document
row is needed.
"""

import re
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from tenderos_backend.core.config import settings

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BEFORE = "032_redteam_passes"
AFTER = "033_document_chunk_extracted_by"


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


@pytest.fixture
def scratch(monkeypatch) -> Iterator[tuple[Config, str]]:
    name = f"migration_033_{uuid.uuid4().hex[:8]}"
    admin_dsn = _sync_dsn(settings.postgres_dsn)
    _execute_as_admin(admin_dsn, f'CREATE DATABASE "{name}"')
    try:
        monkeypatch.setattr(
            settings, "postgres_dsn", re.sub(r"/[^/?]+(\?.*)?$", f"/{name}", settings.postgres_dsn)
        )
        cfg = Config(str(REPO_ROOT / "alembic.ini"))
        command.upgrade(cfg, BEFORE)
        yield cfg, _sync_dsn(admin_dsn, name)
    finally:
        _execute_as_admin(admin_dsn, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _rows(dsn: str, query: str) -> list[tuple]:
    engine = create_engine(dsn)
    try:
        with engine.connect() as conn:
            return [tuple(row) for row in conn.execute(text(query)).all()]
    finally:
        engine.dispose()


def _insert_chunk(dsn: str) -> str:
    chunk_id = str(uuid.uuid4())
    engine = create_engine(dsn)
    try:
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL session_replication_role = replica"))
            conn.execute(
                text(
                    "INSERT INTO document_chunks (id, document_id, chunk_index, chunk_text, "
                    "normalized_text, content_hash) VALUES (:id, :doc, 0, 'x', 'x', :hash)"
                ),
                {"id": chunk_id, "doc": str(uuid.uuid4()), "hash": uuid.uuid4().hex},
            )
    finally:
        engine.dispose()
    return chunk_id


_COLUMN = (
    "SELECT data_type, is_nullable FROM information_schema.columns "
    "WHERE table_name = 'document_chunks' AND column_name = 'extracted_by_task_id'"
)
_FOREIGN_KEY = (
    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
    "WHERE conrelid = 'document_chunks'::regclass AND contype = 'f' "
    "AND pg_get_constraintdef(oid) LIKE '%extracted_by_task_id%'"
)
_INDEX = "SELECT indexdef FROM pg_indexes WHERE indexname = 'idx_document_chunks_unextracted'"


def test_upgrade_adds_the_mark_unread_and_downgrade_drops_it(scratch):
    cfg, dsn = scratch
    chunk_id = _insert_chunk(dsn)
    assert _rows(dsn, _COLUMN) == []

    command.upgrade(cfg, AFTER)
    assert _rows(dsn, _COLUMN) == [("uuid", "YES")]
    assert _rows(dsn, _FOREIGN_KEY) == [
        ("FOREIGN KEY (extracted_by_task_id) REFERENCES workflow_tasks(id) ON DELETE SET NULL",)
    ]
    assert _rows(dsn, _INDEX) == [
        (
            "CREATE INDEX idx_document_chunks_unextracted ON public.document_chunks "
            "USING btree (document_id) WHERE ((extracted_by_task_id IS NULL) "
            "AND (origin = 'parsed'::text))",
        )
    ]
    # No backfill: a chunk from before the migration has not been read.
    assert _rows(
        dsn, f"SELECT extracted_by_task_id FROM document_chunks WHERE id = '{chunk_id}'"
    ) == [(None,)]

    command.downgrade(cfg, BEFORE)
    assert _rows(dsn, _COLUMN) == []
    assert _rows(dsn, _INDEX) == []
    assert _rows(dsn, "SELECT count(*) FROM document_chunks") == [(1,)]

    command.upgrade(cfg, AFTER)
    assert _rows(dsn, _COLUMN) == [("uuid", "YES")]
    assert len(_rows(dsn, _INDEX)) == 1
