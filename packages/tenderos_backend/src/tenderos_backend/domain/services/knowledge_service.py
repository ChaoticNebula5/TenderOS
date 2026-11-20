import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TypedDict

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from tenderos_backend.agents.knowledge.certifications_xlsx import CertificationRow
from tenderos_backend.agents.knowledge.dedup import (
    STRONG_SIGNALS,
    DuplicateVerdict,
    employee_match_signals,
    normalize_employee_email,
    normalize_employee_name,
    resolve_employee_duplicate,
)
from tenderos_backend.agents.knowledge.employee_master import EmployeeMasterRow
from tenderos_backend.agents.knowledge.relationships import (
    DemonstratesCandidate,
    RelationshipResolution,
    RelationshipVerdict,
    WorkedOnCandidate,
)
from tenderos_backend.agents.schemas.knowledge import ExtractedEmployeeProfile
from tenderos_backend.core.enums import Role
from tenderos_backend.core.evidence_validity import today_utc
from tenderos_backend.core.hashing import sha256_hex
from tenderos_backend.db.models.document import Document
from tenderos_backend.db.models.knowledge import (
    Employee,
    KnowledgeImportBatch,
    KnowledgeImportItem,
    KnowledgeItem,
    KnowledgeRelationship,
    TenderKnowledgeSelection,
)
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.repositories.knowledge import KNOWLEDGE_ITEM_ARCHIVED
from tenderos_backend.db.storage import storage
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.events.outbox_events import (
    AGGREGATE_DOCUMENT,
    AGGREGATE_KNOWLEDGE_IMPORT_ITEM,
    AGGREGATE_KNOWLEDGE_SELECTION,
    EVENT_KNOWLEDGE_DOCUMENT_UPLOADED,
    EVENT_KNOWLEDGE_SELECTION_CHANGED,
    EVENT_RESUME_MERGE_APPROVED,
    knowledge_document_uploaded_idempotency_key,
    knowledge_document_uploaded_payload,
    knowledge_selection_changed_idempotency_key,
    knowledge_selection_changed_payload,
    resume_merge_approved_idempotency_key,
    resume_merge_approved_payload,
)
from tenderos_backend.domain.policies.rbac import ACTION_ROLES
from tenderos_backend.domain.services.document_service import create_synthetic_chunk


@dataclass(frozen=True)
class ResolvedPastProject:
    """agents/knowledge/extract.py's extract_capability_statement output,
    after resolving ExtractedPastProject.source_chunk_ref to a real chunk -
    same "agent returns a resolved draft, service persists it" split B5's
    RequirementDraft establishes.
    """

    name: str
    client: str | None
    year: str | None
    description: str | None
    source_ref: str | None
    chunk_id: uuid.UUID
    document_id: uuid.UUID


@dataclass(frozen=True)
class ResolvedCapability:
    name: str
    description: str | None
    source_ref: str | None
    chunk_id: uuid.UUID
    document_id: uuid.UUID


# Statuses the employee-master template's free-text Status column can take
# in this fixture, and the knowledge_items.status each maps to - Part C's
# enum has no literal "removed" value, so "archived" is the closest existing
# fit (the record persists - needed for the L1-seeded "removed employee
# still named in a claim" red-team case - but is no longer active). Any
# other/unrecognized status string is left at knowledge_items' own default
# ("active") rather than rejected outright; a genuinely unrecognized status
# is a data-quality question for a human reviewer, not a hard failure that
# should abort the whole import row.
_EMPLOYEE_STATUS_TO_KNOWLEDGE_ITEM_STATUS = {
    "removed": KNOWLEDGE_ITEM_ARCHIVED,
}


# The knowledge item types whose graph node carries knowledge_items.title
# (graph_projection/projector.py: certification_rows, project_rows,
# capability_rows; employee_rows use the employee's own name).
_TITLED_NODE_ITEM_TYPES = frozenset({"certification", "past_project", "capability"})


async def _bump_bids_projecting(uow: UnitOfWork, knowledge_item_id: uuid.UUID) -> list[uuid.UUID]:
    """A projected field of this knowledge item changed: bump every bid whose
    graph includes the item, in the caller's transaction, in sorted order (two
    edits bumping overlapping bids take the row locks in one order). Returns
    the bumped tender ids.

    Plan item 95 (user decisions Q2 and Q6): any change to a field the
    projector copies onto the item's node, not only archiving or restoring
    (plan items 86 and 88 built this for that case). The graph's coverage
    grading counts a backing fact only while its status is exactly 'active',
    so an 'expired' or 'unverified' status changes graph answers too. The
    caller compares before and after and calls this only on a change; a write
    of a field the graph does not show (an email, a resume) does not bump.

    Which bids: KnowledgeRepository.list_tender_ids_projecting, the
    projector's own reach (selection, evidence link, selected role match,
    claim naming the employee, a held certificate). Finished bids (final gate
    approved) are included: the state version counts changes to the facts a
    bid rests on. Nothing runs on a bump (no outbox event, no job: the user's
    D3), so a finished bid only shows a stale graph if someone opens it."""
    tender_ids = await uow.knowledge.list_tender_ids_projecting(knowledge_item_id)
    for tender_id in tender_ids:
        await uow.tenders.bump_state_version(tender_id)
    return tender_ids


async def _bump_bids_projecting_both(
    uow: UnitOfWork, source_item_id: uuid.UUID, target_item_id: uuid.UUID
) -> list[uuid.UUID]:
    """A new verified relationship is projected only into bids whose graph
    includes both of its ends (the projector skips it otherwise), so only
    those are bumped, in sorted order (plan item 95)."""
    source_bids = set(await uow.knowledge.list_tender_ids_projecting(source_item_id))
    target_bids = set(await uow.knowledge.list_tender_ids_projecting(target_item_id))
    tender_ids = sorted(source_bids & target_bids)
    for tender_id in tender_ids:
        await uow.tenders.bump_state_version(tender_id)
    return tender_ids


logger = logging.getLogger(__name__)


class KnowledgeItemNotFoundError(Exception):
    pass


class EmployeeNotFoundError(Exception):
    pass


class EmployeeArchivedError(Exception):
    """Verifying an employee removed from the roster (plan item 79, C1):
    PRD 10.9's candidate search includes only profiles "not archived", so
    one cannot be verified (and made bid-eligible) while archived."""


class ImportItemNotFoundError(Exception):
    pass


class ImportItemNotInReviewError(Exception):
    """Plan item 80: only a needs_review item is approved or rejected (a
    failed item can also be rejected). Approving an approved item, or
    rejecting a rejected one, is a replay and answers with the item instead."""

    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


class ImportItemNoTargetError(Exception):
    """Plan item 80: a resume item in review names no single employee (strong
    signals matched different people, or the name matched several), or the
    one it named no longer exists. Reject is the only decision for now."""


class ImportItemSourceMissingError(Exception):
    """Plan item 80: a resume item with no source document: written before
    migration 034, or its document is gone. Nothing to merge from."""


class ImportItemFingerprintRequiredError(Exception):
    """Plan item 80, Q1: approving a resume merge names the employee values
    the reviewer saw (ImportItemTarget.fingerprint). An approval without it
    is refused before anything is read or written."""


class ImportItemPayloadInvalidError(Exception):
    """Plan item 80: a resume item whose extracted payload is not a valid
    profile. The panel cannot show what a merge would write, so the item is
    refused (409) rather than failing (500); reject it or upload again."""


class ImportItemTargetChangedError(Exception):
    """Plan item 80, Q1: the employee changed after the reviewer loaded the
    item (another merge, an edit, a verify), so they did not see what the
    merge would change. Nothing is written; the screen reloads the item."""


class ImportItemCandidatesChangedError(Exception):
    """Plan item 111: the employees matching a resume item differ from the
    ones the reviewer was shown (one added, one gone, or one's shown values
    changed), so the choice was made on a list that no longer holds. Nothing
    is written; the screen reloads the item."""


class ImportItemNotACandidateError(Exception):
    """Plan item 111: the employee picked for a resume item is not one of the
    employees it matches now (no signal in common, or in another
    organization: one refusal for both, so the API does not say whether an
    id exists elsewhere)."""


class ImportItemStrongMatchError(Exception):
    """Plan item 111, Q3: importing a resume as a new person when an employee
    on file already has its email or its document (or an employee was made
    from this document before). A second employee with that email would put
    every later resume carrying it into review; pick that employee instead."""


class ImportItemTypeUnsupportedError(Exception):
    """Plan item 111: picking an employee or importing as a new person is for
    resume items (plan item 112 adds the employee master)."""


class TenderNotFoundError(Exception):
    pass


class CrossOrganizationKnowledgeItemError(Exception):
    """Raised when a tender's knowledge selection targets a knowledge_item_id
    belonging to a different organization. tender_knowledge_selections' two
    foreign keys (tenders.id, knowledge_items.id) are independent - Postgres
    has no way to express "same organization_id on both sides" as a single
    CHECK constraint, so this has to be verified in code, same reasoning
    CrossTenderOperationError already established for merge_requirements.
    """


class CrossOrganizationTenderError(Exception):
    """Raised when the caller's own authorized organization_id doesn't match
    tender_id's actual organization_id. The original cross-org fix compared
    the two RESOURCES (knowledge_item vs tender) to each other but never to
    the org the request was actually authorized for - this closes that gap
    by making the caller's org an explicit, independently-checked parameter
    rather than something the service function only ever learns about
    indirectly through whichever resource happens to be in the path.
    """

    pass


def _fingerprint(*parts: str) -> str:
    return sha256_hex("|".join(parts).encode("utf-8"))


async def _claim_or_resume_import_item(
    batch_id: uuid.UUID,
    source_ref: str,
    extracted_payload: dict,
    source_document_id: uuid.UUID | None = None,
) -> tuple[KnowledgeImportItem, bool]:
    """B16: the insert-or-detect step for this file's own 3 select-then-
    insert sites (import_employee_master_row, import_certification_row,
    import_resume) - narrower than document_service.create_document's own
    reread-after-rollback fix, deliberately: all 3 of these functions do
    substantial further work (duplicate-resolution branching, conditional
    create_employee/create_certification calls) between the insert and
    their own commit, all inside one transaction - a bare reread the way
    create_document does it could hand a caller a row still mid-resolution,
    not the finished one (KNOWN_LIMITATIONS.md's own earlier analysis of
    this exact gap).

    Returns (import_item, needs_resolution):
    - needs_resolution=False: `import_item` already reflects a FINISHED
      prior attempt (status is not "pending") - the caller returns it
      directly, exactly like each function's own existing retry-resume
      check already did before this refactor.
    - needs_resolution=True: the caller must run its own resolution logic
      against `import_item.id`, in a fresh UnitOfWork it opens itself.
      Covers three cases with one answer, not three: (a) this call just
      created the row (the ordinary path); (b) an existing row is still
      "pending" because a genuinely concurrent attempt is racing RIGHT
      NOW; (c) an existing row is still "pending" because an earlier
      attempt crashed before ever reaching its own update_import_item
      call. (b) and (c) are indistinguishable from here, and don't need
      to be distinguished - re-running resolution against the same row is
      safe either way: create_employee/create_certification key on their
      own content_fingerprint uniqueness (a second attempt converges on
      the same knowledge_item, doesn't duplicate it), and
      update_import_item is a plain UPDATE, not unique-constrained. A
      true race means two callers redundantly run resolution once each
      and land on the same correct final row - real duplicate WORK, not
      duplicate DATA, and not a crash.
    """
    async with UnitOfWork() as uow:
        existing = await uow.knowledge.get_import_item_by_source_ref(batch_id, source_ref)
        if existing is not None:
            return existing, existing.status == "pending"
        try:
            import_item = await uow.knowledge.create_import_item(
                batch_id=batch_id,
                source_ref=source_ref,
                extracted_payload=extracted_payload,
                source_document_id=source_document_id,
            )
            await uow.commit()
            return import_item, True
        except IntegrityError:
            await uow.rollback()
            async with UnitOfWork() as reread_uow:
                reread = await reread_uow.knowledge.get_import_item_by_source_ref(
                    batch_id, source_ref
                )
            if reread is None:
                raise
            return reread, reread.status == "pending"


async def _create_or_get_employee(
    organization_id: uuid.UUID,
    fingerprint: str,
    name: str,
    title: str,
    employee_code: str | None = None,
    email: str | None = None,
    current_title: str | None = None,
    professional_location: str | None = None,
    years_experience: float | None = None,
    resume_document_id: uuid.UUID | None = None,
    source_document_id: uuid.UUID | None = None,
    parsed_profile: dict | None = None,
) -> uuid.UUID:
    """B16 - found while testing _claim_or_resume_import_item above, not
    part of the finding that sent this pass here: a SECOND, deeper
    select-then-insert race, one layer below the import_items one.
    _claim_or_resume_import_item's own "needs_resolution=True on a
    pending row" case means two concurrent callers can both legitimately
    reach a NEW-employee decision for the SAME fingerprint (same
    duplicate-candidate query, both empty, both resolve NEW) and both
    call create_employee - confirmed live by this file's own new
    concurrent tests, not assumed: 2 of 3 crashed on
    uq_knowledge_item_fingerprint before this existed.

    Own transaction, deliberately separate from the caller's -
    an IntegrityError here must not poison whatever uow the caller is
    using for the surrounding update_knowledge_item/update_import_item/
    associate_knowledge_item_chunks calls that still need to run after
    this returns. Returns knowledge_item.id either way (created or
    converged onto an existing row) - every call site in this file only
    ever needed the id, never the typed Employee row itself.

    Parameters mirror KnowledgeRepository.create_employee's own signature
    exactly rather than a loose **fields: object passthrough - the
    original **fields shape type-checked (mypy can't verify an unpacked
    dict[str, object] against individually-typed keyword parameters) and
    was only caught by running mypy, not by the tests.
    """
    async with UnitOfWork() as uow:
        try:
            knowledge_item, _employee = await uow.knowledge.create_employee(
                organization_id=organization_id,
                name=name,
                content_fingerprint=fingerprint,
                title=title,
                employee_code=employee_code,
                email=email,
                current_title=current_title,
                professional_location=professional_location,
                years_experience=years_experience,
                resume_document_id=resume_document_id,
                source_document_id=source_document_id,
                parsed_profile=parsed_profile,
            )
            await uow.commit()
            return knowledge_item.id
        except IntegrityError:
            await uow.rollback()
            async with UnitOfWork() as reread_uow:
                existing = await reread_uow.knowledge.get_knowledge_item_by_fingerprint(
                    organization_id, "employee", fingerprint
                )
            if existing is None:
                raise
            return existing.id


