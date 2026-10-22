"""Phase 4 Stage 2 review: the three routes that create async operations,
called by a user from another organization.

POST /tenders/{id}/redteam/run, POST /tenders/{id}/proposal/generate and
POST /pricing-documents/{id}/validate are guarded by require_permission,
which resolves the path's resource to its organization and checks the
caller's membership before the route body runs. Like every other
tender-scoped route, it answers 403 ORG_ACCESS_DENIED to a non-member (the
existing tests in test_redteam_routes.py, test_proposal_routes.py and
test_commercial_routes.py assert that status), and so does GET
/tenders/{id}/operations/active. Only the /operations/{id} routes answer
404 (routes/operations.py), because an operation id alone would otherwise
reveal that another organization's operation exists.

What those tests did not check, and these do: the refusal happens before
anything is written, so no async_operations row and no outbox event for an
operation appears.
"""

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from tenderos_backend.db.models.async_operation import AsyncOperation
from tenderos_backend.db.models.outbox import OutboxEvent
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.events.outbox_events import AGGREGATE_ASYNC_OPERATION
from tenderos_backend.domain.services import commercial_service, document_service

pytestmark = pytest.mark.asyncio

FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "synthetic" / "tender_a"


async def _counts(tender_id: str) -> tuple[int, int]:
    """async_operations rows for this tender, and outbox events for any
    operation (tests run one at a time, so the total cannot move under us)."""
    async with UnitOfWork() as uow:
        operations = await uow.session.scalar(
            select(func.count())
            .select_from(AsyncOperation)
            .where(AsyncOperation.tender_id == uuid.UUID(tender_id))
        )
        events = await uow.session.scalar(
            select(func.count())
            .select_from(OutboxEvent)
            .where(OutboxEvent.aggregate_type == AGGREGATE_ASYNC_OPERATION)
        )
    return int(operations or 0), int(events or 0)


async def _pricing_document(org_id: str, tender_id: str) -> str:
    filename = "pricing_schedule_1.xlsx"
    document, _ = await document_service.create_document(
        tender_id=uuid.UUID(tender_id),
        organization_id=uuid.UUID(org_id),
        actor_user_id=uuid.uuid4(),
        filename=filename,
        content=(FIXTURES_DIR / filename).read_bytes(),
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    pricing_documents = await commercial_service.ingest_pricing_workbook(
        document.id, uuid.UUID(tender_id)
    )
    return str(pricing_documents[0].id)


@pytest.mark.parametrize("kind", ["redteam_run", "proposal_generation", "commercial_validation"])
async def test_another_organization_cannot_create_an_operation_and_nothing_is_written(
    client: TestClient, register_actor, kind
):
    headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders",
        json={"name": f"Cross-org {kind} tender"},
        headers=headers,
    ).json()
    outsider_headers, _, _ = register_actor()
    if kind == "redteam_run":
        url, extra = f"/tenders/{tender['id']}/redteam/run", {}
    elif kind == "proposal_generation":
        # The key is required; without it the 400 would come first.
        url, extra = (
            f"/tenders/{tender['id']}/proposal/generate",
            {"Idempotency-Key": str(uuid.uuid4())},
        )
    else:
        pricing_document_id = await _pricing_document(org_id, tender["id"])
        url, extra = f"/pricing-documents/{pricing_document_id}/validate", {}
    before = await _counts(tender["id"])

    response = client.post(url, headers={**outsider_headers, **extra})

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "ORG_ACCESS_DENIED"
    assert await _counts(tender["id"]) == before
    assert before[0] == 0
    # The owner's own request still works: the refusal is about the caller.
    owner = client.post(url, headers={**headers, **extra})
    assert owner.status_code == 202, owner.text
    assert (await _counts(tender["id"]))[0] == 1
