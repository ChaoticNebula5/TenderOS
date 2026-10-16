from collections.abc import AsyncIterator, Awaitable, Callable
from typing import NamedTuple
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from tenderos_backend.core.enums import Role
from tenderos_backend.core.security import decode_token
from tenderos_backend.db.models.user import User
from tenderos_backend.db.session import get_session as _get_backend_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.policies.rbac import ACTION_ROLES
from tenderos_backend.domain.services import tender_service
from tenderos_backend.orchestration.gates import action_for_gate_type

from tenderos_api.errors import OrgAccessDeniedError
from tenderos_api.schemas.requirements import MergeRequirementsRequest
from tenderos_api.sse_tickets import (
    SCOPE_OPERATION,
    SCOPE_WORKFLOW_RUN,
    consume_sse_ticket_with_scope,
)

_bearer_scheme = HTTPBearer()
# auto_error=False: get_current_user_for_sse (below) needs to try the
# header first and fall back to a query param, which means a missing
# header must not raise on its own - only get_current_user_for_sse decides
# that, once it's also checked the query param.
_bearer_scheme_optional = HTTPBearer(auto_error=False)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    async with _get_backend_session() as session:
        yield session


async def _user_from_token(raw_token: str) -> User:
    try:
        payload = decode_token(raw_token)
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token"
        ) from exc

    if payload.get("token_type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not an access token")

    async with UnitOfWork() as uow:
        user = await uow.users.get(UUID(str(payload["user_id"])))
        if user is None or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive"
            )
        return user


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> User:
    return await _user_from_token(credentials.credentials)


_SSE_SCOPE_PATH_PARAMS = {SCOPE_WORKFLOW_RUN: "run_id", SCOPE_OPERATION: "operation_id"}


async def get_current_user_for_sse(
    request: Request,
    ticket: str | None = Query(default=None),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme_optional),
) -> User:
    """EventSource cannot send a custom Authorization header, so
    GET .../events/stream (routes/workflow.py) is the one route in this
    app that needs a token from somewhere other than the header - the
    header path stays available too (curl/Swagger/a future non-browser
    client).

    B15: the real access JWT no longer travels as a query param - that was
    a real, accepted exposure (the token in server access logs and browser
    history for its full jwt_access_minutes life), named and deferred to
    this block in KNOWN_LIMITATIONS.md's B14 SSE-auth entry. A caller
    without a header now mints a short-lived, single-use ticket first
    (POST .../events/ticket -> sse_tickets.mint_sse_ticket) and passes
    that instead - consumed here via sse_tickets.consume_sse_ticket
    (Redis GETDEL, so a ticket cannot be replayed even within its own
    30s window), and checked against request.path_params["run_id"] so a
    ticket only ever opens the one stream it was minted for.
    """
    if credentials is not None:
        return await _user_from_token(credentials.credentials)

    if ticket is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token or ticket",
        )
    resolved = await consume_sse_ticket_with_scope(ticket)
    if resolved is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ticket is invalid, expired, or already used",
        )
    user_id, scope, bound_resource_id = resolved
    # Phase 4: a ticket names its scope; it opens only a stream of that kind
    # (a workflow run's or an async operation's), for the one resource in
    # the route's own path parameter.
    path_param = _SSE_SCOPE_PATH_PARAMS.get(scope)
    requested_id = request.path_params.get(path_param) if path_param else None
    if requested_id is None or str(bound_resource_id) != requested_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ticket is not valid for this stream",
        )

    async with UnitOfWork() as uow:
        user = await uow.users.get(user_id)
        if user is None or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive"
            )
        return user


