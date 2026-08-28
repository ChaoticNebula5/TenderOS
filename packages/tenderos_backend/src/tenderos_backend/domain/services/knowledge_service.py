import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime

from tenderos_backend.agents.knowledge.certifications_xlsx import CertificationRow
from tenderos_backend.agents.knowledge.dedup import (
    DuplicateVerdict,
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
from tenderos_backend.core.hashing import sha256_hex
from tenderos_backend.db.models.document import Document, DocumentChunk
from tenderos_backend.db.models.knowledge import (
    Employee,
    KnowledgeImportBatch,
    KnowledgeImportItem,
    KnowledgeItem,
    KnowledgeRelationship,
    TenderKnowledgeSelection,
)
from tenderos_backend.db.storage import storage
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.events.outbox_events import (
    AGGREGATE_DOCUMENT,
    AGGREGATE_KNOWLEDGE_SELECTION,
    EVENT_KNOWLEDGE_DOCUMENT_UPLOADED,
    EVENT_KNOWLEDGE_SELECTION_CHANGED,
    knowledge_document_uploaded_idempotency_key,
    knowledge_document_uploaded_payload,
    knowledge_selection_changed_idempotency_key,
    knowledge_selection_changed_payload,
)
from tenderos_backend.domain.policies.rbac import ACTION_ROLES
from tenderos_backend.ingestion.chunk import normalize_text
from tenderos_backend.ingestion.embed import embed_texts


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
    "removed": "archived",
}


logger = logging.getLogger(__name__)


class KnowledgeItemNotFoundError(Exception):
    pass


class EmployeeNotFoundError(Exception):
    pass


class ImportItemNotFoundError(Exception):
    pass


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


async def _create_synthetic_chunk(
    uow: UnitOfWork, source_document_id: uuid.UUID, text: str
) -> DocumentChunk:
    """Retrieval is built entirely on document_chunks (Part C gives
    knowledge_items no embedding column of its own), but mechanically
    parsed rows (employee-master, certifications.xlsx) never go through
    B4's parse->chunk->embed pipeline - there's no prose to chunk, just
    spreadsheet rows. This does for one structured row what that pipeline
    does for prose: render it to text, embed it, and store it as a real
    document_chunks row against the already-uploaded source document, so a
    knowledge_item built from a row can still set a real source_chunk_id
    and be found by retrieval. chunk_index is derived from the existing
    chunk count for source_document_id - safe for this codebase's
    sequential (not concurrent) import scripts, same caveat B4's own
    _persist_chunks never needed to consider for a single document's chunks.
    """
    normalized = normalize_text(text)
    existing_count = len(await uow.documents.list_chunks_for_document(source_document_id))
    embedding = (await embed_texts([normalized]))[0]
    return await uow.documents.create_chunk(
        document_id=source_document_id,
        chunk_index=existing_count,
        chunk_text=text,
        normalized_text=normalized,
        content_hash=sha256_hex(normalized.encode("utf-8")),
        embedding=embedding,
    )


async def import_employee_master_row(
    organization_id: uuid.UUID, batch_id: uuid.UUID, row: EmployeeMasterRow
) -> KnowledgeImportItem:
    """One employee_master.xlsx row -> one knowledge_import_items row,
    dedup-adjudicated via agents/knowledge/dedup.py. A duplicate/ambiguous
    match is a normal outcome recorded on the import item (needs_review),
    not a raised error - only a genuine persistence failure propagates.
    """
    source_ref = f"row_{row.row_number}"
    async with UnitOfWork() as uow:
        # A retried worker job re-parses the same file and re-walks every
        # row from the start - without this check, any row already
        # committed by a prior attempt hits uq_knowledge_import_item_
        # source_ref and crashes the retry outright, instead of resuming
        # past whatever already succeeded. See KNOWN_LIMITATIONS.md.
        existing = await uow.knowledge.get_import_item_by_source_ref(batch_id, source_ref)
        if existing is not None:
            return existing

        import_item = await uow.knowledge.create_import_item(
            batch_id=batch_id,
            source_ref=source_ref,
            extracted_payload={
                "employee_code": row.employee_code,
                "name": row.name,
                "role": row.role,
                "status": row.status,
            },
        )

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
            # file - unlike a resume's free-text self-report (import_resume
            # below), which corroborates rather than overwrites.
            await uow.knowledge.update_employee(
                target_id, current_title=row.role or resolution.matched_employee.current_title
            )
            if item_status:
                await uow.knowledge.update_knowledge_item(target_id, status=item_status)
            updated = await uow.knowledge.update_import_item(
                import_item.id, status="approved", target_knowledge_item_id=target_id
            )
            await uow.commit()
            return updated

        # NEW
        fingerprint = _fingerprint(
            str(organization_id), row.employee_code or "", normalize_employee_name(row.name)
        )
        knowledge_item, _employee = await uow.knowledge.create_employee(
            organization_id=organization_id,
            name=row.name,
            content_fingerprint=fingerprint,
            title=row.name,
            employee_code=row.employee_code,
            current_title=row.role,
        )
        if item_status:
            await uow.knowledge.update_knowledge_item(knowledge_item.id, status=item_status)
        updated = await uow.knowledge.update_import_item(
            import_item.id, status="approved", target_knowledge_item_id=knowledge_item.id
        )
        await uow.commit()
        return updated


