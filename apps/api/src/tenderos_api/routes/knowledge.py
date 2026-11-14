import uuid
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from tenderos_backend.db.models.knowledge import KnowledgeImportItem
from tenderos_backend.db.models.user import User
from tenderos_backend.domain.services import document_service, knowledge_service, tender_service

from tenderos_api.dependencies import require_permission
from tenderos_api.idempotency import IdempotencyContext, get_idempotency_context
from tenderos_api.routes.documents import _read_and_validate_upload
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
    # B13: found by route testing, not by reading this file. A fresh
    # request_id every call, not just when _is_new is False - same
    # "simpler than branching, harmless for the genuinely-new-document
    # case" reasoning replace_employee_resume's own call already uses.
    # Without it: two files in the SAME bulk-resumes upload with
    # identical content dedup to the same document (create_document's
    # own org+hash dedup), and this function is called twice for that
    # one document_id with no request_id - the second
    # queue_knowledge_document_import call builds the exact same
    # idempotency_key as the first (knowledge_document_uploaded_
    # idempotency_key's own docstring names this collision by name), and
    # the outbox insert 500s on outbox_events' unique idempotency_key
    # constraint instead of silently no-oping. Caught by
    # tests/route/test_knowledge_routes.py::
    # test_upload_resumes_creates_one_batch_for_multiple_files uploading
    # two byte-identical files, not by inspection.
    await knowledge_service.queue_knowledge_document_import(
        document.id, batch_id, request_id=uuid.uuid4()
    )


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
    filename = file.filename or "employee_master.xlsx"
    content = await _read_and_validate_upload(file, filename, file.content_type)

    document, _is_new = await document_service.create_document(
        organization_id=org_id,
        actor_user_id=current_user.id,
        filename=filename,
        content=content,
        mime_type=file.content_type,
        side="company",
        doc_type="employee_master",
    )
    # G6: "Employee-master imports accept CSV or XLSX" - import_type has to
    # reflect which one was actually uploaded, not assume xlsx always.
    # process_knowledge_document_import (the worker) branches on this value
    # to pick the matching parser.
    import_type = (
        "employee_master_csv" if filename.lower().endswith(".csv") else "employee_master_xlsx"
    )
    batch = await knowledge_service.create_import_batch(
        org_id, import_type, document.id, current_user.id
    )
    # B13: same shape as _upload_batch_document's own fix (see its
    # comment) - queue_knowledge_document_import's idempotency key is
    # built from document_id alone when request_id is omitted, not
    # document_id+batch_id, so re-uploading this exact file a second
    # time (a genuinely ordinary action - starting a fresh import batch
    # from the same spreadsheet) dedups to the same document.id and
    # collides with the FIRST upload's outbox event, even though this is
    # a different batch. A fresh request_id every call is what actually
    # scopes the event to this call, not just this document.
    await knowledge_service.queue_knowledge_document_import(
        document.id, batch.id, request_id=uuid.uuid4()
    )
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
    filename = file.filename or "certifications.xlsx"
    content = await _read_and_validate_upload(file, filename, file.content_type)

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
    # B13: same fix as upload_employee_master above - see that route's
    # own comment for the mechanism (a fresh request_id scopes this
    # outbox event to THIS call, not just this document_id).
    await knowledge_service.queue_knowledge_document_import(
        document.id, batch.id, request_id=uuid.uuid4()
    )
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
        filename = file.filename or "resume.docx"
        content = await _read_and_validate_upload(file, filename, file.content_type)
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
    filename = file.filename or "document.docx"
    content = await _read_and_validate_upload(file, filename, file.content_type)

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
    # B13: same fix as upload_employee_master above - see that route's
    # own comment for the mechanism.
    await knowledge_service.queue_knowledge_document_import(
        document.id, batch.id, request_id=uuid.uuid4()
    )


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


async def _import_item_organization_id(item_id: UUID) -> tuple[KnowledgeImportItem, UUID]:
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