async def _resolve_organization_id(request: Request) -> UUID:
    org_id = request.path_params.get("org_id")
    if org_id is not None:
        return UUID(org_id)

    tender_id = request.path_params.get("tender_id")
    if tender_id is not None:
        tender = await tender_service.get_tender(UUID(tender_id))
        if tender is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tender not found")
        return tender.organization_id

    run_id = request.path_params.get("run_id")
    if run_id is not None:
        return await _organization_id_for_run(UUID(run_id))

    gate_id = request.path_params.get("gate_id")
    if gate_id is not None:
        async with UnitOfWork() as uow:
            gate = await uow.gates.get(UUID(gate_id))
        if gate is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gate not found")
        return await _organization_id_for_run(gate.workflow_run_id)

    document_id = request.path_params.get("document_id")
    if document_id is not None:
        return await _organization_id_for_document(UUID(document_id))

    requirement_id = request.path_params.get("requirement_id")
    if requirement_id is not None:
        return await _organization_id_for_requirement(UUID(requirement_id))

    submission_item_id = request.path_params.get("submission_item_id")
    if submission_item_id is not None:
        return await _organization_id_for_submission_item(UUID(submission_item_id))

    # B6 (G6): knowledge_service's own routes never carry org_id/tender_id
    # in most of their paths (e.g. GET /knowledge/{knowledge_item_id},
    # POST /employees/{knowledge_item_id}/verify) - these three branches
    # were missing entirely until this fix, which meant every such route
    # fell through to the 400 below regardless of the caller's actual
    # permissions. Caught live: a real smoke-test run against the Docker
    # stack returned 400 "No organization context in path" for
    # GET /knowledge/import-batches/{batch_id}/items.
    batch_id = request.path_params.get("batch_id")
    if batch_id is not None:
        return await _organization_id_for_import_batch(UUID(batch_id))

    item_id = request.path_params.get("item_id")
    if item_id is not None:
        return await _organization_id_for_import_item(UUID(item_id))

    knowledge_item_id = request.path_params.get("knowledge_item_id")
    if knowledge_item_id is not None:
        return await _organization_id_for_knowledge_item(UUID(knowledge_item_id))

    # B7 (G7): GET/PATCH /compliance/{compliance_id}, .../approve, .../trace
    # all key on compliance_id with no org_id/tender_id alongside it in the
    # path - added proactively during B7 planning rather than waiting for a
    # smoke test to find the gap, since this is the third block in a row
    # (B6 added batch_id/item_id/knowledge_item_id) that needed a new branch
    # here for its own route shape.
    compliance_id = request.path_params.get("compliance_id")
    if compliance_id is not None:
        return await _organization_id_for_compliance_entry(UUID(compliance_id))

    # B8A (G8): GET /roles/{role_id}/matches, POST /roles/{role_id}/
    # select-candidate both key on role_id with no tender_id alongside it.
    # Simpler than compliance_id's three-hop lookup - role_requirements
    # carries tender_id directly (Part C), so this is one hop, same shape
    # as _organization_id_for_requirement.
    role_id = request.path_params.get("role_id")
    if role_id is not None:
        return await _organization_id_for_role_requirement(UUID(role_id))

    # B8B (G9): POST /pricing-documents/{pricing_document_id}/validate and
    # POST /pricing-findings/{pricing_finding_id}/resolve both key on their
    # own id with no tender_id alongside it. Both one-hop, same shape as
    # role_id above - pricing_documents/pricing_findings both carry
    # tender_id directly (Part C).
    pricing_document_id = request.path_params.get("pricing_document_id")
    if pricing_document_id is not None:
        return await _organization_id_for_pricing_document(UUID(pricing_document_id))

    pricing_finding_id = request.path_params.get("pricing_finding_id")
    if pricing_finding_id is not None:
        return await _organization_id_for_pricing_finding(UUID(pricing_finding_id))

    # B9 (G10): PATCH /proposal/sections/{section_id} and GET /proposal/
    # claims/{claim_id}/trace both key on their own id with no tender_id
    # alongside it - GET /tenders/{tender_id}/proposal/sections and POST
    # .../proposal/generate already resolve through the existing tender_id
    # branch above. section_id is one-hop (proposal_sections carries
    # tender_id directly, Part C); claim_id is two-hop (proposal_claims has
    # no tender_id of its own, only reachable via section_id), same shape as
    # _organization_id_for_compliance_entry.
    section_id = request.path_params.get("section_id")
    if section_id is not None:
        return await _organization_id_for_proposal_section(UUID(section_id))

    claim_id = request.path_params.get("claim_id")
    if claim_id is not None:
        return await _organization_id_for_proposal_claim(UUID(claim_id))

    # B11 (G11): POST /redteam-findings/{finding_id}/resolve keys on
    # finding_id alone, no tender_id in the path - one-hop, same shape as
    # _organization_id_for_pricing_finding (redteam_findings.tender_id is a
    # direct FK, Part C). GET /tenders/{tender_id}/redteam-findings already
    # resolves via the tender_id branch above.
    finding_id = request.path_params.get("finding_id")
    if finding_id is not None:
        return await _organization_id_for_redteam_finding(UUID(finding_id))

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST, detail="No organization context in path"
    )


