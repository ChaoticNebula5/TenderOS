"""Plan item 73, decisions 2 and 3 (user, 2026-11-13), at the HTTP level.

Decision 3: a pending gate's response carries live_summary, its counts as
they are now, beside summary, the snapshot taken when the gate opened. Both
come from gate_summary_service, so the gate rows here are built with it, the
way the gate nodes build theirs. A decided gate has no live counts.

Decision 2: deciding the finding-resolution gate is for bid manager,
approver and admin; every role can still resolve one finding.

Gates are hand-built rows (no graph run): what is under test is the
response and the permission, not how the graph reaches a gate, which
test_gates_routes.py and test_graph_execution.py cover.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import gate_summary_service
from tenderos_backend.orchestration.gates import build_gate_payload

pytestmark = pytest.mark.asyncio


async def _mandatory_requirement(tender_id: uuid.UUID) -> uuid.UUID:
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
        await uow.commit()
        return requirement.id


async def _open_gate(tender_id: uuid.UUID, gate_type: str) -> tuple[uuid.UUID, uuid.UUID]:
    """A run and a pending gate of `gate_type`, its snapshot taken now with
    the same function its node uses. Returns (run id, gate id)."""
    summarise = {
        "requirement_review": gate_summary_service.requirement_review_summary,
        "resource_selection": gate_summary_service.resource_selection_summary,
        "finding_resolution": gate_summary_service.finding_resolution_summary,
    }[gate_type]
    async with UnitOfWork() as uow:
        run = await uow.workflow.create_run(
            tender_id=tender_id, prompt_bundle_version="test", model_config={}
        )
        gate_id = uuid.uuid4()
        payload = build_gate_payload(
            gate_id, gate_type, run.id, targets=[], summary=await summarise(uow, tender_id)
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
    return run.id, gate_id


def _tender(client: TestClient, headers: dict, org_id: str) -> uuid.UUID:
    response = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Live counts bid"}, headers=headers
    )
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


async def test_a_pending_gate_shows_live_counts_beside_its_snapshot(
    client: TestClient, register_actor
):
    headers, _, org_id = register_actor()
    tender_id = _tender(client, headers, org_id)
    requirement_id = await _mandatory_requirement(tender_id)
    run_id, gate_id = await _open_gate(tender_id, "requirement_review")

    # A reviewer verifies the one mandatory requirement after the gate opened.
    # The verdict's own write: since PR 21 a verified requirement has a check
    # time (ck_requirement_verified_has_check_time).
    async with UnitOfWork() as uow:
        requirement = await uow.requirements.get(requirement_id)
        assert requirement is not None
        await uow.requirements.set_verdict_for_text(
            requirement_id, True, requirement.requirement_text
        )
        await uow.commit()

    one = client.get(f"/gates/{gate_id}", headers=headers)
    listed = client.get(f"/workflow-runs/{run_id}/gates", headers=headers)

    assert one.status_code == 200, one.text
    assert listed.status_code == 200, listed.text
    for body in (one.json(), listed.json()[0]):
        # The snapshot: what the gate opened with, unchanged.
        assert body["summary"]["unverified_mandatory_count"] == 1
        # Live: what is true now.
        assert body["live_summary"]["unverified_mandatory_count"] == 0
        assert body["live_summary"]["mandatory_count"] == 1


async def test_a_gate_opened_before_the_two_counts_shows_none_for_them(
    client: TestClient, register_actor
):
    """PR 21 (plan item 59, Q5): a requirement review gate stores its summary
    when it opens. One opened before the two new counts existed has only the
    total, unverified_mandatory_count; the response gives null for the two,
    and its live counts, computed now, have all three."""
    headers, _, org_id = register_actor()
    tender_id = _tender(client, headers, org_id)
    await _mandatory_requirement(tender_id)
    old_summary = {
        "requirement_count": 1,
        "mandatory_count": 1,
        "unverified_mandatory_count": 1,
        "submission_item_count": 0,
        "clarification_count": 0,
    }
    async with UnitOfWork() as uow:
        run = await uow.workflow.create_run(
            tender_id=tender_id, prompt_bundle_version="test", model_config={}
        )
        gate_id = uuid.uuid4()
        await uow.gates.create(
            gate_id=gate_id,
            tender_id=tender_id,
            workflow_run_id=run.id,
            gate_key="requirement_review:plan-0",
            gate_type="requirement_review",
            request_payload=build_gate_payload(
                gate_id, "requirement_review", run.id, targets=[], summary=old_summary
            ),
        )
        await uow.commit()

    body = client.get(f"/gates/{gate_id}", headers=headers).json()

    assert body["summary"]["unverified_mandatory_count"] == 1
    assert body["summary"]["mandatory_not_verified_yet_count"] is None
    assert body["summary"]["mandatory_verification_failed_count"] is None
    assert body["live_summary"]["unverified_mandatory_count"] == 1
    assert body["live_summary"]["mandatory_not_verified_yet_count"] == 1
    assert body["live_summary"]["mandatory_verification_failed_count"] == 0


async def test_a_decided_gate_has_no_live_counts(client: TestClient, register_actor):
    headers, _, org_id = register_actor()
    tender_id = _tender(client, headers, org_id)
    await _mandatory_requirement(tender_id)
    _, gate_id = await _open_gate(tender_id, "requirement_review")

    decided = client.post(
        f"/gates/{gate_id}/decision",
        json={"decision": "approve"},
        headers={**headers, "Idempotency-Key": f"live-{gate_id}"},
    )
    after = client.get(f"/gates/{gate_id}", headers=headers)

    assert decided.status_code == 200, decided.text
    assert decided.json()["live_summary"] is None
    assert after.json()["live_summary"] is None
    assert after.json()["summary"]["unverified_mandatory_count"] == 1


async def test_roles_without_a_pick_count_roles_not_candidate_matches(
    client: TestClient, register_actor
):
    """Item 79's C3: one role scored against four candidates read
    "Unselected roles 4". unselected_role_count counts roles."""
    headers, user_id, org_id = register_actor()
    tender_id = _tender(client, headers, org_id)
    async with UnitOfWork() as uow:
        picked = await uow.talent.create_role_requirement(
            tender_id=tender_id, role_name="Site lead", content_fingerprint=uuid.uuid4().hex
        )
        unpicked = await uow.talent.create_role_requirement(
            tender_id=tender_id, role_name="Supervisor", content_fingerprint=uuid.uuid4().hex
        )
        matches = []
        for role, count in ((picked, 3), (unpicked, 1)):
            for n in range(count):
                item, _ = await uow.knowledge.create_employee(
                    organization_id=uuid.UUID(org_id),
                    name=f"Candidate {role.role_name} {n}",
                    content_fingerprint=uuid.uuid4().hex,
                    title="Candidate",
                )
                matches.append(await uow.talent.create_role_match(role.id, item.id, score=0.5))
        matches[0].human_selected = True
        await uow.commit()
    _, gate_id = await _open_gate(tender_id, "resource_selection")

    body = client.get(f"/gates/{gate_id}", headers=headers).json()

    for counts in (body["summary"], body["live_summary"]):
        assert counts["role_count"] == 2
        assert counts["candidate_count"] == 4
        assert counts["unselected_count"] == 3  # matches, the old meaning
        assert counts["unselected_role_count"] == 1  # roles, what the panel shows


async def _critical_finding(tender_id: uuid.UUID) -> uuid.UUID:
    async with UnitOfWork() as uow:
        finding = await uow.redteam.create_finding(
            tender_id=tender_id,
            target_type="tender",
            target_id=None,
            finding_category="unsupported_claim",
            severity="critical",
            finding_text="A claim has no evidence.",
            violated_condition="Every claim is grounded.",
            content_fingerprint=uuid.uuid4().hex,
        )
        await uow.commit()
        return finding.id


@pytest.mark.parametrize(
    ("role", "may_decide"),
    [
        ("bid_manager", True),
        ("approver", True),
        ("solution_architect", False),
        ("talent_reviewer", False),
        ("commercial_reviewer", False),
        ("proposal_writer", False),
    ],
)
async def test_the_finding_gate_is_decided_only_by_bid_manager_approver_and_admin(
    client: TestClient, register_actor, add_member_with_role, role, may_decide
):
    admin_headers, _, org_id = register_actor()
    tender_id = _tender(client, admin_headers, org_id)
    finding_id = await _critical_finding(tender_id)
    _, gate_id = await _open_gate(tender_id, "finding_resolution")
    member_email = f"{uuid.uuid4()}@example.com"
    member_headers, _, _ = register_actor(email=member_email)
    add_member_with_role(admin_headers, org_id, member_email, role)

    # Every role still resolves one finding (resolve_redteam_finding).
    resolved = client.post(
        f"/redteam-findings/{finding_id}/resolve",
        json={"resolution_note": "Accepted, evidence attached separately."},
        headers=member_headers,
    )
    decided = client.post(
        f"/gates/{gate_id}/decision",
        json={"decision": "approve"},
        headers={**member_headers, "Idempotency-Key": f"finding-{gate_id}"},
    )

    assert resolved.status_code == 200, resolved.text
    if may_decide:
        assert decided.status_code == 200, decided.text
        assert decided.json()["status"] == "approved"
    else:
        assert decided.status_code == 403, decided.text


async def test_the_finding_gate_is_decided_by_an_admin(client: TestClient, register_actor):
    headers, _, org_id = register_actor()
    tender_id = _tender(client, headers, org_id)
    await _critical_finding(tender_id)
    _, gate_id = await _open_gate(tender_id, "finding_resolution")

    body = client.get(f"/gates/{gate_id}", headers=headers).json()
    decided = client.post(
        f"/gates/{gate_id}/decision",
        json={"decision": "approve"},
        headers={**headers, "Idempotency-Key": f"finding-admin-{gate_id}"},
    )

    assert body["live_summary"] == {"open_finding_count": 1, "critical_count": 1}
    assert decided.status_code == 200, decided.text


async def _resource_role_with_two_candidates(
    tender_id: uuid.UUID, org_id: str
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """A role with two unselected candidates. Returns (first match id,
    second match id, second employee's knowledge item id)."""
    async with UnitOfWork() as uow:
        role = await uow.talent.create_role_requirement(
            tender_id=tender_id, role_name="Site lead", content_fingerprint=uuid.uuid4().hex
        )
        made = []
        for n in range(2):
            item, _ = await uow.knowledge.create_employee(
                organization_id=uuid.UUID(org_id),
                name=f"Candidate {n}",
                content_fingerprint=uuid.uuid4().hex,
                title="Candidate",
            )
            made.append((await uow.talent.create_role_match(role.id, item.id, score=0.5), item))
        await uow.commit()
    return made[0][0].id, made[1][0].id, made[1][1].id


async def _set_item_status(knowledge_item_id: uuid.UUID, status: str) -> None:
    async with UnitOfWork() as uow:
        await uow.knowledge.update_knowledge_item(knowledge_item_id, status=status)
        await uow.commit()


async def test_a_pending_resource_gate_counts_a_selection_archived_after_it_opened(
    client: TestClient, register_actor
):
    """Plan item 86, option B: the live count follows an archive made after
    the gate opened; the snapshot keeps what the gate opened with."""
    headers, _, org_id = register_actor()
    tender_id = _tender(client, headers, org_id)
    first_match_id, _, _ = await _resource_role_with_two_candidates(tender_id, org_id)
    async with UnitOfWork() as uow:
        match = await uow.talent.update_role_match(first_match_id, human_selected=True)
        await uow.commit()
    _, gate_id = await _open_gate(tender_id, "resource_selection")

    await _set_item_status(match.employee_knowledge_item_id, "archived")
    body = client.get(f"/gates/{gate_id}", headers=headers).json()

    assert body["summary"]["archived_selected_count"] == 0
    assert body["live_summary"]["archived_selected_count"] == 1
