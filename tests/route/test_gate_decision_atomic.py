"""PR 16 (plan item 21's note with item 67's gate-target note), at the HTTP
level: a gate decision decides the gate only, and records itself and its
replayable answer in one transaction.

Before this, decide_gate applied target_updates in their own committed
transaction ahead of the decision, checking only that each target belonged
to the gate's tender. Observed on the fake stack: approving the compliance
gate with a role match and a pricing finding (neither its target) applied
both, and a refused 409 decision on the decided gate still approved a
compliance entry. No screen and not the frozen E2E spec sent target updates;
every item has its own route. So any non-empty target_updates is refused 422
on every gate, before anything is read or written, and the empty list (the
spec's example payload) is still accepted.

The decision's idempotency record completes inside the decision's own
transaction (plan item 21, limits 1 and 2): a failure anywhere before that
commit records nothing and releases the key, and a decision is never
recorded without its stored answer.

Gates are hand-built rows (no graph run), their summaries taken with the
functions the gate nodes use, as in test_gate_live_counts.py.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from tenderos_backend.db.models.idempotency import IdempotencyRecord
from tenderos_backend.db.models.outbox import OutboxEvent
from tenderos_backend.db.repositories.audit import AuditRepository
from tenderos_backend.db.repositories.idempotency import IdempotencyRepository
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.events.outbox_events import EVENT_GATE_DECISION_RECORDED
from tenderos_backend.domain.services import gate_summary_service
from tenderos_backend.orchestration.gates import build_gate_payload

pytestmark = pytest.mark.asyncio

GATE_TYPES = [
    "requirement_review",
    "compliance_review",
    "resource_selection",
    "commercial_approval",
    "finding_resolution",
    "cost_budget",
    "final_readiness",
]


def _tender(client: TestClient, headers: dict, org_id: str) -> uuid.UUID:
    response = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Gate decision bid"}, headers=headers
    )
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


async def _targets(tender_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    """An unapproved compliance entry and an unresolved pricing finding of
    this bid: (entry id, finding id). Applied, either would change the bid."""
    async with UnitOfWork() as uow:
        document = await uow.documents.create(
            side="client",
            doc_type="rfp",
            filename="rfp.pdf",
            storage_key=f"test/{uuid.uuid4()}",
            sha256=uuid.uuid4().hex,
            mime_type="application/pdf",
            file_size_bytes=10,
            tender_id=tender_id,
        )
        chunk = await uow.documents.create_chunk(
            document_id=document.id,
            chunk_index=0,
            chunk_text="The bidder must be registered.",
            normalized_text="the bidder must be registered.",
            content_hash=uuid.uuid4().hex,
        )
        requirement = await uow.requirements.create(
            tender_id=tender_id,
            display_code=f"REQ-{uuid.uuid4().hex[:6]}",
            requirement_text="The bidder must be registered.",
            normalized_text="the bidder must be registered.",
            category="eligibility",
            mandatory=True,
            source_chunk_id=chunk.id,
            content_fingerprint=uuid.uuid4().hex,
        )
        entry = await uow.compliance.create_compliance_entry(
            requirement_id=requirement.id, status="fully_compliant"
        )
        finding = await uow.commercial.create_pricing_finding(
            tender_id=tender_id,
            finding_type="wrong_total",
            note="The total is wrong.",
            content_fingerprint=uuid.uuid4().hex,
        )
        await uow.commit()
        return entry.id, finding.id


async def _summary(uow: UnitOfWork, gate_type: str, tender_id: uuid.UUID, run_id: uuid.UUID):
    if gate_type == "final_readiness":
        return {"ready": True, "blockers": [], "counts": None, "degraded_stages": []}
    if gate_type == "cost_budget":
        return await gate_summary_service.cost_budget_summary(uow, run_id)
    builder = {
        "requirement_review": gate_summary_service.requirement_review_summary,
        "compliance_review": gate_summary_service.compliance_review_summary,
        "resource_selection": gate_summary_service.resource_selection_summary,
        "commercial_approval": gate_summary_service.commercial_approval_summary,
        "finding_resolution": gate_summary_service.finding_resolution_summary,
    }[gate_type]
    return await builder(uow, tender_id)


async def _open_gate(
    tender_id: uuid.UUID, gate_type: str, targets: list[dict] | None = None
) -> uuid.UUID:
    async with UnitOfWork() as uow:
        run = await uow.workflow.create_run(
            tender_id=tender_id, prompt_bundle_version="test", model_config={}
        )
        gate_id = uuid.uuid4()
        payload = build_gate_payload(
            gate_id,
            gate_type,
            run.id,
            targets=targets or [],
            summary=await _summary(uow, gate_type, tender_id, run.id),
        )
        await uow.gates.create(
            gate_id=gate_id,
            tender_id=tender_id,
            workflow_run_id=run.id,
            gate_key=f"{gate_type}:plan-0",
            gate_type=gate_type,
            request_payload=payload,
        )
        await uow.commit()
    return gate_id


def _decide(client: TestClient, headers: dict, gate_id: uuid.UUID, key: str, **body):
    body.setdefault("decision", "approve")
    return client.post(
        f"/gates/{gate_id}/decision", json=body, headers={**headers, "Idempotency-Key": key}
    )


def _updates(entry_id: uuid.UUID, finding_id: uuid.UUID) -> list[dict]:
    return [
        {"target_type": "compliance_entry", "target_id": str(entry_id), "decision": "approve"},
        {"target_type": "pricing_finding", "target_id": str(finding_id), "decision": "approve"},
    ]


async def _state(tender_id: uuid.UUID, gate_id: uuid.UUID, entry_id, finding_id) -> dict:
    """What a decision could have changed: the targets, the bid's version,
    the gate, and the decision's outbox event."""
    async with UnitOfWork() as uow:
        entry = await uow.compliance.get_compliance_entry(entry_id)
        finding = await uow.commercial.get_pricing_finding(finding_id)
        tender = await uow.tenders.get(tender_id)
        gate = await uow.gates.get(gate_id)
    async with get_session() as session:
        events = (
            await session.execute(
                select(func.count())
                .select_from(OutboxEvent)
                .where(
                    OutboxEvent.aggregate_id == gate_id,
                    OutboxEvent.event_type == EVENT_GATE_DECISION_RECORDED,
                )
            )
        ).scalar_one()
    assert entry is not None and finding is not None and tender is not None and gate is not None
    return {
        "entry_approved": entry.human_approved,
        "finding_resolved": finding.resolved,
        "version": tender.state_version,
        "gate_status": gate.status,
        "decision_events": events,
    }


