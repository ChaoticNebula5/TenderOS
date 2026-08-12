from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from tenderos_backend.db.models.user import User
from tenderos_backend.domain.services import document_service, knowledge_service

from tenderos_api.dependencies import require_permission
from tenderos_api.routes.documents import _validate_upload
from tenderos_api.schemas.knowledge import (
    EmployeeResponse,
    ImportBatchResponse,
    ImportItemDetailResponse,
    ImportItemResponse,
    KnowledgeItemResponse,
    KnowledgeSelectionResponse,
    PatchEmployeeProfileRequest,
    PatchKnowledgeItemRequest,
    RejectImportItemRequest,
    SetKnowledgeSelectionRequest,
    VerifyEmployeeRequest,
)

router = APIRouter(tags=["knowledge"])

# One RBAC action for every G6 write - Part H already locks
# manage_organization_knowledge (BID_MANAGER/SOLUTION_ARCHITECT/
# TALENT_REVIEWER/ADMIN) for exactly this surface; no new action key needed.
_MANAGE = "manage_organization_knowledge"


async def _upload_batch_document(
    org_id: UUID,
    actor_user_id: UUID,
    filename: str,
    content: bytes,
    mime_type: str | None,
    doc_type: str,
    batch_id: UUID,
) -> None:
    document, _is_new = await document_service.create_document(
        organization_id=org_id,
        actor_user_id=actor_user_id,
        filename=filename,
        content=content,
        mime_type=mime_type,
        side="company",
        doc_type=doc_type,
    )
    await knowledge_service.queue_knowledge_document_import(document.id, batch_id)


