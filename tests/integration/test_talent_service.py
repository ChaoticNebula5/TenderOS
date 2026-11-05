"""domain/services/talent_service.py against real Postgres - B8A's own
Tests list, mapped directly: candidate-pool filtering (org isolation, bid
eligibility, profile-review state), selection audit + state_version bump,
and deselect-does-not-delete."""

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select
from tenderos_backend.core.enums import Role
from tenderos_backend.db.models.audit import AuditEvent
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services.auth_service import register
from tenderos_backend.domain.services.talent_service import (
    CrossOrganizationRoleError,
    _compute_candidate_talent_signals,
    find_candidate_pool,
    set_candidate_selection,
)

pytestmark = pytest.mark.asyncio


async def _make_org_and_tender(name: str) -> tuple[uuid.UUID, uuid.UUID]:
    async with get_session() as session:
        org = Organization(name=f"{name} Org")
        session.add(org)
        await session.flush()
        tender = Tender(organization_id=org.id, name=f"{name} Tender")
        session.add(tender)
        await session.flush()
        await session.commit()
        return org.id, tender.id


async def _make_actor_with_membership(org_id: uuid.UUID, role: Role = Role.ADMIN) -> uuid.UUID:
    # set_candidate_selection now derives+verifies real membership
    # internally (the CodeRabbit-review fix closing the vacuous
    # organization_id-parameter check) - a bare uuid.uuid4() actor with no
    # real membership row can no longer stand in for "an authorized
    # caller" the way it could when the check compared a value against
    # itself. Role.ADMIN by default since ADMIN is in every ACTION_ROLES
    # set, same "authorized for anything in their own org" shape
    # auth_service.register's own default membership already establishes.
    async with UnitOfWork() as uow:
        user = await uow.users.create(email=f"{uuid.uuid4().hex}@example.test", password_hash="x")
        await uow.memberships.create(org_id, user.id, role)
        await uow.commit()
        return user.id


async def _make_employee(
    org_id: uuid.UUID,
    name: str,
    *,
    eligible_for_bid: bool = True,
    profile_review_status: str = "verified",
    availability_status: str = "available",
) -> uuid.UUID:
    async with UnitOfWork() as uow:
        knowledge_item, employee = await uow.knowledge.create_employee(
            organization_id=org_id,
            name=name,
            content_fingerprint=str(uuid.uuid4()),
            title=name,
        )
        await uow.knowledge.update_employee(
            employee.knowledge_item_id,
            eligible_for_bid=eligible_for_bid,
            profile_review_status=profile_review_status,
            availability_status=availability_status,
        )
        await uow.commit()
        return knowledge_item.id


async def test_candidate_pool_excludes_employees_from_another_organization():
    org_a, _ = await _make_org_and_tender("Pool Org A")
    org_b, _ = await _make_org_and_tender("Pool Org B")
    await _make_employee(org_a, "Alice A")
    await _make_employee(org_b, "Bob B")

    pool_a = await find_candidate_pool(org_a)

    assert len(pool_a) == 1
    assert pool_a[0].name == "Alice A"


async def test_candidate_pool_excludes_employees_not_eligible_for_bid():
    org_id, _ = await _make_org_and_tender("Eligibility Test")
    await _make_employee(org_id, "Eligible Emp", eligible_for_bid=True)
    await _make_employee(org_id, "Ineligible Emp", eligible_for_bid=False)

    pool = await find_candidate_pool(org_id)

    names = {e.name for e in pool}
    assert names == {"Eligible Emp"}


async def test_candidate_pool_includes_stale_and_needs_review_profiles_not_hidden():
    # PRD 10.9: "surface explicit gaps and stale or unknown profile facts" -
    # this project's own interpretation is warn, not exclude (see
    # talent_service.py's list_candidate_pool_employees docstring).
    org_id, _ = await _make_org_and_tender("Stale Visibility Test")
    await _make_employee(org_id, "Stale Emp", profile_review_status="stale")
    await _make_employee(org_id, "Needs Review Emp", profile_review_status="needs_review")

    pool = await find_candidate_pool(org_id)

    names = {e.name for e in pool}
    assert names == {"Stale Emp", "Needs Review Emp"}


async def test_candidate_pool_excludes_archived_and_rejected_profiles():
    org_id, _ = await _make_org_and_tender("Hard Exclusion Test")
    await _make_employee(org_id, "Archived Emp", profile_review_status="archived")
    await _make_employee(org_id, "Rejected Emp", profile_review_status="rejected")
    await _make_employee(org_id, "Verified Emp", profile_review_status="verified")

    pool = await find_candidate_pool(org_id)

    names = {e.name for e in pool}
    assert names == {"Verified Emp"}