# Plan item 112 (user decisions 2026-11-20): what an employee-master row
# writes onto an employee, in the order the review panel shows it. The import
# types whose items are master rows.
MASTER_APPLY_FIELDS = ("name", "employee_code", "current_title", "status")
MASTER_IMPORT_TYPES = frozenset({"employee_master_csv", "employee_master_xlsx"})
# The fields of MASTER_APPLY_FIELDS on the employee's graph node (the code is
# not projected: tests/projection_inputs.py's PROJECTION_SCOPE).
_MASTER_PROJECTED_FIELDS = frozenset({"name", "current_title", "status"})
# The automatic branch's audit actor (Q8): an apply no person decided.
_MASTER_IMPORT_ACTOR = "employee_master_import"


@dataclass(frozen=True)
class MasterApplyField:
    """One of MASTER_APPLY_FIELDS as the apply sees it: the value on file,
    the sheet's (None where it writes nothing: a blank cell, or a status that
    maps to none), and whether the apply writes it. The review panel renders
    these as they are; it has no copy of the rule."""

    field: str
    on_file: object
    from_sheet: object
    changes: bool
    # The sheet's code belongs to another employee, so it is not written:
    # employee codes are unique in an organization (uq_employee_code_per_org).
    held_by_other: bool = False


def master_row_from_payload(payload: dict) -> EmployeeMasterRow | None:
    """A master import item's extracted_payload as the row it was made from
    (import_employee_master_row stores the parser's values, already trimmed);
    None when it does not read as one (no name)."""
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        return None

    def text(key: str) -> str | None:
        value = payload.get(key)
        if not isinstance(value, str):
            return None
        return value.strip() or None

    return EmployeeMasterRow(
        row_number=0,
        employee_code=text("employee_code"),
        name=name.strip(),
        role=text("role"),
        status=text("status"),
    )


def codes_held_by_others(employees: list[Employee], employee_id: uuid.UUID) -> frozenset[str]:
    """The employee codes of every employee on file other than this one."""
    return frozenset(
        other.employee_code
        for other in employees
        if other.knowledge_item_id != employee_id and other.employee_code is not None
    )


def master_apply_fields(
    employee: Employee,
    item_status: str,
    row: EmployeeMasterRow,
    held_codes: frozenset[str] = frozenset(),
) -> list[MasterApplyField]:
    """Every field of MASTER_APPLY_FIELDS, in that order. The rule:
    - the title: the row's role wherever it has one (the master is the
      organization's own HR record, so it overwrites even a verified title,
      as the import always has);
    - the status: only "removed" maps to one (archived). Nothing restores an
      archived employee (Q6, user decision 2026-11-20: parity with the
      import); restoring is the profile's own action;
    - the name: the sheet's, when it differs as the duplicate check compares
      names (Q5 b: the review case where the code matched);
    - the code: the sheet's, when it has one that differs, unless another
      employee holds it (Q5 b, and for a name match whose sheet code differs
      from the one on file: the user's addition 2026-11-20).
    In the automatic branch the name and the code never differ (a duplicate
    is a code match with the same name), so the one rule serves both."""
    new_status = _EMPLOYEE_STATUS_TO_KNOWLEDGE_ITEM_STATUS.get(row.status or "")
    code_held = row.employee_code is not None and row.employee_code in held_codes
    return [
        MasterApplyField(
            field="name",
            on_file=employee.name,
            from_sheet=row.name,
            changes=normalize_employee_name(row.name) != normalize_employee_name(employee.name),
        ),
        MasterApplyField(
            field="employee_code",
            on_file=employee.employee_code,
            from_sheet=row.employee_code,
            changes=(
                row.employee_code is not None
                and not code_held
                and row.employee_code != employee.employee_code
            ),
            held_by_other=code_held,
        ),
        MasterApplyField(
            field="current_title",
            on_file=employee.current_title,
            from_sheet=row.role,
            changes=row.role is not None and row.role != employee.current_title,
        ),
        MasterApplyField(
            field="status",
            on_file=item_status,
            from_sheet=new_status,
            changes=new_status is not None and new_status != item_status,
        ),
    ]


def master_target_fingerprint(employee: Employee, item_status: str) -> str:
    """What the master panel shows of an employee, as one hash: the name,
    the code, the title and the status. Approval compares it with the values
    read under the employee's row lock."""
    shown = {
        "name": employee.name,
        "employee_code": employee.employee_code,
        "current_title": employee.current_title,
        "status": item_status,
    }
    return hashlib.sha256(json.dumps(shown, sort_keys=True).encode()).hexdigest()


def _master_audit_values(employee: Employee, item_status: str) -> dict[str, object]:
    return {
        "name": employee.name,
        "employee_code": employee.employee_code,
        "current_title": employee.current_title,
        "status": item_status,
    }


async def _apply_master_row_to_employee(
    uow: UnitOfWork,
    organization_id: uuid.UUID,
    employee: Employee,
    knowledge_item: KnowledgeItem,
    row: EmployeeMasterRow,
    *,
    actor_type: str,
    actor_id: str,
    import_item_id: uuid.UUID,
) -> dict[str, tuple[object, object]]:
    """The one master apply (plan item 112), for import_employee_master_row's
    automatic branch and for approval, so the two cannot drift. `employee`
    and `knowledge_item` are read under their row locks by the caller, in
    that order (FOR NO KEY UPDATE: both are written here). Writes the
    changed fields of master_apply_fields; bumps the bids projecting the
    employee if the name, the title or the status changed (plan item 95; a
    "removed" row archives, plan items 86 and 88); writes an
    employee_master_apply audit row (Q8, both callers). Returns the changes.
    Lock order: the employee, then its knowledge item, then the bids."""
    others = await uow.knowledge.find_employee_duplicate_candidates(organization_id)
    held = codes_held_by_others(others, employee.knowledge_item_id)
    entries = master_apply_fields(employee, knowledge_item.status, row, held)
    changes = {entry.field: (entry.on_file, entry.from_sheet) for entry in entries if entry.changes}
    before = _master_audit_values(employee, knowledge_item.status)
    employee_fields = {field: new for field, (_old, new) in changes.items() if field != "status"}
    if employee_fields:
        await uow.knowledge.update_employee(employee.knowledge_item_id, **employee_fields)
    if "status" in changes:
        await uow.knowledge.update_knowledge_item(
            employee.knowledge_item_id, status=changes["status"][1]
        )
    if _MASTER_PROJECTED_FIELDS.intersection(changes):
        await _bump_bids_projecting(uow, employee.knowledge_item_id)
    await uow.audit.create(
        organization_id=organization_id,
        actor_type=actor_type,
        actor_id=actor_id,
        action="employee_master_apply",
        target_type="employee",
        target_id=employee.knowledge_item_id,
        before=before,
        after=_master_audit_values(employee, knowledge_item.status),
        metadata={"import_item_id": str(import_item_id), "fields_changed": sorted(changes)},
    )
    return changes


class MasterEmployeeFields(TypedDict):
    """What an employee made from a master row holds: one definition for
    import_employee_master_row's NEW branch and for a reviewer's import as a
    new person (plan item 112), so the two cannot drift."""

    name: str
    title: str
    employee_code: str | None
    current_title: str | None


def master_employee_fingerprint(organization_id: uuid.UUID, row: EmployeeMasterRow) -> str:
    return _fingerprint(
        str(organization_id), row.employee_code or "", normalize_employee_name(row.name)
    )


def master_employee_fields(row: EmployeeMasterRow) -> MasterEmployeeFields:
    return MasterEmployeeFields(
        name=row.name, title=row.name, employee_code=row.employee_code, current_title=row.role
    )


async def import_employee_master_row(
    organization_id: uuid.UUID, batch_id: uuid.UUID, row: EmployeeMasterRow
) -> KnowledgeImportItem:
    """One employee_master.xlsx row -> one knowledge_import_items row,
    dedup-adjudicated via agents/knowledge/dedup.py. A duplicate/ambiguous
    match is a normal outcome recorded on the import item (needs_review),
    not a raised error - only a genuine persistence failure propagates.

    B16: the claim step (retry-resume check + insert) is now
    _claim_or_resume_import_item, shared with import_certification_row and
    import_resume - see its own docstring for why a bare reread-after-
    rollback (document_service.create_document's own pattern) isn't safe
    here, and what "needs_resolution" actually covers.
    """
    source_ref = f"row_{row.row_number}"
    import_item, needs_resolution = await _claim_or_resume_import_item(
        batch_id,
        source_ref,
        {
            "employee_code": row.employee_code,
            "name": row.name,
            "role": row.role,
            "status": row.status,
        },
    )
    if not needs_resolution:
        return import_item

    async with UnitOfWork() as uow:
        candidates = await uow.knowledge.find_employee_duplicate_candidates(organization_id)
        resolution = resolve_employee_duplicate(
            candidate_employee_code=row.employee_code,
            candidate_name=row.name,
            candidate_email=None,
            candidate_resume_document_id=None,
            candidates=candidates,
        )

        if resolution.verdict == DuplicateVerdict.NEEDS_REVIEW:
            updated = await uow.knowledge.update_import_item(
                import_item.id,
                status="needs_review",
                error_message=resolution.reason,
                target_knowledge_item_id=(
                    resolution.matched_employee.knowledge_item_id
                    if resolution.matched_employee
                    else None
                ),
            )
            await uow.commit()
            return updated

        item_status = _EMPLOYEE_STATUS_TO_KNOWLEDGE_ITEM_STATUS.get(row.status or "")

        if resolution.verdict == DuplicateVerdict.DUPLICATE:
            assert resolution.matched_employee is not None
            target_id = resolution.matched_employee.knowledge_item_id
            # The employee-master spreadsheet is the org's own HR-record
            # path, treated as more current than whatever was already on
            # file. A resume's free-text self-report (import_resume below)
            # overwrites only through _merge_resume_into_employee, and a
            # verified profile only after a person approves it (plan item 80).
            # Plan item 59, fix 7: "before" comes from the employee's row
            # locked (the duplicate check read it unlocked), then from its
            # knowledge item locked. Lock order: the employee, then the item,
            # then the bids' tenders, as verify and the resume merge. Plan
            # item 112: through the one apply approval uses too.
            employee = await uow.knowledge.get_employee_for_update(target_id)
            if employee is None:
                raise EmployeeNotFoundError()
            knowledge_item = await uow.knowledge.get_knowledge_item_for_update(target_id)
            if knowledge_item is None:
                raise EmployeeNotFoundError()
            await _apply_master_row_to_employee(
                uow,
                organization_id,
                employee,
                knowledge_item,
                row,
                actor_type="system",
                actor_id=_MASTER_IMPORT_ACTOR,
                import_item_id=import_item.id,
            )
            updated = await uow.knowledge.update_import_item(
                import_item.id, status="approved", target_knowledge_item_id=target_id
            )
            await uow.commit()
            return updated

        # NEW (plan item 112: one definition with a reviewer's import as new)
        knowledge_item_id = await _create_or_get_employee(
            organization_id,
            master_employee_fingerprint(organization_id, row),
            **master_employee_fields(row),
        )
        if item_status:
            # _create_or_get_employee commits on its own and can converge onto
            # a row that already exists (a retried or concurrent import), so
            # this write can flip an existing item: it goes through the same
            # bump (plan items 88 and 95). A brand-new row reaches no bid,
            # and the bump then touches nothing. Plan item 59, D2: the item
            # is read locked, so an edit of the item that converged-onto row
            # has open is waited for and compared with. Lock order: the item,
            # then the bids' tenders.
            existing = await uow.knowledge.get_knowledge_item_for_update(knowledge_item_id)
            before_status = existing.status if existing is not None else ""
            await uow.knowledge.update_knowledge_item(knowledge_item_id, status=item_status)
            if item_status != before_status:
                await _bump_bids_projecting(uow, knowledge_item_id)
        updated = await uow.knowledge.update_import_item(
            import_item.id, status="approved", target_knowledge_item_id=knowledge_item_id
        )
        await uow.commit()
        return updated


# Plan item 80 (user decisions 2026-11-18). The four profile fields a resume
# can change, in the order the review panel shows them. The name is never
# written by a resume: the match was made on it, and a strong-signal match
# whose name differs keeps the employee's.
RESUME_MERGE_FIELDS = ("email", "current_title", "professional_location", "years_experience")

_RESUME_FIELD_LABELS = {
    "email": "email",
    "current_title": "title",
    "professional_location": "location",
    "years_experience": "years of experience",
}

PROFILE_VERIFIED = "verified"

# The automatic branch's audit actor (Q3): a merge no person decided.
_RESUME_IMPORT_ACTOR = "resume_import"


def _field_value(source: object, field: str) -> object:
    # years_experience is a Numeric column (a Decimal) on the employee and a
    # float on the profile; compared, stored in audit rows and fingerprinted
    # as a float. A string is read without its surrounding spaces, and one
    # that is blank or only spaces reads as no value (plan item 112's note,
    # built with item 111): the merge writes these values, so it writes them
    # trimmed, and a value on file that differs only in surrounding spaces is
    # the same value.
    value = getattr(source, field)
    if field == "years_experience":
        return float(value) if value is not None else None  # type: ignore[arg-type]
    if isinstance(value, str):
        value = value.strip()
        return value if value else None
    return value


@dataclass(frozen=True)
class ResumeMergeField:
    """One of RESUME_MERGE_FIELDS as the merge sees it: the value on file,
    the resume's (None where the resume leaves it blank), and whether the
    merge writes it. The review panel renders these as they are (plan item
    80): it has no copy of the rule."""

    field: str
    on_file: object
    from_resume: object
    changes: bool
    # Plan item 111: the resume's email belongs to another employee on file,
    # so the merge keeps this employee's (two employees with one email would
    # send every later resume carrying it to review, as a conflict).
    held_by_other: bool = False


def _same_value(field: str, old: object, new: object) -> bool:
    # An email is compared as the duplicate check compares it (dedup.py's
    # normalize_employee_email): one differing only in case or spaces is the
    # same email, so it neither changes the stored value nor sends a verified
    # profile to review.
    if field == "email":
        return normalize_employee_email(old) == normalize_employee_email(new)  # type: ignore[arg-type]
    return old == new


def emails_held_by_others(employees: list[Employee], employee_id: uuid.UUID) -> frozenset[str]:
    """Plan item 111: the emails, as the duplicate check compares them, of
    every employee on file other than this one."""
    held: set[str] = set()
    for other in employees:
        if other.knowledge_item_id == employee_id:
            continue
        email = normalize_employee_email(other.email)
        if email is not None:
            held.add(email)
    return frozenset(held)


def resume_merge_fields(
    employee: Employee,
    profile: ExtractedEmployeeProfile,
    held_emails: frozenset[str] = frozenset(),
) -> list[ResumeMergeField]:
    """Every field of RESUME_MERGE_FIELDS, in that order. The rule (Q2, the
    automatic branch's since B6): the resume's value wherever it has one; a
    field it leaves blank keeps what is on file. Plan item 111: an email
    another employee holds (held_emails, from emails_held_by_others) is kept,
    not written."""
    fields: list[ResumeMergeField] = []
    for field in RESUME_MERGE_FIELDS:
        new = _field_value(profile, field)
        old = _field_value(employee, field)
        held = (
            field == "email"
            and isinstance(new, str)
            and normalize_employee_email(new) in held_emails
        )
        fields.append(
            ResumeMergeField(
                field=field,
                on_file=old,
                from_resume=new,
                changes=new is not None and not held and not _same_value(field, old, new),
                held_by_other=held,
            )
        )
    return fields


