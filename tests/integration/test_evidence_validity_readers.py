"""Plan items 89 and 91: every evidence reader judges a knowledge item now,
by the one definition (core/evidence_validity.py), and none trusts a
knowledge-item link's stored verified/validity_status.

Each reader is given the same kinds of link:
- a certificate whose valid_until is yesterday, and one whose status is
  `expired`, both stored verified and valid (as the evaluation that cited
  them while in date would store them): neither counts;
- a certificate in date but stored invalid and unverified (an evaluation
  that ran while it was lapsed, before it was renewed): it counts, because
  the stored values are not read for it;
- a document excerpt (no item): its stored values decide, as before.
Plus `unverified` (Q1: valid, not verified), an employee's live profile
review (Q2), the roster readers staying on archived (Q4), readiness step
2's messages (addition 3), and a date passing reaching readiness with no
bump.
"""

import uuid
from datetime import timedelta

import pytest
import pytest_asyncio
from readiness_facts import add_parsed_document, mark_chunks_read
from sqlalchemy import update
from tenderos_backend.agents.knowledge.retrieval import KnowledgeSearchResult
from tenderos_backend.core.evidence_validity import today_utc
from tenderos_backend.db.models.knowledge import Employee
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import (
    compliance_service,
    proposal_service,
    readiness_service,
)
from tenderos_backend.graph_projection.driver import get_session as get_neo4j_session

pytestmark = pytest.mark.asyncio

TODAY = today_utc()
YESTERDAY = TODAY - timedelta(days=1)
TOMORROW = TODAY + timedelta(days=1)


@pytest_asyncio.fixture(autouse=True)
async def _clean_neo4j():
    yield
    async with get_neo4j_session() as session:
        await session.run("MATCH (n) DETACH DELETE n")


async def _bid() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    """(org_id, tender_id, chunk_id, compliance_entry_id): one approved,
    fully compliant entry with no evidence yet."""
    async with get_session() as session:
        org = Organization(name="Evidence Validity Org")
        session.add(org)
        await session.flush()
        tender = Tender(organization_id=org.id, name="Evidence Validity Tender")
        session.add(tender)
        await session.commit()
    await add_parsed_document(tender.id)
    async with UnitOfWork() as uow:
        document = await uow.documents.create(
            side="client",
            doc_type="rfp",
            filename="req.txt",
            storage_key=f"test/{uuid.uuid4()}",
            sha256=uuid.uuid4().hex,
            mime_type="text/plain",
            file_size_bytes=10,
            tender_id=tender.id,
        )
        await uow.documents.update_parse_status(document.id, "parsed")
        chunk = await uow.documents.create_chunk(
            document_id=document.id,
            chunk_index=0,
            chunk_text="req",
            normalized_text="req",
            content_hash=uuid.uuid4().hex,
        )
        requirement = await uow.requirements.create(
            tender_id=tender.id,
            display_code=f"TECH-{uuid.uuid4().hex[:6]}",
            requirement_text="Hold an ISO 27001 certification.",
            normalized_text="hold an iso 27001 certification.",
            category="technical",
            mandatory=True,
            source_chunk_id=chunk.id,
            content_fingerprint=uuid.uuid4().hex,
        )
        entry = await uow.compliance.create_compliance_entry(
            requirement_id=requirement.id, status="fully_compliant"
        )
        await uow.compliance.update_compliance_entry(entry.id, human_approved=True)
        await uow.commit()
    await mark_chunks_read(tender.id)
    return org.id, tender.id, chunk.id, entry.id


async def _cert(
    org_id: uuid.UUID, title: str, status: str = "active", valid_until=TOMORROW, holder=None
) -> uuid.UUID:
    async with UnitOfWork() as uow:
        item, _ = await uow.knowledge.create_certification(
            organization_id=org_id,
            title=title,
            content_fingerprint=uuid.uuid4().hex,
            holder_employee_item_id=holder,
            valid_until=valid_until,
            status=status,
        )
        await uow.commit()
        return item.id


async def _employee(org_id: uuid.UUID, profile: str) -> uuid.UUID:
    async with UnitOfWork() as uow:
        item, _ = await uow.knowledge.create_employee(
            organization_id=org_id,
            name="Priya Ostrowski",
            content_fingerprint=uuid.uuid4().hex,
            title="Priya Ostrowski",
        )
        await uow.session.execute(
            update(Employee)
            .where(Employee.knowledge_item_id == item.id)
            .values(profile_review_status=profile)
        )
        await uow.commit()
        return item.id


