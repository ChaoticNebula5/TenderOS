import uuid
from dataclasses import dataclass

from sqlalchemy import exists, select

from tenderos_backend.core.hashing import sha256_hex
from tenderos_backend.db.models.agent_trace import AgentTrace
from tenderos_backend.db.models.requirement import (
    ClarificationItem,
    Requirement,
    SubmissionItem,
)
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.events.outbox_events import (
    AGGREGATE_REQUIREMENT,
    EVENT_REQUIREMENT_VERIFICATION_REQUESTED,
    requirement_verification_requested_idempotency_key,
    requirement_verification_requested_payload,
)
from tenderos_backend.ingestion.chunk import normalize_text

_CATEGORY_PREFIX = {
    "technical": "TECH",
    "staffing": "STAFF",
    "experience": "EXP",
    "commercial": "COM",
    "eligibility": "ELIG",
    "submission": "SUB",
    "legal": "LEGAL",
    "security": "SEC",
    "other": "OTHER",
}


class RequirementNotFoundError(Exception):
    pass


class SubmissionItemNotFoundError(Exception):
    pass


class CrossTenderOperationError(Exception):
    """Raised by merge when the given requirement_ids don't all belong to the
    same tender - merging across tenders would violate the tenant invariant
    (Build Guide 'every referenced requirement... same tenant')."""


def _fingerprint(*parts: str) -> str:
    return sha256_hex("|".join(parts).encode("utf-8"))


@dataclass(frozen=True)
class RequirementDraft:
    """What the extraction agent hands back per requirement (agents/schemas
    mirrors this shape for LLM structured-output validation) - document_id
    travels alongside source_chunk_id because I2's fingerprint formula needs
    it and the batcher already knows it from the join query that built the
    batch, so persistence never needs a second lookup per item."""

    requirement_text: str
    category: str
    mandatory: bool
    source_chunk_id: uuid.UUID
    document_id: uuid.UUID
    confidence: float | None = None


@dataclass(frozen=True)
class SubmissionItemDraft:
    name: str
    mandatory: bool
    source_chunk_id: uuid.UUID | None = None


@dataclass(frozen=True)
class ClarificationDraft:
    question: str
    reason: str | None = None
    requirement_id: uuid.UUID | None = None


async def persist_extraction_results(
    tender_id: uuid.UUID, drafts: list[RequirementDraft]
) -> list[Requirement]:
    """Called once by orchestration/nodes/requirement.py's real extraction
    node, after the LLM call(s) return - never by an API route (line 538: no
    LLM/eval work inside a FastAPI request, and this is downstream of that
    same call). Fingerprint-based dedup means a retried workflow run replays
    safely: existing rows are returned as-is, only genuinely new content
    creates a row.
    """
    persisted: list[Requirement] = []
    async with UnitOfWork() as uow:
        for draft in drafts:
            normalized = normalize_text(draft.requirement_text)
            fingerprint = _fingerprint(
                str(draft.document_id), str(draft.source_chunk_id), normalized
            )
            existing = await uow.requirements.get_by_fingerprint(tender_id, fingerprint)
            if existing is not None:
                persisted.append(existing)
                continue

            category_count = len(
                [
                    r
                    for r in await uow.requirements.list_for_tender(
                        tender_id, include_rejected=True
                    )
                    if r.category == draft.category
                ]
            )
            display_code = (
                f"{_CATEGORY_PREFIX[draft.category]}-{category_count + 1:03d}"
            )

            created = await uow.requirements.create(
                tender_id=tender_id,
                display_code=display_code,
                requirement_text=draft.requirement_text,
                normalized_text=normalized,
                category=draft.category,
                mandatory=draft.mandatory,
                source_chunk_id=draft.source_chunk_id,
                content_fingerprint=fingerprint,
                confidence=draft.confidence,
            )
            persisted.append(created)
        await uow.commit()
    return persisted


async def persist_submission_items(
    tender_id: uuid.UUID, drafts: list[SubmissionItemDraft]
) -> list[SubmissionItem]:
    # No I2 formula is locked for submission items (I2 only names Requirement/
    # Evidence/Proposal claim/Red-team finding) - a checklist item usually has
    # no single source_chunk_id (nullable in schema) so its normalized name
    # is the only stable identity available; a tender-scoped hash of that is
    # sufficient since G5's checklist is inherently a short, distinct list.
    persisted: list[SubmissionItem] = []
    async with UnitOfWork() as uow:
        for draft in drafts:
            fingerprint = _fingerprint(normalize_text(draft.name))
            existing = await uow.requirements.get_submission_item_by_fingerprint(
                tender_id, fingerprint
            )
            if existing is not None:
                persisted.append(existing)
                continue
            created = await uow.requirements.create_submission_item(
                tender_id=tender_id,
                name=draft.name,
                mandatory=draft.mandatory,
                content_fingerprint=fingerprint,
                source_chunk_id=draft.source_chunk_id,
            )
            persisted.append(created)
        await uow.commit()
    return persisted