def resume_merge_changes(
    employee: Employee,
    profile: ExtractedEmployeeProfile,
    held_emails: frozenset[str] = frozenset(),
) -> dict[str, tuple[object, object]]:
    """{field: (on file, from resume)} for every field the merge writes: the
    changing entries of resume_merge_fields, so the merge, the automatic
    branch's review rule and the panel read one list."""
    return {
        entry.field: (entry.on_file, entry.from_resume)
        for entry in resume_merge_fields(employee, profile, held_emails)
        if entry.changes
    }


def resume_merge_overwrites(
    employee: Employee,
    profile: ExtractedEmployeeProfile,
    held_emails: frozenset[str] = frozenset(),
) -> list[str]:
    """The changed fields that already hold a value (Q3): filling a blank is
    not an overwrite."""
    return [
        field
        for field, (old, _new) in resume_merge_changes(employee, profile, held_emails).items()
        if old is not None
    ]


def resume_target_fingerprint(employee: Employee) -> str:
    """Q1: what the review panel shows of the matched employee, as one hash:
    the four fields, the resume document and the review status. The screen
    sends it back on Approve, and approval compares it with the employee's
    values read under the row lock, so "the reviewer saw every change" holds
    for the values actually merged."""
    shown: dict[str, object] = {
        field: _field_value(employee, field) for field in RESUME_MERGE_FIELDS
    }
    shown["resume_document_id"] = (
        str(employee.resume_document_id) if employee.resume_document_id is not None else None
    )
    shown["profile_review_status"] = employee.profile_review_status
    return hashlib.sha256(json.dumps(shown, sort_keys=True).encode()).hexdigest()


def _merge_audit_values(employee: Employee) -> dict[str, object]:
    values: dict[str, object] = {
        field: _field_value(employee, field) for field in RESUME_MERGE_FIELDS
    }
    values["resume_document_id"] = (
        str(employee.resume_document_id) if employee.resume_document_id is not None else None
    )
    return values


async def _merge_resume_into_employee(
    uow: UnitOfWork,
    organization_id: uuid.UUID,
    employee: Employee,
    profile: ExtractedEmployeeProfile,
    resume_document_id: uuid.UUID,
    resume_chunk_ids: list[uuid.UUID],
    *,
    actor_type: str,
    actor_id: str,
    import_item_id: uuid.UUID,
) -> dict[str, tuple[object, object]]:
    """The one resume merge (plan item 80), for import_resume's automatic
    branch and for approval, so the two cannot drift. `employee` is read
    under its row lock by the caller. Writes the changed fields, the resume
    document and the parsed profile; adds the resume's chunks (additive: an
    old resume's chunks stay citable, see KNOWN_LIMITATIONS.md); bumps the
    bids projecting the employee only if the title changed, the one field
    here on the graph node (plan item 95); writes an employee_resume_merge
    audit row. The review status, eligibility and name are not touched.
    Plan item 111: an email another employee holds is kept. Returns the
    changes."""
    others = await uow.knowledge.find_employee_duplicate_candidates(organization_id)
    held = emails_held_by_others(others, employee.knowledge_item_id)
    changes = resume_merge_changes(employee, profile, held)
    before = _merge_audit_values(employee)
    await uow.knowledge.update_employee(
        employee.knowledge_item_id,
        **{field: new for field, (_old, new) in changes.items()},
        resume_document_id=resume_document_id,
        parsed_profile=profile.model_dump(),
    )
    chunks_added = await uow.knowledge.associate_knowledge_item_chunks(
        employee.knowledge_item_id, resume_chunk_ids
    )
    if "current_title" in changes:
        await _bump_bids_projecting(uow, employee.knowledge_item_id)
    await uow.audit.create(
        organization_id=organization_id,
        actor_type=actor_type,
        actor_id=actor_id,
        action="employee_resume_merge",
        target_type="employee",
        target_id=employee.knowledge_item_id,
        before=before,
        after=_merge_audit_values(employee),
        metadata={
            "import_item_id": str(import_item_id),
            "fields_changed": sorted(changes),
            "chunks_added": chunks_added,
        },
    )
    return changes


class ResumeEmployeeFields(TypedDict):
    """What an employee made from a resume holds (plan item 111): one
    definition for import_resume's NEW branch and for a reviewer's import as
    a new person, so the two cannot drift."""

    name: str
    title: str
    email: str | None
    current_title: str | None
    professional_location: str | None
    years_experience: float | None
    resume_document_id: uuid.UUID
    source_document_id: uuid.UUID
    parsed_profile: dict


def resume_employee_fingerprint(organization_id: uuid.UUID, resume_document_id: uuid.UUID) -> str:
    """One employee per resume document: a second import of the same
    document converges on the employee the first made."""
    return _fingerprint(str(organization_id), "resume", str(resume_document_id))


def resume_employee_fields(
    profile: ExtractedEmployeeProfile, resume_document_id: uuid.UUID
) -> ResumeEmployeeFields:
    """The profile's values as the merge reads them (_field_value: strings
    trimmed, blank as none), and the name trimmed."""
    name = profile.name.strip()
    email = _field_value(profile, "email")
    current_title = _field_value(profile, "current_title")
    location = _field_value(profile, "professional_location")
    years = _field_value(profile, "years_experience")
    return ResumeEmployeeFields(
        name=name,
        title=name,
        email=email if isinstance(email, str) else None,
        current_title=current_title if isinstance(current_title, str) else None,
        professional_location=location if isinstance(location, str) else None,
        years_experience=years if isinstance(years, float) else None,
        resume_document_id=resume_document_id,
        source_document_id=resume_document_id,
        parsed_profile=profile.model_dump(),
    )


async def import_resume(
    organization_id: uuid.UUID,
    batch_id: uuid.UUID,
    source_ref: str,
    resume_document_id: uuid.UUID,
    profile: ExtractedEmployeeProfile,
    resume_chunk_ids: list[uuid.UUID],
) -> KnowledgeImportItem:
    """One resume's already-extracted profile (agents/knowledge/extract.py's
    output) -> one knowledge_import_items row, same dedup-adjudication shape
    as import_employee_master_row above. certification_mentions/
    project_mentions ride along inside parsed_profile as claims for a
    reviewer to see - they do not create certifications/past_projects rows
    themselves (see ExtractedCertificationMention/ExtractedProjectMention's
    own docstrings for why).

    resume_chunk_ids: every document_chunk belonging to this résumé
    (callers already have this - it's the same resume_chunks list they just
    passed to extract_employee_profile). Associated to the employee's
    knowledge_item via knowledge_item_chunks below, in both branches that
    touch an employee record, so retrieval can map any of the résumé's
    chunks (summary, experience, certifications, education) back to this
    employee - not just whichever one a scalar FK could have pointed at.
    See docs/eval/citation-correctness-fix-options.md.

    B16: two fixes, found together while giving this function the same
    select-then-insert treatment as import_employee_master_row/
    import_certification_row. First, the intended one: the claim step is
    now _claim_or_resume_import_item (see its own docstring), covering
    genuine concurrency the way the other two already did. Second, found
    only by reading this function directly rather than trusting the
    finding that sent this pass here: this function had NO retry-resume
    check at all before this fix - unlike its two siblings, it went
    straight to create_import_item with no get_import_item_by_source_ref
    guard first. A plain sequential retry (a worker job re-processing the
    same batch after a crash, zero concurrency involved) would have hit
    uq_knowledge_import_item_source_ref and crashed outright, every time -
    a real, separate, pre-existing gap this refactor closes as a side
    effect, not something the original finding named.
    """
    import_item, needs_resolution = await _claim_or_resume_import_item(
        batch_id, source_ref, profile.model_dump(), source_document_id=resume_document_id
    )
    if not needs_resolution:
        return import_item

    async with UnitOfWork() as uow:
        candidates = await uow.knowledge.find_employee_duplicate_candidates(organization_id)
        resolution = resolve_employee_duplicate(
            candidate_employee_code=None,
            candidate_name=profile.name,
            candidate_email=profile.email,
            candidate_resume_document_id=resume_document_id,
            candidates=candidates,
        )

        if resolution.verdict == DuplicateVerdict.NEEDS_REVIEW:
            updated = await uow.knowledge.update_import_item(
                import_item.id,
                status="needs_review",
                error_message=resolution.reason,
                target_knowledge_item_id=(
                    resolution.matched_employee.knowledge_item_id
                    if resolution.matched_employee
                    else None
                ),
            )
            await uow.commit()
            return updated

        if resolution.verdict == DuplicateVerdict.DUPLICATE:
            assert resolution.matched_employee is not None
            target_id = resolution.matched_employee.knowledge_item_id
            # Plan item 80: read under the row lock, so the comparison below
            # and the merge see the values an approval or a verify running
            # now would leave (lock order: the employee, then this item's
            # row, never the other way; see approve_import_item).
            employee = await uow.knowledge.get_employee_for_update(target_id)
            assert employee is not None
            overwrites = resume_merge_overwrites(
                employee, profile, emails_held_by_others(candidates, target_id)
            )
            if employee.profile_review_status == PROFILE_VERIFIED and overwrites:
                # Q3: a verified profile changes only after a person has
                # seen the change. Filling a blank, or adding chunks, is not
                # one, so those still merge on their own.
                updated = await uow.knowledge.update_import_item(
                    import_item.id,
                    status="needs_review",
                    target_knowledge_item_id=target_id,
                    error_message=(
                        f"{resolution.reason}, but this resume would change the verified "
                        "profile's "
                        + ", ".join(_RESUME_FIELD_LABELS[field] for field in overwrites)
                        + "; approve to apply it"
                    ),
                )
                await uow.commit()
                return updated
            await _merge_resume_into_employee(
                uow,
                organization_id,
                employee,
                profile,
                resume_document_id,
                resume_chunk_ids,
                actor_type="system",
                actor_id=_RESUME_IMPORT_ACTOR,
                import_item_id=import_item.id,
            )
            updated = await uow.knowledge.update_import_item(
                import_item.id, status="approved", target_knowledge_item_id=target_id
            )
            await uow.commit()
            return updated

        # NEW
        fingerprint = resume_employee_fingerprint(organization_id, resume_document_id)
        fields = resume_employee_fields(profile, resume_document_id)
        knowledge_item_id = await _create_or_get_employee(organization_id, fingerprint, **fields)
        await uow.knowledge.associate_knowledge_item_chunks(knowledge_item_id, resume_chunk_ids)
        updated = await uow.knowledge.update_import_item(
            import_item.id, status="approved", target_knowledge_item_id=knowledge_item_id
        )
        await uow.commit()
        return updated


async def import_worked_on_relationship(
    organization_id: uuid.UUID,
    employee_knowledge_item_id: uuid.UUID,
    candidate: WorkedOnCandidate,
    resolution: RelationshipResolution,
) -> KnowledgeRelationship | None:
    """Persists agents/knowledge/relationships.py's
    resolve_worked_on_verification outcome. REJECTED writes nothing at all -
    Part D2's provenance requirement is about relationships that actually
    exist in the graph, not a log of every candidate the LLM proposed and
    this policy discarded (agent_traces already has the full LLM call/output
    for that audit trail, per extract_worked_on's _write_trace).
    verified=True only for a VERIFIED resolution; NEEDS_REVIEW still writes
    the row (so it's visible to a reviewer and traversable once approved),
    just with verified=False.
    """
    if resolution.verdict == RelationshipVerdict.REJECTED:
        return None

    async with UnitOfWork() as uow:
        fingerprint = _fingerprint(
            str(organization_id),
            str(employee_knowledge_item_id),
            str(candidate.project.knowledge_item_id),
        )
        existing = await uow.knowledge.get_relationship_by_fingerprint(organization_id, fingerprint)
        if existing is not None:
            return existing

        relationship = await uow.knowledge.create_relationship(
            organization_id=organization_id,
            source_knowledge_item_id=employee_knowledge_item_id,
            relationship_type="WORKED_ON",
            target_knowledge_item_id=candidate.project.knowledge_item_id,
            confidence=candidate.llm_confidence,
            verified=resolution.verdict == RelationshipVerdict.VERIFIED,
            content_fingerprint=fingerprint,
        )
        if relationship.verified:
            # The graph projects verified relationships between two items a
            # bid includes (plan item 95).
            await _bump_bids_projecting_both(
                uow, employee_knowledge_item_id, candidate.project.knowledge_item_id
            )
        await uow.commit()
        return relationship


async def import_demonstrates_relationship(
    organization_id: uuid.UUID,
    project_knowledge_item_id: uuid.UUID,
    candidate: DemonstratesCandidate,
    resolution: RelationshipResolution,
) -> KnowledgeRelationship | None:
    """DEMONSTRATES' persistence step - identical shape to
    import_worked_on_relationship above (REJECTED writes nothing,
    NEEDS_REVIEW writes verified=False, VERIFIED writes verified=True),
    just Project -> Capability instead of Employee -> Project.
    """
    if resolution.verdict == RelationshipVerdict.REJECTED:
        return None

    async with UnitOfWork() as uow:
        fingerprint = _fingerprint(
            str(organization_id),
            str(project_knowledge_item_id),
            str(candidate.capability.knowledge_item_id),
        )
        existing = await uow.knowledge.get_relationship_by_fingerprint(organization_id, fingerprint)
        if existing is not None:
            return existing

        relationship = await uow.knowledge.create_relationship(
            organization_id=organization_id,
            source_knowledge_item_id=project_knowledge_item_id,
            relationship_type="DEMONSTRATES",
            target_knowledge_item_id=candidate.capability.knowledge_item_id,
            confidence=candidate.llm_confidence,
            verified=resolution.verdict == RelationshipVerdict.VERIFIED,
            content_fingerprint=fingerprint,
        )
        if relationship.verified:
            # The graph projects verified relationships between two items a
            # bid includes (plan item 95).
            await _bump_bids_projecting_both(
                uow, project_knowledge_item_id, candidate.capability.knowledge_item_id
            )
        await uow.commit()
        return relationship