async def _link(
    tender_id: uuid.UUID,
    entry_id: uuid.UUID,
    item_id: uuid.UUID | None,
    *,
    verified: bool = True,
    validity: str = "valid",
    chunk_id: uuid.UUID | None = None,
):
    async with UnitOfWork() as uow:
        link = await uow.compliance.create_evidence_link(
            tender_id=tender_id,
            source_kind="certification" if item_id else "document_excerpt",
            content_fingerprint=uuid.uuid4().hex,
            knowledge_item_id=item_id,
            source_chunk_id=chunk_id,
            verified=verified,
            validity_status=validity,
        )
        await uow.compliance.link_evidence(entry_id, link.id)
        await uow.commit()
        return link


async def _step2_messages(tender_id: uuid.UUID, entry_id: uuid.UUID) -> list[str]:
    result = await readiness_service.evaluate_readiness(tender_id)
    return [
        b["message"]
        for b in result.blockers
        if b["category"] == readiness_service.COMPLIANT_DECISION_WITHOUT_VALID_EVIDENCE
        and b["target_id"] == str(entry_id)
    ]


async def _filter_offers(link) -> bool:
    async with UnitOfWork() as uow:
        facts = await proposal_service._prepare_evidence_facts(uow, [link], set(), 1)
    return [f.evidence_link_id for f in facts] == [link.id]


async def _claim_trace(link_id: uuid.UUID) -> tuple[str, bool]:
    async with UnitOfWork() as uow:
        entry = await proposal_service._resolve_evidence_trace_entry(uow, link_id)
    assert entry is not None
    return entry.validity_status, entry.verified


async def _compliance_trace(entry_id: uuid.UUID, link_id: uuid.UUID) -> tuple[str, bool]:
    trace = await compliance_service.get_compliance_trace(entry_id)
    (entry,) = [e for e in trace.evidence if e.evidence_link_id == link_id]
    return entry.validity_status, entry.verified


async def _fresh_signals(item_id: uuid.UUID, item_type: str) -> tuple[str, bool]:
    result = KnowledgeSearchResult(
        knowledge_item_id=item_id,
        item_type=item_type,
        title="t",
        excerpt="e",
        document_id=uuid.uuid4(),
        chunk_id=uuid.uuid4(),
        page_num=None,
        heading_path=None,
        fusion_score=1.0,
        rerank_score=1.0,
    )
    async with UnitOfWork() as uow:
        signals = await compliance_service._compute_candidate_signals(uow, result, TODAY)
    return signals.validity_status, signals.verified


# ----------------------------------------------------------------------
# A certificate whose date passed, or whose status is expired, stored valid
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "valid_until", "expected_message_tail"),
    [
        ("active", YESTERDAY, f"its evidence 'ISO 27001 Lead Implementer' expired on {YESTERDAY}."),
        ("expired", None, "its evidence 'ISO 27001 Lead Implementer' expired."),
    ],
    ids=["date passed", "expired status"],
)
async def test_a_lapsed_certificate_stored_valid_counts_nowhere(
    status, valid_until, expected_message_tail
):
    org_id, tender_id, _chunk, entry_id = await _bid()
    cert_id = await _cert(org_id, "ISO 27001 Lead Implementer", status, valid_until)
    link = await _link(tender_id, entry_id, cert_id)

    messages = await _step2_messages(tender_id, entry_id)
    assert len(messages) == 1 and messages[0].endswith(expected_message_tail), messages
    assert not await _filter_offers(link)
    assert await _claim_trace(link.id) == ("expired", status == "active")
    assert await _compliance_trace(entry_id, link.id) == ("expired", status == "active")
    assert await _fresh_signals(cert_id, "certification") == ("expired", status == "active")


async def test_a_certificate_stored_invalid_but_in_date_now_counts_everywhere():
    """The stored values are not read for a knowledge-item link."""
    org_id, tender_id, _chunk, entry_id = await _bid()
    cert_id = await _cert(org_id, "PMP", "active", TOMORROW)
    link = await _link(tender_id, entry_id, cert_id, verified=False, validity="expired")

    assert await _step2_messages(tender_id, entry_id) == []
    assert await _filter_offers(link)
    assert await _claim_trace(link.id) == ("valid", True)
    assert await _compliance_trace(entry_id, link.id) == ("valid", True)


@pytest.mark.parametrize(
    ("verified", "validity", "counts"), [(True, "valid", True), (False, "valid", False)]
)
async def test_a_document_excerpt_keeps_its_stored_values(verified, validity, counts):
    _org_id, tender_id, chunk_id, entry_id = await _bid()
    link = await _link(
        tender_id, entry_id, None, verified=verified, validity=validity, chunk_id=chunk_id
    )

    assert (await _step2_messages(tender_id, entry_id) == []) is counts
    assert await _filter_offers(link) is counts
    assert await _claim_trace(link.id) == (validity, verified)