async def get_submission_item(item_id: uuid.UUID) -> SubmissionItem | None:
    async with UnitOfWork() as uow:
        return await uow.requirements.get_submission_item(item_id)


async def list_submission_items_for_tender(tender_id: uuid.UUID) -> list[SubmissionItem]:
    async with UnitOfWork() as uow:
        return await uow.requirements.list_submission_items_for_tender(tender_id)


async def patch_submission_item(
    item_id: uuid.UUID,
    organization_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    status: str,
    matched_document_id: uuid.UUID | None = None,
) -> SubmissionItem:
    """The write path readiness_service.py's blocker 4
    (missing_mandatory_submission_item) and redteam_checks.py's identical-
    named finding have both been waiting on since B5 -
    SubmissionRepository.update_submission_item existed with zero callers
    until now (KNOWN_LIMITATIONS.md: "No screen manages submission-item
    status"). Same before/after audit-event shape as patch_requirement
    above.

    Bumps tenders.state_version deliberately, not because Part D's
    projection contract requires it - SubmissionItem is not one of Part
    D1's 11 node labels and no D2 relationship touches it, confirmed by
    reading Part D directly, not assumed. The bump exists purely so a
    stale readiness_reports row (UNIQUE(tender_id, tender_state_version))
    doesn't silently keep reporting a blocker that's already cleared -
    readiness-report freshness, not a graph invariant. If this ever reads
    as wrong because Part D still shows no SubmissionItem node, that's
    not a contradiction to "fix" by removing the bump.
    """
    async with UnitOfWork() as uow:
        item = await uow.requirements.get_submission_item(item_id)
        if item is None:
            raise SubmissionItemNotFoundError()

        before = {
            "status": item.status,
            "matched_document_id": (
                str(item.matched_document_id) if item.matched_document_id else None
            ),
        }
        # matched_document_id=None means "don't touch this field" (see
        # `fields` below - it's only ever included when not None), not
        # "clear it." materially_changed has to honor that same meaning:
        # comparing None against an already-set item.matched_document_id
        # unconditionally would read a PATCH that only touches status as
        # having also changed the document, bumping state_version for a
        # field nothing actually wrote (CodeRabbit-caught).
        materially_changed = status != item.status or (
            matched_document_id is not None
            and matched_document_id != item.matched_document_id
        )

        fields: dict[str, object] = {"status": status}
        if matched_document_id is not None:
            fields["matched_document_id"] = matched_document_id
        updated = await uow.requirements.update_submission_item(item_id, **fields)

        after = {
            "status": updated.status,
            "matched_document_id": (
                str(updated.matched_document_id) if updated.matched_document_id else None
            ),
        }
        await uow.audit.create(
            organization_id=organization_id,
            actor_type="user",
            actor_id=str(actor_user_id),
            action="submission_item_status_change",
            tender_id=updated.tender_id,
            target_type="submission_item",
            target_id=updated.id,
            before=before,
            after=after,
        )

        # See docstring: deliberate for readiness-report freshness, not a
        # Part D projection requirement. Gated the same way compliance_
        # service.py gates its own bump (materially_changed) - a no-op
        # PATCH (same status, no new document) shouldn't mark an
        # otherwise-fresh graph/readiness state stale for nothing.
        if materially_changed:
            await uow.tenders.bump_state_version(updated.tender_id)

        await uow.commit()
        return updated


async def persist_clarifications(
    tender_id: uuid.UUID, drafts: list[ClarificationDraft]
) -> list[ClarificationItem]:
    persisted: list[ClarificationItem] = []
    async with UnitOfWork() as uow:
        for draft in drafts:
            fingerprint = _fingerprint(
                str(draft.requirement_id) if draft.requirement_id else "none",
                normalize_text(draft.question),
            )
            existing = await uow.requirements.get_clarification_item_by_fingerprint(
                tender_id, fingerprint
            )
            if existing is not None:
                persisted.append(existing)
                continue
            created = await uow.requirements.create_clarification_item(
                tender_id=tender_id,
                question=draft.question,
                reason=draft.reason,
                requirement_id=draft.requirement_id,
                content_fingerprint=fingerprint,
            )
            persisted.append(created)
        await uow.commit()
    return persisted