async def import_past_project(
    organization_id: uuid.UUID, batch_id: uuid.UUID, source_ref: str, project: ResolvedPastProject
) -> KnowledgeImportItem:
    """One extracted, chunk-resolved project from a capability statement ->
    one knowledge_import_items row. Not dedup-adjudicated - like
    certifications, a company's own past project is a company-level fact
    with no ambiguous "is this the same project" identity question, unlike
    matching people. project.chunk_id/document_id are already resolved real
    IDs (agents/knowledge/extract.py's job), not something this function
    looks up.

    Phase 4 consumer guard: the row is claimed through
    _claim_or_resume_import_item, like its siblings, instead of a bare
    insert. A second delivery of the same import used to hit
    uq_knowledge_import_item_source_ref and fail the job; now a row a
    previous attempt already finished is returned as is, and a row it left
    pending is resolved again (safe: the knowledge item converges on its
    content_fingerprint).
    """
    import_item, needs_resolution = await _claim_or_resume_import_item(
        batch_id,
        source_ref,
        {
            "name": project.name,
            "client": project.client,
            "year": project.year,
            "description": project.description,
            "source_ref": project.source_ref,
            "chunk_id": str(project.chunk_id),
        },
    )
    if not needs_resolution:
        return import_item
    async with UnitOfWork() as uow:
        fingerprint = _fingerprint(
            str(organization_id), project.name, project.client or "", project.year or ""
        )
        existing = await uow.knowledge.get_knowledge_item_by_fingerprint(
            organization_id, "past_project", fingerprint
        )
        if existing is not None:
            updated = await uow.knowledge.update_import_item(
                import_item.id, status="approved", target_knowledge_item_id=existing.id
            )
            await uow.commit()
            return updated

        knowledge_item, _project = await uow.knowledge.create_past_project(
            organization_id=organization_id,
            title=project.name,
            content_fingerprint=fingerprint,
            client_name=project.client,
            description=project.description,
            source_document_id=project.document_id,
            source_chunk_id=project.chunk_id,
        )
        updated = await uow.knowledge.update_import_item(
            import_item.id, status="approved", target_knowledge_item_id=knowledge_item.id
        )
        await uow.commit()
        return updated


async def import_capability(
    organization_id: uuid.UUID, batch_id: uuid.UUID, source_ref: str, capability: ResolvedCapability
) -> KnowledgeImportItem:
    """Same fingerprint-idempotent, non-dedup-adjudicated, chunk-resolved
    shape as import_past_project above, with the same Phase 4 claim-or-resume
    guard."""
    import_item, needs_resolution = await _claim_or_resume_import_item(
        batch_id,
        source_ref,
        {
            "name": capability.name,
            "description": capability.description,
            "source_ref": capability.source_ref,
            "chunk_id": str(capability.chunk_id),
        },
    )
    if not needs_resolution:
        return import_item
    async with UnitOfWork() as uow:
        fingerprint = _fingerprint(str(organization_id), capability.name)
        existing = await uow.knowledge.get_knowledge_item_by_fingerprint(
            organization_id, "capability", fingerprint
        )
        if existing is not None:
            updated = await uow.knowledge.update_import_item(
                import_item.id, status="approved", target_knowledge_item_id=existing.id
            )
            await uow.commit()
            return updated

        knowledge_item, _capability = await uow.knowledge.create_capability(
            organization_id=organization_id,
            title=capability.name,
            content_fingerprint=fingerprint,
            description=capability.description,
            source_document_id=capability.document_id,
            source_chunk_id=capability.chunk_id,
        )
        updated = await uow.knowledge.update_import_item(
            import_item.id, status="approved", target_knowledge_item_id=knowledge_item.id
        )
        await uow.commit()
        return updated


def certification_row_from_payload(payload: dict) -> CertificationRow | None:
    """A certification import item's extracted_payload as the row it was
    made from (plan item 112); None when it does not read as one (no name,
    or a date that is not an ISO date)."""
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        return None

    def text(key: str) -> str | None:
        value = payload.get(key)
        if not isinstance(value, str):
            return None
        return value.strip() or None

    def day(key: str) -> tuple[bool, date | None]:
        value = payload.get(key)
        if value is None:
            return True, None
        if not isinstance(value, str):
            return False, None
        try:
            return True, date.fromisoformat(value)
        except ValueError:
            return False, None

    issue_ok, issue_date = day("issue_date")
    expiry_ok, expiry_date = day("expiry_date")
    if not (issue_ok and expiry_ok):
        return None
    return CertificationRow(
        row_number=0,
        cert_key=text("cert_key"),
        employee_ref=text("employee_ref"),
        name=name.strip(),
        issuer=text("issuer"),
        issue_date=issue_date,
        expiry_date=expiry_date,
    )


def certification_fingerprint(
    organization_id: uuid.UUID, holder_id: uuid.UUID, row: CertificationRow
) -> str:
    """A certificate's identity: (holder, name, issue date, expiry date)."""
    return _fingerprint(
        str(organization_id),
        str(holder_id),
        row.name,
        row.issue_date.isoformat() if row.issue_date else "",
        row.expiry_date.isoformat() if row.expiry_date else "",
    )


async def _create_certificate_from_row(
    uow: UnitOfWork,
    organization_id: uuid.UUID,
    holder_id: uuid.UUID,
    row: CertificationRow,
    source_document_id: uuid.UUID,
    today: date,
) -> tuple[uuid.UUID, bool]:
    """The one certificate creation (plan item 112), for
    import_certification_row's automatic branch and for approval with a
    chosen holder, so the two cannot drift. Returns (the certificate's
    knowledge item id, whether this call created it).

    An existing certificate with this fingerprint is the answer. Otherwise
    the status is expired or active against `today`, and a synthetic chunk
    "<name>, issued by <issuer>, valid from <date> to <date>." on the sheet
    (reused for the same text, plan item 59 PR 19) is its source. Both are
    written in the caller's transaction, inside a savepoint: a concurrent
    identical row that commits first makes the insert fail on
    uq_knowledge_item_fingerprint, the savepoint rolls back (the chunk with
    it, so no orphan), and the certificate that won is read and returned.
    The chunk's per-document lock is held until the caller commits.

    No bump: a new certificate reaches no bid until something links it
    (tests/projection_inputs.py's ALLOWED_WITHOUT_BUMP)."""
    fingerprint = certification_fingerprint(organization_id, holder_id, row)
    existing = await uow.knowledge.get_knowledge_item_by_fingerprint(
        organization_id, "certification", fingerprint
    )
    if existing is not None:
        return existing.id, False
    status = "expired" if row.expiry_date and row.expiry_date < today else "active"
    chunk_text = (
        f"{row.name}, issued by {row.issuer or 'an unspecified issuer'}, "
        f"valid from {row.issue_date.isoformat() if row.issue_date else 'an unspecified date'} "
        f"to {row.expiry_date.isoformat() if row.expiry_date else 'an unspecified date'}."
    )
    try:
        async with uow.session.begin_nested():
            chunk = await create_synthetic_chunk(uow, source_document_id, chunk_text)
            knowledge_item, _certification = await uow.knowledge.create_certification(
                organization_id=organization_id,
                title=row.name,
                content_fingerprint=fingerprint,
                holder_employee_item_id=holder_id,
                issuer=row.issuer,
                valid_from=row.issue_date,
                valid_until=row.expiry_date,
                source_document_id=source_document_id,
                source_chunk_id=chunk.id,
                status=status,
            )
        return knowledge_item.id, True
    except IntegrityError:
        won = await uow.knowledge.get_knowledge_item_by_fingerprint(
            organization_id, "certification", fingerprint
        )
        if won is None:
            raise
        return won.id, False


async def import_certification_row(
    organization_id: uuid.UUID,
    batch_id: uuid.UUID,
    source_document_id: uuid.UUID,
    row: CertificationRow,
    today: date,
) -> KnowledgeImportItem:
    """One certifications.xlsx row -> one knowledge_import_items row.
    Deterministic, not dedup-adjudicated the way employees are: a
    certification's identity is its (holder, name, issue_date, expiry_date)
    tuple, which either exactly matches an existing row (a re-import,
    handled via content_fingerprint the same way requirement_service.py
    handles re-extraction) or it doesn't - there's no ambiguous "is this the
    same certification" judgment call the way there is for people. `today`
    is a parameter rather than date.today() so a re-run of the eval/tests
    against a fixed evaluation date is reproducible, matching this system's
    determinism-where-possible posture elsewhere (e.g. temperature=0).
    `source_document_id` is the already-uploaded certifications.xlsx
    document (required, not resolved here) - create_synthetic_chunk below
    needs a real document to attach a chunk to.

    B16: the claim step is now _claim_or_resume_import_item, shared with
    import_employee_master_row and import_resume - see its own docstring.
    """
    source_ref = f"row_{row.row_number}"
    import_item, needs_resolution = await _claim_or_resume_import_item(
        batch_id,
        source_ref,
        {
            "cert_key": row.cert_key,
            "employee_ref": row.employee_ref,
            "name": row.name,
            "issuer": row.issuer,
            "issue_date": row.issue_date.isoformat() if row.issue_date else None,
            "expiry_date": row.expiry_date.isoformat() if row.expiry_date else None,
        },
    )
    if not needs_resolution:
        return import_item

    async with UnitOfWork() as uow:
        # A blank holder cell goes to review, never to a holderless approved
        # certificate (KNOWN_LIMITATIONS.md). The status cannot tell "no
        # holder given" from "a holder code not found", so the reason text
        # says which.
        if not row.employee_ref:
            updated = await uow.knowledge.update_import_item(
                import_item.id,
                status="needs_review",
                error_message=(
                    "no employee reference given for this row - the certification "
                    "holder cannot be identified"
                ),
            )
            await uow.commit()
            return updated

        employee = await uow.knowledge.get_employee_by_code(organization_id, row.employee_ref)
        if employee is None:
            updated = await uow.knowledge.update_import_item(
                import_item.id,
                status="needs_review",
                error_message=(
                    f"no employee found with employee_code {row.employee_ref!r} "
                    "in this organization"
                ),
            )
            await uow.commit()
            return updated
        # Plan item 112: through the one creation approval uses too, in this
        # transaction (a savepoint converges a concurrent identical row).
        knowledge_item_id, _created = await _create_certificate_from_row(
            uow, organization_id, employee.knowledge_item_id, row, source_document_id, today
        )
        updated = await uow.knowledge.update_import_item(
            import_item.id, status="approved", target_knowledge_item_id=knowledge_item_id
        )
        await uow.commit()
        return updated


_BATCH_PARSE_FAILURE_SOURCE_REF = "_batch_parse_failure"


async def record_batch_parse_failure(
    batch_id: uuid.UUID, error_message: str
) -> KnowledgeImportItem:
    """Called when the parse step itself raises, before any per-row
    knowledge_import_items row ever gets created - process_knowledge_
    document_import's own try/except around parse_employee_master(_csv)/
    parse_certifications. Without this, _compute_batch_progress sees
    total=0 and reports "queued" forever (KNOWN_LIMITATIONS.md: "a failure
    before any item row is created leaves no terminal state at all") -
    indistinguishable from an import that simply hasn't started, with no
    signal a human reviewer could act on. One synthetic failed item gives
    _compute_batch_progress a real total>0/failed==total to compute
    "failed" from, the same status a per-row failure already produces -
    idempotent on retry via the same get_import_item_by_source_ref check
    every per-row import function uses, since a retried job re-running the
    same broken file would otherwise hit this function a second time and
    collide with its own sentinel row.
    """
    async with UnitOfWork() as uow:
        existing = await uow.knowledge.get_import_item_by_source_ref(
            batch_id, _BATCH_PARSE_FAILURE_SOURCE_REF
        )
        if existing is not None:
            return existing

        import_item = await uow.knowledge.create_import_item(
            batch_id=batch_id,
            source_ref=_BATCH_PARSE_FAILURE_SOURCE_REF,
            extracted_payload={},
        )
        updated = await uow.knowledge.update_import_item(
            import_item.id, status="failed", error_message=error_message
        )
        await uow.commit()
        return updated


# ------------------------------------------------------------------
# Query, review, and approval - G6's read/write surface for
# apps/api/src/tenderos_api/routes/knowledge.py. Everything above this
# point is the import/extraction pipeline; everything below is what a
# human reviewer actually does with what that pipeline produced.
# ------------------------------------------------------------------


async def create_import_batch(
    organization_id: uuid.UUID,
    import_type: str,
    source_document_id: uuid.UUID | None,
    created_by: uuid.UUID,
) -> KnowledgeImportBatch:
    async with UnitOfWork() as uow:
        batch = await uow.knowledge.create_import_batch(
            organization_id=organization_id,
            import_type=import_type,
            source_document_id=source_document_id,
            created_by=created_by,
        )
        await uow.commit()
        return batch


async def queue_knowledge_document_import(
    document_id: uuid.UUID, batch_id: uuid.UUID, request_id: uuid.UUID | None = None
) -> None:
    """Fires KnowledgeDocumentUploaded so process_knowledge_document_import
    (apps/worker/jobs/knowledge_import.py) picks up the actual mechanical/
    LLM import step - kept as its own function so upload routes never touch
    uow.outbox directly (routes call services, per A4.1's layering).
    request_id must be passed (fresh, e.g. uuid.uuid4()) whenever this is
    re-queuing processing for a document that may already have fired this
    event once (replace_employee_resume, when content dedup reused an
    existing document row) - see
    knowledge_document_uploaded_idempotency_key's docstring.
    """
    async with UnitOfWork() as uow:
        await uow.outbox.create(
            aggregate_type=AGGREGATE_DOCUMENT,
            aggregate_id=document_id,
            event_type=EVENT_KNOWLEDGE_DOCUMENT_UPLOADED,
            payload=knowledge_document_uploaded_payload(document_id, batch_id),
            idempotency_key=knowledge_document_uploaded_idempotency_key(document_id, request_id),
        )
        await uow.commit()


async def get_knowledge_item(knowledge_item_id: uuid.UUID) -> KnowledgeItem | None:
    async with UnitOfWork() as uow:
        return await uow.knowledge.get_knowledge_item(knowledge_item_id)


async def list_knowledge_for_org(
    organization_id: uuid.UUID, item_type: str | None = None
) -> list[KnowledgeItem]:
    async with UnitOfWork() as uow:
        return await uow.knowledge.list_knowledge_items_for_org(organization_id, item_type)


