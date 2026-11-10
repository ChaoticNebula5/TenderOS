"""domain/services/gate_target_service.py against real Postgres.

Same self-built-fixture-helper convention as test_redteam_service.py.
Covers: the three legitimate dispatch paths (compliance_entry, role_match,
pricing_finding), the permissive-on-unknown posture, and - the point Q4's
correction was specifically about - the cross-tenant ownership reject,
verified both for the single-entry function and for apply_target_updates'
own batch-level "validate everything before applying anything" guarantee
(no partial writes when one entry in a multi-entry batch is foreign).
"""

import uuid
from datetime import date

import pytest
import pytest_asyncio
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import gate_target_service

pytestmark = pytest.mark.asyncio


async def _org_and_tender(name: str) -> tuple[uuid.UUID, uuid.UUID]:
    async with get_session() as session:
        org = Organization(name=f"{name} Org")
        session.add(org)
        await session.flush()
        tender = Tender(
            organization_id=org.id, name=f"{name} Tender", submission_date=date(2026, 12, 1)
        )
        session.add(tender)
        await session.flush()
        await session.commit()
        return org.id, tender.id


async def _make_user() -> uuid.UUID:
    async with UnitOfWork() as uow:
        user = await uow.users.create(email=f"{uuid.uuid4().hex}@example.test", password_hash="x")
        await uow.commit()
        return user.id


async def _chunk(tender_id: uuid.UUID) -> uuid.UUID:
    async with UnitOfWork() as uow:
        document = await uow.documents.create(
            side="client",
            doc_type="rfp",
            filename="req.txt",
            storage_key=f"test/{uuid.uuid4()}",
            sha256=uuid.uuid4().hex,
            mime_type="text/plain",
            file_size_bytes=10,
            tender_id=tender_id,
        )
        chunk = await uow.documents.create_chunk(
            document_id=document.id,
            chunk_index=0,
            chunk_text="req",
            normalized_text="req",
            content_hash=uuid.uuid4().hex,
        )
        await uow.commit()
        return chunk.id


async def _requirement(tender_id: uuid.UUID, chunk_id: uuid.UUID) -> uuid.UUID:
    async with UnitOfWork() as uow:
        requirement = await uow.requirements.create(
            tender_id=tender_id,
            display_code=f"REQ-{uuid.uuid4().hex[:6]}",
            requirement_text="Sample requirement text.",
            normalized_text="sample requirement text.",
            category="experience",
            mandatory=True,
            source_chunk_id=chunk_id,
            content_fingerprint=uuid.uuid4().hex,
        )
        await uow.commit()
        return requirement.id


async def _compliance_entry(requirement_id: uuid.UUID) -> uuid.UUID:
    async with UnitOfWork() as uow:
        entry = await uow.compliance.create_compliance_entry(
            requirement_id=requirement_id, status="fully_compliant"
        )
        await uow.commit()
        return entry.id


async def _role_match(tender_id: uuid.UUID, org_id: uuid.UUID) -> uuid.UUID:
    async with UnitOfWork() as uow:
        knowledge_item, _employee = await uow.knowledge.create_employee(
            organization_id=org_id,
            name="Candidate",
            content_fingerprint=uuid.uuid4().hex,
            title="Candidate",
        )
        role = await uow.talent.create_role_requirement(
            tender_id=tender_id,
            role_name="Solution Architect",
            content_fingerprint=uuid.uuid4().hex,
        )
        match = await uow.talent.create_role_match(
            role_requirement_id=role.id, employee_knowledge_item_id=knowledge_item.id
        )
        await uow.commit()
        return match.id


async def _pricing_finding(tender_id: uuid.UUID) -> uuid.UUID:
    async with UnitOfWork() as uow:
        finding = await uow.commercial.create_pricing_finding(
            tender_id=tender_id,
            finding_type="wrong_total",
            note="Line total mismatch.",
            content_fingerprint=uuid.uuid4().hex,
        )
        await uow.commit()
        return finding.id


@pytest_asyncio.fixture
async def actor_id() -> uuid.UUID:
    return await _make_user()


# ----------------------------------------------------------------------
# Legitimate dispatch: each target_type applies via its real, existing
# G7/G8/G9 service call.
# ----------------------------------------------------------------------


async def test_compliance_entry_approve_dispatches_to_approve_compliance_entry(actor_id):
    org_id, tender_id = await _org_and_tender("ApplyCompliance")
    chunk_id = await _chunk(tender_id)
    requirement_id = await _requirement(tender_id, chunk_id)
    entry_id = await _compliance_entry(requirement_id)

    await gate_target_service.apply_target_update(
        tender_id,
        {"target_type": "compliance_entry", "target_id": str(entry_id), "decision": "approve"},
        actor_id,
    )

    async with UnitOfWork() as uow:
        entry = await uow.compliance.get_compliance_entry(entry_id)
    assert entry.human_approved is True


async def test_role_match_approve_dispatches_to_select_candidate(actor_id):
    org_id, tender_id = await _org_and_tender("ApplyRoleMatch")
    match_id = await _role_match(tender_id, org_id)

    await gate_target_service.apply_target_update(
        tender_id,
        {"target_type": "role_match", "target_id": str(match_id), "decision": "approve"},
        actor_id,
    )

    async with UnitOfWork() as uow:
        match = await uow.talent.get_role_match(match_id)
    assert match.human_selected is True


async def test_pricing_finding_approve_dispatches_to_resolve_pricing_finding(actor_id):
    org_id, tender_id = await _org_and_tender("ApplyPricing")
    finding_id = await _pricing_finding(tender_id)

    await gate_target_service.apply_target_update(
        tender_id,
        {"target_type": "pricing_finding", "target_id": str(finding_id), "decision": "approve"},
        actor_id,
    )

    async with UnitOfWork() as uow:
        finding = await uow.commercial.get_pricing_finding(finding_id)
    assert finding.resolved is True


