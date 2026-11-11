"""domain/services/gate_service.py::record_decision - CodeRabbit review of
B12's waiver enforcement (routes/gates.py's decide_gate): the ROUTE kept
approval back until each blocker was resolved or waived, correctly, but the
waived flag was computed, used to permit the override, and then discarded -
never passed into record_decision, so the persisted gate row and audit event
were indistinguishable from a normal approval. This tests the actual
persistence mechanism record_decision now provides: a waived decision is
distinguishable from a normal one in both the gate row's own decision_payload
and its audit event, and records which blockers were bypassed.

Same self-built-fixture-helper convention as every other integration test
in this codebase.
"""

import uuid
from datetime import date

import pytest
from sqlalchemy import select
from tenderos_backend.db.models.audit import AuditEvent
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import gate_service
from tenderos_backend.orchestration.gates import build_gate_payload, derive_gate_key

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


async def _open_gate(tender_id: uuid.UUID, gate_type: str) -> uuid.UUID:
    async with UnitOfWork() as uow:
        run = await uow.workflow.create_run(
            tender_id=tender_id, prompt_bundle_version="v1", model_config={}
        )
        gate_id = uuid.uuid4()
        payload = build_gate_payload(gate_id, gate_type, run.id, targets=[], summary={})
        await uow.gates.create(
            gate_id=gate_id,
            tender_id=tender_id,
            workflow_run_id=run.id,
            gate_key=derive_gate_key(gate_type, 0),
            gate_type=gate_type,
            request_payload=payload,
        )
        await uow.commit()
        return gate_id


async def _audit_after_for_gate(gate_id: uuid.UUID) -> dict | None:
    async with UnitOfWork() as uow:
        result = await uow.session.execute(
            select(AuditEvent).where(
                AuditEvent.target_type == "approval_gate", AuditEvent.target_id == gate_id
            )
        )
        event = result.scalar_one_or_none()
    return event.after if event else None


async def test_waived_decision_is_recorded_distinctly_from_a_normal_one():
    org_id, tender_id = await _org_and_tender("WaiverPersisted")
    # Each gate opens its own run, and a tender has at most one active run
    # (plan item 43), so the second gate gets its own tender.
    waived_org_id, waived_tender_id = await _org_and_tender("WaiverPersistedWaived")
    actor_id = await _make_user()

    normal_gate_id = await _open_gate(tender_id, "final_readiness")
    waived_gate_id = await _open_gate(waived_tender_id, "final_readiness")

    fake_blockers = [{"category": "unresolved_mandatory_requirement", "target_id": None}]

    normal_gate, _ = await gate_service.record_decision(
        gate_id=normal_gate_id,
        organization_id=org_id,
        decided_by=actor_id,
        decision="approve",
        notes=None,
    )
    waived_gate, _ = await gate_service.record_decision(
        gate_id=waived_gate_id,
        organization_id=waived_org_id,
        decided_by=actor_id,
        decision="approve",
        notes=None,
        waived=True,
        waived_blockers=fake_blockers,
    )

    # Distinguishable in the gate row's own decision_payload.
    assert normal_gate.decision_payload["waived"] is False
    assert "waived_blockers" not in normal_gate.decision_payload

    assert waived_gate.decision_payload["waived"] is True
    assert waived_gate.decision_payload["waived_blockers"] == fake_blockers

    # Distinguishable in the audit event too - a waiver enforced but not
    # recorded is an auditable bypass with no audit trail.
    normal_after = await _audit_after_for_gate(normal_gate_id)
    waived_after = await _audit_after_for_gate(waived_gate_id)

    assert normal_after["waived"] is False
    assert "waived_blockers" not in normal_after

    assert waived_after["waived"] is True
    assert waived_after["waived_blockers"] == fake_blockers


async def test_waived_defaults_to_false_when_not_specified():
    # The everyday case (every non-final_readiness gate decision, and every
    # final_readiness approval that never needed a waiver) must not carry a
    # spurious "waived" record just because the parameter exists now.
    org_id, tender_id = await _org_and_tender("WaiverDefault")
    actor_id = await _make_user()
    gate_id = await _open_gate(tender_id, "requirement_review")

    gate, _ = await gate_service.record_decision(
        gate_id=gate_id,
        organization_id=org_id,
        decided_by=actor_id,
        decision="approve",
        notes=None,
    )

    assert gate.decision_payload["waived"] is False
    assert "waived_blockers" not in gate.decision_payload