async def import_resume(
    organization_id: uuid.UUID,
    batch_id: uuid.UUID,
    source_ref: str,
    resume_document_id: uuid.UUID,
    profile: ExtractedEmployeeProfile,
) -> KnowledgeImportItem:
    """One resume's already-extracted profile (agents/knowledge/extract.py's
    output) -> one knowledge_import_items row, same dedup-adjudication shape
    as import_employee_master_row above. certification_mentions/
    project_mentions ride along inside parsed_profile as claims for a
    reviewer to see - they do not create certifications/past_projects rows
    themselves (see ExtractedCertificationMention/ExtractedProjectMention's
    own docstrings for why).
    """
    async with UnitOfWork() as uow:
        import_item = await uow.knowledge.create_import_item(
            batch_id=batch_id, source_ref=source_ref, extracted_payload=profile.model_dump()
        )

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
            await uow.knowledge.update_employee(
                target_id,
                email=profile.email or resolution.matched_employee.email,
                current_title=profile.current_title or resolution.matched_employee.current_title,
                professional_location=(
                    profile.professional_location
                    or resolution.matched_employee.professional_location
                ),
                years_experience=(
                    profile.years_experience
                    if profile.years_experience is not None
                    else resolution.matched_employee.years_experience
                ),
                resume_document_id=resume_document_id,
                parsed_profile=profile.model_dump(),
            )
            updated = await uow.knowledge.update_import_item(
                import_item.id, status="approved", target_knowledge_item_id=target_id
            )
            await uow.commit()
            return updated

        # NEW
        fingerprint = _fingerprint(str(organization_id), "resume", str(resume_document_id))
        knowledge_item, _employee = await uow.knowledge.create_employee(
            organization_id=organization_id,
            name=profile.name,
            content_fingerprint=fingerprint,
            title=profile.name,
            email=profile.email,
            current_title=profile.current_title,
            professional_location=profile.professional_location,
            years_experience=profile.years_experience,
            resume_document_id=resume_document_id,
            source_document_id=resume_document_id,
            parsed_profile=profile.model_dump(),
        )
        updated = await uow.knowledge.update_import_item(
            import_item.id, status="approved", target_knowledge_item_id=knowledge_item.id
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
    """
    async with UnitOfWork() as uow:
        import_item = await uow.knowledge.create_import_item(
            batch_id=batch_id,
            source_ref=source_ref,
            extracted_payload={
                "name": project.name,
                "client": project.client,
                "year": project.year,
                "description": project.description,
                "source_ref": project.source_ref,
                "chunk_id": str(project.chunk_id),
            },
        )
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
    shape as import_past_project above."""
    async with UnitOfWork() as uow:
        import_item = await uow.knowledge.create_import_item(
            batch_id=batch_id,
            source_ref=source_ref,
            extracted_payload={
                "name": capability.name,
                "description": capability.description,
                "source_ref": capability.source_ref,
                "chunk_id": str(capability.chunk_id),
            },
        )
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
    document (required, not resolved here) - _create_synthetic_chunk below
    needs a real document to attach a chunk to.
    """
    source_ref = f"row_{row.row_number}"
    async with UnitOfWork() as uow:
        # Same retry-resume reasoning as import_employee_master_row. Named
        # distinctly from `existing` below (the fingerprint-based dedup
        # lookup, a KnowledgeItem | None) - same function, two different
        # "does this already exist" checks against two different types.
        existing_item = await uow.knowledge.get_import_item_by_source_ref(batch_id, source_ref)
        if existing_item is not None:
            return existing_item

        import_item = await uow.knowledge.create_import_item(
            batch_id=batch_id,
            source_ref=source_ref,
            extracted_payload={
                "cert_key": row.cert_key,
                "employee_ref": row.employee_ref,
                "name": row.name,
                "issuer": row.issuer,
                "issue_date": row.issue_date.isoformat() if row.issue_date else None,
                "expiry_date": row.expiry_date.isoformat() if row.expiry_date else None,
            },
        )

        holder: uuid.UUID | None = None
        # TODO(human): handle a blank employee cell (row.employee_ref is
        # None) - currently falls straight through the `if row.employee_ref`
        # check below with holder left at None, which lets the function
        # continue on to create an APPROVED certification with no holder at
        # all (KNOWN_LIMITATIONS.md: "a certification row with a blank
        # employee cell creates an approved, holderless certification
        # instead of routing to review").
        #
        # Steps (plain-English, not code):
        #   1. Before the `if row.employee_ref:` block below, check whether
        #      row.employee_ref is falsy (None or blank - the parser already
        #      collapses an empty cell to None, so a plain truthiness check
        #      is enough).
        #   2. If it is, decide the review outcome for this row and call
        #      uow.knowledge.update_import_item(import_item.id, status=...,
        #      error_message=...) - same call shape as the sibling
        #      "employee_ref given but not found" branch a few lines below,
        #      which you can use as a direct template.
        #   3. await uow.commit() and return the updated item, same as that
        #      sibling branch - do not fall through to the certification-
        #      creation code further down.
        #
        # What's already available:
        #   - `row: CertificationRow` - .employee_ref is None here (that's
        #     why you're in this branch), plus .row_number, .name, etc.
        #   - `import_item` - already created above, in this same open
        #     transaction, not yet committed.
        #   - `uow` - the same UnitOfWork the "not found" branch below uses.
        #
        # The specific edge case worth deciding by hand: the status
        # vocabulary (Part C's CHECK constraint on knowledge_import_items.
        # status) has no value more specific than "needs_review" - it
        # cannot distinguish "no employee was ever specified" from
        # "employee_ref was given but doesn't match anyone" at the status
        # level. The only place that distinction CAN live is the
        # error_message text, which means whether to preserve it (and how)
        # is a real, not-obvious choice, not boilerplate - the "not found"
        # branch's own message ("no employee found with employee_code...")
        # would be actively misleading reused verbatim for a row that never
        # had a code typed in at all.
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
        holder = employee.knowledge_item_id

        fingerprint = _fingerprint(
            str(organization_id),
            str(holder),
            row.name,
            row.issue_date.isoformat() if row.issue_date else "",
            row.expiry_date.isoformat() if row.expiry_date else "",
        )
        existing = await uow.knowledge.get_knowledge_item_by_fingerprint(
            organization_id, "certification", fingerprint
        )
        if existing is not None:
            updated = await uow.knowledge.update_import_item(
                import_item.id, status="approved", target_knowledge_item_id=existing.id
            )
            await uow.commit()
            return updated

        item_status = "expired" if row.expiry_date and row.expiry_date < today else "active"
        chunk_text = (
            f"{row.name}, issued by {row.issuer or 'an unspecified issuer'}, "
            f"valid from {row.issue_date.isoformat() if row.issue_date else 'an unspecified date'} "
            f"to {row.expiry_date.isoformat() if row.expiry_date else 'an unspecified date'}."
        )
        chunk = await _create_synthetic_chunk(uow, source_document_id, chunk_text)
        knowledge_item, _certification = await uow.knowledge.create_certification(
            organization_id=organization_id,
            title=row.name,
            content_fingerprint=fingerprint,
            holder_employee_item_id=holder,
            issuer=row.issuer,
            valid_from=row.issue_date,
            valid_until=row.expiry_date,
            source_document_id=source_document_id,
            source_chunk_id=chunk.id,
            status=item_status,
        )
        updated = await uow.knowledge.update_import_item(
            import_item.id, status="approved", target_knowledge_item_id=knowledge_item.id
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
        item = await uow.knowledge.get_knowledge_item(knowledge_item_id)
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
    "new-tender candidate search sees only verified bid-eligible profiles"
    (B6 smoke test). Backed by KnowledgeRepository.list_bid_eligible_employees,
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
    own "do not proceed if": imported profiles become bid-eligible without
    human approval).
    """
    async with UnitOfWork() as uow:
        employee = await uow.knowledge.get_employee(knowledge_item_id)
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
        await uow.commit()
        return updated


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
    B6's "imported profiles become bid-eligible without human approval"
    do-not-proceed-if item by construction: there is no path to eligibility
    that does not go through this reviewer-invoked action.
    """
    async with UnitOfWork() as uow:
        employee = await uow.knowledge.get_employee(knowledge_item_id)
        if employee is None:
            raise EmployeeNotFoundError()

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
        computed_status = "queued"
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
    item_id: uuid.UUID, organization_id: uuid.UUID, actor_user_id: uuid.UUID
) -> KnowledgeImportItem:
    """G6's POST /knowledge/import-items/{id}/approve - for an item sitting
    at needs_review (a dedup match the deterministic policy couldn't
    resolve on its own), this confirms the existing target_knowledge_item_id
    resolution is correct (e.g. "yes, this resume really is the same Dana
    Whitfield already on file"). It does not create a new knowledge_item -
    approving a needs_review row means agreeing with the match it already
    proposed, not retrying the import as a fresh one.
    """
    async with UnitOfWork() as uow:
        item = await uow.knowledge.get_import_item(item_id)
        if item is None:
            raise ImportItemNotFoundError()
        before = {"status": item.status}
        updated = await uow.knowledge.update_import_item(item_id, status="approved")
        await uow.audit.create(
            organization_id=organization_id,
            actor_type="user",
            actor_id=str(actor_user_id),
            action="knowledge_import_item_approve",
            target_type="knowledge_import_item",
            target_id=item_id,
            before=before,
            after={"status": updated.status},
        )
        await uow.commit()
        return updated


async def reject_import_item(
    item_id: uuid.UUID, organization_id: uuid.UUID, actor_user_id: uuid.UUID, reason: str | None
) -> KnowledgeImportItem:
    """Rejects a needs_review (or failed) import row outright - the
    reviewer decided this row should not become or update a knowledge_item
    at all (e.g. "no, that's a different Dana Whitfield, don't merge").
    Does not delete anything already created; if a needs_review row's
    target_knowledge_item_id pointed at an existing employee, that employee
    is untouched - only this import row's own status changes.
    """
    async with UnitOfWork() as uow:
        item = await uow.knowledge.get_import_item(item_id)
        if item is None:
            raise ImportItemNotFoundError()
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
    The Build Guide's full selection-change flow (impact preview -> confirm
    -> update selection -> bump state version -> outbox
    ImpactReanalysisRequested -> projection -> targeted re-evaluation) needed
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
        if membership is None or Role(membership.role) not in ACTION_ROLES["manage_organization_knowledge"]:
            logger.warning(
                "unauthorized knowledge selection attempt: actor=%s tender=%s (org=%s)",
                actor_user_id, tender_id, tender.organization_id,
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
                actor_user_id, tender_id, tender.organization_id,
                knowledge_item_id, knowledge_item.organization_id,
            )
            raise CrossOrganizationKnowledgeItemError()

        updated = await uow.knowledge.upsert_selection(
            tender_id, knowledge_item_id, selected, actor_user_id, change_reason
        )
        await uow.tenders.bump_state_version(tender_id)
        # tender is the same ORM instance bump_state_version's own
        # uow.session.get(Tender, tender_id) call resolved (one session,
        # one identity map for this whole UnitOfWork) - its state_version
        # already reflects the increment above with no extra query needed.
        await uow.outbox.create(
            aggregate_type=AGGREGATE_KNOWLEDGE_SELECTION,
            aggregate_id=updated.id,
            event_type=EVENT_KNOWLEDGE_SELECTION_CHANGED,
            payload=knowledge_selection_changed_payload(
                tender_id, knowledge_item_id, selected, tender.state_version
            ),
            idempotency_key=knowledge_selection_changed_idempotency_key(
                tender_id, tender.state_version
            ),
        )
        await uow.commit()
        return updated


async def replace_employee_resume(
    knowledge_item_id: uuid.UUID,
    organization_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    filename: str,
    content: bytes,
    mime_type: str | None,
) -> Employee:
    """G6's POST /employees/{id}/resume - "replacing a resume preserves the
    prior document version" (B6 smoke test). Creates a genuinely new
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
