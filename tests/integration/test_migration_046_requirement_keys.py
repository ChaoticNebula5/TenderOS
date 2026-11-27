"""Migration 046 on a real database at 045 (plan item 126): the
requirement_keys table, backfilled with every requirement's own key and with
the key each plan item 125 overlap citation would have had, the foreign key
cascading; the requirement_code_counters table, backfilled with each prefix's
highest number in use; and a round trip.

Built like test_migration_045_requirement_citations.py: a scratch database
upgraded to 045 with real Alembic, with helpers copied, this codebase's
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
from tenderos_backend.core.config import settings
from tenderos_backend.core.hashing import sha256_hex

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BEFORE = "045_requirement_citations"
AFTER = "046_requirement_keys"


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
    name = f"migration_046_{uuid.uuid4().hex[:8]}"
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


def _requirement(
    db: Scratch, tender: uuid.UUID, chunk: uuid.UUID, normalized: str, fingerprint: str
) -> uuid.UUID:
    requirement_id = uuid.uuid4()
    db.execute(
        "INSERT INTO requirements (id, tender_id, display_code, requirement_text, normalized_text, "
        "category, mandatory, source_chunk_id, content_fingerprint) VALUES "
        "(:id, :tender, 'REQ-001', :text, :text, 'other', true, :chunk, :fp)",
        {
            "id": requirement_id,
            "tender": tender,
            "chunk": chunk,
            "text": normalized,
            "fp": fingerprint,
        },
    )
    db.execute(
        "INSERT INTO requirement_citations (requirement_id, chunk_id) VALUES (:r, :c)",
        {"r": requirement_id, "c": chunk},
    )
    return requirement_id


def _keys(db: Scratch) -> dict[str, uuid.UUID]:
    return {
        row["content_fingerprint"]: row["requirement_id"]
        for row in db.rows("SELECT content_fingerprint, requirement_id FROM requirement_keys")
    }


def _python_key(document: uuid.UUID, chunk: uuid.UUID, normalized: str) -> str:
    # requirement_service._fingerprint's formula, computed here independently.
    return sha256_hex(f"{document}|{chunk}|{normalized}".encode())


def test_upgrade_backfills_each_requirements_own_key_and_its_overlap_copies_keys(
    scratch: Scratch,
):
    tender, document = _bid(scratch)
    earlier, later, elsewhere = (_chunk(scratch, document, i) for i in (0, 1, 5))
    text_a = "Le soumissionnaire doit répondre à la question."
    own_a = _python_key(document, earlier, text_a)
    copied = _requirement(scratch, tender, earlier, text_a, own_a)
    # PR 38 recorded its overlap copy in the later chunk as a citation only.
    scratch.execute(
        "INSERT INTO requirement_citations (requirement_id, chunk_id) VALUES (:r, :c)",
        {"r": copied, "c": later},
    )
    # A row made by a merge before this item: its key names the tender.
    merged_key = sha256_hex(f"{tender}|{elsewhere}|merged text".encode())
    merged = _requirement(scratch, tender, elsewhere, "merged text", merged_key)

    command.upgrade(scratch.cfg, AFTER)

    assert scratch.version() == AFTER
    assert _keys(scratch) == {
        own_a: copied,
        # The copy's key, computed in SQL, equals Python's for the same parts
        # (the accented text checks the UTF-8 encoding on both sides).
        _python_key(document, later, text_a): copied,
        merged_key: merged,
    }


def test_a_computed_key_already_held_stays_with_its_requirement(scratch: Scratch):
    """Two rows of the same text, one in each chunk, both extracted before
    PR 38, and the first also citing the second's chunk: the computed key is
    the second row's own, which it keeps."""
    tender, document = _bid(scratch)
    earlier, later = _chunk(scratch, document, 0), _chunk(scratch, document, 1)
    words = "The bidder shall hold a certificate."
    first = _requirement(scratch, tender, earlier, words, _python_key(document, earlier, words))
    second_key = _python_key(document, later, words)
    second = _requirement(scratch, tender, later, words, second_key)
    scratch.execute(
        "INSERT INTO requirement_citations (requirement_id, chunk_id) VALUES (:r, :c)",
        {"r": first, "c": later},
    )

    command.upgrade(scratch.cfg, AFTER)

    assert _keys(scratch)[second_key] == second


def test_upgrade_sets_each_prefix_counter_to_the_highest_number_in_use(scratch: Scratch):
    """Rejected rows count (their numbers are in use), a code that is not
    PREFIX-NNN counts for nothing, and each bid has its own counters."""
    tender, document = _bid(scratch)
    other, other_document = _bid(scratch)
    chunk, other_chunk = _chunk(scratch, document, 0), _chunk(scratch, other_document, 0)
    seeded = [
        (tender, chunk, "ELIG-001", False),
        (tender, chunk, "ELIG-007", True),
        (tender, chunk, "TECH-002", False),
        (tender, chunk, "manual", False),
        (other, other_chunk, "ELIG-003", False),
    ]
    for bid, cited, code, rejected in seeded:
        scratch.execute(
            "INSERT INTO requirements (id, tender_id, display_code, requirement_text, "
            "normalized_text, category, mandatory, source_chunk_id, content_fingerprint, "
            "rejected_at) VALUES (:id, :tender, :code, 'Text.', 'Text.', 'other', true, :chunk, "
            ":fp, CASE WHEN :rejected THEN now() END)",
            {
                "id": uuid.uuid4(),
                "tender": bid,
                "code": code,
                "chunk": cited,
                "fp": uuid.uuid4().hex,
                "rejected": rejected,
            },
        )

    command.upgrade(scratch.cfg, AFTER)

    counters = {
        (row["tender_id"], row["prefix"]): row["last_number"]
        for row in scratch.rows(
            "SELECT tender_id, prefix, last_number FROM requirement_code_counters"
        )
    }
    assert counters == {(tender, "ELIG"): 7, (tender, "TECH"): 2, (other, "ELIG"): 3}


def test_a_requirements_keys_go_with_it(scratch: Scratch):
    command.upgrade(scratch.cfg, AFTER)
    tender, document = _bid(scratch)
    chunk = _chunk(scratch, document, 0)
    removed = _requirement(scratch, tender, chunk, "Text.", uuid.uuid4().hex)
    scratch.execute(
        "INSERT INTO requirement_keys (content_fingerprint, requirement_id) VALUES (:k, :r)",
        {"k": uuid.uuid4().hex, "r": removed},
    )
    assert len(_keys(scratch)) == 1, "only the row inserted here: the table starts empty"

    scratch.execute("DELETE FROM requirements WHERE id = :id", {"id": removed})

    assert _keys(scratch) == {}


def test_downgrade_drops_the_table_and_upgrade_runs_again(scratch: Scratch):
    tender, document = _bid(scratch)
    chunk = _chunk(scratch, document, 0)
    fingerprint = _python_key(document, chunk, "Text.")
    requirement_id = _requirement(scratch, tender, chunk, "Text.", fingerprint)
    command.upgrade(scratch.cfg, AFTER)

    command.downgrade(scratch.cfg, BEFORE)

    assert scratch.version() == BEFORE
    assert scratch.rows(
        "SELECT count(*) AS n FROM information_schema.tables "
        "WHERE table_name IN ('requirement_keys', 'requirement_code_counters')"
    ) == [{"n": 0}]
    assert [
        row["content_fingerprint"]
        for row in scratch.rows("SELECT content_fingerprint FROM requirements")
    ] == [fingerprint]

    command.upgrade(scratch.cfg, AFTER)

    assert _keys(scratch) == {fingerprint: requirement_id}