@router.get("/employees/{knowledge_item_id}", response_model=EmployeeResponse)
async def get_employee(
    knowledge_item_id: UUID, current_user: User = Depends(require_permission(_MANAGE))
) -> EmployeeResponse:
    """B13 (screen 4, profile detail/verification): Part G's own G6 list
    never named this route - GET /knowledge/{id} (generic, title/status
    only) and GET /organizations/{org_id}/employees (bid-eligible-only,
    excludes exactly the unverified profiles a reviewer needs to inspect)
    were the only two GETs. knowledge_service.get_employee and
    EmployeeResponse both already existed (patch_employee_profile/
    verify_employee/replace_employee_resume below all fetch through the
    same function before mutating) - this exposes them as their own read,
    it does not add a capability. Same `_MANAGE` action as every other
    route in this module, not a new view_* action: unlike view_workflow/
    view_graph/view_readiness/view_gates (all cases of a workflow-
    transparency read wrongly gated behind a narrow AUTHORING action),
    this is personnel data - list_bid_eligible_employees already returns
    the same full EmployeeResponse shape under this same 4-role action,
    with no prior finding that scope is wrong. Reusing it is consistency
    with this module's own existing design, not an oversight.
    """
    employee = await knowledge_service.get_employee(knowledge_item_id)
    if employee is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")
    return EmployeeResponse.model_validate(employee, from_attributes=True)


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
    try:
        updated = await knowledge_service.verify_employee_profile(
            knowledge_item_id, employee.organization_id, current_user.id, body.eligible_for_bid
        )
    except knowledge_service.EmployeeArchivedError as exc:
        # Plan item 79, C1: a removed employee cannot be verified into the
        # candidate pool. Restoring the knowledge item's status (the talent
        # library's status edit) is the way back.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Employee is archived (removed from the roster); restore it before verifying",
        ) from exc
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

    filename = file.filename or "resume.docx"
    content = await _read_and_validate_upload(file, filename, file.content_type)

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
    idempotency: IdempotencyContext = Depends(
        get_idempotency_context("knowledge_selection_change", required=True)
    ),
) -> KnowledgeSelectionResponse:
    """No impact-preview/state-version-bump/projection wiring here - see
    knowledge_service.set_knowledge_selection's docstring for why (B7/B10
    dependencies not built yet in B6). This persists the selection row
    itself, the piece B6 owns.

    Fetches tender only for this route's own 404 - set_knowledge_selection
    no longer takes organization_id as a parameter at all (an earlier
    version of both this route and that function did; removed because
    every caller only ever had one way to produce that value - re-deriving
    it from this same tender_id - making the comparison it fed vacuous).
    The function verifies actor_user_id's own membership internally instead
    - see its own docstring. This route call previously still passed the
    old 6-argument shape after that signature changed underneath it (a real
    bug: the route raised TypeError on every call, caught only because no
    test in this codebase exercises the route layer directly) - fixed here.

    B13 (§3c): Idempotency-Key added - Part I1 names "knowledge selection
    change" as one of its six required-Idempotency-Key operations
    verbatim; this route never had it wired in, the last of the six left
    missing. Same required=True shape select_candidate/decide_gate/
    create_workflow_run already use.
    """
    if idempotency.replay_response is not None:
        _, cached_body = idempotency.replay_response
        return KnowledgeSelectionResponse.model_validate(cached_body)

    tender = await tender_service.get_tender(tender_id)
    if tender is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tender not found")

    try:
        updated = await knowledge_service.set_knowledge_selection(
            tender_id,
            knowledge_item_id,
            body.selected,
            current_user.id,
            body.change_reason,
        )
    except (
        knowledge_service.TenderNotFoundError,
        knowledge_service.CrossOrganizationTenderError,
    ) as exc:
        # Same generic 404 for both, same reasoning as the knowledge-item
        # pair below: a caller must not be able to tell "gone since I
        # fetched it" apart from "belongs to another organization."
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tender not found"
        ) from exc
    except (
        knowledge_service.KnowledgeItemNotFoundError,
        knowledge_service.CrossOrganizationKnowledgeItemError,
    ) as exc:
        # Same generic 404 for both - a caller must not be able to tell
        # "this knowledge_item_id doesn't exist" apart from "it exists,
        # but in another organization." Distinguishing the two in the
        # response would let a caller enumerate valid knowledge-item ids
        # across organizations by watching which error comes back. The
        # real reason is still logged at the service layer (knowledge_
        # service.set_knowledge_selection), which is the point - the
        # CALLER can't tell them apart, not that nobody can.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge item not found"
        ) from exc
    response = KnowledgeSelectionResponse.model_validate(updated, from_attributes=True)
    await idempotency.finalize(200, response.model_dump(mode="json"))
    return response