async def _organization_id_for_run(run_id: UUID) -> UUID:
    async with UnitOfWork() as uow:
        run = await uow.workflow.get_run(run_id)
        if run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Workflow run not found"
            )
        tender = await uow.tenders.get(run.tender_id)
    if tender is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tender not found")
    return tender.organization_id


async def _organization_id_for_document(document_id: UUID) -> UUID:
    """A document is scoped to EITHER a tender or an organization directly
    (Part C's ck_document_scope CHECK enforces exactly one). Tender-scoped
    documents resolve through their tender; org-scoped ones (B6's company
    knowledge) already carry organization_id.
    """
    async with UnitOfWork() as uow:
        document = await uow.documents.get(document_id)
        if document is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
        if document.organization_id is not None:
            return document.organization_id
        if document.tender_id is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Document has neither tender nor organization scope",
            )
        tender = await uow.tenders.get(document.tender_id)
    if tender is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tender not found")
    return tender.organization_id


async def _organization_id_for_requirement(requirement_id: UUID) -> UUID:
    async with UnitOfWork() as uow:
        requirement = await uow.requirements.get(requirement_id)
        if requirement is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Requirement not found"
            )
        tender = await uow.tenders.get(requirement.tender_id)
    if tender is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tender not found")
    return tender.organization_id


async def _organization_id_for_submission_item(submission_item_id: UUID) -> UUID:
    # Deliberately its own path-param name (submission_item_id), not the
    # existing item_id branch below - item_id already resolves
    # knowledge_import_items (B6), an unrelated resource. Reusing it here
    # would have silently looked up the wrong table for this route's id.
    async with UnitOfWork() as uow:
        item = await uow.requirements.get_submission_item(submission_item_id)
        if item is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Submission item not found"
            )
        tender = await uow.tenders.get(item.tender_id)
    if tender is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tender not found")
    return tender.organization_id


async def _organization_id_for_import_batch(batch_id: UUID) -> UUID:
    async with UnitOfWork() as uow:
        batch = await uow.knowledge.get_import_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import batch not found")
    return batch.organization_id


async def _organization_id_for_import_item(item_id: UUID) -> UUID:
    async with UnitOfWork() as uow:
        item = await uow.knowledge.get_import_item(item_id)
        if item is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Import item not found"
            )
        batch = await uow.knowledge.get_import_batch(item.batch_id)
    if batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import batch not found")
    return batch.organization_id


async def _organization_id_for_knowledge_item(knowledge_item_id: UUID) -> UUID:
    async with UnitOfWork() as uow:
        item = await uow.knowledge.get_knowledge_item(knowledge_item_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge item not found"
        )
    return item.organization_id