async def _records(key: str) -> list[tuple[str, int | None]]:
    async with get_session() as session:
        rows = await session.execute(
            select(IdempotencyRecord.status, IdempotencyRecord.response_status).where(
                IdempotencyRecord.idempotency_key == key
            )
        )
        return [tuple(row) for row in rows.all()]


@pytest.mark.parametrize("gate_type", GATE_TYPES)
async def test_target_updates_are_refused_on_every_gate_and_nothing_applies(
    client: TestClient, register_actor, gate_type: str
):
    # The fake stack's P2 shape: a compliance entry and a pricing finding,
    # listed as the compliance gate's targets or not, on any gate.
    headers, _, org_id = register_actor()
    tender_id = _tender(client, headers, org_id)
    entry_id, finding_id = await _targets(tender_id)
    gate_id = await _open_gate(
        tender_id,
        gate_type,
        targets=[{"target_type": "compliance_entry", "target_id": str(entry_id)}],
    )
    before = await _state(tender_id, gate_id, entry_id, finding_id)
    key = f"refused-{gate_id}"

    refused = _decide(client, headers, gate_id, key, target_updates=_updates(entry_id, finding_id))

    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["code"] == "GATE_TARGET_UPDATES"
    assert await _state(tender_id, gate_id, entry_id, finding_id) == before
    assert before["gate_status"] == "pending" and before["decision_events"] == 0
    # The key was released: the same key decides the gate once the request
    # carries no updates.
    assert await _records(key) == []
    decided = _decide(client, headers, gate_id, key, decision="request_changes")
    assert decided.status_code == 200, decided.text


async def test_a_decided_gate_refuses_target_updates_and_applies_nothing(
    client: TestClient, register_actor
):
    # The fake stack's P1 shape: before PR 16, a second decision on a decided
    # gate was refused 409 with its target updates already applied. A replay
    # of the same decision that carries updates is refused too (user, Q4):
    # a 200 would claim updates that were never applied.
    headers, _, org_id = register_actor()
    tender_id = _tender(client, headers, org_id)
    entry_id, finding_id = await _targets(tender_id)
    gate_id = await _open_gate(tender_id, "compliance_review")
    assert _decide(client, headers, gate_id, f"first-{gate_id}").status_code == 200
    before = await _state(tender_id, gate_id, entry_id, finding_id)

    other = _decide(
        client,
        headers,
        gate_id,
        f"other-{gate_id}",
        decision="request_changes",
        target_updates=_updates(entry_id, finding_id),
    )
    same = _decide(
        client, headers, gate_id, f"same-{gate_id}", target_updates=_updates(entry_id, finding_id)
    )

    for response in (other, same):
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "GATE_TARGET_UPDATES"
    assert await _state(tender_id, gate_id, entry_id, finding_id) == before
    assert before["entry_approved"] is False and before["decision_events"] == 1


