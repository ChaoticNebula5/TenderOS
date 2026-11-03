"""Phase 4 plan item 41: an organization-filtered vector search returns its
LIMIT, not the few rows that survive pgvector's filter-after-scan.

`search_chunks_vector_for_org` orders by `embedding <=> :query` over one
HNSW index shared by every organization and filters by organization after
the index scan. With pgvector's defaults the scan yields hnsw.ef_search (40)
candidates from any organization, so a small organization in a large table
got few of its own (observed in item 40: 1 row of 20). The method now turns
on an iterative scan for its own transaction.

Each case runs in one transaction that is rolled back: seed one large and
one small organization with deterministic random vectors, force the index
(sequential scans and sorts off; on tables this small the planner would
otherwise skip it and no test would show the problem), call the real
method, and EXPLAIN the exact statement it sent. The EXPLAIN matters:
without the index, the planner reaches the chunks through the btree
indexes and a sort on distance, and returns every row without any
iterative scan. Sorts are off as well because, with only sequential scans
off, that btree path and the HNSW scan cost about the same here (observed
2026-11-03: 498.81 to 504.40 against 454.98 to 567.43), so the planner
chose either from run to run and the plan check failed 3 of 3 times in one
session after passing in others.
"""

import logging
import random
import uuid

import pytest
from sqlalchemy import event, insert, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from tenderos_backend.core.config import settings
from tenderos_backend.db.models.document import Document, DocumentChunk
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.repositories.documents import DocumentRepository

pytestmark = pytest.mark.asyncio

_DIMENSIONS = 384
_LIMIT = 20
_LARGE_ORGANIZATION_CHUNKS = 2000
_HNSW_INDEX = "idx_document_chunks_embedding_hnsw"


def _vector(rng: random.Random) -> list[float]:
    return [rng.gauss(0.0, 1.0) for _ in range(_DIMENSIONS)]


async def _seed_organization(
    session: AsyncSession, chunks: int, rng: random.Random
) -> tuple[uuid.UUID, uuid.UUID]:
    org = Organization(name=f"Vector Filter Org {uuid.uuid4().hex[:8]}")
    session.add(org)
    await session.flush()
    document = Document(
        organization_id=org.id,
        side="company",
        doc_type="company_document",
        filename="knowledge.docx",
        storage_key=f"test/{uuid.uuid4()}",
        sha256=uuid.uuid4().hex,
        parse_status="parsed",
    )
    session.add(document)
    await session.flush()
    await session.execute(
        insert(DocumentChunk),
        [
            {
                "document_id": document.id,
                "chunk_index": i,
                "chunk_text": f"chunk {i}",
                "normalized_text": f"chunk {i}",
                "content_hash": f"hash-{i}",
                "embedding": _vector(rng),
            }
            for i in range(chunks)
        ],
    )
    return org.id, document.id


async def _search_the_small_organization(small_chunks: int):
    """Returns the document of each row the real method found, the small
    organization's document and id, and the plan of the statement sent."""
    rng = random.Random(41)
    engine = create_async_engine(settings.postgres_dsn)
    sent: list[tuple[str, object]] = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        if "<=>" in statement:
            sent.append((statement, parameters))

    event.listen(engine.sync_engine, "before_cursor_execute", _record)
    try:
        async with AsyncSession(engine) as session:
            # SET LOCAL here as in the method: these end with this
            # transaction, which is rolled back below.
            await session.execute(text("SET LOCAL enable_seqscan = off"))
            await session.execute(text("SET LOCAL enable_sort = off"))
            await _seed_organization(session, _LARGE_ORGANIZATION_CHUNKS, rng)
            small_org, small_document = await _seed_organization(session, small_chunks, rng)
            await session.execute(text("ANALYZE documents, document_chunks"))

            rows = await DocumentRepository(session).search_chunks_vector_for_org(
                small_org, _vector(rng), _LIMIT
            )
            # Read before the rollback, which expires the loaded rows.
            found = [chunk.document_id for chunk, _ in rows]

            assert len(sent) == 1, sent
            statement, parameters = sent[0]
            conn = await session.connection()
            plan = "\n".join(
                row[0] for row in await conn.exec_driver_sql(f"EXPLAIN {statement}", parameters)
            )
            await session.rollback()
    finally:
        await engine.dispose()
    return found, small_document, small_org, plan


async def test_a_small_organization_in_a_large_table_gets_its_limit():
    found, small_document, _, plan = await _search_the_small_organization(small_chunks=30)

    assert f"Index Scan using {_HNSW_INDEX}" in plan, plan
    assert len(found) == min(_LIMIT, 30)
    assert set(found) == {small_document}


async def test_fewer_chunks_than_the_limit_returns_them_all_and_logs_the_shortfall(caplog):
    caplog.set_level(logging.INFO, logger="tenderos_backend.db.repositories.documents")

    found, small_document, small_org, plan = await _search_the_small_organization(small_chunks=5)

    assert f"Index Scan using {_HNSW_INDEX}" in plan, plan
    assert len(found) == 5
    assert set(found) == {small_document}
    shortfalls = [r.getMessage() for r in caplog.records if "vector search" in r.getMessage()]
    assert shortfalls == [f"vector search for organization {small_org} returned 5 of 20 rows"]