async def _organization_id_for_compliance_entry(compliance_entry_id: UUID) -> UUID:
    """compliance_entries carries no organization_id or tender_id of its
    own (Part C) - it only ever reaches one through its one-to-one
    requirement, which is itself only ever reached through its tender. Same
    two-hop shape as _organization_id_for_requirement, just one hop longer.
    """
    async with UnitOfWork() as uow:
        entry = await uow.compliance.get_compliance_entry(compliance_entry_id)
        if entry is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Compliance entry not found"
            )
        requirement = await uow.requirements.get(entry.requirement_id)
        if requirement is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Requirement not found"
            )
        tender = await uow.tenders.get(requirement.tender_id)
    if tender is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tender not found")
    return tender.organization_id


async def _organization_id_for_role_requirement(role_requirement_id: UUID) -> UUID:
    async with UnitOfWork() as uow:
        role = await uow.talent.get_role_requirement(role_requirement_id)
        if role is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Role requirement not found"
            )
        tender = await uow.tenders.get(role.tender_id)
    if tender is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tender not found")
    return tender.organization_id


async def _organization_id_for_pricing_document(pricing_document_id: UUID) -> UUID:
    async with UnitOfWork() as uow:
        pricing_document = await uow.commercial.get_pricing_document(pricing_document_id)
        if pricing_document is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Pricing document not found"
            )
        tender = await uow.tenders.get(pricing_document.tender_id)
    if tender is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tender not found")
    return tender.organization_id


async def _organization_id_for_pricing_finding(pricing_finding_id: UUID) -> UUID:
    async with UnitOfWork() as uow:
        finding = await uow.commercial.get_pricing_finding(pricing_finding_id)
        if finding is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Pricing finding not found"
            )
        tender = await uow.tenders.get(finding.tender_id)
    if tender is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tender not found")
    return tender.organization_id


async def _organization_id_for_redteam_finding(finding_id: UUID) -> UUID:
    async with UnitOfWork() as uow:
        finding = await uow.redteam.get(finding_id)
        if finding is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Finding not found")
        tender = await uow.tenders.get(finding.tender_id)
    if tender is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tender not found")
    return tender.organization_id


async def _organization_id_for_proposal_section(section_id: UUID) -> UUID:
    async with UnitOfWork() as uow:
        section = await uow.proposal.get_proposal_section(section_id)
        if section is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Proposal section not found"
            )
        tender = await uow.tenders.get(section.tender_id)
    if tender is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tender not found")
    return tender.organization_id


async def _organization_id_for_proposal_claim(claim_id: UUID) -> UUID:
    """proposal_claims carries no tender_id of its own (Part C) - only
    reachable through its section, same two-hop shape as
    _organization_id_for_compliance_entry.
    """
    async with UnitOfWork() as uow:
        claim = await uow.proposal.get_proposal_claim(claim_id)
        if claim is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Proposal claim not found"
            )
        section = await uow.proposal.get_proposal_section(claim.section_id)
        if section is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Proposal section not found"
            )
        tender = await uow.tenders.get(section.tender_id)
    if tender is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tender not found")
    return tender.organization_id


async def _check_permission(organization_id: UUID, current_user: User, action: str) -> None:
    async with UnitOfWork() as uow:
        membership = await uow.memberships.get(organization_id, current_user.id)

        if membership is None:
            raise OrgAccessDeniedError()

        member_role = Role(membership.role)
        if member_role not in ACTION_ROLES[action]:
            raise OrgAccessDeniedError()


def require_permission(action: str) -> Callable[..., Awaitable[User]]:
    """Dependency factory: only lets the request through if the caller has a
    membership in the request's organization AND that membership's role is
    permitted for `action` per Part H's RBAC matrix (tenderos_backend.domain.
    policies.rbac.ACTION_ROLES).
    """

    async def _dependency(request: Request, current_user: User = Depends(get_current_user)) -> User:
        organization_id = await _resolve_organization_id(request)
        await _check_permission(organization_id, current_user, action)
        return current_user

    return _dependency


