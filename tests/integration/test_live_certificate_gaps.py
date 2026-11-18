"""Plan items 89 and 91 (user decision Q5): a role match's certificate gaps
found after scoring. A selected employee's required certificate that lapses,
is withdrawn or is marked unverified after the match was scored shows as a
gap marked found_after_scoring in the red team's role_qualification_gap and
(tests/route/test_role_match_live_gaps.py) the role-match API; the stored
gaps are not rewritten, and a requirement they already name is not
repeated."""

import uuid
from datetime import timedelta

import pytest
from tenderos_backend.core.evidence_validity import today_utc
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import redteam_checks, talent_service

pytestmark = pytest.mark.asyncio

TODAY = today_utc()


async def _selected_match(cert_valid_until, cert_status="active", stored_gaps=None):
    """A tender with one role requiring "ISO 27001", and a selected match
    for an employee holding "ISO 27001 Lead Implementer"."""
    async with get_session() as session:
        org = Organization(name="Live Gaps Org")
        session.add(org)
        await session.flush()
        tender = Tender(organization_id=org.id, name="Live Gaps Tender")
        session.add(tender)
        await session.commit()
    async with UnitOfWork() as uow:
        employee, _ = await uow.knowledge.create_employee(
            organization_id=org.id,
            name="Marcus Ilori",
            content_fingerprint=uuid.uuid4().hex,
            title="Marcus Ilori",
        )
        cert, _ = await uow.knowledge.create_certification(
            organization_id=org.id,
            title="ISO 27001 Lead Implementer",
            content_fingerprint=uuid.uuid4().hex,
            holder_employee_item_id=employee.id,
            valid_until=cert_valid_until,
            status=cert_status,
        )
        role = await uow.talent.create_role_requirement(
            tender_id=tender.id,
            role_name="Security Lead",
            content_fingerprint=uuid.uuid4().hex,
            required_certs=["ISO 27001"],
        )
        match = await uow.talent.create_role_match(
            role_requirement_id=role.id,
            employee_knowledge_item_id=employee.id,
            gaps=stored_gaps or [],
        )
        await uow.talent.update_role_match(match.id, human_selected=True)
        await uow.commit()
    return tender.id, match.id


async def _live_gaps(match_id):
    async with UnitOfWork() as uow:
        match = await uow.talent.get_role_match(match_id)
        return (await talent_service.live_certificate_gaps_in_uow(uow, [match], TODAY))[match_id]


async def _redteam(tender_id):
    async with UnitOfWork() as uow:
        return await redteam_checks.check_role_qualification_gaps(uow, tender_id)


async def test_an_in_date_certificate_gives_no_gap_and_no_finding():
    tender_id, match_id = await _selected_match(TODAY + timedelta(days=30))

    assert await _live_gaps(match_id) == []
    assert await _redteam(tender_id) == []


@pytest.mark.parametrize(
    ("valid_until", "status", "gap_status"),
    [
        (TODAY - timedelta(days=1), "active", "expired"),
        (None, "archived", "unverified"),
        (None, "unverified", "unverified"),
    ],
    ids=["lapsed", "withdrawn", "unverified"],
)
async def test_a_certificate_no_longer_held_shows_as_found_after_scoring(
    valid_until, status, gap_status
):
    tender_id, match_id = await _selected_match(valid_until, status)

    assert await _live_gaps(match_id) == [
        {"criterion": "ISO 27001", "status": gap_status, "found_after_scoring": True}
    ]
    (finding,) = await _redteam(tender_id)
    assert finding.finding_category == "role_qualification_gap"
    assert "1 unmet requirement" in finding.finding_text
    assert "ISO 27001" in finding.violated_condition
    async with UnitOfWork() as uow:
        assert (await uow.talent.get_role_match(match_id)).gaps == []


async def test_a_gap_the_scoring_already_named_is_not_repeated():
    stored = [{"criterion": "ISO 27001", "status": "expired"}]
    tender_id, match_id = await _selected_match(TODAY - timedelta(days=1), stored_gaps=stored)

    assert await _live_gaps(match_id) == []
    (finding,) = await _redteam(tender_id)
    assert "1 unmet requirement" in finding.finding_text