async def patch_knowledge_item(
    knowledge_item_id: uuid.UUID,
    organization_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    *,
    title: str | None = None,
    status: str | None = None,
) -> KnowledgeItem:
    """G6's PATCH /knowledge/{id} - generic field correction for any
    knowledge_items row (title/status only; type-specific fields like an
    employee's email go through patch_employee_profile below, since those
    live on the child table, not knowledge_items itself). Same
    before/after audit-event shape as requirement_service.patch_requirement.
    """
    async with UnitOfWork() as uow:
        # Plan item 59, fix 7: locked before "before" is taken, so a second
        # edit of the item waits for this one and compares with what it
        # committed. Unlocked, the second read the old value, set it back,
        # wrote nothing (no net change) and did not bump, and its edit was
        # lost. Lock order: the item, then the bids' tenders (sorted).
        item = await uow.knowledge.get_knowledge_item_for_update(knowledge_item_id)
        if item is None:
            raise KnowledgeItemNotFoundError()

        before = {"title": item.title, "status": item.status}
        fields: dict[str, object] = {}
        if title is not None:
            fields["title"] = title
        if status is not None:
            fields["status"] = status
        updated = (
            await uow.knowledge.update_knowledge_item(knowledge_item_id, **fields)
            if fields
            else item
        )
        after = {"title": updated.title, "status": updated.status}
        # The status is on every projected item's graph node, any status, not
        # only archiving (plan item 95, Q2). The title is on a certificate's,
        # a project's and a capability's node, not an employee's (whose node
        # carries the employee row's name).
        title_projected = updated.item_type in _TITLED_NODE_ITEM_TYPES
        if before["status"] != after["status"] or (
            title_projected and before["title"] != after["title"]
        ):
            await _bump_bids_projecting(uow, knowledge_item_id)

        await uow.audit.create(
            organization_id=organization_id,
            actor_type="user",
            actor_id=str(actor_user_id),
            action="knowledge_item_edit",
            target_type="knowledge_item",
            target_id=knowledge_item_id,
            before=before,
            after=after,
        )
        await uow.commit()
        return updated


async def get_employee(knowledge_item_id: uuid.UUID) -> Employee | None:
    async with UnitOfWork() as uow:
        return await uow.knowledge.get_employee(knowledge_item_id)


async def list_bid_eligible_employees(organization_id: uuid.UUID) -> list[Employee]:
    """G6's GET /organizations/{org_id}/employees, bid-eligible-only view -
    B6's smoke test wants a candidate search for a new tender to find only
    profiles that are verified and bid-eligible. Backed by
    KnowledgeRepository.list_bid_eligible_employees,
    which already filters eligible_for_bid=true AND profile_review_status=
    'verified' at the query level - not a filter applied after the fact,
    so there's no path that accidentally returns an unreviewed profile.
    """
    async with UnitOfWork() as uow:
        return await uow.knowledge.list_bid_eligible_employees(organization_id)


async def patch_employee_profile(
    knowledge_item_id: uuid.UUID,
    organization_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    *,
    name: str | None = None,
    email: str | None = None,
    current_title: str | None = None,
    professional_location: str | None = None,
    years_experience: float | None = None,
    availability_status: str | None = None,
    availability_as_of: date | None = None,
) -> Employee:
    """G6's PATCH /employees/{id}/profile - PRD 'profile correction', kept
    deliberately separate from verify_employee_profile below ('approval').
    Correcting a typo in someone's title does not itself mean a reviewer
    has vetted the whole profile for bid use - conflating the two would let
    an unrelated edit accidentally leave eligible_for_bid or
    profile_review_status looking more authoritative than it is. Does not
    accept eligible_for_bid - that decision is verify_employee_profile's,
    an explicit approval action, not a side effect of a field edit (B6's
    own "do not proceed if" condition: an imported profile turning
    bid-eligible with no human approving it).
    """
    async with UnitOfWork() as uow:
        # Plan item 59, fix 7: locked before "before", as patch_knowledge_item.
        # Lock order: the employee, then the bids' tenders (sorted).
        employee = await uow.knowledge.get_employee_for_update(knowledge_item_id)
        if employee is None:
            raise EmployeeNotFoundError()

        before = {
            "name": employee.name,
            "email": employee.email,
            "current_title": employee.current_title,
            "professional_location": employee.professional_location,
            "years_experience": (
                float(employee.years_experience) if employee.years_experience is not None else None
            ),
            "availability_status": employee.availability_status,
            "availability_as_of": (
                employee.availability_as_of.isoformat() if employee.availability_as_of else None
            ),
        }

        fields: dict[str, object] = {}
        if name is not None:
            fields["name"] = name
        if email is not None:
            fields["email"] = email
        if current_title is not None:
            fields["current_title"] = current_title
        if professional_location is not None:
            fields["professional_location"] = professional_location
        if years_experience is not None:
            fields["years_experience"] = years_experience
        if availability_status is not None:
            fields["availability_status"] = availability_status
        if availability_as_of is not None:
            fields["availability_as_of"] = availability_as_of
        # A field edit is itself a form of review activity - stale/unverified
        # profiles should not look freshly reviewed just because a field was
        # corrected, so profile_review_status is deliberately NOT touched
        # here even when fields is non-empty; only verify_employee_profile
        # advances it.

        updated = (
            await uow.knowledge.update_employee(knowledge_item_id, **fields) if fields else employee
        )

        after = {
            "name": updated.name,
            "email": updated.email,
            "current_title": updated.current_title,
            "professional_location": updated.professional_location,
            "years_experience": (
                float(updated.years_experience) if updated.years_experience is not None else None
            ),
            "availability_status": updated.availability_status,
            "availability_as_of": (
                updated.availability_as_of.isoformat() if updated.availability_as_of else None
            ),
        }

        await uow.audit.create(
            organization_id=organization_id,
            actor_type="user",
            actor_id=str(actor_user_id),
            action="employee_profile_edit",
            target_type="employee",
            target_id=knowledge_item_id,
            before=before,
            after=after,
        )
        # Of these fields the employee's graph node carries the name, title
        # and availability (plan item 95); email, location and experience
        # are not projected.
        if any(before[field] != after[field] for field in _PROJECTED_PROFILE_FIELDS):
            await _bump_bids_projecting(uow, knowledge_item_id)
        await uow.commit()
        return updated


# The patch_employee_profile fields the projector copies onto the Employee
# node (graph_projection/projector.py, employee_rows).
_PROJECTED_PROFILE_FIELDS = ("name", "current_title", "availability_status")


async def verify_employee_profile(
    knowledge_item_id: uuid.UUID,
    organization_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    eligible_for_bid: bool,
) -> Employee:
    """G6's POST /employees/{id}/verify - the approval action, distinct
    from patch_employee_profile's correction action above. Sets
    profile_review_status='verified', stamps who/when, and requires the
    reviewer to make an explicit eligible_for_bid decision as part of
    approving (not a separate call, not a default) - this is the only
    place in the codebase that can set eligible_for_bid=true, satisfying
    B6's do-not-proceed-if item (an imported profile turning bid-eligible
    with no human approving it) by construction: there is no path to eligibility
    that does not go through this reviewer-invoked action.
    """
    async with UnitOfWork() as uow:
        # Plan item 59, D1: locked before "before" (the review status and
        # eligibility), as the profile edit. Unlocked, a second verify read
        # the first's old eligibility, and its UPDATE carried only the new
        # verification time: its own eligibility decision was lost.
        employee = await uow.knowledge.get_employee_for_update(knowledge_item_id)
        if employee is None:
            raise EmployeeNotFoundError()
        # Removed from the roster: the same column the candidate pool reads
        # (KnowledgeRepository's KNOWLEDGE_ITEM_ARCHIVED). Restoring the
        # knowledge item's status is the way back. Plan item 59, fix 8: read
        # FOR SHARE and held to commit, so an archive not yet committed is
        # waited for and one arriving now waits for this verify. This
        # transaction never writes the item, so the share is never upgraded.
        # Lock order: the employee, then its item, then the bids' tenders.
        knowledge_item = await uow.knowledge.get_knowledge_item_for_share(knowledge_item_id)
        if knowledge_item is not None and knowledge_item.status == KNOWLEDGE_ITEM_ARCHIVED:
            raise EmployeeArchivedError()

        before = {
            "profile_review_status": employee.profile_review_status,
            "eligible_for_bid": employee.eligible_for_bid,
        }

        updated = await uow.knowledge.update_employee(
            knowledge_item_id,
            profile_review_status="verified",
            profile_last_verified_at=datetime.now(UTC),
            profile_verified_by=actor_user_id,
            eligible_for_bid=eligible_for_bid,
        )

        after = {
            "profile_review_status": updated.profile_review_status,
            "eligible_for_bid": updated.eligible_for_bid,
        }
        await uow.audit.create(
            organization_id=organization_id,
            actor_type="user",
            actor_id=str(actor_user_id),
            action="employee_verify",
            target_type="employee",
            target_id=knowledge_item_id,
            before=before,
            after=after,
        )
        # Both are on the employee's graph node (plan item 95); the
        # verification stamp is not.
        if before != after:
            await _bump_bids_projecting(uow, knowledge_item_id)
        await uow.commit()
        return updated


@dataclass(frozen=True)
class ImportBatchProgress:
    """ImportBatchResponse's actual data source. KnowledgeImportBatch's own
    status/total_items/completed_items/failed_items columns are dead - no
    writer anywhere sets them past their creation-time defaults (see
    KNOWN_LIMITATIONS.md "knowledge_import_batches.status never advances past
    'queued'"). Rather than add batch-column writes to every import path that
    would need to keep them current (employee-master rows, certification
    rows, capability statement, resumes - each its own call site in
    apps/worker/jobs/knowledge_import.py), this is computed fresh from
    knowledge_import_items on every read, the same "recompute from source of
    truth at read time" fix D7's evaluate_evidence_coverage already uses for
    evidence rows rather than trusting a denormalized copy - a value with one
    real writer (each item's own status transition, already correct) instead
    of two that can drift apart.
    """

    id: uuid.UUID
    organization_id: uuid.UUID
    import_type: str
    source_document_id: uuid.UUID | None
    status: str
    total_items: int
    completed_items: int
    failed_items: int
    created_at: datetime


_IMPORT_ITEM_IN_PROGRESS_STATUSES = frozenset({"pending", "extracted"})


def _compute_batch_progress(
    batch: KnowledgeImportBatch, items: list[KnowledgeImportItem]
) -> ImportBatchProgress:
    total = len(items)
    failed = sum(1 for item in items if item.status == "failed")
    in_progress = sum(1 for item in items if item.status in _IMPORT_ITEM_IN_PROGRESS_STATUSES)
    completed = total - failed - in_progress

    if total == 0:
        # Plan item 78: a capability statement whose extraction found
        # nothing creates no items, but the worker stamps completed_at when
        # it finishes (only that path does). Without this it read "queued"
        # forever, and the talent library, which now follows the batch its
        # upload returned, would wait on it until its poll gave up.
        computed_status = "completed" if batch.completed_at is not None else "queued"
    elif in_progress > 0:
        computed_status = "processing"
    elif any(item.status == "needs_review" for item in items):
        computed_status = "needs_review"
    elif failed == total:
        computed_status = "failed"
    else:
        computed_status = "completed"

    return ImportBatchProgress(
        id=batch.id,
        organization_id=batch.organization_id,
        import_type=batch.import_type,
        source_document_id=batch.source_document_id,
        status=computed_status,
        total_items=total,
        completed_items=completed,
        failed_items=failed,
        created_at=batch.created_at,
    )


async def list_import_batches(organization_id: uuid.UUID) -> list[ImportBatchProgress]:
    async with UnitOfWork() as uow:
        batches = await uow.knowledge.list_import_batches_for_org(organization_id)
        progress: list[ImportBatchProgress] = []
        for batch in batches:
            items = await uow.knowledge.list_import_items_for_batch(batch.id)
            progress.append(_compute_batch_progress(batch, items))
        return progress


async def get_import_batch(batch_id: uuid.UUID) -> ImportBatchProgress | None:
    async with UnitOfWork() as uow:
        batch = await uow.knowledge.get_import_batch(batch_id)
        if batch is None:
            return None
        items = await uow.knowledge.list_import_items_for_batch(batch_id)
        return _compute_batch_progress(batch, items)


async def list_import_items(batch_id: uuid.UUID) -> list[KnowledgeImportItem]:
    async with UnitOfWork() as uow:
        return await uow.knowledge.list_import_items_for_batch(batch_id)


async def get_import_item(item_id: uuid.UUID) -> KnowledgeImportItem | None:
    async with UnitOfWork() as uow:
        return await uow.knowledge.get_import_item(item_id)