async def get_requirement(requirement_id: uuid.UUID) -> Requirement | None:
    async with UnitOfWork() as uow:
        return await uow.requirements.get(requirement_id)


async def list_requirements_for_tender(tender_id: uuid.UUID) -> list[Requirement]:
    async with UnitOfWork() as uow:
        return await uow.requirements.list_for_tender(tender_id)


async def patch_requirement(
    requirement_id: uuid.UUID,
    organization_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    *,
    requirement_text: str | None = None,
    category: str | None = None,
    mandatory: bool | None = None,
    action: str | None = None,
    notes: str | None = None,
) -> Requirement:
    """G5's PATCH /requirements/{id} - covers PRD 10.3's confirm/edit/reject
    in one route, dispatched by `action`. Any field edit or a bare confirm
    (action=None or 'confirm', no field changes) sets human_reviewed=true;
    action='reject' soft-deletes per the plan's reject-semantics decision.
    Every call writes one audit_events row with a before/after snapshot -
    G5's 'all human edits write audit events' rule.
    """
    async with UnitOfWork() as uow:
        requirement = await uow.requirements.get(requirement_id)
        if requirement is None:
            raise RequirementNotFoundError()

        before = {
            "requirement_text": requirement.requirement_text,
            "category": requirement.category,
            "mandatory": requirement.mandatory,
            "human_reviewed": requirement.human_reviewed,
            "rejected_at": requirement.rejected_at.isoformat() if requirement.rejected_at else None,
        }

        if action == "reject":
            updated = await uow.requirements.reject(requirement_id)
        else:
            fields: dict[str, object] = {"human_reviewed": True}
            if requirement_text is not None:
                fields["requirement_text"] = requirement_text
                fields["normalized_text"] = normalize_text(requirement_text)
            if category is not None:
                fields["category"] = category
            if mandatory is not None:
                fields["mandatory"] = mandatory
            updated = await uow.requirements.update_fields(requirement_id, **fields)

        after = {
            "requirement_text": updated.requirement_text,
            "category": updated.category,
            "mandatory": updated.mandatory,
            "human_reviewed": updated.human_reviewed,
            "rejected_at": updated.rejected_at.isoformat() if updated.rejected_at else None,
        }
        await uow.audit.create(
            organization_id=organization_id,
            actor_type="user",
            actor_id=str(actor_user_id),
            action=f"requirement_{action or 'edit'}",
            tender_id=updated.tender_id,
            target_type="requirement",
            target_id=updated.id,
            before=before,
            after=after,
            metadata={"notes": notes} if notes else None,
        )
        await uow.commit()
        return updated


async def merge_requirements(
    requirement_ids: list[uuid.UUID],
    merged_text: str,
    category: str,
    mandatory: bool,
    organization_id: uuid.UUID,
    actor_user_id: uuid.UUID,
) -> Requirement:
    async with UnitOfWork() as uow:
        originals: list[Requirement] = []
        for rid in requirement_ids:
            original = await uow.requirements.get(rid)
            if original is None:
                raise RequirementNotFoundError()
            originals.append(original)
        tender_ids = {o.tender_id for o in originals}
        if len(tender_ids) != 1:
            raise CrossTenderOperationError()
        tender_id = tender_ids.pop()

        # Schema has one source_chunk_id per requirement - merging keeps the
        # first original's chunk as primary provenance (a real data-model
        # compromise, not an oversight; the full merged-from set is preserved
        # in this audit row's `before`, so provenance isn't actually lost,
        # just not queryable from the new row's own source_chunk_id column).
        primary = originals[0]
        fingerprint = _fingerprint(
            str(primary.tender_id), str(primary.source_chunk_id), normalize_text(merged_text)
        )
        category_count = len(
            [
                r
                for r in await uow.requirements.list_for_tender(tender_id, include_rejected=True)
                if r.category == category
            ]
        )
        merged = await uow.requirements.create(
            tender_id=tender_id,
            display_code=f"{_CATEGORY_PREFIX[category]}-{category_count + 1:03d}",
            requirement_text=merged_text,
            normalized_text=normalize_text(merged_text),
            category=category,
            mandatory=mandatory,
            source_chunk_id=primary.source_chunk_id,
            content_fingerprint=fingerprint,
        )
        merged = await uow.requirements.update_fields(merged.id, human_reviewed=True)

        before = {
            "merged_from": [
                {
                    "id": str(o.id),
                    "display_code": o.display_code,
                    "requirement_text": o.requirement_text,
                }
                for o in originals
            ]
        }
        for original in originals:
            await uow.requirements.delete(original.id)

        await uow.audit.create(
            organization_id=organization_id,
            actor_type="user",
            actor_id=str(actor_user_id),
            action="requirement_merge",
            tender_id=tender_id,
            target_type="requirement",
            target_id=merged.id,
            before=before,
            after={"id": str(merged.id), "requirement_text": merged.requirement_text},
        )
        await uow.commit()
        return merged


