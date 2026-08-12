from collections.abc import AsyncIterator, Awaitable, Callable
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request, status
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

_bearer_scheme = HTTPBearer()


async def get_db_session() -> AsyncIterator[AsyncSession]:
    async with _get_backend_session() as session:
        yield session


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> User:
    try:
        payload = decode_token(credentials.credentials)
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