def require_permission_for_sse(action: str) -> Callable[..., Awaitable[User]]:
    """Same resolve-and-check as require_permission(action), sourcing
    current_user from get_current_user_for_sse instead of get_current_user -
    the one difference GET .../events/stream needs, since EventSource can't
    send the header every other route's auth relies on."""

    async def _dependency(
        request: Request, current_user: User = Depends(get_current_user_for_sse)
    ) -> User:
        organization_id = await _resolve_organization_id(request)
        await _check_permission(organization_id, current_user, action)
        return current_user

    return _dependency


class AuthorizedActor(NamedTuple):
    """current_user plus the organization_id require_permission_with_org
    already resolved (from the target resource in the path) and verified
    (current_user has a permitted membership in it). That organization_id
    is still the RESOURCE's org, not some independently-sourced "caller's
    home org" - _resolve_organization_id has no other way to determine
    scope for a path keyed on a single resource id. What actually protects
    the route is _check_permission rejecting an unpermitted caller before
    the route body runs, same as plain require_permission. This exists so
    a route can hand that already-resolved value to its service function
    without a second, redundant fetch of the same resource - not so the
    service can re-verify anything by comparing against it (a comparison
    of a value against itself proves nothing, which is the exact defect
    this was added to stop reproducing - see knowledge_service.
    set_knowledge_selection / talent_service.set_candidate_selection).
    """

    user: User
    organization_id: UUID


def require_permission_with_org(action: str) -> Callable[..., Awaitable[AuthorizedActor]]:
    """Same resolve-and-check as require_permission(action), for routes
    whose caller wants the already-resolved organization_id itself (e.g.
    to log it) instead of re-deriving it from the target resource a second
    time inside the route. Not a substitute for a service function
    verifying its own caller - see AuthorizedActor's docstring.
    """

    async def _dependency(
        request: Request, current_user: User = Depends(get_current_user)
    ) -> AuthorizedActor:
        organization_id = await _resolve_organization_id(request)
        await _check_permission(organization_id, current_user, action)
        return AuthorizedActor(user=current_user, organization_id=organization_id)

    return _dependency


async def require_gate_decision_permission(
    request: Request, current_user: User = Depends(get_current_user)
) -> User:
    """RBAC for POST /gates/{gate_id}/decision specifically.

    require_permission's action is a fixed string chosen at route-decoration
    time, but this route's required action depends on the gate's OWN
    gate_type (orchestration.gates.GATE_TYPE_ACTIONS - a gate opened as
    "final_readiness" needs a different action than one opened as
    "requirement_review"), which isn't known until the gate is fetched from
    the DB. Same membership/role check as require_permission, just with the
    action resolved dynamically instead of passed in statically.
    """
    gate_id = request.path_params.get("gate_id")
    if gate_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No gate_id in path")
    async with UnitOfWork() as uow:
        gate = await uow.gates.get(UUID(gate_id))
    if gate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gate not found")

    organization_id = await _resolve_organization_id(request)
    await _check_permission(organization_id, current_user, action_for_gate_type(gate.gate_type))
    return current_user


async def require_merge_permission(
    body: MergeRequirementsRequest, current_user: User = Depends(get_current_user)
) -> User:
    """RBAC for POST /requirements/merge specifically - that route has no
    {requirement_id} in its path (it takes several, in the body), so
    require_permission's path-param-based _resolve_organization_id has
    nothing to key off. Resolves organization_id from the body's first
    requirement_id instead - same "resolve dynamically, not from the path"
    shape as require_gate_decision_permission, just body-keyed instead of
    gate-type-keyed. FastAPI parses/validates `body` once and shares it with
    the route handler's own body parameter - not read twice.
    """
    if not body.requirement_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="requirement_ids must not be empty"
        )
    organization_id = await _organization_id_for_requirement(body.requirement_ids[0])
    await _check_permission(organization_id, current_user, "review_requirements")
    return current_user