@router.post(
    "/organizations/{org_id}/knowledge/import-batches/employee-master",
    response_model=ImportBatchResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_employee_master(
    org_id: UUID,
    file: UploadFile = File(...),
    current_user: User = Depends(require_permission(_MANAGE)),
) -> ImportBatchResponse:
    """G6's bulk employee-master upload. 202: the actual row parsing runs on
    the worker (process_knowledge_document_import), never inside this
    request (line 538) - re-poll GET .../import-batches/{id} for progress.
    """
    content = await file.read()
    filename = file.filename or "employee_master.xlsx"
    _validate_upload(filename, file.content_type, len(content))

    document, _is_new = await document_service.create_document(
        organization_id=org_id,
        actor_user_id=current_user.id,
        filename=filename,
        content=content,
        mime_type=file.content_type,
        side="company",
        doc_type="employee_master",
    )
    batch = await knowledge_service.create_import_batch(
        org_id, "employee_master_xlsx", document.id, current_user.id
    )
    await knowledge_service.queue_knowledge_document_import(document.id, batch.id)
    return ImportBatchResponse.model_validate(batch, from_attributes=True)


@router.post(
    "/organizations/{org_id}/knowledge/import-batches/certifications",
    response_model=ImportBatchResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_certifications(
    org_id: UUID,
    file: UploadFile = File(...),
    current_user: User = Depends(require_permission(_MANAGE)),
) -> ImportBatchResponse:
    content = await file.read()
    filename = file.filename or "certifications.xlsx"
    _validate_upload(filename, file.content_type, len(content))

    document, _is_new = await document_service.create_document(
        organization_id=org_id,
        actor_user_id=current_user.id,
        filename=filename,
        content=content,
        mime_type=file.content_type,
        side="company",
        doc_type="certification_documents",
    )
    batch = await knowledge_service.create_import_batch(
        org_id, "certification_documents", document.id, current_user.id
    )
    await knowledge_service.queue_knowledge_document_import(document.id, batch.id)
    return ImportBatchResponse.model_validate(batch, from_attributes=True)


@router.post(
    "/organizations/{org_id}/knowledge/import-batches/resumes",
    response_model=ImportBatchResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_resumes(
    org_id: UUID,
    files: list[UploadFile] = File(...),
    current_user: User = Depends(require_permission(_MANAGE)),
) -> ImportBatchResponse:
    """G6's bulk resume upload - "one import item per file" (B6 build
    list). One batch, one document + one KnowledgeDocumentUploaded event
    per resume - each resume is processed (and can fail/retry)
    independently of the others in the same batch.
    """
    batch = await knowledge_service.create_import_batch(
        org_id, "bulk_resumes", None, current_user.id
    )
    for file in files:
        content = await file.read()
        filename = file.filename or "resume.docx"
        _validate_upload(filename, file.content_type, len(content))
        await _upload_batch_document(
            org_id,
            current_user.id,
            filename,
            content,
            file.content_type,
            "resume",
            batch.id,
        )
    return ImportBatchResponse.model_validate(batch, from_attributes=True)


@router.post(
    "/organizations/{org_id}/knowledge/documents",
    response_model=KnowledgeItemResponse | None,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_knowledge_document(
    org_id: UUID,
    file: UploadFile = File(...),
    current_user: User = Depends(require_permission(_MANAGE)),
) -> None:
    """G6's generic org-knowledge document upload - the capability
    statement's route (Part C's import_type enum has no dedicated value
    for it, so this uses "manual", same as knowledge_service's own
    create_import_batch call sites for it). Returns nothing meaningful yet
    (202, no body) since extraction hasn't run - poll
    GET /organizations/{org_id}/knowledge for the resulting projects/
    capabilities once processing completes.
    """
    content = await file.read()
    filename = file.filename or "document.docx"
    _validate_upload(filename, file.content_type, len(content))

    document, _is_new = await document_service.create_document(
        organization_id=org_id,
        actor_user_id=current_user.id,
        filename=filename,
        content=content,
        mime_type=file.content_type,
        side="company",
        doc_type="capability_statement",
    )
    batch = await knowledge_service.create_import_batch(
        org_id, "manual", document.id, current_user.id
    )
    await knowledge_service.queue_knowledge_document_import(document.id, batch.id)


@router.get(
    "/organizations/{org_id}/knowledge/import-batches", response_model=list[ImportBatchResponse]
)
async def list_import_batches(
    org_id: UUID, current_user: User = Depends(require_permission(_MANAGE))
) -> list[ImportBatchResponse]:
    batches = await knowledge_service.list_import_batches(org_id)
    return [ImportBatchResponse.model_validate(b, from_attributes=True) for b in batches]


@router.get("/knowledge/import-batches/{batch_id}", response_model=ImportBatchResponse)
async def get_import_batch(
    batch_id: UUID, current_user: User = Depends(require_permission(_MANAGE))
) -> ImportBatchResponse:
    batch = await knowledge_service.get_import_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import batch not found")
    return ImportBatchResponse.model_validate(batch, from_attributes=True)


@router.get(
    "/knowledge/import-batches/{batch_id}/items", response_model=list[ImportItemDetailResponse]
)
async def list_import_items(
    batch_id: UUID, current_user: User = Depends(require_permission(_MANAGE))
) -> list[ImportItemDetailResponse]:
    items = await knowledge_service.list_import_items(batch_id)
    return [ImportItemDetailResponse.model_validate(i, from_attributes=True) for i in items]


async def _import_item_organization_id(item_id: UUID) -> tuple[UUID, UUID]:
    """Resolves (item, organization_id) for an import item - organization_id
    lives on the batch, not the item itself, so approve/reject need this
    extra hop before they can write an audit event."""
    item = await knowledge_service.get_import_item(item_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import item not found")
    batch = await knowledge_service.get_import_batch(item.batch_id)
    if batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import batch not found")
    return item, batch.organization_id


@router.post("/knowledge/import-items/{item_id}/approve", response_model=ImportItemResponse)
async def approve_import_item(
    item_id: UUID, current_user: User = Depends(require_permission(_MANAGE))
) -> ImportItemResponse:
    _item, org_id = await _import_item_organization_id(item_id)
    updated = await knowledge_service.approve_import_item(item_id, org_id, current_user.id)
    return ImportItemResponse.model_validate(updated, from_attributes=True)


@router.post("/knowledge/import-items/{item_id}/reject", response_model=ImportItemResponse)
async def reject_import_item(
    item_id: UUID,
    body: RejectImportItemRequest,
    current_user: User = Depends(require_permission(_MANAGE)),
) -> ImportItemResponse:
    _item, org_id = await _import_item_organization_id(item_id)
    updated = await knowledge_service.reject_import_item(
        item_id, org_id, current_user.id, body.reason
    )
    return ImportItemResponse.model_validate(updated, from_attributes=True)


@router.get("/organizations/{org_id}/knowledge", response_model=list[KnowledgeItemResponse])
async def list_organization_knowledge(
    org_id: UUID,
    item_type: str | None = None,
    current_user: User = Depends(require_permission(_MANAGE)),
) -> list[KnowledgeItemResponse]:
    items = await knowledge_service.list_knowledge_for_org(org_id, item_type)
    return [KnowledgeItemResponse.model_validate(i, from_attributes=True) for i in items]


@router.get("/organizations/{org_id}/employees", response_model=list[EmployeeResponse])
async def list_bid_eligible_employees(
    org_id: UUID, current_user: User = Depends(require_permission(_MANAGE))
) -> list[EmployeeResponse]:
    """ "New tender candidate search sees only verified bid-eligible
    profiles" (B6 smoke test) - the ONLY employee-listing route, and it is
    bid-eligible-only by construction (see
    KnowledgeRepository.list_bid_eligible_employees). There is no route
    that lists every employee regardless of review state; a reviewer works
    the import-batch/item queue instead (GET .../import-batches/{id}/items),
    which is where unreviewed profiles are actually visible.
    """
    employees = await knowledge_service.list_bid_eligible_employees(org_id)
    return [EmployeeResponse.model_validate(e, from_attributes=True) for e in employees]


@router.get("/knowledge/{knowledge_item_id}", response_model=KnowledgeItemResponse)
async def get_knowledge_item(
    knowledge_item_id: UUID, current_user: User = Depends(require_permission(_MANAGE))
) -> KnowledgeItemResponse:
    item = await knowledge_service.get_knowledge_item(knowledge_item_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge item not found"
        )
    return KnowledgeItemResponse.model_validate(item, from_attributes=True)


@router.patch("/knowledge/{knowledge_item_id}", response_model=KnowledgeItemResponse)
async def patch_knowledge_item(
    knowledge_item_id: UUID,
    body: PatchKnowledgeItemRequest,
    current_user: User = Depends(require_permission(_MANAGE)),
) -> KnowledgeItemResponse:
    item = await knowledge_service.get_knowledge_item(knowledge_item_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge item not found"
        )
    updated = await knowledge_service.patch_knowledge_item(
        knowledge_item_id,
        item.organization_id,
        current_user.id,
        title=body.title,
        status=body.status,
    )
    return KnowledgeItemResponse.model_validate(updated, from_attributes=True)


@router.patch("/employees/{knowledge_item_id}/profile", response_model=EmployeeResponse)
async def patch_employee_profile(
    knowledge_item_id: UUID,
    body: PatchEmployeeProfileRequest,
    current_user: User = Depends(require_permission(_MANAGE)),
) -> EmployeeResponse:
    employee = await knowledge_service.get_employee(knowledge_item_id)
    if employee is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")
    updated = await knowledge_service.patch_employee_profile(
        knowledge_item_id,
        employee.organization_id,
        current_user.id,
        name=body.name,
        email=body.email,
        current_title=body.current_title,
        professional_location=body.professional_location,
        years_experience=body.years_experience,
        availability_status=body.availability_status,
        availability_as_of=body.availability_as_of,
    )
    return EmployeeResponse.model_validate(updated, from_attributes=True)


@router.post("/employees/{knowledge_item_id}/verify", response_model=EmployeeResponse)
async def verify_employee(
    knowledge_item_id: UUID,
    body: VerifyEmployeeRequest,
    current_user: User = Depends(require_permission(_MANAGE)),
) -> EmployeeResponse:
    employee = await knowledge_service.get_employee(knowledge_item_id)
    if employee is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")
    updated = await knowledge_service.verify_employee_profile(
        knowledge_item_id, employee.organization_id, current_user.id, body.eligible_for_bid
    )
    return EmployeeResponse.model_validate(updated, from_attributes=True)


@router.post(
    "/employees/{knowledge_item_id}/resume",
    response_model=EmployeeResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def replace_employee_resume(
    knowledge_item_id: UUID,
    file: UploadFile = File(...),
    current_user: User = Depends(require_permission(_MANAGE)),
) -> EmployeeResponse:
    employee = await knowledge_service.get_employee(knowledge_item_id)
    if employee is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")

    content = await file.read()
    filename = file.filename or "resume.docx"
    _validate_upload(filename, file.content_type, len(content))

    updated = await knowledge_service.replace_employee_resume(
        knowledge_item_id,
        employee.organization_id,
        current_user.id,
        filename,
        content,
        file.content_type,
    )
    return EmployeeResponse.model_validate(updated, from_attributes=True)


@router.get(
    "/tenders/{tender_id}/knowledge-selections", response_model=list[KnowledgeSelectionResponse]
)
async def list_knowledge_selections(
    tender_id: UUID,
    selected_only: bool = False,
    current_user: User = Depends(require_permission(_MANAGE)),
) -> list[KnowledgeSelectionResponse]:
    selections = await knowledge_service.list_knowledge_selections(tender_id, selected_only)
    return [KnowledgeSelectionResponse.model_validate(s, from_attributes=True) for s in selections]


@router.put(
    "/tenders/{tender_id}/knowledge-selections/{knowledge_item_id}",
    response_model=KnowledgeSelectionResponse,
)
async def set_knowledge_selection(
    tender_id: UUID,
    knowledge_item_id: UUID,
    body: SetKnowledgeSelectionRequest,
    current_user: User = Depends(require_permission(_MANAGE)),
) -> KnowledgeSelectionResponse:
    """No impact-preview/state-version-bump/projection wiring here - see
    knowledge_service.set_knowledge_selection's docstring for why (B7/B10
    dependencies not built yet in B6). This persists the selection row
    itself, the piece B6 owns.
    """
    updated = await knowledge_service.set_knowledge_selection(
        tender_id, knowledge_item_id, body.selected, current_user.id, body.change_reason
    )
    return KnowledgeSelectionResponse.model_validate(updated, from_attributes=True)
