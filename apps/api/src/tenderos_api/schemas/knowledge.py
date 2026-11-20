from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field


class KnowledgeItemResponse(BaseModel):
    id: UUID
    organization_id: UUID
    item_type: str
    title: str
    status: str
    valid_from: date | None
    valid_until: date | None
    source_document_id: UUID | None
    source_chunk_id: UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class EmployeeResponse(BaseModel):
    knowledge_item_id: UUID
    organization_id: UUID
    employee_code: str | None
    name: str
    email: str | None
    current_title: str | None
    years_experience: float | None
    professional_location: str | None
    availability_status: str
    availability_as_of: date | None
    eligible_for_bid: bool
    profile_review_status: str
    profile_last_verified_at: datetime | None
    profile_verified_by: UUID | None
    resume_document_id: UUID | None

    # parsed_profile is deliberately absent: it carries the raw LLM
    # extraction (certification_mentions/project_mentions and everything
    # else the model produced), which is review-queue detail, not the
    # steady-state profile shape most callers of this response need.
    model_config = {"from_attributes": True}


class ImportBatchResponse(BaseModel):
    id: UUID
    organization_id: UUID
    import_type: str
    source_document_id: UUID | None
    status: str
    total_items: int
    completed_items: int
    failed_items: int
    created_at: datetime

    model_config = {"from_attributes": True}


class ImportItemResponse(BaseModel):
    id: UUID
    batch_id: UUID
    source_ref: str
    status: str
    target_knowledge_item_id: UUID | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime

    # extracted_payload is deliberately absent from the list/summary shape
    # used elsewhere - it is exposed only where a reviewer actually needs
    # to see what was parsed (the item detail view), not on every list row.
    model_config = {"from_attributes": True}


class ImportItemFieldChangeResponse(BaseModel):
    """Plan item 80: one profile field as the merge sees it
    (knowledge_service.ResumeMergeField), in the merge's own field order."""

    field: str
    on_file: str | float | None
    from_resume: str | float | None
    changes: bool
    # Plan item 111: the resume's email belongs to another employee, so the
    # merge keeps this one's.
    held_by_other: bool = False

    model_config = {"from_attributes": True}


class ImportItemTargetResponse(BaseModel):
    """Plan item 80: the employee a resume item names, as the review panel
    shows it (knowledge_service.ImportItemTarget). `fingerprint` is what
    Approve sends back; approval refuses it once the employee's values
    differ (409 IMPORT_ITEM_TARGET_CHANGED)."""

    knowledge_item_id: UUID
    name: str
    email: str | None
    current_title: str | None
    professional_location: str | None
    years_experience: float | None
    resume_document_id: UUID | None
    profile_review_status: str
    fingerprint: str
    merged_into_existing: bool
    # What approving writes, field by field; null when the item's extracted
    # payload is not a valid profile (approval then answers 409
    # IMPORT_ITEM_PAYLOAD_INVALID).
    changes: list[ImportItemFieldChangeResponse] | None

    model_config = {"from_attributes": True}


class ImportItemCandidateResponse(BaseModel):
    """Plan item 111: one employee a resume item in review matches
    (knowledge_service.ImportItemCandidate). A pick sends its id and its
    `fingerprint` back."""

    knowledge_item_id: UUID
    name: str
    email: str | None
    current_title: str | None
    professional_location: str | None
    years_experience: float | None
    resume_document_id: UUID | None
    profile_review_status: str
    archived: bool
    signals: list[str]
    fingerprint: str
    changes: list[ImportItemFieldChangeResponse]

    model_config = {"from_attributes": True}


class ImportItemCandidatesResponse(BaseModel):
    """Plan item 111: a resume item's candidates. `fingerprint` goes back with
    a pick or an import as a new person; `strong_match` means import as a new
    person is refused (409 IMPORT_ITEM_STRONG_MATCH)."""

    fingerprint: str
    candidates: list[ImportItemCandidateResponse]
    strong_match: bool

    model_config = {"from_attributes": True}


class MasterApplyFieldResponse(BaseModel):
    """Plan item 112: one field as the master apply sees it
    (knowledge_service.MasterApplyField), in the apply's own order: name,
    employee_code, current_title, status."""

    field: str
    on_file: str | None
    from_sheet: str | None
    changes: bool
    # The sheet's code belongs to another employee, so it is not written.
    held_by_other: bool = False

    model_config = {"from_attributes": True}


