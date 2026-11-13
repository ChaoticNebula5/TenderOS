"""Phase 4 plan item 62: company-knowledge lexical search matches a chunk that
shares any of the query's terms, not only one that holds all of them.

`search_chunks_lexical_for_org` built its query with plainto_tsquery, which
joins every term with AND. A requirement sentence has 10 or more terms, so a
knowledge chunk almost never held them all: the evaluation under eval/ found
lexical rows for 0 of 101 benchmark queries, and reciprocal rank fusion had
only the vector list to fuse. The method now keeps plainto_tsquery's parsing,
stemming and stop words and joins the terms with OR; ts_rank orders the
chunks that share more terms first.

Each case seeds its organizations in one transaction, calls the real method
on that session, and rolls back. No embeddings: the lexical channel reads
normalized_text only.
"""

import uuid

import pytest
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from tenderos_backend.core.config import settings
from tenderos_backend.db.models.document import Document, DocumentChunk
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.repositories.documents import DocumentRepository

pytestmark = pytest.mark.asyncio

_LIMIT = 20

# Two sentences, as a requirement often is. Its terms after parsing include
# 'claus', 'bidder', 'requir', 'technic' and 'bid', which no knowledge chunk
# below contains, so under AND nothing matches.
_REQUIREMENT = (
    "Clause 9.1: The bidder is required to hold a current ISO 27001 certification. "
    "A copy of the certificate must be submitted with the technical bid."
)


async def _organization_with_chunks(session: AsyncSession, texts: list[str]) -> tuple:
    """One organization with one company document holding these chunks, in
    order. Returns the organization id and the chunk ids."""
    org = Organization(name=f"Lexical Org {uuid.uuid4().hex[:8]}")
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
    ids = [uuid.uuid4() for _ in texts]
    await session.execute(
        insert(DocumentChunk),
        [
            {
                "id": chunk_id,
                "document_id": document.id,
                "chunk_index": i,
                "chunk_text": text,
                "normalized_text": text.lower(),
                "content_hash": f"hash-{i}",
            }
            for i, (chunk_id, text) in enumerate(zip(ids, texts, strict=True))
        ],
    )
    return org.id, ids


async def _search(setup, query: str) -> tuple[list[uuid.UUID], dict]:
    """Runs setup(session) in a fresh transaction, searches the organization
    it names, and rolls back. Returns the found chunk ids in rank order and
    whatever setup returned."""
    engine = create_async_engine(settings.postgres_dsn)
    try:
        async with AsyncSession(engine) as session:
            seeded = await setup(session)
            rows = await DocumentRepository(session).search_chunks_lexical_for_org(
                seeded["search_org"], query, _LIMIT
            )
            # Read before the rollback, which expires the loaded rows.
            found = [chunk.id for chunk, _ in rows]
            await session.rollback()
    finally:
        await engine.dispose()
    return found, seeded


async def test_a_requirement_sentence_finds_a_chunk_that_shares_only_some_terms():
    async def setup(session):
        org, (certificate, unrelated) = await _organization_with_chunks(
            session,
            [
                "Velantra holds ISO 27001 certification for its information security system.",
                "Office furniture is supplied under a framework agreement.",
            ],
        )
        return {"search_org": org, "certificate": certificate, "unrelated": unrelated}

    found, seeded = await _search(setup, _REQUIREMENT)

    assert found == [seeded["certificate"]]


async def test_a_chunk_sharing_more_terms_ranks_above_one_sharing_fewer():
    async def setup(session):
        org, (fewer, more) = await _organization_with_chunks(
            session,
            [
                # Shares 'iso' only.
                "An overview of ISO standards.",
                # Shares 'hold', 'current', 'iso', '27001', 'certif'.
                "We hold a current ISO 27001 certification, renewed each year.",
            ],
        )
        return {"search_org": org, "fewer": fewer, "more": more}

    found, seeded = await _search(setup, _REQUIREMENT)

    assert found == [seeded["more"], seeded["fewer"]]


async def test_a_query_of_only_stop_words_returns_no_rows_and_does_not_fail():
    async def setup(session):
        org, _ = await _organization_with_chunks(
            session, ["The company is one of the oldest in the region."]
        )
        return {"search_org": org}

    found, _ = await _search(setup, "the and of to is it be as")

    assert found == []


async def test_another_organizations_matching_chunks_are_never_returned():
    async def setup(session):
        own, (own_chunk,) = await _organization_with_chunks(
            session, ["We hold ISO 27001 certification."]
        )
        # A better match than the searching organization's own chunk.
        await _organization_with_chunks(
            session, ["The bidder holds a current ISO 27001 certification for technical bids."]
        )
        return {"search_org": own, "own_chunk": own_chunk}

    found, seeded = await _search(setup, _REQUIREMENT)

    assert found == [seeded["own_chunk"]]


async def test_punctuation_and_quotes_in_the_query_do_not_break_it():
    """The OR is made by rewriting the query's text form; a quote, a slash,
    a colon or a literal & or | in the requirement must still parse."""

    async def setup(session):
        org, (certificate,) = await _organization_with_chunks(
            session, ["ISO 27001 certification held since 2019."]
        )
        return {"search_org": org, "certificate": certificate}

    query = "O'Brien's team & co-operation | ISO/IEC 27001:2022 (certification) - see 'Annex B'!"
    found, seeded = await _search(setup, query)

    assert found == [seeded["certificate"]]
