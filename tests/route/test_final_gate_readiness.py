"""Plan item 93 (W2) at the HTTP level: the final readiness gate's approval
recomputes readiness and refuses READINESS_CHANGED when the result is not
the report that was current before the request, or when the bid's state
version moves before the decision is recorded. A final gate decision with
target_updates is refused 422 before anything is applied.

No route test covered the waiver check before this file. Gates are
hand-built rows (no graph run), as in test_gate_live_counts.py; the graph is
projected for real, since readiness checks its freshness.

Plan items 94, 96 and 99: a clean bid also has a parsed document and a
complete red-team pass recorded at its current version
(tests/readiness_facts.py), and the approval's recompute reads the same
recorded passes as the page's generate, never the gate payload's frozen
degraded stages. Since plan item 104 that document's text has also been
read by a completed extraction.
"""

import uuid

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from readiness_facts import (
    add_parsed_document,
    mark_chunks_read,
    project_and_review,
    record_complete_pass,
)
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import graph_service, readiness_service
from tenderos_backend.graph_projection.driver import get_session as get_neo4j_session
from tenderos_backend.orchestration.gates import build_gate_payload

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _clean_neo4j():
    yield
    async with get_neo4j_session() as session:
        await session.run("MATCH (n) DETACH DELETE n")


def _tender(client: TestClient, headers: dict, org_id: str) -> uuid.UUID:
    response = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Final gate bid"}, headers=headers
    )
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


async def _final_gate(tender_id: uuid.UUID, degraded_stages: list[str] | None = None) -> uuid.UUID:
    """A run and a pending final readiness gate, its payload shaped as
    final_gate_node builds it (the degraded stages in its summary)."""
    async with UnitOfWork() as uow:
        run = await uow.workflow.create_run(
            tender_id=tender_id, prompt_bundle_version="test", model_config={}
        )
        gate_id = uuid.uuid4()
        payload = build_gate_payload(
            gate_id,
            "final_readiness",
            run.id,
            targets=[],
            summary={
                "ready": True,
                "blockers": [],
                "counts": None,
                "degraded_stages": degraded_stages or [],
            },
        )
        await uow.gates.create(
            gate_id=gate_id,
            tender_id=tender_id,
            workflow_run_id=run.id,
            gate_key="final_readiness:plan-0",
            gate_type="final_readiness",
            request_payload=payload,
        )
        await uow.commit()
    return gate_id


async def _critical_finding(tender_id: uuid.UUID) -> uuid.UUID:
    async with UnitOfWork() as uow:
        finding = await uow.redteam.create_finding(
            tender_id=tender_id,
            target_type="requirement",
            target_id=None,
            finding_category="missed_mandatory_requirement",
            severity="critical",
            finding_text="Test finding.",
            violated_condition="test",
            content_fingerprint=uuid.uuid4().hex,
        )
        await uow.commit()
        return finding.id