class MasterTargetResponse(BaseModel):
    """Plan item 112: the employee a master item names, as the review panel
    shows it (knowledge_service.MasterTarget). `fingerprint` goes back with
    Approve; `archived` means the employee stays archived (Q6)."""

    knowledge_item_id: UUID
    name: str
    employee_code: str | None
    current_title: str | None
    status: str
    archived: bool
    fingerprint: str
    # What approving writes; null when the item's payload is not a master row
    # (approval then answers 409 IMPORT_ITEM_PAYLOAD_INVALID).
    changes: list[MasterApplyFieldResponse] | None

    model_config = {"from_attributes": True}


class MasterCandidateResponse(BaseModel):
    """Plan item 112: one employee a master item in review matches (the code,
    the name). A pick sends its id and `fingerprint` back."""

    knowledge_item_id: UUID
    name: str
    employee_code: str | None
    current_title: str | None
    status: str
    archived: bool
    signals: list[str]
    fingerprint: str
    changes: list[MasterApplyFieldResponse]

    model_config = {"from_attributes": True}


class MasterCandidatesResponse(BaseModel):
    """Plan item 112: a master item's candidates; as ImportItemCandidatesResponse."""

    fingerprint: str
    candidates: list[MasterCandidateResponse]
    strong_match: bool

    model_config = {"from_attributes": True}


class CertificateHolderResponse(BaseModel):
    """Plan item 112: a live employee a certification item can be given to.
    Approve sends `knowledge_item_id` as target_employee_id and
    `fingerprint` as target_fingerprint."""

    knowledge_item_id: UUID
    name: str
    employee_code: str | None
    email: str | None
    current_title: str | None
    fingerprint: str

    model_config = {"from_attributes": True}


class ImportItemDetailResponse(ImportItemResponse):
    extracted_payload: dict
    # Plan item 80: set for a resume item that names an employee; null
    # for every other item.
    target_employee: ImportItemTargetResponse | None = None
    # Plan item 111: set for a resume item in review whose resume can be
    # read; null for every other item.
    candidates: ImportItemCandidatesResponse | None = None
    # Plan item 112: set for an employee-master item that names an employee,
    # and for one in review whose row can be read; null for every other item.
    master_target: MasterTargetResponse | None = None
    master_candidates: MasterCandidatesResponse | None = None


class KnowledgeSelectionResponse(BaseModel):
    id: UUID
    tender_id: UUID
    knowledge_item_id: UUID
    selected: bool
    selected_at: datetime | None
    selected_by: UUID | None
    deselected_at: datetime | None
    deselected_by: UUID | None
    change_reason: str | None

    model_config = {"from_attributes": True}


class PatchKnowledgeItemRequest(BaseModel):
    title: str | None = None
    status: str | None = Field(default=None, pattern="^(active|expired|archived|unverified)$")


class PatchEmployeeProfileRequest(BaseModel):
    """G6's PATCH /employees/{id}/profile - correction, not approval. See
    knowledge_service.patch_employee_profile for why eligible_for_bid is
    deliberately not a field here."""

    name: str | None = None
    email: str | None = None
    current_title: str | None = None
    professional_location: str | None = None
    years_experience: float | None = None
    availability_status: str | None = Field(
        default=None, pattern="^(available|partially_available|unavailable|unknown)$"
    )
    availability_as_of: date | None = None


class VerifyEmployeeRequest(BaseModel):
    """G6's POST /employees/{id}/verify - the reviewer must explicitly
    decide bid-eligibility as part of approving, not as a separate step."""

    eligible_for_bid: bool


class RejectImportItemRequest(BaseModel):
    reason: str | None = None


class ApproveImportItemRequest(BaseModel):
    # Plan item 80, Q1: the target_employee.fingerprint the reviewer was
    # shown. Required to approve a resume merge (422 without it); plan item
    # 112: and an employee-master or certification item (the
    # master_target's, a master candidate's, or the chosen holder's). The
    # body itself is optional (other import types ignore it).
    target_fingerprint: str | None = None
    # Plan item 111: the candidate to merge into, instead of the one the
    # item names, with that candidate's fingerprint as target_fingerprint
    # and the candidates.fingerprint the reviewer was shown. Plan item 112:
    # the same for a master item; for a certification item, the holder
    # chosen from GET /knowledge/import-batches/{id}/holders (no
    # candidates_fingerprint).
    target_employee_id: UUID | None = None
    candidates_fingerprint: str | None = None


class ImportAsNewEmployeeRequest(BaseModel):
    # Plan item 111: the candidates.fingerprint the reviewer was shown
    # (plan item 112: master_candidates.fingerprint for a master item).
    candidates_fingerprint: str | None = None


class SetKnowledgeSelectionRequest(BaseModel):
    selected: bool
    change_reason: str | None = None