async def split_requirement(
    requirement_id: uuid.UUID,
    new_items: list[dict],
    organization_id: uuid.UUID,
    actor_user_id: uuid.UUID,
) -> list[Requirement]:
    async with UnitOfWork() as uow:
        original = await uow.requirements.get(requirement_id)
        if original is None:
            raise RequirementNotFoundError()

        created: list[Requirement] = []
        for item in new_items:
            text = item["requirement_text"]
            item_category = item.get("category", original.category)
            item_mandatory = item.get("mandatory", original.mandatory)
            fingerprint = _fingerprint(
                str(original.tender_id), str(original.source_chunk_id), normalize_text(text)
            )
            category_count = len(
                [
                    r
                    for r in await uow.requirements.list_for_tender(
                        original.tender_id, include_rejected=True
                    )
                    if r.category == item_category
                ]
            )
            new_requirement = await uow.requirements.create(
                tender_id=original.tender_id,
                display_code=f"{_CATEGORY_PREFIX[item_category]}-{category_count + 1:03d}",
                requirement_text=text,
                normalized_text=normalize_text(text),
                category=item_category,
                mandatory=item_mandatory,
                source_chunk_id=original.source_chunk_id,
                content_fingerprint=fingerprint,
            )
            new_requirement = await uow.requirements.update_fields(
                new_requirement.id, human_reviewed=True
            )
            created.append(new_requirement)

        before = {
            "split_from": {
                "id": str(original.id),
                "display_code": original.display_code,
                "requirement_text": original.requirement_text,
            }
        }
        await uow.requirements.delete(original.id)

        await uow.audit.create(
            organization_id=organization_id,
            actor_type="user",
            actor_id=str(actor_user_id),
            action="requirement_split",
            tender_id=original.tender_id,
            target_type="requirement",
            target_id=created[0].id,
            before=before,
            after={"split_into": [str(r.id) for r in created]},
        )
        await uow.commit()
        return created


async def request_verification(requirement_id: uuid.UUID) -> None:
    """G5's POST /requirements/{id}/verify - writes the outbox intent only.
    outbox_dispatcher.py routes RequirementVerificationRequested to the
    'extraction' queue; apps/worker/jobs/verification.py does the actual
    agent call. Never calls the LLM client itself (line 538).
    """
    request_id = uuid.uuid4()
    async with UnitOfWork() as uow:
        requirement = await uow.requirements.get(requirement_id)
        if requirement is None:
            raise RequirementNotFoundError()
        await uow.outbox.create(
            aggregate_type=AGGREGATE_REQUIREMENT,
            aggregate_id=requirement_id,
            event_type=EVENT_REQUIREMENT_VERIFICATION_REQUESTED,
            payload=requirement_verification_requested_payload(requirement_id),
            idempotency_key=requirement_verification_requested_idempotency_key(
                requirement_id, request_id
            ),
        )
        await uow.commit()


# The agent_name verify.py records its traces under
# (agents/requirements/verify.py, _AGENT_NAME).
_VERIFICATION_AGENT_NAME = "requirement_verification"


async def verification_recorded_for_event(requirement_id: uuid.UUID, event_id: uuid.UUID) -> bool:
    """Phase 4 consumer guard for verify_requirement_job: has a completed
    verification of this requirement already been recorded for this exact
    outbox event? Matching the event id, not "any verification since the
    event", matters: a second request made while an earlier verification is
    still running must not be skipped just because that earlier one finishes
    after it."""
    async with UnitOfWork() as uow:
        result = await uow.session.execute(
            select(
                exists().where(
                    AgentTrace.agent_name == _VERIFICATION_AGENT_NAME,
                    AgentTrace.status == "completed",
                    AgentTrace.input_refs["requirement_id"].astext == str(requirement_id),
                    AgentTrace.input_refs["event_id"].astext == str(event_id),
                )
            )
        )
        return bool(result.scalar())