async def test_an_empty_target_updates_list_is_accepted(client: TestClient, register_actor):
    # The spec's example decision payload carries an empty list.
    headers, _, org_id = register_actor()
    tender_id = _tender(client, headers, org_id)
    gate_id = await _open_gate(tender_id, "compliance_review")

    response = _decide(
        client, headers, gate_id, f"empty-{gate_id}", target_updates=[], notes="Reviewed."
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "approved"


async def test_a_failure_inside_the_decisions_transaction_records_nothing(
    client: TestClient, register_actor, monkeypatch
):
    # The audit row is the last write before the stored answer: a failure
    # there rolls the decision back with it, and the key is released, so the
    # same key then decides the gate once, with one event.
    headers, _, org_id = register_actor()
    tender_id = _tender(client, headers, org_id)
    entry_id, finding_id = await _targets(tender_id)
    gate_id = await _open_gate(tender_id, "compliance_review")
    key = f"audit-fails-{gate_id}"
    real_create = AuditRepository.create

    async def audit_fails(self, *args, **kwargs):
        if kwargs.get("action") == "record_gate_decision":
            raise RuntimeError("audit write failed")
        return await real_create(self, *args, **kwargs)

    monkeypatch.setattr(AuditRepository, "create", audit_fails)
    failed = _decide(client, headers, gate_id, key)
    monkeypatch.setattr(AuditRepository, "create", real_create)
    assert failed.status_code == 500, failed.text

    state = await _state(tender_id, gate_id, entry_id, finding_id)
    assert state["gate_status"] == "pending" and state["decision_events"] == 0
    assert await _records(key) == []

    retried = _decide(client, headers, gate_id, key)
    assert retried.status_code == 200, retried.text
    assert (await _state(tender_id, gate_id, entry_id, finding_id))["decision_events"] == 1


async def test_the_stored_answer_commits_with_the_decision(
    client: TestClient, register_actor, monkeypatch
):
    # Plan item 21, limits 1 and 2: before PR 16 the answer was stored in a
    # transaction after the decision's commit, so a failure there left the
    # gate decided with no stored answer, and the released key let a retry
    # decide again. Now storing it is part of the decision's transaction.
    headers, _, org_id = register_actor()
    tender_id = _tender(client, headers, org_id)
    entry_id, finding_id = await _targets(tender_id)
    gate_id = await _open_gate(tender_id, "compliance_review")
    key = f"store-fails-{gate_id}"

    async def store_fails(self, *args, **kwargs):
        raise RuntimeError("storing the answer failed")

    monkeypatch.setattr(IdempotencyRepository, "complete", store_fails)
    failed = _decide(client, headers, gate_id, key)
    assert failed.status_code == 500, failed.text

    state = await _state(tender_id, gate_id, entry_id, finding_id)
    assert state["gate_status"] == "pending" and state["decision_events"] == 0


async def test_a_repeated_decision_with_a_new_key_stores_its_replay(
    client: TestClient, register_actor
):
    # The same decision on a decided gate, under a new key, is a replay (no
    # second event). Its answer is stored under that key in the same
    # transaction, so repeating that request replays it again.
    headers, _, org_id = register_actor()
    tender_id = _tender(client, headers, org_id)
    entry_id, finding_id = await _targets(tender_id)
    gate_id = await _open_gate(tender_id, "compliance_review")
    assert _decide(client, headers, gate_id, f"first-{gate_id}").status_code == 200
    key = f"again-{gate_id}"

    again = _decide(client, headers, gate_id, key)
    repeated = _decide(client, headers, gate_id, key)

    assert again.status_code == 200, again.text
    assert await _records(key) == [("completed", 200)]
    assert repeated.status_code == 200, repeated.text
    assert repeated.json() == again.json()
    assert (await _state(tender_id, gate_id, entry_id, finding_id))["decision_events"] == 1
