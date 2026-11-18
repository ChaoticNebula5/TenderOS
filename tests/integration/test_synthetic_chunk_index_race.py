"""Plan item 59, PR 19: synthetic chunks on one document, created at the same
time or with the same text.

A synthetic chunk's index is one past the highest synthetic index on its
document (DocumentRepository.next_synthetic_chunk_index). Two transactions
that read it before either commits pick the same index, and the second
insert fails on uq_document_chunk_index: two quick Resolve clicks on two
findings in one pricing sheet answered the second with a 500 (observed
before this fix). create_synthetic_chunk now takes a per-document advisory
lock before reading the index, and the pricing path locks the tender first,
so the order there is tender, then document.

Each race runs in one order, with no sleeps: the first transaction is held
open, the second is started and the test waits until Postgres shows it
waiting on a lock, then the first commits.
"""

import asyncio
import uuid
from datetime import date

import pytest
from sqlalchemy import select, text
from tenderos_backend.agents.knowledge.certifications_xlsx import CertificationRow
from tenderos_backend.db.models.audit import AuditEvent
from tenderos_backend.db.models.commercial import PricingFinding
from tenderos_backend.db.models.document import DocumentChunk
from tenderos_backend.db.models.knowledge import KnowledgeItem
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import commercial_service, document_service, knowledge_service

pytestmark = pytest.mark.asyncio

# Any session of this database waiting on a lock: a row lock, a unique
# index wait or an advisory lock.
_SOMEONE_WAITING = text(
    "SELECT count(*) FROM pg_stat_activity "
    "WHERE datname = current_database() AND wait_event_type = 'Lock'"
)


async def _waiting(task: asyncio.Task, what: str) -> None:
    """Wait at most 10 s until a session waits on a lock, with `task` still
    running."""
    for _ in range(200):
        if task.done():
            break
        async with get_session() as session:
            if (await session.execute(_SOMEONE_WAITING)).scalar_one() > 0:
                return
        await asyncio.sleep(0.05)
    raise AssertionError(f"{what}: {task!r}")


async def _tender() -> uuid.UUID:
    async with get_session() as session:
        org = Organization(name=f"Chunk Index {uuid.uuid4().hex[:8]} Org")
        session.add(org)
        await session.flush()
        tender = Tender(
            organization_id=org.id, name="Chunk Index", submission_date=date(2026, 12, 1)
        )
        session.add(tender)
        await session.flush()
        await session.commit()
        return tender.id