async def test_an_unverified_certificate_is_valid_but_not_verified():
    """Q1: label valid, verified false, so it counts nowhere."""
    org_id, tender_id, _chunk, entry_id = await _bid()
    cert_id = await _cert(org_id, "CISSP", "unverified", None)
    link = await _link(tender_id, entry_id, cert_id)

    messages = await _step2_messages(tender_id, entry_id)
    assert len(messages) == 1 and messages[0].endswith("its evidence 'CISSP' is unverified.")
    assert not await _filter_offers(link)
    assert await _claim_trace(link.id) == ("valid", False)
    assert await _fresh_signals(cert_id, "certification") == ("valid", False)


async def test_a_withdrawn_certificate_says_so():
    org_id, tender_id, _chunk, entry_id = await _bid()
    cert_id = await _cert(org_id, "PRINCE2 Practitioner", "archived", None)
    await _link(tender_id, entry_id, cert_id)

    messages = await _step2_messages(tender_id, entry_id)
    assert len(messages) == 1 and messages[0].endswith(
        "its evidence 'PRINCE2 Practitioner' was withdrawn."
    )


async def test_mixed_reasons_give_the_generic_message():
    org_id, tender_id, _chunk, entry_id = await _bid()
    await _link(tender_id, entry_id, await _cert(org_id, "A", "active", YESTERDAY))
    await _link(tender_id, entry_id, await _cert(org_id, "B", "archived", None))

    (message,) = await _step2_messages(tender_id, entry_id)
    assert message == (
        f"Compliance entry {entry_id} is marked fully_compliant "
        "with no verified, currently-valid evidence."
    )


async def test_two_lapsed_certificates_are_named_in_title_order():
    org_id, tender_id, _chunk, entry_id = await _bid()
    await _link(tender_id, entry_id, await _cert(org_id, "Zeta", "active", YESTERDAY))
    await _link(tender_id, entry_id, await _cert(org_id, "Alpha", "active", YESTERDAY))

    (message,) = await _step2_messages(tender_id, entry_id)
    assert message.endswith(
        f"its evidence 'Alpha' expired on {YESTERDAY} and 'Zeta' expired on {YESTERDAY}."
    )


# ----------------------------------------------------------------------
# Q2: an employee's profile review is read live
# ----------------------------------------------------------------------


@pytest.mark.parametrize(("profile", "counts"), [("verified", True), ("needs_review", False)])
async def test_an_employee_link_counts_only_while_the_profile_is_verified(profile, counts):
    org_id, tender_id, _chunk, entry_id = await _bid()
    employee_id = await _employee(org_id, profile)
    link = await _link(tender_id, entry_id, employee_id)

    assert (await _step2_messages(tender_id, entry_id) == []) is counts
    assert await _filter_offers(link) is counts
    assert await _claim_trace(link.id) == ("valid", counts)
    assert await _fresh_signals(employee_id, "employee") == ("valid", counts)


# ----------------------------------------------------------------------
# Q4: the roster readers stay on archived
# ----------------------------------------------------------------------


async def test_a_selected_employee_marked_unverified_is_not_removed_from_the_roster():
    org_id, tender_id, _chunk, _entry = await _bid()
    employee_id = await _employee(org_id, "verified")
    async with UnitOfWork() as uow:
        await uow.knowledge.update_knowledge_item(employee_id, status="unverified")
        role = await uow.talent.create_role_requirement(
            tender_id=tender_id, role_name="Architect", content_fingerprint=uuid.uuid4().hex
        )
        match = await uow.talent.create_role_match(
            role_requirement_id=role.id, employee_knowledge_item_id=employee_id
        )
        await uow.talent.update_role_match(match.id, human_selected=True)
        await uow.commit()

    result = await readiness_service.evaluate_readiness(tender_id)
    assert readiness_service.SELECTED_EMPLOYEE_REMOVED not in {
        b["category"] for b in result.blockers
    }


# ----------------------------------------------------------------------
# Item 91: a date passing reaches readiness with no bump
# ----------------------------------------------------------------------


async def test_a_date_passing_changes_readiness_at_the_same_state_version(monkeypatch):
    org_id, tender_id, _chunk, entry_id = await _bid()
    cert_id = await _cert(org_id, "ISO 27001 Lead Implementer", "active", TODAY)
    await _link(tender_id, entry_id, cert_id)
    async with UnitOfWork() as uow:
        version_before = (await uow.tenders.get(tender_id)).state_version

    assert await _step2_messages(tender_id, entry_id) == []
    first = await readiness_service.get_latest_readiness(tender_id)

    monkeypatch.setattr(readiness_service, "today_utc", lambda: TOMORROW)
    messages = await _step2_messages(tender_id, entry_id)
    second = await readiness_service.get_latest_readiness(tender_id)
    async with UnitOfWork() as uow:
        version_after = (await uow.tenders.get(tender_id)).state_version

    assert len(messages) == 1 and messages[0].endswith(f"expired on {TODAY}.")
    assert version_after == version_before
    assert first is not None and second is not None and first.report_id != second.report_id