# ----------------------------------------------------------------------
# Permissive on unknown target_type/decision values.
# ----------------------------------------------------------------------


async def test_unknown_target_type_is_a_silent_noop(actor_id):
    org_id, tender_id = await _org_and_tender("UnknownType")
    # Must not raise - forward-compatibility, same posture as
    # map_finding_to_corrective_task's unknown-category handling.
    await gate_target_service.apply_target_update(
        tender_id,
        {"target_type": "not_a_real_type", "target_id": str(uuid.uuid4()), "decision": "approve"},
        actor_id,
    )


async def test_unknown_decision_is_a_silent_noop(actor_id):
    org_id, tender_id = await _org_and_tender("UnknownDecision")
    chunk_id = await _chunk(tender_id)
    requirement_id = await _requirement(tender_id, chunk_id)
    entry_id = await _compliance_entry(requirement_id)

    await gate_target_service.apply_target_update(
        tender_id,
        {"target_type": "compliance_entry", "target_id": str(entry_id), "decision": "reject"},
        actor_id,
    )

    async with UnitOfWork() as uow:
        entry = await uow.compliance.get_compliance_entry(entry_id)
    assert entry.human_approved is False, "an unrecognized decision value must not mutate anything"


# ----------------------------------------------------------------------
# Q4's correction: strict, hard-reject ownership check - the exact
# select_candidate cross-tenant shape, now closed here instead.
# ----------------------------------------------------------------------


async def test_cross_tenant_target_id_is_rejected(actor_id):
    _org_a, tender_a = await _org_and_tender("OwnerA")
    org_b, tender_b = await _org_and_tender("OwnerB")
    match_id = await _role_match(tender_b, org_b)

    with pytest.raises(gate_target_service.TargetOwnershipError):
        await gate_target_service.apply_target_update(
            tender_a,
            {"target_type": "role_match", "target_id": str(match_id), "decision": "approve"},
            actor_id,
        )

    async with UnitOfWork() as uow:
        match = await uow.talent.get_role_match(match_id)
    assert match.human_selected is False, "a rejected cross-tenant target must never be applied"


async def test_batch_with_one_foreign_entry_applies_nothing(actor_id):
    """The specific gap Q4's correction caught. apply_target_updates now
    runs the whole batch - every ownership check and every dispatch - in
    ONE shared transaction, committed once at the end (CodeRabbit review):
    an exception from a later entry rolls back an earlier entry's
    already-executed-but-not-yet-committed write too, regardless of
    position in the batch.
    """
    org_a, tender_a = await _org_and_tender("BatchOwnerA")
    org_b, tender_b = await _org_and_tender("BatchOwnerB")

    chunk_id = await _chunk(tender_a)
    requirement_id = await _requirement(tender_a, chunk_id)
    legitimate_entry_id = await _compliance_entry(requirement_id)
    foreign_match_id = await _role_match(tender_b, org_b)

    with pytest.raises(gate_target_service.TargetOwnershipError):
        await gate_target_service.apply_target_updates(
            tender_a,
            [
                {
                    "target_type": "compliance_entry",
                    "target_id": str(legitimate_entry_id),
                    "decision": "approve",
                },
                {
                    "target_type": "role_match",
                    "target_id": str(foreign_match_id),
                    "decision": "approve",
                },
            ],
            actor_id,
        )

    async with UnitOfWork() as uow:
        entry = await uow.compliance.get_compliance_entry(legitimate_entry_id)
    assert entry.human_approved is False, (
        "the legitimate entry listed BEFORE the foreign one must not have been "
        "applied - no partial writes when the batch as a whole is rejected"
    )


async def test_a_later_dispatch_failure_rolls_back_an_earlier_success(actor_id, monkeypatch):
    """CodeRabbit review: the ownership pre-check (above) only proves atomicity
    for ownership failures specifically. A LATER dispatch failing for an
    unrelated reason (a real DB error, a row deleted mid-batch) after an
    EARLIER dispatch in the same batch already ran is a different failure
    mode - both entries here pass ownership cleanly, so this exercises the
    one shared transaction directly rather than the ownership guard.
    resolve_pricing_finding_in_uow is monkeypatched to fail only for this
    test's specific target, simulating that unrelated failure without
    needing to actually corrupt the database to trigger one.
    """
    org_id, tender_id = await _org_and_tender("DispatchFailureRollback")
    chunk_id = await _chunk(tender_id)
    requirement_id = await _requirement(tender_id, chunk_id)
    entry_id = await _compliance_entry(requirement_id)
    finding_id = await _pricing_finding(tender_id)

    async def _boom(uow, target_id, actor_user_id):
        raise RuntimeError("simulated dispatch failure unrelated to ownership")

    monkeypatch.setattr(gate_target_service, "resolve_pricing_finding_in_uow", _boom)

    with pytest.raises(RuntimeError, match="simulated dispatch failure"):
        await gate_target_service.apply_target_updates(
            tender_id,
            [
                {
                    "target_type": "compliance_entry",
                    "target_id": str(entry_id),
                    "decision": "approve",
                },
                {
                    "target_type": "pricing_finding",
                    "target_id": str(finding_id),
                    "decision": "approve",
                },
            ],
            actor_id,
        )

    async with UnitOfWork() as uow:
        entry = await uow.compliance.get_compliance_entry(entry_id)
    assert entry.human_approved is False, (
        "the earlier compliance_entry approval must be rolled back when a LATER "
        "dispatch in the same batch fails, even for a reason unrelated to "
        "ownership - the whole batch shares one transaction now"
    )