def _generate(client: TestClient, headers: dict, tender_id: uuid.UUID) -> dict:
    response = client.post(f"/tenders/{tender_id}/readiness/generate", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _decide(client: TestClient, headers: dict, gate_id: uuid.UUID, **body: object):
    return client.post(
        f"/gates/{gate_id}/decision",
        json={"decision": "approve", **body},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )


async def _gate(gate_id: uuid.UUID):
    async with UnitOfWork() as uow:
        return await uow.gates.get(gate_id)


async def _version(tender_id: uuid.UUID) -> int:
    async with UnitOfWork() as uow:
        tender = await uow.tenders.get(tender_id)
        assert tender is not None
        return tender.state_version


async def test_a_ready_bid_is_approved_against_the_report_that_was_shown(
    client: TestClient, register_actor
):
    headers, _, org_id = register_actor()
    tender_id = _tender(client, headers, org_id)
    await add_parsed_document(tender_id)
    await project_and_review(tender_id)
    gate_id = await _final_gate(tender_id)
    shown = _generate(client, headers, tender_id)
    assert shown["ready"] is True

    response = _decide(client, headers, gate_id)

    assert response.status_code == 200, response.text
    gate = await _gate(gate_id)
    assert gate is not None and gate.status == "approved"
    assert gate.decision_payload["waived"] is False


async def test_a_report_changed_since_it_was_shown_is_refused_then_approved(
    client: TestClient, register_actor
):
    """A red-team finding raised after the report was shown moves no
    version. The approval's recompute lands on a different report, so it is
    refused; that report is now the current one, so approving again (with a
    waiver, since it is not ready) goes through and records its blockers."""
    headers, _, org_id = register_actor()
    tender_id = _tender(client, headers, org_id)
    await add_parsed_document(tender_id)
    await project_and_review(tender_id)
    gate_id = await _final_gate(tender_id)
    shown = _generate(client, headers, tender_id)
    assert shown["ready"] is True
    await _critical_finding(tender_id)

    refused = _decide(client, headers, gate_id, waive=True)
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "READINESS_CHANGED"
    gate = await _gate(gate_id)
    assert gate is not None and gate.status == "pending"

    current = client.get(f"/tenders/{tender_id}/readiness", headers=headers).json()
    assert current["id"] != shown["id"]
    assert current["ready"] is False

    approved = _decide(client, headers, gate_id, waive=True)
    assert approved.status_code == 200, approved.text
    gate = await _gate(gate_id)
    assert gate is not None
    assert gate.decision_payload["waived"] is True
    assert [b["category"] for b in gate.decision_payload["waived_blockers"]] == [
        readiness_service.CRITICAL_REDTEAM_FINDING
    ]


def _decide_with_key(client: TestClient, headers: dict, gate_id: uuid.UUID, key: str, **body):
    return client.post(
        f"/gates/{gate_id}/decision",
        json={"decision": "approve", **body},
        headers={**headers, "Idempotency-Key": key},
    )


async def test_after_readiness_changed_the_panels_next_click_goes_through(
    client: TestClient, register_actor
):
    """The web's real behaviour (GateDecisionPanel.handleDecide): a new
    Idempotency-Key per click, reused only across retryTransient's retries,
    which never retry a 409. So after READINESS_CHANGED the reviewer's next
    click sends a new key, and the recomputed report is now the current one,
    so it goes through."""
    headers, _, org_id = register_actor()
    tender_id = _tender(client, headers, org_id)
    await add_parsed_document(tender_id)
    await project_and_review(tender_id)
    gate_id = await _final_gate(tender_id)
    _generate(client, headers, tender_id)
    await _critical_finding(tender_id)

    first_click = _decide_with_key(client, headers, gate_id, str(uuid.uuid4()), waive=True)
    second_click = _decide_with_key(client, headers, gate_id, str(uuid.uuid4()), waive=True)

    assert first_click.status_code == 409, first_click.text
    assert first_click.json()["error"]["code"] == "READINESS_CHANGED"
    assert second_click.status_code == 200, second_click.text
    gate = await _gate(gate_id)
    assert gate is not None and gate.status == "approved"


async def test_after_readiness_changed_the_same_key_runs_again_not_replayed(
    client: TestClient, register_actor
):
    """A refused decision's key is released (the idempotency dependency
    deletes its record on any exception), not stored as the 409 or left in
    progress, so the same key sent again runs the decision again."""
    headers, _, org_id = register_actor()
    tender_id = _tender(client, headers, org_id)
    await add_parsed_document(tender_id)
    await project_and_review(tender_id)
    gate_id = await _final_gate(tender_id)
    _generate(client, headers, tender_id)
    await _critical_finding(tender_id)
    key = str(uuid.uuid4())

    first = _decide_with_key(client, headers, gate_id, key, waive=True)
    again = _decide_with_key(client, headers, gate_id, key, waive=True)

    assert first.json()["error"]["code"] == "READINESS_CHANGED"
    assert again.status_code == 200, again.text


async def test_not_ready_without_a_waiver_is_still_refused(client: TestClient, register_actor):
    headers, _, org_id = register_actor()
    tender_id = _tender(client, headers, org_id)
    await _critical_finding(tender_id)
    await add_parsed_document(tender_id)
    await project_and_review(tender_id)
    gate_id = await _final_gate(tender_id)
    _generate(client, headers, tender_id)

    response = _decide(client, headers, gate_id)

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "READINESS_NOT_WAIVED"


async def test_with_no_report_ever_shown_the_first_approval_is_refused(
    client: TestClient, register_actor
):
    headers, _, org_id = register_actor()
    tender_id = _tender(client, headers, org_id)
    await add_parsed_document(tender_id)
    await project_and_review(tender_id)
    gate_id = await _final_gate(tender_id)

    first = _decide(client, headers, gate_id)
    second = _decide(client, headers, gate_id)

    assert first.status_code == 409, first.text
    assert first.json()["error"]["code"] == "READINESS_CHANGED"
    assert second.status_code == 200, second.text


async def test_the_page_and_the_approval_read_the_same_red_team_facts(
    client: TestClient, register_actor
):
    """Plan item 94. The gate's payload still says the run's red team
    failed, but a complete pass is recorded at the current version (a later
    successful pass). The page's generate and the approval's recompute read
    the same recorded passes, so they agree, and the approval goes through
    with no READINESS_CHANGED and no waiver."""
    headers, _, org_id = register_actor()
    tender_id = _tender(client, headers, org_id)
    await add_parsed_document(tender_id)
    await project_and_review(tender_id)
    gate_id = await _final_gate(tender_id, degraded_stages=["red_team:LLMTimeoutError"])
    assert _generate(client, headers, tender_id)["ready"] is True

    response = _decide(client, headers, gate_id)

    assert response.status_code == 200, response.text
    gate = await _gate(gate_id)
    assert gate is not None and gate.decision_payload["waived"] is False


async def test_a_failed_pass_blocks_the_page_and_the_approval_alike_until_a_pass_completes(
    client: TestClient, register_actor
):
    """The run's pass failed and nothing has completed at this version: the
    page shows the red-team blocker, and the approval sees the same report
    (READINESS_NOT_WAIVED, not READINESS_CHANGED). A complete pass then
    clears both."""
    headers, _, org_id = register_actor()
    tender_id = _tender(client, headers, org_id)
    await add_parsed_document(tender_id)
    await graph_service.project_tender(tender_id)
    async with UnitOfWork() as uow:
        await uow.redteam.record_pass(tender_id, outcome="failed", error_code="LLMTimeoutError")
        await uow.commit()
    gate_id = await _final_gate(tender_id, degraded_stages=["red_team:LLMTimeoutError"])
    shown = _generate(client, headers, tender_id)
    assert [b["category"] for b in shown["blockers"]] == [
        readiness_service.RED_TEAM_REVIEW_INCOMPLETE
    ]

    refused = _decide(client, headers, gate_id)
    assert refused.json()["error"]["code"] == "READINESS_NOT_WAIVED"

    await record_complete_pass(tender_id)
    assert _generate(client, headers, tender_id)["ready"] is True
    approved = _decide(client, headers, gate_id)
    assert approved.status_code == 200, approved.text


async def test_a_pass_finishing_between_the_page_and_the_approval_is_readiness_changed(
    client: TestClient, register_actor
):
    """W2 with the new fact: a pass completing after the page's report moves
    no version but changes readiness, so the approval's recompute lands on a
    different report and is refused once; approving again goes through, and
    nothing is waived since the bid is now ready."""
    headers, _, org_id = register_actor()
    tender_id = _tender(client, headers, org_id)
    await add_parsed_document(tender_id)
    await graph_service.project_tender(tender_id)
    gate_id = await _final_gate(tender_id)
    shown = _generate(client, headers, tender_id)
    assert shown["ready"] is False
    await record_complete_pass(tender_id)

    changed = _decide(client, headers, gate_id, waive=True)
    approved = _decide(client, headers, gate_id, waive=True)

    assert changed.json()["error"]["code"] == "READINESS_CHANGED"
    assert approved.status_code == 200, approved.text
    gate = await _gate(gate_id)
    assert gate is not None and gate.decision_payload["waived"] is False


async def test_an_extraction_finishing_between_the_page_and_the_approval_is_readiness_changed(
    client: TestClient, register_actor
):
    """Plan item 104 with W2: an extraction completing after the page's report
    marks the chunks it read and moves no version (chunks are not projected),
    but changes readiness, so the approval's recompute lands on a different
    report and is refused once; approving again goes through, and nothing is
    waived since the bid is now ready. mark_chunks_read writes what the
    extraction node writes when its task completes."""
    headers, _, org_id = register_actor()
    tender_id = _tender(client, headers, org_id)
    await add_parsed_document(tender_id)
    async with UnitOfWork() as uow:
        amendment = await uow.documents.create(
            side="client",
            doc_type="amendment",
            filename="amendment_2.pdf",
            storage_key=f"test/{uuid.uuid4()}",
            sha256=uuid.uuid4().hex,
            mime_type="application/pdf",
            file_size_bytes=10,
            tender_id=tender_id,
        )
        await uow.documents.update_parse_status(amendment.id, "parsed")
        await uow.documents.create_chunk(
            document_id=amendment.id,
            chunk_index=0,
            chunk_text="Clause 5 is withdrawn.",
            normalized_text="clause 5 is withdrawn.",
            content_hash=uuid.uuid4().hex,
        )
        await uow.commit()
    await project_and_review(tender_id)
    gate_id = await _final_gate(tender_id)
    shown = _generate(client, headers, tender_id)
    assert [b["category"] for b in shown["blockers"]] == ["document_not_extracted"]
    version = await _version(tender_id)
    await mark_chunks_read(tender_id)
    assert await _version(tender_id) == version, "precondition: the marks move no version"

    changed = _decide(client, headers, gate_id, waive=True)
    approved = _decide(client, headers, gate_id, waive=True)

    assert changed.json()["error"]["code"] == "READINESS_CHANGED"
    assert approved.status_code == 200, approved.text
    gate = await _gate(gate_id)
    assert gate is not None and gate.decision_payload["waived"] is False


async def test_a_version_move_between_the_recompute_and_the_write_is_refused(
    client: TestClient, register_actor, monkeypatch: pytest.MonkeyPatch
):
    """The decision is recorded only if the bid is still at the recomputed
    report's version: the write is conditional, so a change landing after
    the recompute is refused too and nothing is recorded."""
    headers, _, org_id = register_actor()
    tender_id = _tender(client, headers, org_id)
    await add_parsed_document(tender_id)
    await project_and_review(tender_id)
    gate_id = await _final_gate(tender_id)
    _generate(client, headers, tender_id)

    real = readiness_service.evaluate_readiness

    async def _then_something_moves(tid):
        result = await real(tid)
        async with UnitOfWork() as uow:
            await uow.tenders.bump_state_version(tid)
            await uow.commit()
        return result

    monkeypatch.setattr(readiness_service, "evaluate_readiness", _then_something_moves)
    response = _decide(client, headers, gate_id)

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "READINESS_CHANGED"
    gate = await _gate(gate_id)
    assert gate is not None and gate.status == "pending"


async def _unapproved_compliance_entry(tender_id: uuid.UUID) -> uuid.UUID:
    async with UnitOfWork() as uow:
        document = await uow.documents.create(
            side="client",
            doc_type="rfp",
            filename="rfp.txt",
            storage_key=f"test/{uuid.uuid4()}",
            sha256=uuid.uuid4().hex,
            mime_type="text/plain",
            file_size_bytes=10,
            tender_id=tender_id,
        )
        chunk = await uow.documents.create_chunk(
            document_id=document.id,
            chunk_index=0,
            chunk_text="Sample requirement text.",
            normalized_text="sample requirement text.",
            content_hash=uuid.uuid4().hex,
        )
        requirement = await uow.requirements.create(
            tender_id=tender_id,
            display_code=f"REQ-{uuid.uuid4().hex[:6]}",
            requirement_text="Sample requirement text.",
            normalized_text="sample requirement text.",
            category="experience",
            mandatory=True,
            source_chunk_id=chunk.id,
            content_fingerprint=uuid.uuid4().hex,
        )
        entry = await uow.compliance.create_compliance_entry(
            requirement_id=requirement.id, status="non_compliant"
        )
        await uow.commit()
        return entry.id


async def test_final_gate_target_updates_are_refused_before_anything_applies(
    client: TestClient, register_actor
):
    """A real, unapproved compliance entry of this bid: without the refusal,
    the update would approve it and move the bid's version."""
    headers, _, org_id = register_actor()
    tender_id = _tender(client, headers, org_id)
    entry_id = await _unapproved_compliance_entry(tender_id)
    gate_id = await _final_gate(tender_id)
    version = await _version(tender_id)

    response = _decide(
        client,
        headers,
        gate_id,
        target_updates=[
            {"target_type": "compliance_entry", "target_id": str(entry_id), "decision": "approve"}
        ],
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "FINAL_GATE_TARGET_UPDATES"
    assert await _version(tender_id) == version
    async with UnitOfWork() as uow:
        entry = await uow.compliance.get_compliance_entry(entry_id)
    assert entry is not None and entry.human_approved is False
    gate = await _gate(gate_id)
    assert gate is not None and gate.status == "pending"