async def _pricing_document(tender_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    async with UnitOfWork() as uow:
        document = await uow.documents.create(
            side="client",
            doc_type="rfp",
            filename="pricing.xlsx",
            storage_key=f"test/{uuid.uuid4()}",
            sha256=uuid.uuid4().hex,
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            file_size_bytes=10,
            tender_id=tender_id,
        )
        pricing_document = await uow.commercial.create_pricing_document(
            tender_id=tender_id, document_id=document.id, sheet_name="Schedule"
        )
        await uow.commit()
        return document.id, pricing_document.id


async def _finding(tender_id: uuid.UUID, pricing_document_id: uuid.UUID, note: str) -> uuid.UUID:
    async with UnitOfWork() as uow:
        finding = await uow.commercial.create_pricing_finding(
            tender_id=tender_id,
            finding_type="wrong_total",
            note=note,
            content_fingerprint=uuid.uuid4().hex,
            pricing_document_id=pricing_document_id,
        )
        await uow.commit()
        return finding.id


async def _chunks(document_id: uuid.UUID) -> list[tuple[int, str, str]]:
    async with get_session() as session:
        rows = await session.execute(
            select(DocumentChunk.chunk_index, DocumentChunk.origin, DocumentChunk.chunk_text)
            .where(DocumentChunk.document_id == document_id)
            .order_by(DocumentChunk.origin, DocumentChunk.chunk_index)
        )
        return [(r[0], r[1], r[2]) for r in rows.all()]


async def _resolved(finding_id: uuid.UUID) -> bool:
    async with get_session() as session:
        return (
            await session.execute(
                select(PricingFinding.resolved).where(PricingFinding.id == finding_id)
            )
        ).scalar_one()


async def test_two_resolves_on_one_pricing_sheet_both_resolve():
    """R2 of item 59's reproduction: the second resolve failed on
    uq_document_chunk_index and its finding stayed unresolved. Now it waits
    for the first and takes the next index."""
    tender_id = await _tender()
    document_id, pricing_document_id = await _pricing_document(tender_id)
    first = await _finding(tender_id, pricing_document_id, "Item 4 total is wrong.")
    second = await _finding(tender_id, pricing_document_id, "Item 9 total is wrong.")
    user_id = uuid.uuid4()

    async with UnitOfWork() as holder:
        await commercial_service.resolve_pricing_finding_in_uow(holder, first, user_id)
        other = asyncio.create_task(commercial_service.resolve_pricing_finding(second, user_id))
        await _waiting(other, "the second resolve never waited for the first")
        await holder.commit()

    resolved = await asyncio.wait_for(other, timeout=10)
    assert resolved.resolved is True
    assert await _resolved(first) is True
    assert await _resolved(second) is True
    assert await _chunks(document_id) == [
        (0, "synthetic", "Item 4 total is wrong."),
        (1, "synthetic", "Item 9 total is wrong."),
    ]


async def test_two_synthetic_chunks_for_one_document_take_consecutive_indexes():
    """The document lock itself, without the pricing path's tender lock: the
    certification path has no tender, so only this lock orders it."""
    tender_id = await _tender()
    document_id, _ = await _pricing_document(tender_id)

    async def second_chunk() -> int:
        async with UnitOfWork() as uow:
            chunk = await document_service.create_synthetic_chunk(
                uow, document_id, "second", embed=False
            )
            await uow.commit()
            return chunk.chunk_index

    async with UnitOfWork() as holder:
        first = await document_service.create_synthetic_chunk(
            holder, document_id, "first", embed=False
        )
        other = asyncio.create_task(second_chunk())
        await _waiting(other, "the second chunk never waited for the first")
        await holder.commit()

    assert first.chunk_index == 0
    assert await asyncio.wait_for(other, timeout=10) == 1


async def test_the_pricing_path_takes_the_tender_before_touching_any_chunk():
    """Lock order on the pricing path: the tender, then the document. While
    another transaction holds the tender, a resolve waits on it before it has
    read an index, inserted a chunk or taken a document lock. Taking the
    tender after a chunk (the bump at the end, as before) could deadlock two
    cross-sheet findings that ground the same two sheets in opposite
    orders."""
    tender_id = await _tender()
    document_id, pricing_document_id = await _pricing_document(tender_id)
    finding = await _finding(tender_id, pricing_document_id, "Item 4 total is wrong.")

    async with UnitOfWork() as holder:
        await holder.session.execute(
            text("SELECT 1 FROM tenders WHERE id = :id FOR UPDATE"), {"id": tender_id}
        )
        resolve = asyncio.create_task(
            commercial_service.resolve_pricing_finding(finding, uuid.uuid4())
        )
        await _waiting(resolve, "the resolve never waited for the tender")
        # What the waiting resolve holds: no lock on document_chunks and no
        # advisory lock, so it has not reached the chunk yet.
        held = (
            await holder.session.execute(
                text(
                    "SELECT count(*) FROM pg_locks l "
                    "JOIN pg_stat_activity a ON a.pid = l.pid "
                    "LEFT JOIN pg_class c ON c.oid = l.relation "
                    "WHERE a.wait_event_type = 'Lock' AND l.granted "
                    "AND (c.relname = 'document_chunks' OR l.locktype = 'advisory')"
                )
            )
        ).scalar_one()
        assert held == 0, "the resolve took a chunk or document lock before the tender"
        await holder.commit()

    await asyncio.wait_for(resolve, timeout=10)
    assert await _resolved(finding) is True
    assert await _chunks(document_id) == [(0, "synthetic", "Item 4 total is wrong.")]


async def test_a_synthetic_chunk_with_the_same_text_on_one_document_is_reused():
    """uq_document_chunk_hash (document, origin, hash) refused a second
    synthetic chunk with the same text, even one after the other. The
    existing chunk is now reused."""
    tender_id = await _tender()
    document_id, _ = await _pricing_document(tender_id)

    async with UnitOfWork() as uow:
        first = await document_service.create_synthetic_chunk(
            uow, document_id, "Same text.", embed=False
        )
        await uow.commit()
    async with UnitOfWork() as uow:
        second = await document_service.create_synthetic_chunk(
            uow, document_id, "Same text.", embed=False
        )
        await uow.commit()

    assert second.id == first.id
    assert await _chunks(document_id) == [(0, "synthetic", "Same text.")]


async def test_a_parsed_chunk_with_the_same_text_is_not_reused():
    """Guard (passes before this fix too): the reuse is among synthetic
    chunks only. A parsed chunk belongs to the parser, and a re-parse may
    delete it, so a record must never cite it as its own synthetic chunk.
    The hash key includes the origin, so the two do not collide."""
    tender_id = await _tender()
    document_id, _ = await _pricing_document(tender_id)
    async with UnitOfWork() as uow:
        parsed = await uow.documents.create_chunk(
            document_id=document_id,
            chunk_index=0,
            chunk_text="Same text.",
            normalized_text=document_service.normalize_text("Same text."),
            # The hash create_synthetic_chunk computes for the same text, so
            # only the origin tells the two apart.
            content_hash=document_service.sha256_hex(
                document_service.normalize_text("Same text.").encode("utf-8")
            ),
        )
        await uow.commit()

    async with UnitOfWork() as uow:
        synthetic = await document_service.create_synthetic_chunk(
            uow, document_id, "Same text.", embed=False
        )
        await uow.commit()

    assert synthetic.id != parsed.id
    assert synthetic.origin == "synthetic"
    assert await _chunks(document_id) == [
        (0, "parsed", "Same text."),
        (1, "synthetic", "Same text."),
    ]


async def test_two_holders_of_the_same_certificate_in_one_sheet_both_import():
    """The user-visible case of the same-text failure: two employees holding
    the same certificate (same name, issuer and dates) on one certifications
    sheet render the same chunk text. The second row used to raise
    IntegrityError (its certificate's fingerprint differs by holder, so the
    re-read found nothing). Both rows now import and cite the one chunk."""
    async with get_session() as session:
        org = Organization(name=f"Same Cert {uuid.uuid4().hex[:8]} Org")
        session.add(org)
        await session.flush()
        await session.commit()
        org_id = org.id
    async with UnitOfWork() as uow:
        for code in ("E-1", "E-2"):
            await uow.knowledge.create_employee(
                organization_id=org_id,
                name=f"Holder {code}",
                content_fingerprint=uuid.uuid4().hex,
                title=f"Holder {code}",
                employee_code=code,
            )
        document = await uow.documents.create(
            side="company",
            doc_type="certification_documents",
            filename="certifications.xlsx",
            storage_key=f"test/{uuid.uuid4()}",
            sha256=uuid.uuid4().hex,
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            file_size_bytes=10,
            organization_id=org_id,
        )
        batch = await uow.knowledge.create_import_batch(
            organization_id=org_id,
            import_type="certification_documents",
            source_document_id=document.id,
            created_by=None,
        )
        await uow.commit()

    items = []
    for row_number, code in ((2, "E-1"), (3, "E-2")):
        row = CertificationRow(
            row_number=row_number,
            cert_key=f"CERT-{row_number}",
            employee_ref=code,
            name="ISO 27001 Lead Auditor",
            issuer="Example Body",
            issue_date=date(2024, 1, 1),
            expiry_date=date(2027, 1, 1),
        )
        items.append(
            await knowledge_service.import_certification_row(
                org_id, batch.id, document.id, row, today=date(2026, 1, 1)
            )
        )

    assert [item.status for item in items] == ["approved", "approved"]
    async with get_session() as session:
        cited = (
            (
                await session.execute(
                    select(KnowledgeItem.source_chunk_id).where(
                        KnowledgeItem.source_document_id == document.id,
                        KnowledgeItem.item_type == "certification",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(cited) == 2
    assert len(set(cited)) == 1, "both certificates cite the one synthetic chunk"


async def test_one_finding_resolved_twice_at_once_grounds_once_and_audits_what_it_found():
    """A double submit of one finding. The second resolve waits for the
    first, then reads the finding again under the tender's lock: it adds no
    second chunk or link, and its audit row's "before" says the finding was
    already resolved (read before the lock, it said unresolved)."""
    tender_id = await _tender()
    document_id, pricing_document_id = await _pricing_document(tender_id)
    finding = await _finding(tender_id, pricing_document_id, "Item 4 total is wrong.")
    user_id = uuid.uuid4()

    async with UnitOfWork() as holder:
        await commercial_service.resolve_pricing_finding_in_uow(holder, finding, user_id)
        other = asyncio.create_task(commercial_service.resolve_pricing_finding(finding, user_id))
        await _waiting(other, "the second resolve never waited for the first")
        await holder.commit()

    await asyncio.wait_for(other, timeout=10)
    assert await _chunks(document_id) == [(0, "synthetic", "Item 4 total is wrong.")]
    async with UnitOfWork() as uow:
        links = await uow.compliance.list_evidence_links_for_tender(tender_id)
    assert len(links) == 1
    async with get_session() as session:
        befores = (
            (
                await session.execute(
                    select(AuditEvent.before)
                    .where(
                        AuditEvent.target_type == "pricing_finding",
                        AuditEvent.target_id == finding,
                    )
                    .order_by(AuditEvent.created_at)
                )
            )
            .scalars()
            .all()
        )
    assert befores == [{"resolved": False}, {"resolved": True}]