async def approve_import_item(
    item_id: uuid.UUID,
    organization_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    target_fingerprint: str | None = None,
    target_employee_id: uuid.UUID | None = None,
    candidates_fingerprint: str | None = None,
) -> KnowledgeImportItem:
    """G6's POST /knowledge/import-items/{id}/approve, for an item sitting at
    needs_review (a dedup match the deterministic policy could not settle).

    Plan item 80 (user decisions 2026-11-18, option C): approving a resume
    item merges the resume into the employee it matched, through the same
    _merge_resume_into_employee as import_resume's automatic branch. The
    reviewer saw the change: the request carries the fingerprint of the
    employee values the screen showed (resume_target_fingerprint), compared
    under the employee's row lock; a different one is refused, nothing
    written. The profile's review status is kept (Q1). A WORKED_ON
    extraction for the merged resume is queued (outbox, Q6). Other import
    types keep their status-only approval (plan note).

    Status rule: only needs_review is approved; approving an approved item
    is a replay and returns it with no write and no audit row; anything else
    is refused. Lock order: this item's row, then the employee's (then the
    bids a title change bumps). import_resume's automatic branch locks the
    employee and then only its own new item, which is still pending in that
    transaction; approval refuses a pending item before asking for the
    employee lock, so the two never wait on each other in a circle. verify
    and the profile edit take the employee row alone (then bumped bids).

    Plan item 111 (user decisions 2026-11-20): target_employee_id picks the
    employee to merge into, from the item's candidates (resume_candidates),
    whether or not the item names one already (Q2). The request carries the
    candidates_fingerprint the reviewer was shown; a different candidate set
    is refused (409 IMPORT_ITEM_CANDIDATES_CHANGED), and so is an employee
    that is not a candidate now. The pick then merges exactly as above, the
    chosen employee's own fingerprint compared under its row lock. On an
    approved item, a request naming another employee is refused (Q4): a
    replay is only the same decision.

    Plan item 112 (user decisions 2026-11-20): an employee-master item
    applies its row through _apply_master_row_to_employee, the automatic
    branch's own apply, onto the employee it names or one picked from its
    candidates (as resumes, by the master's own signals: the code and the
    name); target_fingerprint is master_target_fingerprint. An archived
    employee is applied to and stays archived (Q6). A certification item is
    created under the holder the reviewer chose (target_employee_id, any
    live employee of the organization) through _create_certificate_from_row;
    target_fingerprint is certificate_holder_fingerprint, compared under the
    holder's row lock, and the sheet's holder code is written onto a holder
    who has none, so the same row finds them next time. Lock order for a
    certification: the item, the holder's employee row (FOR NO KEY UPDATE:
    the code write is an UPDATE of it), the holder's knowledge item (FOR
    SHARE: read, never written here), the sheet's chunk lock, the insert.
    """
    async with UnitOfWork() as uow:
        item = await uow.knowledge.get_import_item_for_update(item_id)
        if item is None:
            raise ImportItemNotFoundError()
        batch = await uow.knowledge.get_import_batch(item.batch_id)
        import_type = batch.import_type if batch is not None else None
        if item.status == "approved":
            if target_employee_id is not None and (
                await _decided_employee(uow, item, import_type) != target_employee_id
            ):
                raise ImportItemNotInReviewError(item.status)
            return item
        if item.status != "needs_review":
            raise ImportItemNotInReviewError(item.status)
        resume_item = import_type == "bulk_resumes"
        master_item = import_type in MASTER_IMPORT_TYPES
        certification_item = import_type == "certification_documents"
        if target_employee_id is not None and not (
            resume_item or master_item or certification_item
        ):
            raise ImportItemTypeUnsupportedError()
        merge_changes: dict[str, tuple[object, object]] | None = None
        master_changes: dict[str, tuple[object, object]] | None = None
        certificate: dict[str, object] | None = None
        picked_from: list[uuid.UUID] | None = None
        new_target: uuid.UUID | None = None
        if resume_item:
            target_id = item.target_knowledge_item_id
            if target_employee_id is not None:
                picked_from = await _check_resume_pick(
                    uow, item, organization_id, target_employee_id, candidates_fingerprint
                )
                target_id = target_employee_id
                new_target = target_employee_id
            merge_changes = await _approve_resume_merge(
                uow, item, target_id, organization_id, actor_user_id, target_fingerprint
            )
        elif master_item:
            row = master_row_from_payload(item.extracted_payload)
            if row is None:
                raise ImportItemPayloadInvalidError()
            target_id = item.target_knowledge_item_id
            if target_employee_id is not None:
                picked_from = await _check_master_pick(
                    uow, row, organization_id, target_employee_id, candidates_fingerprint
                )
                target_id = target_employee_id
                new_target = target_employee_id
            master_changes = await _approve_master_apply(
                uow, item, row, target_id, organization_id, actor_user_id, target_fingerprint
            )
        elif certification_item:
            assert batch is not None
            certificate = await _approve_certificate(
                uow, item, batch, organization_id, target_employee_id, target_fingerprint
            )
            new_target = uuid.UUID(str(certificate["certification"]))
        before = {"status": item.status}
        fields: dict[str, object] = {"status": "approved"}
        if new_target is not None:
            fields["target_knowledge_item_id"] = new_target
        updated = await uow.knowledge.update_import_item(item_id, **fields)
        metadata: dict[str, object] | None = None
        changes = merge_changes if merge_changes is not None else master_changes
        if changes is not None:
            metadata = {
                "employee": str(updated.target_knowledge_item_id),
                "fields_changed": sorted(changes),
            }
            if picked_from is not None:
                metadata["decision"] = "pick"
                metadata["candidates"] = sorted(str(c) for c in picked_from)
        if certificate is not None:
            metadata = certificate
        await uow.audit.create(
            organization_id=organization_id,
            actor_type="user",
            actor_id=str(actor_user_id),
            action="knowledge_import_item_approve",
            target_type="knowledge_import_item",
            target_id=item_id,
            before=before,
            after={"status": updated.status},
            metadata=metadata,
        )
        if merge_changes is not None:
            await uow.outbox.create(
                aggregate_type=AGGREGATE_KNOWLEDGE_IMPORT_ITEM,
                aggregate_id=item_id,
                event_type=EVENT_RESUME_MERGE_APPROVED,
                payload=resume_merge_approved_payload(item_id),
                idempotency_key=resume_merge_approved_idempotency_key(item_id),
            )
        await uow.commit()
        return updated


async def _approve_resume_merge(
    uow: UnitOfWork,
    item: KnowledgeImportItem,
    target_id: uuid.UUID | None,
    organization_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    target_fingerprint: str | None,
) -> dict[str, tuple[object, object]]:
    if target_id is None:
        raise ImportItemNoTargetError()
    if item.source_document_id is None:
        raise ImportItemSourceMissingError()
    if target_fingerprint is None:
        raise ImportItemFingerprintRequiredError()
    employee = await uow.knowledge.get_employee_for_update(target_id)
    if employee is None:
        raise ImportItemNoTargetError()
    knowledge_item = await uow.knowledge.get_knowledge_item(employee.knowledge_item_id)
    if knowledge_item is not None and knowledge_item.status == KNOWLEDGE_ITEM_ARCHIVED:
        raise EmployeeArchivedError()
    if resume_target_fingerprint(employee) != target_fingerprint:
        raise ImportItemTargetChangedError()
    profile = _item_profile(item)
    if profile is None:
        raise ImportItemPayloadInvalidError()
    chunks = await uow.documents.list_chunks_for_document(item.source_document_id)
    return await _merge_resume_into_employee(
        uow,
        organization_id,
        employee,
        profile,
        item.source_document_id,
        [chunk.id for chunk in chunks],
        actor_type="user",
        actor_id=str(actor_user_id),
        import_item_id=item.id,
    )


async def _decided_employee(
    uow: UnitOfWork, item: KnowledgeImportItem, import_type: str | None
) -> uuid.UUID | None:
    """The employee an approved item was decided for: the one it names, or
    for a certification item the certificate's holder (Q4's replay rule)."""
    if import_type == "certification_documents" and item.target_knowledge_item_id is not None:
        certification = await uow.knowledge.get_certification(item.target_knowledge_item_id)
        return certification.holder_employee_item_id if certification is not None else None
    return item.target_knowledge_item_id


def _master_matches(
    row: EmployeeMasterRow, employees: list[Employee]
) -> list[tuple[Employee, tuple[str, ...]]]:
    """Plan item 112: the employees a master row matches now, by the
    duplicate check's own signals (the code and the name; a master row
    carries no email or document)."""
    return employee_match_signals(
        candidate_employee_code=row.employee_code,
        candidate_name=row.name,
        candidate_email=None,
        candidate_resume_document_id=None,
        candidates=employees,
    )


