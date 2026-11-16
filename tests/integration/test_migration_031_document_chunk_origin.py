"""Migration 031 (plan item 83) against a scratch database: upgrade adds
document_chunks.origin and backfills "synthetic" for exactly the chunks a
link with no knowledge item or a certification cites; the two unique keys
gain origin; downgrade renumbers synthetic chunks after the parsed ones and
restores the old keys, or refuses when a parsed and a synthetic chunk of one
document share a hash. test_schema_diff.py separately checks the upgraded
schema matches the models.

Helpers copied from test_migration_028_refresh_token_resent_at.py rather
than imported, this codebase's convention for test files. Seed rows are raw
SQL with foreign keys off (session_replication_role=replica), so no tender,
organization or user rows are needed; CHECK constraints still apply.
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
BEFORE = "030_readiness_report_content"
AFTER = "031_document_chunk_origin"


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
    name = f"migration_031_{uuid.uuid4().hex[:8]}"
    admin_dsn = _sync_dsn(settings.postgres_dsn)
    _execute_as_admin(admin_dsn, f'CREATE DATABASE "{name}"')
    try:
        # env.py reads settings.postgres_dsn and builds an async engine.
        monkeypatch.setattr(
            settings, "postgres_dsn", re.sub(r"/[^/?]+(\?.*)?$", f"/{name}", settings.postgres_dsn)
        )
        cfg = Config(str(REPO_ROOT / "alembic.ini"))
        command.upgrade(cfg, BEFORE)
        yield cfg, _sync_dsn(admin_dsn, name)
    finally:
        _execute_as_admin(admin_dsn, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _run(dsn: str, statements: list[tuple[str, dict]]) -> None:
    engine = create_engine(dsn)
    try:
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL session_replication_role = replica"))
            for statement, params in statements:
                conn.execute(text(statement), params)
    finally:
        engine.dispose()


def _rows(dsn: str, query: str) -> list[tuple]:
    engine = create_engine(dsn)
    try:
        with engine.connect() as conn:
            return [tuple(row) for row in conn.execute(text(query)).all()]
    finally:
        engine.dispose()


def _chunk(document_id: uuid.UUID, index: int, body: str) -> tuple[uuid.UUID, tuple[str, dict]]:
    chunk_id = uuid.uuid4()
    return chunk_id, (
        "INSERT INTO document_chunks (id, document_id, chunk_index, chunk_text, normalized_text, "
        "content_hash) VALUES (:id, :doc, :idx, :body, :body, :hash)",
        {"id": chunk_id, "doc": document_id, "idx": index, "body": body, "hash": body},
    )


def _seed(dsn: str) -> dict[str, uuid.UUID]:
    """A tender document and a certifications document, with one chunk per
    case the backfill must decide:
    - tender_cited: a parsed chunk a compliance link cites, with a knowledge
      item (compliance's materialize_evidence shape): stays parsed;
    - pricing: cited by a link with no knowledge item (pricing grounding):
      synthetic;
    - cert: cited by a certification's source_chunk_id: synthetic;
    - cert_doc_parsed: a parsed chunk on the certifications document that
      nothing cites (two real ones exist in dev data): stays parsed;
    - project: cited by a past project's source_chunk_id: stays parsed."""
    tender_doc, cert_doc = uuid.uuid4(), uuid.uuid4()
    ids: dict[str, uuid.UUID] = {"tender_doc": tender_doc}
    statements: list[tuple[str, dict]] = [
        (
            "INSERT INTO documents (id, tender_id, side, doc_type, filename, storage_key, sha256) "
            "VALUES (:id, gen_random_uuid(), 'client', 'pricing_workbook', 'p.xlsx', :key, :key)",
            {"id": tender_doc, "key": str(tender_doc)},
        ),
        (
            "INSERT INTO documents (id, organization_id, side, doc_type, filename, storage_key, "
            "sha256) VALUES (:id, gen_random_uuid(), 'company', 'certification_documents', "
            "'c.xlsx', :key, :key)",
            {"id": cert_doc, "key": str(cert_doc)},
        ),
    ]
    for name, document, index in (
        ("tender_cited", tender_doc, 0),
        ("pricing", tender_doc, 1),
        ("cert", cert_doc, 0),
        ("cert_doc_parsed", cert_doc, 1),
        ("project", cert_doc, 2),
    ):
        ids[name], statement = _chunk(document, index, f"{name} text")
        statements.append(statement)
    statements += [
        (
            "INSERT INTO evidence_links (tender_id, source_kind, content_fingerprint, "
            "knowledge_item_id, source_chunk_id) "
            "VALUES (gen_random_uuid(), 'past_project', :fp, gen_random_uuid(), :chunk)",
            {"fp": uuid.uuid4().hex, "chunk": ids["tender_cited"]},
        ),
        (
            "INSERT INTO evidence_links (tender_id, source_kind, content_fingerprint, "
            "source_chunk_id) VALUES (gen_random_uuid(), 'document_excerpt', :fp, :chunk)",
            {"fp": uuid.uuid4().hex, "chunk": ids["pricing"]},
        ),
        (
            "INSERT INTO knowledge_items (organization_id, item_type, title, content_fingerprint, "
            "source_chunk_id) VALUES (gen_random_uuid(), 'certification', 'PMP', :fp, :chunk)",
            {"fp": uuid.uuid4().hex, "chunk": ids["cert"]},
        ),
        (
            "INSERT INTO knowledge_items (organization_id, item_type, title, content_fingerprint, "
            "source_chunk_id) VALUES (gen_random_uuid(), 'past_project', 'Depot', :fp, :chunk)",
            {"fp": uuid.uuid4().hex, "chunk": ids["project"]},
        ),
    ]
    _run(dsn, statements)
    return ids


def _origins(dsn: str) -> dict[uuid.UUID, str]:
    return dict(_rows(dsn, "SELECT id, origin FROM document_chunks"))


def _has_origin(dsn: str) -> bool:
    return bool(
        _rows(
            dsn,
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'document_chunks' AND column_name = 'origin'",
        )
    )


def _unique_keys(dsn: str) -> dict[str, str]:
    return dict(
        _rows(
            dsn,
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = 'document_chunks'::regclass AND contype = 'u'",
        )
    )


def test_upgrade_marks_exactly_the_cited_synthetic_chunks_and_downgrade_renumbers(scratch):
    cfg, dsn = scratch
    ids = _seed(dsn)

    command.upgrade(cfg, AFTER)
    origins = _origins(dsn)
    assert {name: origins[ids[name]] for name in ids if name != "tender_doc"} == {
        "tender_cited": "parsed",
        "pricing": "synthetic",
        "cert": "synthetic",
        "cert_doc_parsed": "parsed",
        "project": "parsed",
    }
    assert _unique_keys(dsn) == {
        "uq_document_chunk_index": "UNIQUE (document_id, origin, chunk_index)",
        "uq_document_chunk_hash": "UNIQUE (document_id, origin, content_hash)",
    }

    # Allowed now: a parsed chunk on the index the synthetic one holds (a
    # re-parse that grew).
    grown, statement = _chunk(ids["tender_doc"], 1, "grown text")
    _run(dsn, [statement])

    command.downgrade(cfg, BEFORE)
    assert not _has_origin(dsn)
    assert _unique_keys(dsn) == {
        "uq_document_chunk_index": "UNIQUE (document_id, chunk_index)",
        "uq_document_chunk_hash": "UNIQUE (document_id, content_hash)",
    }
    indexes = dict(
        _rows(
            dsn,
            f"SELECT id, chunk_index FROM document_chunks WHERE document_id = '{ids['tender_doc']}'",
        )
    )
    assert indexes == {ids["tender_cited"]: 0, grown: 1, ids["pricing"]: 2}, (
        "the synthetic chunk moves after the highest parsed index"
    )

    command.upgrade(cfg, AFTER)
    origins = _origins(dsn)
    assert origins[ids["pricing"]] == "synthetic" and origins[grown] == "parsed"


def test_downgrade_refuses_when_a_parsed_and_a_synthetic_chunk_share_a_hash(scratch):
    cfg, dsn = scratch
    ids = _seed(dsn)
    command.upgrade(cfg, AFTER)
    # A parsed chunk whose text equals the pricing finding's note.
    clash_id = uuid.uuid4()
    _run(
        dsn,
        [
            (
                "INSERT INTO document_chunks (id, document_id, chunk_index, chunk_text, "
                "normalized_text, content_hash) VALUES (:id, :doc, 5, 'x', 'x', 'pricing text')",
                {"id": clash_id, "doc": ids["tender_doc"]},
            )
        ],
    )

    with pytest.raises(RuntimeError, match="share a content_hash"):
        command.downgrade(cfg, BEFORE)
    assert _has_origin(dsn), "a refused downgrade changes nothing"
