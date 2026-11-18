"""Plan items 89 and 91 (user decision Q5): GET /roles/{id}/matches returns
the scoring's stored gaps followed by the certificate gaps found after
scoring, read at request time and marked found_after_scoring."""

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from tenderos_backend.core.evidence_validity import today_utc
from tenderos_backend.db.unit_of_work import UnitOfWork

pytestmark = pytest.mark.asyncio


async def _role_and_match(tender_id: str, org_id: str, cert_valid_until) -> str:
    async with UnitOfWork() as uow:
        employee, _ = await uow.knowledge.create_employee(
            organization_id=uuid.UUID(org_id),
            name="Tomas Reyes",
            content_fingerprint=uuid.uuid4().hex,
            title="Tomas Reyes",
        )
        await uow.knowledge.create_certification(
            organization_id=uuid.UUID(org_id),
            title="PMP",
            content_fingerprint=uuid.uuid4().hex,
            holder_employee_item_id=employee.id,
            valid_until=cert_valid_until,
        )
        role = await uow.talent.create_role_requirement(
            tender_id=uuid.UUID(tender_id),
            role_name="Project Manager",
            content_fingerprint=uuid.uuid4().hex,
            required_certs=["PMP"],
        )
        await uow.talent.create_role_match(
            role_requirement_id=role.id,
            employee_knowledge_item_id=employee.id,
            gaps=[{"criterion": "availability", "status": "unknown"}],
        )
        await uow.commit()
        return str(role.id)


@pytest.mark.parametrize(
    ("days", "live"),
    [(30, []), (-1, [{"criterion": "PMP", "status": "expired", "found_after_scoring": True}])],
    ids=["in date", "lapsed after scoring"],
)
async def test_matches_carry_the_gaps_found_after_scoring(
    client: TestClient, register_actor, days, live
):
    headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Live Gaps Bid"}, headers=headers
    ).json()
    role_id = await _role_and_match(tender["id"], org_id, today_utc() + timedelta(days=days))

    response = client.get(f"/roles/{role_id}/matches", headers=headers)

    assert response.status_code == 200, response.text
    (match,) = response.json()
    assert match["gaps"] == [{"criterion": "availability", "status": "unknown"}, *live]