async def _item_statuses(uow: UnitOfWork, ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    statuses: dict[uuid.UUID, str] = {}
    for knowledge_item_id in ids:
        knowledge_item = await uow.knowledge.get_knowledge_item(knowledge_item_id)
        statuses[knowledge_item_id] = knowledge_item.status if knowledge_item is not None else ""
    return statuses


def master_candidates_fingerprint(
    matches: list[tuple[Employee, tuple[str, ...]]], statuses: dict[uuid.UUID, str]
) -> str:
    """As resume_candidates_fingerprint, for a master row's candidates: each
    one's id, signals and master_target_fingerprint, in id order."""
    shown = sorted(
        (
            str(employee.knowledge_item_id),
            sorted(signals),
            master_target_fingerprint(employee, statuses.get(employee.knowledge_item_id, "")),
        )
        for employee, signals in matches
    )
    return hashlib.sha256(json.dumps(shown).encode()).hexdigest()


async def _check_master_pick(
    uow: UnitOfWork,
    row: EmployeeMasterRow,
    organization_id: uuid.UUID,
    target_employee_id: uuid.UUID,
    candidates_fingerprint: str | None,
) -> list[uuid.UUID]:
    """As _check_resume_pick, for a master row: the pick is one of the
    candidates shown, and that list still holds (read under the item's lock,
    before the chosen employee is locked). Returns the candidate ids."""
    if candidates_fingerprint is None:
        raise ImportItemFingerprintRequiredError()
    employees = await uow.knowledge.find_employee_duplicate_candidates(organization_id)
    matches = _master_matches(row, employees)
    candidate_ids = [employee.knowledge_item_id for employee, _ in matches]
    statuses = await _item_statuses(uow, candidate_ids)
    if master_candidates_fingerprint(matches, statuses) != candidates_fingerprint:
        raise ImportItemCandidatesChangedError()
    if target_employee_id not in candidate_ids:
        raise ImportItemNotACandidateError()
    return candidate_ids


async def _approve_master_apply(
    uow: UnitOfWork,
    item: KnowledgeImportItem,
    row: EmployeeMasterRow,
    target_id: uuid.UUID | None,
    organization_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    target_fingerprint: str | None,
) -> dict[str, tuple[object, object]]:
    if target_id is None:
        raise ImportItemNoTargetError()
    if target_fingerprint is None:
        raise ImportItemFingerprintRequiredError()
    employee = await uow.knowledge.get_employee_for_update(target_id)
    if employee is None:
        raise ImportItemNoTargetError()
    knowledge_item = await uow.knowledge.get_knowledge_item_for_update(target_id)
    if knowledge_item is None:
        raise ImportItemNoTargetError()
    if master_target_fingerprint(employee, knowledge_item.status) != target_fingerprint:
        raise ImportItemTargetChangedError()
    return await _apply_master_row_to_employee(
        uow,
        organization_id,
        employee,
        knowledge_item,
        row,
        actor_type="user",
        actor_id=str(actor_user_id),
        import_item_id=item.id,
    )


def certificate_holder_fingerprint(employee: Employee, archived: bool) -> str:
    """What the holder choice shows of an employee, as one hash: the name,
    the code and whether they are archived (plan item 112)."""
    shown = {"name": employee.name, "employee_code": employee.employee_code, "archived": archived}
    return hashlib.sha256(json.dumps(shown, sort_keys=True).encode()).hexdigest()


async def _approve_certificate(
    uow: UnitOfWork,
    item: KnowledgeImportItem,
    batch: KnowledgeImportBatch,
    organization_id: uuid.UUID,
    holder_id: uuid.UUID | None,
    target_fingerprint: str | None,
) -> dict[str, object]:
    """Plan item 112: a certification item in review becomes a certificate
    under the holder the reviewer chose. Returns the audit metadata."""
    if holder_id is None:
        raise ImportItemNoTargetError()
    if target_fingerprint is None:
        raise ImportItemFingerprintRequiredError()
    row = certification_row_from_payload(item.extracted_payload)
    if row is None:
        raise ImportItemPayloadInvalidError()
    if batch.source_document_id is None:
        raise ImportItemSourceMissingError()
    # FOR NO KEY UPDATE, the mode the code write's UPDATE takes: a FOR SHARE
    # read upgraded by a later write is the lock-upgrade trap (plan item 59,
    # PR 20).
    holder = await uow.knowledge.get_employee_for_update(holder_id)
    if holder is None or holder.organization_id != organization_id:
        raise ImportItemNotACandidateError()
    holder_item = await uow.knowledge.get_knowledge_item_for_share(holder_id)
    if holder_item is None:
        raise ImportItemNotACandidateError()
    archived = holder_item.status == KNOWLEDGE_ITEM_ARCHIVED
    if archived:
        raise EmployeeArchivedError()
    if certificate_holder_fingerprint(holder, archived) != target_fingerprint:
        raise ImportItemTargetChangedError()
    # Both codes go in the audit row (user, PR 25's review): a sheet code
    # that differs from one the holder already has is kept as it is, so the
    # row comes back to review until the sheet is fixed. Read before the
    # write below, which a rolled-back savepoint would leave unreadable.
    holder_code = holder.employee_code
    code_written = False
    if row.employee_ref is not None and holder.employee_code is None:
        # The sheet's code onto a holder who has none, so the same row finds
        # them next time (the re-import check). Not when another employee
        # holds it: codes are unique in an organization.
        if await uow.knowledge.get_employee_by_code(organization_id, row.employee_ref) is None:
            try:
                async with uow.session.begin_nested():
                    await uow.knowledge.update_employee(holder_id, employee_code=row.employee_ref)
                code_written = True
            except IntegrityError:
                # Taken by a concurrent write since the read above.
                code_written = False
    certification_id, created = await _create_certificate_from_row(
        uow, organization_id, holder_id, row, batch.source_document_id, today_utc()
    )
    return {
        "certification": str(certification_id),
        "holder": str(holder_id),
        "created": created,
        "sheet_holder_code": row.employee_ref,
        "holder_code": holder_code,
        "holder_code_written": code_written,
    }


def _resume_matches(
    item: KnowledgeImportItem, profile: ExtractedEmployeeProfile, employees: list[Employee]
) -> list[tuple[Employee, tuple[str, ...]]]:
    """Plan item 111: the employees a resume item matches now, by the
    duplicate check's own signals (the name, the email, the resume
    document; a resume carries no employee code)."""
    return employee_match_signals(
        candidate_employee_code=None,
        candidate_name=profile.name,
        candidate_email=profile.email,
        candidate_resume_document_id=item.source_document_id,
        candidates=employees,
    )


def resume_candidates_fingerprint(matches: list[tuple[Employee, tuple[str, ...]]]) -> str:
    """Plan item 111: the candidate list as the reviewer saw it, as one hash:
    each candidate's id, its signals and its resume_target_fingerprint, in
    id order. Both decisions send it back, so a candidate added, gone, or
    changed since the screen loaded is refused, not decided on."""
    shown = sorted(
        (str(employee.knowledge_item_id), sorted(signals), resume_target_fingerprint(employee))
        for employee, signals in matches
    )
    return hashlib.sha256(json.dumps(shown).encode()).hexdigest()


async def _check_resume_pick(
    uow: UnitOfWork,
    item: KnowledgeImportItem,
    organization_id: uuid.UUID,
    target_employee_id: uuid.UUID,
    candidates_fingerprint: str | None,
) -> list[uuid.UUID]:
    """Plan item 111: a pick is one of the candidates the reviewer was shown,
    and that list still holds. Read before the chosen employee is locked
    (the item is); the chosen employee's own values are then compared under
    its row lock by _approve_resume_merge. Returns the candidate ids."""
    if candidates_fingerprint is None:
        raise ImportItemFingerprintRequiredError()
    if item.source_document_id is None:
        raise ImportItemSourceMissingError()
    profile = _item_profile(item)
    if profile is None:
        raise ImportItemPayloadInvalidError()
    employees = await uow.knowledge.find_employee_duplicate_candidates(organization_id)
    matches = _resume_matches(item, profile, employees)
    if resume_candidates_fingerprint(matches) != candidates_fingerprint:
        raise ImportItemCandidatesChangedError()
    candidate_ids = [employee.knowledge_item_id for employee, _ in matches]
    if target_employee_id not in candidate_ids:
        raise ImportItemNotACandidateError()
    return candidate_ids


async def _made_from_item(uow: UnitOfWork, item: KnowledgeImportItem) -> bool:
    """An approved resume item whose employee was made from its own resume
    (the NEW branch, or import as a new person), not merged into one made
    from another document."""
    if item.target_knowledge_item_id is None or item.source_document_id is None:
        return False
    knowledge_item = await uow.knowledge.get_knowledge_item(item.target_knowledge_item_id)
    return (
        knowledge_item is not None and knowledge_item.source_document_id == item.source_document_id
    )


async def import_item_as_new_employee(
    item_id: uuid.UUID,
    organization_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    candidates_fingerprint: str | None,
) -> KnowledgeImportItem:
    """Plan item 111 (user decisions 2026-11-20): a resume item in review
    becomes a new employee, made as import_resume's NEW branch makes one
    (resume_employee_fields, resume_employee_fingerprint), with the resume's
    chunks, and the item approved naming them.

    Refused (Q3, 409 IMPORT_ITEM_STRONG_MATCH) when a candidate shares the
    resume's email or its document, or an employee was already made from this
    document: a name-only match is allowed, the case this exists for. The
    candidate list is compared with the one the reviewer saw (409
    IMPORT_ITEM_CANDIDATES_CHANGED). The employee is created in this
    transaction, under the item's row lock, so a refusal leaves nothing
    behind. Status rule (Q4): only needs_review; a replay of an item already
    imported this way returns it; any other approved item is refused. Audit:
    knowledge_import_item_approve with decision "new". WORKED_ON: the same
    ResumeMergeApproved event as a merge. Lock order: the item only.

    Plan item 112: an employee-master item becomes a new employee made as
    import_employee_master_row's NEW branch makes one (master_employee_fields,
    master_employee_fingerprint, and the row's status), refused when a
    candidate shares its code (the master's one strong signal: codes are
    unique) or an employee already has its fingerprint. No WORKED_ON event:
    a master row carries no resume."""
    async with UnitOfWork() as uow:
        item = await uow.knowledge.get_import_item_for_update(item_id)
        if item is None:
            raise ImportItemNotFoundError()
        batch = await uow.knowledge.get_import_batch(item.batch_id)
        if batch is not None and batch.import_type in MASTER_IMPORT_TYPES:
            return await _import_master_item_as_new(
                uow, item, organization_id, actor_user_id, candidates_fingerprint
            )
        resume_item = batch is not None and batch.import_type == "bulk_resumes"
        if item.status == "approved":
            if resume_item and await _made_from_item(uow, item):
                return item
            raise ImportItemNotInReviewError(item.status)
        if item.status != "needs_review":
            raise ImportItemNotInReviewError(item.status)
        if not resume_item:
            raise ImportItemTypeUnsupportedError()
        if candidates_fingerprint is None:
            raise ImportItemFingerprintRequiredError()
        document_id = item.source_document_id
        if document_id is None:
            raise ImportItemSourceMissingError()
        profile = _item_profile(item)
        if profile is None:
            raise ImportItemPayloadInvalidError()
        employees = await uow.knowledge.find_employee_duplicate_candidates(organization_id)
        matches = _resume_matches(item, profile, employees)
        if resume_candidates_fingerprint(matches) != candidates_fingerprint:
            raise ImportItemCandidatesChangedError()
        if any(STRONG_SIGNALS.intersection(signals) for _, signals in matches):
            raise ImportItemStrongMatchError()
        fingerprint = resume_employee_fingerprint(organization_id, document_id)
        if await uow.knowledge.get_knowledge_item_by_fingerprint(
            organization_id, "employee", fingerprint
        ):
            raise ImportItemStrongMatchError()
        fields = resume_employee_fields(profile, document_id)
        knowledge_item, _employee = await uow.knowledge.create_employee(
            organization_id=organization_id, content_fingerprint=fingerprint, **fields
        )
        chunks = await uow.documents.list_chunks_for_document(document_id)
        await uow.knowledge.associate_knowledge_item_chunks(
            knowledge_item.id, [chunk.id for chunk in chunks]
        )
        updated = await uow.knowledge.update_import_item(
            item_id, status="approved", target_knowledge_item_id=knowledge_item.id
        )
        await uow.audit.create(
            organization_id=organization_id,
            actor_type="user",
            actor_id=str(actor_user_id),
            action="knowledge_import_item_approve",
            target_type="knowledge_import_item",
            target_id=item_id,
            before={"status": "needs_review"},
            after={"status": updated.status},
            metadata={
                "decision": "new",
                "employee": str(knowledge_item.id),
                "candidates": sorted(str(e.knowledge_item_id) for e, _ in matches),
            },
        )
        await uow.outbox.create(
            aggregate_type=AGGREGATE_KNOWLEDGE_IMPORT_ITEM,
            aggregate_id=item_id,
            event_type=EVENT_RESUME_MERGE_APPROVED,
            payload=resume_merge_approved_payload(item_id),
            idempotency_key=resume_merge_approved_idempotency_key(item_id),
        )
        await uow.commit()
        return updated


async def _import_master_item_as_new(
    uow: UnitOfWork,
    item: KnowledgeImportItem,
    organization_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    candidates_fingerprint: str | None,
) -> KnowledgeImportItem:
    """import_item_as_new_employee for an employee-master item (plan item
    112), in the caller's transaction, under the item's row lock."""
    row = master_row_from_payload(item.extracted_payload)
    if item.status == "approved":
        if row is not None and item.target_knowledge_item_id is not None:
            made = await uow.knowledge.get_knowledge_item(item.target_knowledge_item_id)
            if made is not None and made.content_fingerprint == master_employee_fingerprint(
                organization_id, row
            ):
                return item
        raise ImportItemNotInReviewError(item.status)
    if item.status != "needs_review":
        raise ImportItemNotInReviewError(item.status)
    if candidates_fingerprint is None:
        raise ImportItemFingerprintRequiredError()
    if row is None:
        raise ImportItemPayloadInvalidError()
    employees = await uow.knowledge.find_employee_duplicate_candidates(organization_id)
    matches = _master_matches(row, employees)
    statuses = await _item_statuses(uow, [employee.knowledge_item_id for employee, _ in matches])
    if master_candidates_fingerprint(matches, statuses) != candidates_fingerprint:
        raise ImportItemCandidatesChangedError()
    if any(STRONG_SIGNALS.intersection(signals) for _, signals in matches):
        raise ImportItemStrongMatchError()
    fingerprint = master_employee_fingerprint(organization_id, row)
    if await uow.knowledge.get_knowledge_item_by_fingerprint(
        organization_id, "employee", fingerprint
    ):
        raise ImportItemStrongMatchError()
    knowledge_item, _employee = await uow.knowledge.create_employee(
        organization_id=organization_id,
        content_fingerprint=fingerprint,
        **master_employee_fields(row),
    )
    item_status = _EMPLOYEE_STATUS_TO_KNOWLEDGE_ITEM_STATUS.get(row.status or "")
    if item_status:
        # As the NEW branch: a new employee reaches no bid, so no bump.
        await uow.knowledge.update_knowledge_item(knowledge_item.id, status=item_status)
    updated = await uow.knowledge.update_import_item(
        item.id, status="approved", target_knowledge_item_id=knowledge_item.id
    )
    await uow.audit.create(
        organization_id=organization_id,
        actor_type="user",
        actor_id=str(actor_user_id),
        action="knowledge_import_item_approve",
        target_type="knowledge_import_item",
        target_id=item.id,
        before={"status": "needs_review"},
        after={"status": updated.status},
        metadata={
            "decision": "new",
            "employee": str(knowledge_item.id),
            "candidates": sorted(str(e.knowledge_item_id) for e, _ in matches),
        },
    )
    await uow.commit()
    return updated


# Plan item 80: the statuses a reviewer can reject from (a failed item can be
# rejected too, to clear it from the queue).
_REJECTABLE_IMPORT_ITEM_STATUSES = frozenset({"needs_review", "failed"})


async def reject_import_item(
    item_id: uuid.UUID, organization_id: uuid.UUID, actor_user_id: uuid.UUID, reason: str | None
) -> KnowledgeImportItem:
    """Rejects a needs_review (or failed) import row outright - the
    reviewer decided this row should not become or update a knowledge_item
    at all (e.g. "no, that's a different Dana Whitfield, don't merge").
    Does not delete anything already created; if a needs_review row's
    target_knowledge_item_id pointed at an existing employee, that employee
    is untouched - only this import row's own status changes.

    Plan item 80: the item is read under its row lock; rejecting a rejected
    item is a replay (returned, nothing written), and an approved one, or
    one still importing, is refused.
    """
    async with UnitOfWork() as uow:
        item = await uow.knowledge.get_import_item_for_update(item_id)
        if item is None:
            raise ImportItemNotFoundError()
        if item.status == "rejected":
            return item
        if item.status not in _REJECTABLE_IMPORT_ITEM_STATUSES:
            raise ImportItemNotInReviewError(item.status)
        before = {"status": item.status, "error_message": item.error_message}
        updated = await uow.knowledge.update_import_item(
            item_id, status="rejected", error_message=reason
        )
        await uow.audit.create(
            organization_id=organization_id,
            actor_type="user",
            actor_id=str(actor_user_id),
            action="knowledge_import_item_reject",
            target_type="knowledge_import_item",
            target_id=item_id,
            before=before,
            after={"status": updated.status, "error_message": updated.error_message},
            metadata={"reason": reason} if reason else None,
        )
        await uow.commit()
        return updated


@dataclass(frozen=True)
class ImportItemTarget:
    """The employee a resume item names, as the review panel shows it (plan
    item 80): the four fields, the resume document, the review status, the
    fingerprint Approve sends back, and whether an approved item went into an
    employee that already existed (merged) rather than one made from it."""

    knowledge_item_id: uuid.UUID
    name: str
    email: str | None
    current_title: str | None
    professional_location: str | None
    years_experience: float | None
    resume_document_id: uuid.UUID | None
    profile_review_status: str
    fingerprint: str
    merged_into_existing: bool
    # resume_merge_fields for this item's resume; None when its extracted
    # payload is not a valid profile (the panel then says so and offers
    # Reject only, and approval refuses it).
    changes: list[ResumeMergeField] | None


def _item_profile(item: KnowledgeImportItem) -> ExtractedEmployeeProfile | None:
    try:
        return ExtractedEmployeeProfile.model_validate(item.extracted_payload)
    except ValidationError:
        return None


async def list_import_items_with_targets(
    batch_id: uuid.UUID,
) -> list[tuple[KnowledgeImportItem, ImportItemTarget | None]]:
    """list_import_items, each resume item with the employee it names (plan
    item 80). Other import types, and items naming no employee, get None."""
    async with UnitOfWork() as uow:
        items = await uow.knowledge.list_import_items_for_batch(batch_id)
        batch = await uow.knowledge.get_import_batch(batch_id)
        if batch is None or batch.import_type != "bulk_resumes":
            return [(item, None) for item in items]
        employees = await uow.knowledge.find_employee_duplicate_candidates(batch.organization_id)
        out: list[tuple[KnowledgeImportItem, ImportItemTarget | None]] = []
        for item in items:
            target: ImportItemTarget | None = None
            if item.target_knowledge_item_id is not None:
                employee = await uow.knowledge.get_employee(item.target_knowledge_item_id)
                knowledge_item = await uow.knowledge.get_knowledge_item(
                    item.target_knowledge_item_id
                )
                if employee is not None and knowledge_item is not None:
                    profile = _item_profile(item)
                    target = ImportItemTarget(
                        knowledge_item_id=employee.knowledge_item_id,
                        name=employee.name,
                        email=employee.email,
                        current_title=employee.current_title,
                        professional_location=employee.professional_location,
                        years_experience=(
                            float(employee.years_experience)
                            if employee.years_experience is not None
                            else None
                        ),
                        resume_document_id=employee.resume_document_id,
                        profile_review_status=employee.profile_review_status,
                        fingerprint=resume_target_fingerprint(employee),
                        # An employee made from this item's resume (the NEW
                        # branch) has it as its source; a merge went into one
                        # made from another document.
                        merged_into_existing=(
                            item.status == "approved"
                            and item.source_document_id is not None
                            and knowledge_item.source_document_id != item.source_document_id
                        ),
                        changes=(
                            resume_merge_fields(
                                employee,
                                profile,
                                emails_held_by_others(employees, employee.knowledge_item_id),
                            )
                            if profile is not None
                            else None
                        ),
                    )
            out.append((item, target))
        return out


@dataclass(frozen=True)
class ImportItemCandidate:
    """Plan item 111: one employee a resume item matches, as the review panel
    shows it: the values, the signals it matched on, whether it is archived
    (no merge), its own fingerprint (sent back with a pick) and what merging
    into it would change."""

    knowledge_item_id: uuid.UUID
    name: str
    email: str | None
    current_title: str | None
    professional_location: str | None
    years_experience: float | None
    resume_document_id: uuid.UUID | None
    profile_review_status: str
    archived: bool
    signals: list[str]
    fingerprint: str
    changes: list[ResumeMergeField]


@dataclass(frozen=True)
class ImportItemCandidates:
    """Plan item 111: a resume item's candidates, the fingerprint of the list
    (sent back with either decision), and whether a strong signal matched
    (then import as a new person is refused, Q3)."""

    fingerprint: str
    candidates: list[ImportItemCandidate]
    strong_match: bool


async def list_resume_candidates(batch_id: uuid.UUID) -> dict[uuid.UUID, ImportItemCandidates]:
    """Plan item 111: the candidates of each resume item in review whose
    resume and profile can be read, by item id. Other import types, decided
    items, and items with no document or an unreadable profile have none
    (they can only be rejected, as before)."""
    async with UnitOfWork() as uow:
        batch = await uow.knowledge.get_import_batch(batch_id)
        if batch is None or batch.import_type != "bulk_resumes":
            return {}
        items = await uow.knowledge.list_import_items_for_batch(batch_id)
        employees = await uow.knowledge.find_employee_duplicate_candidates(batch.organization_id)
        out: dict[uuid.UUID, ImportItemCandidates] = {}
        for item in items:
            profile = _item_profile(item)
            if item.status != "needs_review" or item.source_document_id is None or profile is None:
                continue
            matches = _resume_matches(item, profile, employees)
            archived = await uow.knowledge.archived_item_ids(
                employee.knowledge_item_id for employee, _ in matches
            )
            out[item.id] = ImportItemCandidates(
                fingerprint=resume_candidates_fingerprint(matches),
                candidates=[
                    ImportItemCandidate(
                        knowledge_item_id=employee.knowledge_item_id,
                        name=employee.name,
                        email=employee.email,
                        current_title=employee.current_title,
                        professional_location=employee.professional_location,
                        years_experience=(
                            float(employee.years_experience)
                            if employee.years_experience is not None
                            else None
                        ),
                        resume_document_id=employee.resume_document_id,
                        profile_review_status=employee.profile_review_status,
                        archived=employee.knowledge_item_id in archived,
                        signals=list(signals),
                        fingerprint=resume_target_fingerprint(employee),
                        changes=resume_merge_fields(
                            employee,
                            profile,
                            emails_held_by_others(employees, employee.knowledge_item_id),
                        ),
                    )
                    for employee, signals in matches
                ],
                strong_match=any(STRONG_SIGNALS.intersection(s) for _, s in matches),
            )
        return out


@dataclass(frozen=True)
class MasterTarget:
    """Plan item 112: the employee a master item names, as the review panel
    shows it: the values, the status, the fingerprint Approve sends back, and
    what approving writes (None for a payload that does not read as a row)."""

    knowledge_item_id: uuid.UUID
    name: str
    employee_code: str | None
    current_title: str | None
    status: str
    archived: bool
    fingerprint: str
    changes: list[MasterApplyField] | None


@dataclass(frozen=True)
class MasterCandidate:
    """Plan item 112: one employee a master item in review matches (the
    code, the name), as ImportItemCandidate for resumes."""

    knowledge_item_id: uuid.UUID
    name: str
    employee_code: str | None
    current_title: str | None
    status: str
    archived: bool
    signals: list[str]
    fingerprint: str
    changes: list[MasterApplyField]


@dataclass(frozen=True)
class MasterCandidates:
    """A master item's candidates, the list's fingerprint (sent back with a
    pick or import as new), and whether import as new is refused: a code
    matched, or an employee already has the row's NEW fingerprint."""

    fingerprint: str
    candidates: list[MasterCandidate]
    strong_match: bool


async def list_master_review(
    batch_id: uuid.UUID,
) -> tuple[dict[uuid.UUID, MasterTarget], dict[uuid.UUID, MasterCandidates]]:
    """Plan item 112: for an employee-master batch, the employee each item
    names (any status), and the candidates of each item in review whose
    payload reads as a row, by item id. Other import types get neither."""
    async with UnitOfWork() as uow:
        batch = await uow.knowledge.get_import_batch(batch_id)
        if batch is None or batch.import_type not in MASTER_IMPORT_TYPES:
            return {}, {}
        items = await uow.knowledge.list_import_items_for_batch(batch_id)
        employees = await uow.knowledge.find_employee_duplicate_candidates(batch.organization_id)
        by_id = {employee.knowledge_item_id: employee for employee in employees}
        # Statuses only for the employees shown: the targets and candidates.
        shown_ids: set[uuid.UUID] = set()
        for item in items:
            if item.target_knowledge_item_id in by_id:
                shown_ids.add(item.target_knowledge_item_id)
            row = master_row_from_payload(item.extracted_payload)
            if item.status == "needs_review" and row is not None:
                shown_ids.update(e.knowledge_item_id for e, _ in _master_matches(row, employees))
        statuses = await _item_statuses(uow, sorted(shown_ids))
        targets: dict[uuid.UUID, MasterTarget] = {}
        candidates: dict[uuid.UUID, MasterCandidates] = {}
        for item in items:
            row = master_row_from_payload(item.extracted_payload)
            target = (
                by_id.get(item.target_knowledge_item_id) if item.target_knowledge_item_id else None
            )
            if target is not None:
                status = statuses.get(target.knowledge_item_id, "")
                targets[item.id] = MasterTarget(
                    knowledge_item_id=target.knowledge_item_id,
                    name=target.name,
                    employee_code=target.employee_code,
                    current_title=target.current_title,
                    status=status,
                    archived=status == KNOWLEDGE_ITEM_ARCHIVED,
                    fingerprint=master_target_fingerprint(target, status),
                    changes=(
                        master_apply_fields(
                            target,
                            status,
                            row,
                            codes_held_by_others(employees, target.knowledge_item_id),
                        )
                        if row is not None
                        else None
                    ),
                )
            if item.status != "needs_review" or row is None:
                continue
            matches = _master_matches(row, employees)
            made_before = await uow.knowledge.get_knowledge_item_by_fingerprint(
                batch.organization_id,
                "employee",
                master_employee_fingerprint(batch.organization_id, row),
            )
            candidates[item.id] = MasterCandidates(
                fingerprint=master_candidates_fingerprint(matches, statuses),
                candidates=[
                    MasterCandidate(
                        knowledge_item_id=employee.knowledge_item_id,
                        name=employee.name,
                        employee_code=employee.employee_code,
                        current_title=employee.current_title,
                        status=statuses.get(employee.knowledge_item_id, ""),
                        archived=statuses.get(employee.knowledge_item_id)
                        == KNOWLEDGE_ITEM_ARCHIVED,
                        signals=list(signals),
                        fingerprint=master_target_fingerprint(
                            employee, statuses.get(employee.knowledge_item_id, "")
                        ),
                        changes=master_apply_fields(
                            employee,
                            statuses.get(employee.knowledge_item_id, ""),
                            row,
                            codes_held_by_others(employees, employee.knowledge_item_id),
                        ),
                    )
                    for employee, signals in matches
                ],
                strong_match=(
                    any(STRONG_SIGNALS.intersection(s) for _, s in matches)
                    or made_before is not None
                ),
            )
        return targets, candidates


@dataclass(frozen=True)
class CertificateHolder:
    """Plan item 112: one live employee a certification item can be given
    to, with the fingerprint Approve sends back with the choice."""

    knowledge_item_id: uuid.UUID
    name: str
    employee_code: str | None
    email: str | None
    current_title: str | None
    fingerprint: str


async def list_certificate_holders(batch_id: uuid.UUID) -> list[CertificateHolder] | None:
    """Plan item 112 (Q7 a): the organization's live employees, by name, for
    a certification batch's holder choice; [] for other import types, None
    for no such batch. Archived employees are left out: removed from the
    roster, they hold no new certificate (approval refuses one, 409
    EMPLOYEE_ARCHIVED)."""
    async with UnitOfWork() as uow:
        batch = await uow.knowledge.get_import_batch(batch_id)
        if batch is None:
            return None
        if batch.import_type != "certification_documents":
            return []
        employees = await uow.knowledge.find_employee_duplicate_candidates(batch.organization_id)
        archived = await uow.knowledge.archived_item_ids(
            employee.knowledge_item_id for employee in employees
        )
        return [
            CertificateHolder(
                knowledge_item_id=employee.knowledge_item_id,
                name=employee.name,
                employee_code=employee.employee_code,
                email=employee.email,
                current_title=employee.current_title,
                fingerprint=certificate_holder_fingerprint(employee, False),
            )
            for employee in sorted(
                employees, key=lambda e: (e.name.casefold(), str(e.knowledge_item_id))
            )
            if employee.knowledge_item_id not in archived
        ]


async def list_knowledge_selections(
    tender_id: uuid.UUID, selected_only: bool = False
) -> list[TenderKnowledgeSelection]:
    async with UnitOfWork() as uow:
        return await uow.knowledge.list_selections_for_tender(tender_id, selected_only)


async def set_knowledge_selection(
    tender_id: uuid.UUID,
    knowledge_item_id: uuid.UUID,
    selected: bool,
    actor_user_id: uuid.UUID,
    change_reason: str | None = None,
) -> TenderKnowledgeSelection:
    """G6's PUT /tenders/{tender_id}/knowledge-selections/{knowledge_item_id}.
    The Build Guide's full selection-change flow (preview the impact, the
    user confirms, write the selection, bump the state version, emit
    ImpactReanalysisRequested through the outbox, re-project, re-evaluate
    only what was affected) needed
    graph projection (B10) and targeted re-evaluation (B14) to exist first -
    both now do. B14 wires the last two steps in: this call writes a
    KnowledgeSelectionChanged outbox event in the same transaction as the
    state_version bump below (Part C's own state-version invariant list -
    see KNOWN_LIMITATIONS.md for which of the six mutation types still have
    no bump call site), which outbox_dispatcher.py routes to
    reanalyze_knowledge_selection_change - the actual projection +
    targeted-reevaluation job, apps/worker/jobs/reanalysis.py.

    Verifies actor_user_id's own membership+role against tender_id's real
    organization_id, fetched right here - it does not take organization_id
    as a parameter and trust a caller to have derived it correctly. An
    earlier version of this check accepted organization_id as an argument
    and compared it against tender.organization_id; every caller (the
    route included) only ever had one way to produce that argument -
    re-deriving it from this same tender_id - so the comparison was always
    true and caught nothing. Checking actor_user_id's actual membership
    instead means this function stays correct regardless of what a future
    caller (a different route, a worker, admin tooling) passes in, rather
    than depending on every future caller re-implementing the route's own
    authorization resolution correctly.
    """
    async with UnitOfWork() as uow:
        tender = await uow.tenders.get(tender_id)
        if tender is None:
            raise TenderNotFoundError()
        membership = await uow.memberships.get(tender.organization_id, actor_user_id)
        if (
            membership is None
            or Role(membership.role) not in ACTION_ROLES["manage_organization_knowledge"]
        ):
            logger.warning(
                "unauthorized knowledge selection attempt: actor=%s tender=%s (org=%s)",
                actor_user_id,
                tender_id,
                tender.organization_id,
            )
            raise CrossOrganizationTenderError()
        knowledge_item = await uow.knowledge.get_knowledge_item(knowledge_item_id)
        if knowledge_item is None:
            raise KnowledgeItemNotFoundError()
        # tender_knowledge_selections' two foreign keys are independent, so
        # nothing at the schema level stops org A's tender from selecting
        # org B's knowledge item - checked here instead. Left unchecked, the
        # graph projector (projector.py's reachable-shared-node walk) would
        # MERGE org B's employee/certification/project node straight into
        # org A's tender graph, and D7/proposal drafting could cite it as
        # supporting evidence for an org A claim.
        if knowledge_item.organization_id != tender.organization_id:
            # The route collapses this into the same generic 404 a
            # nonexistent knowledge_item_id gets - a caller must not be
            # able to distinguish "doesn't exist" from "exists in another
            # organization" by watching which error comes back (that
            # distinction alone would let a caller enumerate valid
            # knowledge-item ids across organizations). Logged here, at the
            # only point that still knows the real reason, precisely
            # because a cross-org access attempt is worth recording as a
            # security-relevant event, not just for debugging - the point
            # is that the CALLER can't tell the two apart, not that this
            # service stops knowing.
            logger.warning(
                "cross-organization knowledge selection attempt: actor=%s "
                "tender=%s (org=%s) knowledge_item=%s (org=%s)",
                actor_user_id,
                tender_id,
                tender.organization_id,
                knowledge_item_id,
                knowledge_item.organization_id,
            )
            raise CrossOrganizationKnowledgeItemError()

        updated = await record_knowledge_selection_in_uow(
            uow, tender, knowledge_item_id, selected, actor_user_id, change_reason
        )
        await uow.commit()
        return updated


async def record_knowledge_selection_in_uow(
    uow: UnitOfWork,
    tender: Tender,
    knowledge_item_id: uuid.UUID,
    selected: bool,
    actor_user_id: uuid.UUID,
    change_reason: str | None = None,
) -> TenderKnowledgeSelection:
    """The transaction-open, no-commit core of set_knowledge_selection: the
    selection row, a new state version and its KnowledgeSelectionChanged
    event, for a caller that has already checked access.

    increment_state_version, not bump_state_version (plan item 95): the new
    version is this event's identity (its outbox key, the reanalysis job id
    and the reanalysis claim are keyed on it), so two selection changes in
    one transaction must each move it, or the second event would share the
    first's key and be dropped as a duplicate. Today every selection change
    commits on its own (set_knowledge_selection, one per request), but
    nothing structural stops a batch.
    """
    updated = await uow.knowledge.upsert_selection(
        tender.id, knowledge_item_id, selected, actor_user_id, change_reason
    )
    await uow.tenders.increment_state_version(tender.id)
    # tender is the same ORM instance increment_state_version's own
    # uow.session.get(Tender, tender_id) call resolved (one session,
    # one identity map for this whole UnitOfWork) - its state_version
    # already reflects the increment above with no extra query needed.
    await uow.outbox.create(
        aggregate_type=AGGREGATE_KNOWLEDGE_SELECTION,
        aggregate_id=updated.id,
        event_type=EVENT_KNOWLEDGE_SELECTION_CHANGED,
        payload=knowledge_selection_changed_payload(
            tender.id, knowledge_item_id, selected, tender.state_version
        ),
        idempotency_key=knowledge_selection_changed_idempotency_key(
            tender.id, tender.state_version
        ),
    )
    return updated


async def replace_employee_resume(
    knowledge_item_id: uuid.UUID,
    organization_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    filename: str,
    content: bytes,
    mime_type: str | None,
) -> Employee:
    """G6's POST /employees/{id}/resume - B6's smoke test: a replaced resume
    keeps its earlier document version. Creates a genuinely new
    Document row (version = old + 1, supersedes_document_id = old.id) for
    genuinely new content - the prior version's row is never touched, only
    superseded, so it stays queryable for anyone tracing what an old
    extraction was actually based on.

    Checks get_by_org_and_hash first, though: documents.uq_org_document_hash
    (Part C, locked) forbids two rows sharing (organization_id, sha256)
    regardless of filename or caller intent - a naive "always insert" here
    would raise IntegrityError the moment a "replacement" happens to be
    byte-identical to something already on file for this org (caught live,
    B6's own Docker smoke test: re-uploading the same resume bytes under a
    new filename). Same content reuses the existing document row rather
    than trying to duplicate it - re-extraction still gets queued either
    way, since re-running it against the same content is safe/idempotent.
    """
    async with UnitOfWork() as uow:
        employee = await uow.knowledge.get_employee(knowledge_item_id)
        if employee is None:
            raise EmployeeNotFoundError()
        previous_document_id = employee.resume_document_id
        previous_version = 1
        if previous_document_id is not None:
            previous = await uow.session.get(Document, previous_document_id)
            if previous is not None:
                previous_version = previous.version

        sha256 = sha256_hex(content)
        existing = await uow.documents.get_by_org_and_hash(organization_id, sha256)
        if existing is not None:
            document = existing
        else:
            storage_key = f"organizations/{organization_id}/{sha256}/{filename}"
            await storage.put(storage_key, content, content_type=mime_type)
            document = await uow.documents.create(
                side="company",
                doc_type="resume",
                filename=filename,
                storage_key=storage_key,
                sha256=sha256,
                mime_type=mime_type,
                file_size_bytes=len(content),
                organization_id=organization_id,
                version=previous_version + 1,
                supersedes_document_id=previous_document_id,
            )

        updated = await uow.knowledge.update_employee(
            knowledge_item_id, resume_document_id=document.id
        )

        batch = await uow.knowledge.create_import_batch(
            organization_id=organization_id,
            import_type="bulk_resumes",
            source_document_id=document.id,
            created_by=actor_user_id,
        )

        await uow.outbox.create(
            aggregate_type=AGGREGATE_DOCUMENT,
            aggregate_id=document.id,
            event_type=EVENT_KNOWLEDGE_DOCUMENT_UPLOADED,
            payload=knowledge_document_uploaded_payload(document.id, batch.id),
            # Fresh request_id always, not just when `existing` was reused
            # above - simpler than branching, and harmless for the
            # genuinely-new-document case (a brand new document_id can
            # never collide regardless of suffix).
            idempotency_key=knowledge_document_uploaded_idempotency_key(document.id, uuid.uuid4()),
        )
        await uow.audit.create(
            organization_id=organization_id,
            actor_type="user",
            actor_id=str(actor_user_id),
            action="employee_resume_replace",
            target_type="employee",
            target_id=knowledge_item_id,
            before={
                "resume_document_id": str(previous_document_id) if previous_document_id else None
            },
            after={"resume_document_id": str(document.id)},
        )
        await uow.commit()
        return updated