async def test_cert_signal_flags_expired_and_missing_certs():
    org_id, _ = await _make_org_and_tender("Cert Signal Test")
    employee_item_id = await _make_employee(org_id, "Cert Test Emp")
    async with UnitOfWork() as uow:
        await uow.knowledge.create_certification(
            organization_id=org_id,
            title="PMP",
            content_fingerprint=str(uuid.uuid4()),
            holder_employee_item_id=employee_item_id,
            valid_until=date.today() - timedelta(days=1),
        )
        employee = await uow.knowledge.get_employee(employee_item_id)
        signals = await _compute_candidate_talent_signals(
            uow, employee, required_certs=["PMP", "ISO 27001"], today=date.today()
        )

    by_cert = {f.required_cert: f.validity_status for f in signals.cert_findings}
    assert by_cert["PMP"] == "expired"
    assert by_cert["ISO 27001"] == "unverified"


async def test_select_candidate_writes_audit_and_bumps_state_version():
    org_id, tender_id = await _make_org_and_tender("Selection Audit Test")
    employee_item_id = await _make_employee(org_id, "Selectable Emp")
    async with UnitOfWork() as uow:
        role = await uow.talent.create_role_requirement(
            tender_id=tender_id, role_name="Project Manager", content_fingerprint=str(uuid.uuid4())
        )
        match = await uow.talent.create_role_match(
            role_requirement_id=role.id, employee_knowledge_item_id=employee_item_id
        )
        before_version = (await uow.tenders.get(tender_id)).state_version
        await uow.commit()
    actor_id = await _make_actor_with_membership(org_id)

    updated = await set_candidate_selection(role.id, employee_item_id, actor_id, selected=True)

    assert updated.human_selected is True
    async with UnitOfWork() as uow:
        after_version = (await uow.tenders.get(tender_id)).state_version
        result = await uow.session.execute(
            select(AuditEvent).where(
                AuditEvent.target_type == "role_match", AuditEvent.target_id == match.id
            )
        )
    events = result.scalars().all()
    assert after_version == before_version + 1
    assert len(events) == 1
    assert events[0].action == "role_select"
    assert events[0].actor_id == str(actor_id)


async def test_set_candidate_selection_rejects_a_role_outside_the_authorized_organization():
    """Regression test for the same tenant-binding gap fixed in
    knowledge_service.set_knowledge_selection - and, for a while, only
    LOOKED fixed here too. The previous version of this test injected an
    arbitrary organization_id argument directly at the function-call level
    (bypassing the only real caller, routes/talent.py's select_candidate
    route) and used a random, non-existent actor_id (uuid.uuid4(), never
    registered, never a member of anything). That correctly exercised the
    comparison LOGIC (a genuinely mismatched value is rejected) but never
    the real code path: the route derives whatever it passes from role_id
    itself, so no real caller could ever produce the mismatch this test
    manufactured directly - which is exactly why the underlying bug
    survived a "passing" test. set_candidate_selection no longer takes
    organization_id as a parameter at all; this test now uses a real
    registered actor with real ADMIN membership in org A (via auth_service.
    register, same as knowledge_service's own equivalent test) attempting
    to select a candidate for a role that genuinely belongs to org B - the
    function's own internal membership derivation is what has to catch
    this now, not an injected argument.
    """
    registered_a = await register(
        email=f"{uuid.uuid4().hex}@example.test",
        password="hunter2hunter2",
        organization_name="Role Auth Org A",
    )
    org_b, tender_b = await _make_org_and_tender("Role Auth Org B")
    employee_b = await _make_employee(org_b, "Org B Candidate")
    async with UnitOfWork() as uow:
        role_b = await uow.talent.create_role_requirement(
            tender_id=tender_b, role_name="Org B Role", content_fingerprint=str(uuid.uuid4())
        )
        await uow.talent.create_role_match(
            role_requirement_id=role_b.id, employee_knowledge_item_id=employee_b
        )
        await uow.commit()

    with pytest.raises(CrossOrganizationRoleError):
        await set_candidate_selection(role_b.id, employee_b, registered_a.user.id, selected=True)

    async with UnitOfWork() as uow:
        match = await uow.talent.get_role_match_by_candidate(role_b.id, employee_b)
    assert match is not None
    assert match.human_selected is False, (
        "a caller not authorized for org B must not select its candidate"
    )


async def test_deselect_candidate_does_not_delete_the_role_match_row():
    org_id, tender_id = await _make_org_and_tender("Deselect Test")
    employee_item_id = await _make_employee(org_id, "Deselectable Emp")
    async with UnitOfWork() as uow:
        role = await uow.talent.create_role_requirement(
            tender_id=tender_id, role_name="Network Engineer", content_fingerprint=str(uuid.uuid4())
        )
        await uow.talent.create_role_match(
            role_requirement_id=role.id, employee_knowledge_item_id=employee_item_id
        )
        await uow.commit()
    actor_id = await _make_actor_with_membership(org_id)

    await set_candidate_selection(role.id, employee_item_id, actor_id, selected=True)
    updated = await set_candidate_selection(role.id, employee_item_id, actor_id, selected=False)

    assert updated.human_selected is False
    async with UnitOfWork() as uow:
        still_there = await uow.talent.get_role_match_by_candidate(role.id, employee_item_id)
        employee_still_exists = await uow.knowledge.get_employee(employee_item_id)
    assert still_there is not None, "deselecting must update in place, never delete the row"
    assert employee_still_exists is not None, "deselecting must not delete the employee either"
