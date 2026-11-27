import re
import unicodedata
import uuid
from dataclasses import dataclass

from sqlalchemy import exists, select

from tenderos_backend.core.hashing import sha256_hex
from tenderos_backend.db.models.agent_trace import AgentTrace
from tenderos_backend.db.models.compliance import ComplianceEntry
from tenderos_backend.db.models.document import CHUNK_ORIGIN_PARSED, DocumentChunk
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
from tenderos_backend.ingestion.chunk import carried_overlap, normalize_text

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


class RepeatedRequirementError(Exception):
    """Raised by merge when one requirement id is given twice (plan item
    126): the first id is the survivor, so a repeat would delete it."""


class RequirementRejectedError(Exception):
    """Raised by merge and split when a requirement given is rejected (plan
    item 126): it is out of the bid, and is brought back (un-rejected) first.
    Merging it would carry it back in; splitting it in place would leave the
    first part rejected and the others live."""

    def __init__(self, display_codes: list[str]) -> None:
        super().__init__(", ".join(display_codes))
        self.display_codes = display_codes


@dataclass(frozen=True)
class ComplianceDecision:
    """One original's compliance decision, as a merge conflict names it."""

    display_code: str
    status: str
    decided_by: str
    human_approved: bool


class MergeComplianceConflictError(Exception):
    """Raised by merge when the originals' compliance decisions disagree
    (plan item 126, Q2 a): a different status or a different approval.
    Nothing is written; the reviewer makes them agree on the Compliance page
    and merges again."""

    def __init__(self, decisions: list[ComplianceDecision]) -> None:
        super().__init__(
            "; ".join(
                f"{d.display_code}: {d.status}, decided by {d.decided_by}, "
                f"{'approved' if d.human_approved else 'not approved'}"
                for d in decisions
            )
        )
        self.decisions = decisions


def _fingerprint(*parts: str) -> str:
    return sha256_hex("|".join(parts).encode("utf-8"))


async def _next_display_code(uow: UnitOfWork, tender_id: uuid.UUID, category: str) -> str:
    """The bid's next display code for this category's prefix ("ELIG-004"),
    from its counter (migration 046), so a number is never handed out twice,
    even after a merge deletes the row that held it. Extraction and split
    call it under the bid's requirement-set lock; it is safe without one, as
    the counter's upsert locks its row, so two takers get two numbers."""
    prefix = _CATEGORY_PREFIX[category]
    return f"{prefix}-{await uow.requirements.next_display_number(tender_id, prefix):03d}"


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


_WORD = re.compile(r"[\w\[\]-]+")


def _words(text: str) -> set[str]:
    """A text's words for the overlap check: NFKC-normalized, casefolded,
    every character but word characters (any script's letters and digits),
    brackets and hyphens a separator (the planted "[REQ-024]" stays one
    word). Unicode-aware, so an accented or non-Latin word is checked like
    any other rather than dropped (security review of PR 38: an ASCII-only
    pattern checked only a mixed text's ASCII words).

    NFKC first (plan item 126), on both sides: chunk text is stored as the
    parser read it (normalize_text only collapses whitespace), and the
    model's requirement text need not be in the same form. A decomposed
    accent (an "e" then a combining accent, as some editors save it) is not
    a word character and would split the word; full-width letters casefold
    to full-width letters. pdfplumber expands the Latin ligatures itself, and
    casefold expands them too, so a ligature already matched."""
    return set(_WORD.findall(unicodedata.normalize("NFKC", text).casefold()))


def _overlapping_neighbours(a: DocumentChunk, b: DocumentChunk) -> bool:
    """Two chunks _pack_units could have carried text between: parsed chunks
    of one document, one index apart, with the same parent and the same
    provenance (page, sheet, cell range, heading path), since it carries only
    inside one provenance group (ingestion/chunk.py)."""
    return (
        a.document_id == b.document_id
        and a.origin == CHUNK_ORIGIN_PARSED
        and b.origin == CHUNK_ORIGIN_PARSED
        and abs(a.chunk_index - b.chunk_index) == 1
        and a.parent_chunk_id == b.parent_chunk_id
        and a.page_num == b.page_num
        and a.sheet_name == b.sheet_name
        and a.cell_range == b.cell_range
        and a.heading_path == b.heading_path
    )


def _in_carried_overlap(normalized_text: str, a: DocumentChunk, b: DocumentChunk) -> bool:
    """Whether every word of the requirement is in the lines carried between
    the two chunks (user decision Q1 a: every word). The model restates a
    requirement (observed: "[REQ-024] The platform is encouraged ..." from
    "The platform [REQ-024] is encouraged ..."), so its text is checked by
    its words, not verbatim; on the real-provider run every one of the 22
    doubled rows scored every word."""
    earlier, later = (a, b) if a.chunk_index < b.chunk_index else (b, a)
    words = _words(normalized_text)
    return bool(words) and words <= _words(carried_overlap(earlier.chunk_text, later.chunk_text))


async def _same_requirement(
    uow: UnitOfWork, tender_id: uuid.UUID, draft: RequirementDraft, normalized: str
) -> Requirement | None:
    """Plan item 125: the existing row (rejected ones included) this draft is
    another copy of, or None.

    - A row with exactly this text that already cites the draft's chunk (a
      second extraction, finding a copy recorded before): whatever its flags,
      as the exact key ignores them.
    - Otherwise a row with exactly this text, category and mandatory flag that
      cites an overlapping neighbour of the draft's chunk, with the
      requirement inside the lines carried between them.

    - Plan item 126: otherwise the row that this text's key in an
      overlapping neighbour chunk finds, with the requirement inside the
      lines carried between them. This finds a row a person has since
      edited, merged, split or re-flagged, which the text no longer finds:
      its keys still name the text extraction first found. A row a person has
      reviewed has the person's flags now, so its flags are not compared (a
      match changes nothing on the row); an unreviewed row's must agree.

    Never the same sentence in a distant section, across a page or heading,
    in another document, outside the carried lines, or in other words.
    """
    candidates = await uow.requirements.list_by_normalized_text(tender_id, normalized)
    citations = await uow.requirements.list_citations([row.id for row in candidates])
    for row in candidates:
        if draft.source_chunk_id in citations[row.id]:
            return row
    draft_chunk = await uow.documents.get_chunk(draft.source_chunk_id)
    if draft_chunk is None:
        return None
    for row in candidates:
        if row.category != draft.category or row.mandatory != draft.mandatory:
            continue
        for chunk_id in citations[row.id]:
            cited = await uow.documents.get_chunk(chunk_id)
            if (
                cited is not None
                and _overlapping_neighbours(cited, draft_chunk)
                and _in_carried_overlap(normalized, cited, draft_chunk)
            ):
                return row
    for neighbour in await uow.documents.list_adjacent_parsed_chunks(draft_chunk):
        if not _overlapping_neighbours(neighbour, draft_chunk):
            continue
        keyed = await uow.requirements.get_by_fingerprint(
            tender_id, _fingerprint(str(neighbour.document_id), str(neighbour.id), normalized)
        )
        if keyed is None:
            continue
        if not keyed.human_reviewed and (
            keyed.category != draft.category or keyed.mandatory != draft.mandatory
        ):
            continue
        if _in_carried_overlap(normalized, neighbour, draft_chunk):
            return keyed
    return None


async def persist_extraction_results(
    tender_id: uuid.UUID, drafts: list[RequirementDraft]
) -> list[Requirement]:
    """Called once by orchestration/nodes/requirement.py's real extraction
    node, after the LLM call(s) return - never by an API route (line 538: no
    LLM/eval work inside a FastAPI request, and this is downstream of that
    same call). Fingerprint-based dedup means a retried workflow run replays
    safely: existing rows are returned as-is, only genuinely new content
    creates a row.

    Plan item 125: a requirement the chunker's overlap put whole in two
    neighbouring chunks is found from both, and the two copies' keys differ
    only in their chunk. _same_requirement recognises the second copy (and a
    copy found again by a later extraction), which then adds its chunk to the
    row's citations instead of making a row. All drafts persist in this one
    transaction, so the rows created earlier in the batch are seen here.

    Returns each requirement once, in the order it was first met: a second
    copy (an overlap copy, or the same requirement twice from one chunk)
    resolves to a row already in the list. The extraction node verifies once
    per entry (a model call) and counts the entries in its task output.

    Plan item 126: keys are read from requirement_keys, so a key a merge or
    split moved finds the row it now names; a copy that _same_requirement
    matches records its own key there, so the next extraction finds it
    directly. The tender's requirement-set lock is taken first, so a merge
    or split never commits between a key's lookup and its citation: without
    it, a merge could delete the row a key just found, and the citation
    would fail its foreign key or the deleted row be returned.
    """
    persisted: list[Requirement] = []
    async with UnitOfWork() as uow:
        await uow.requirements.lock_tender_requirements(tender_id)
        created_any = False
        for draft in drafts:
            normalized = normalize_text(draft.requirement_text)
            fingerprint = _fingerprint(
                str(draft.document_id), str(draft.source_chunk_id), normalized
            )
            existing = await uow.requirements.get_by_fingerprint(tender_id, fingerprint)
            if existing is not None:
                persisted.append(existing)
                continue
            # Plan item 125: the same requirement found again, in a chunk the
            # row already cites or in that chunk's overlapping neighbour,
            # adds a citation rather than a row.
            same = await _same_requirement(uow, tender_id, draft, normalized)
            if same is not None:
                await uow.requirements.add_citation(same.id, draft.source_chunk_id)
                await uow.requirements.add_key(same.id, fingerprint)
                persisted.append(same)
                continue

            created = await uow.requirements.create(
                tender_id=tender_id,
                display_code=await _next_display_code(uow, tender_id, draft.category),
                requirement_text=draft.requirement_text,
                normalized_text=normalized,
                category=draft.category,
                mandatory=draft.mandatory,
                source_chunk_id=draft.source_chunk_id,
                content_fingerprint=fingerprint,
                confidence=draft.confidence,
            )
            persisted.append(created)
            created_any = True
        if created_any:
            # Requirements are graph nodes: one bump for the whole batch
            # (plan item 95). It runs before the run's project_graph step.
            await uow.tenders.bump_state_version(tender_id)
        await uow.commit()
    unique: dict[uuid.UUID, Requirement] = {}
    for requirement in persisted:
        unique.setdefault(requirement.id, requirement)
    return list(unique.values())


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

    Does not bump tenders.state_version (plan item 95): submission items are
    not projected (no graph node or relationship), so a tick must not make
    the graph stale. The bump used to be here only so readiness's stored
    report, then one per state version, would refresh; since plan item 93
    readiness computes on every generate, so the next generate drops a
    cleared blocker at the same version. Accepted with it (decision 0103): a
    tick landing between the final gate approval's recompute and its
    version-conditional write is not caught by that write, as red-team
    findings already are not (decision 0102).
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


async def citations_for(requirement_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[uuid.UUID]]:
    """Each requirement's cited chunks, the primary citation first (plan item
    125): what the API returns as source_chunk_ids."""
    async with UnitOfWork() as uow:
        return await uow.requirements.list_citations(requirement_ids)


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
        # Plan item 59, D4: locked before "before" is taken, so an edit
        # committing meanwhile is waited for and read, not lost. Lock order:
        # the requirement, then its bid's tender (the bump).
        requirement = await uow.requirements.get_for_update(requirement_id)
        if requirement is None:
            raise RequirementNotFoundError()

        before = {
            "requirement_text": requirement.requirement_text,
            "category": requirement.category,
            "mandatory": requirement.mandatory,
            "human_reviewed": requirement.human_reviewed,
            "rejected_at": requirement.rejected_at.isoformat() if requirement.rejected_at else None,
            "verified": requirement.verified,
        }

        if action == "reject":
            updated = await uow.requirements.reject(requirement_id)
        else:
            fields: dict[str, object] = {"human_reviewed": True}
            if requirement_text is not None:
                fields["requirement_text"] = requirement_text
                fields["normalized_text"] = normalize_text(requirement_text)
                if requirement_text != requirement.requirement_text:
                    # Plan item 59, Q5 (PR 21): the verdict was on the old
                    # text, so it goes back to not checked yet (false, no
                    # time), under the lock above. The person can press
                    # Verify again. The same text sent again keeps it.
                    fields["verified"] = False
                    fields["verification_checked_at"] = None
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
            "verified": updated.verified,
        }
        # The graph's Requirement node carries the category, the mandatory
        # flag and verified; the text, the reviewed flag and the check time are
        # not projected, so a confirm changes no graph (plan item 95), and a
        # text edit only when it resets a verdict. Whether it is rejected
        # decides whether the requirement, its compliance entry and a role
        # whose every source is rejected are in the graph at all (plan item
        # 97), so a reject bumps; a second reject of a rejected one does not.
        rejected_changed = (before["rejected_at"] is None) != (after["rejected_at"] is None)
        if rejected_changed or any(
            before[key] != after[key] for key in ("category", "mandatory", "verified")
        ):
            await uow.tenders.bump_state_version(updated.tender_id)
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


def _entry_snapshot(entry: ComplianceEntry | None) -> dict[str, object] | None:
    if entry is None:
        return None
    return {
        "id": str(entry.id),
        "status": entry.status,
        "decided_by": entry.decided_by,
        "human_approved": entry.human_approved,
    }


def _entry_to_keep(
    originals: list[Requirement], entries: dict[uuid.UUID, ComplianceEntry]
) -> ComplianceEntry | None:
    """The compliance entry a merge keeps (plan item 126, Q2 a as the user
    refined it), or None when no original has one.

    The originals with an entry must agree: the same status and the same
    approval. An original with no entry has no decision to disagree with.
    Who decided may differ; then a reviewer's entry is kept (the first in
    request order), so the survivor keeps item 121's protection: a
    re-evaluation leaves a reviewer's status in place and reopens it rather
    than replacing it. Otherwise the first in request order, the survivor's
    own when it has one. Disagreeing entries refuse the merge, naming each;
    no decision is ever picked for the reviewer."""
    decided = [(o, entries[o.id]) for o in originals if o.id in entries]
    if not decided:
        return None
    first = decided[0][1]
    if any(
        e.status != first.status or e.human_approved != first.human_approved for _, e in decided
    ):
        raise MergeComplianceConflictError(
            [
                ComplianceDecision(o.display_code, e.status, e.decided_by, e.human_approved)
                for o, e in decided
            ]
        )
    reviewer = next((e for _, e in decided if e.decided_by == "reviewer"), None)
    return reviewer or first


async def merge_requirements(
    requirement_ids: list[uuid.UUID],
    merged_text: str,
    category: str,
    mandatory: bool,
    organization_id: uuid.UUID,
    actor_user_id: uuid.UUID,
) -> Requirement:
    """G5's POST /requirements/merge (plan item 126). The first id is the
    survivor (Q1 a): it is updated in place, as an edit, so its id, display
    code, history and everything that references it stay where they are. The
    other originals' attachments move to it, and then they are deleted:

    - compliance: one entry is kept, by _entry_to_keep's rule, and gains the
      others' evidence; the others are dropped; differing decisions refuse;
    - claim links, role sources and citations: copied to the survivor where
      it lacks them, then the originals' own go with them;
    - clarifications: moved, all of them. Each keeps its fingerprint, which
      names the original, so a later re-evaluation asking the identical
      question of the survivor adds a second copy beside it; nothing is lost
      and the person sees both;
    - extraction keys: moved, so the next extraction finds the survivor for
      any original's text and never brings an original back.

    The survivor is human_reviewed (a merge is a reviewer's act). As an edit
    (patch_requirement): a changed text resets its verdict to not checked
    yet; its content_fingerprint, display code and compliance approval stay;
    nothing is queued (no verification, no compliance re-evaluation). A
    rejected original refuses the merge.

    One transaction. Lock order: the tender's requirement-set lock (so an
    extraction or split of the bid runs before or after, never between),
    then the originals' compliance entries, then the originals, then the
    tender (the bump), the order every other writer of these rows keeps."""
    if len(set(requirement_ids)) != len(requirement_ids):
        raise RepeatedRequirementError()
    async with UnitOfWork() as uow:
        first = await uow.requirements.get(requirement_ids[0])
        if first is None:
            raise RequirementNotFoundError()
        tender_id = first.tender_id
        await uow.requirements.lock_tender_requirements(tender_id)
        entries = await uow.compliance.lock_compliance_entries_by_requirements(requirement_ids)
        locked = {r.id: r for r in await uow.requirements.lock_for_merge(requirement_ids)}
        if len(locked) != len(requirement_ids):
            raise RequirementNotFoundError()
        originals = [locked[rid] for rid in requirement_ids]
        if {o.tender_id for o in originals} != {tender_id}:
            raise CrossTenderOperationError()
        rejected = [o.display_code for o in originals if o.rejected_at is not None]
        if rejected:
            raise RequirementRejectedError(rejected)
        kept = _entry_to_keep(originals, entries)

        survivor, others = originals[0], originals[1:]
        other_ids = [o.id for o in others]
        before = {
            "merged_from": [
                {
                    "id": str(o.id),
                    "display_code": o.display_code,
                    "requirement_text": o.requirement_text,
                    "category": o.category,
                    "mandatory": o.mandatory,
                    "verified": o.verified,
                    "compliance_entry": _entry_snapshot(entries.get(o.id)),
                }
                for o in originals
            ]
        }

        dropped = [e for e in entries.values() if kept is None or e.id != kept.id]
        evidence_added: list[uuid.UUID] = []
        if kept is not None:
            evidence_added = await uow.compliance.copy_evidence([e.id for e in dropped], kept.id)
            # Dropped first: the survivor's own entry may be one, and an entry
            # moved onto the survivor needs its unique requirement_id free.
            for entry in dropped:
                await uow.compliance.delete_compliance_entry(entry.id)
            if kept.requirement_id != survivor.id:
                await uow.compliance.update_compliance_entry(kept.id, requirement_id=survivor.id)
        claims = await uow.proposal.copy_claim_links(other_ids, survivor.id)
        roles = await uow.talent.copy_role_sources(other_ids, survivor.id)
        citations = await uow.requirements.copy_citations(other_ids, survivor.id)
        clarifications = await uow.requirements.move_clarifications(other_ids, survivor.id)
        keys = await uow.requirements.move_keys(other_ids, survivor.id)
        for original in others:
            await uow.requirements.delete(original.id)

        fields: dict[str, object] = {
            "requirement_text": merged_text,
            "normalized_text": normalize_text(merged_text),
            "category": category,
            "mandatory": mandatory,
            "human_reviewed": True,
        }
        if merged_text != survivor.requirement_text:
            # As patch_requirement (plan item 59, Q5): the verdict was on the
            # old text, so it goes back to not checked yet.
            fields["verified"] = False
            fields["verification_checked_at"] = None
        merged = await uow.requirements.update_fields(survivor.id, **fields)

        await uow.audit.create(
            organization_id=organization_id,
            actor_type="user",
            actor_id=str(actor_user_id),
            action="requirement_merge",
            tender_id=tender_id,
            target_type="requirement",
            target_id=merged.id,
            before=before,
            after={
                "id": str(merged.id),
                "display_code": merged.display_code,
                "requirement_text": merged.requirement_text,
                "category": merged.category,
                "mandatory": merged.mandatory,
                "verified": merged.verified,
                "compliance_entry_kept": str(kept.id) if kept is not None else None,
                "compliance_entries_dropped": [str(e.id) for e in dropped],
                "evidence_added": [str(i) for i in evidence_added],
                "claims_linked": [str(i) for i in claims],
                "roles_sourced": [str(i) for i in roles],
                "citations_added": [str(i) for i in citations],
                "clarifications_moved": [str(i) for i in clarifications],
                "keys_moved": keys,
            },
        )
        # The originals' nodes are gone and their edges are the survivor's
        # now: the graph changed (plan item 95). The next projection rebuilds
        # the bid's subgraph from these rows.
        await uow.tenders.bump_state_version(tender_id)
        await uow.commit()
        return merged


async def split_requirement(
    requirement_id: uuid.UUID,
    new_items: list[dict],
    organization_id: uuid.UUID,
    actor_user_id: uuid.UUID,
) -> list[Requirement]:
    """G5's POST /requirements/{id}/split (plan item 126, by merge's logic).
    The first part is the original, updated in place as an edit with the
    first item's text and flags: it keeps the original's id, display code,
    compliance entry and decision (Q4 a), clarifications, citations and
    extraction keys, so the next extraction finds the first part for the
    original's text and never brings the original back. A changed text
    resets its verdict, as an edit's; nothing is queued.

    Every later part is a new row, human_reviewed and unverified, with no
    compliance entry (a mandatory part shows as unresolved until analysed or
    decided). It cites every chunk the original cites, primary first, and is
    linked to every claim and role the original is (Q5 a: a claim or role
    built on the whole requirement concerns each part; a claim link never
    counts as covering a requirement, so this makes no part look covered).
    Its key is unique, from a fresh uuid, so no extraction matches it and no
    two parts collide; it is in requirement_keys, as every requirement's own
    key is. Its display code is the bid's next for its prefix.

    A rejected requirement is refused: split in place, its first part would
    stay rejected and the others live. Lock order: the tender's
    requirement-set lock, then the original, then the tender (the bump)."""
    async with UnitOfWork() as uow:
        found = await uow.requirements.get(requirement_id)
        if found is None:
            raise RequirementNotFoundError()
        tender_id = found.tender_id
        await uow.requirements.lock_tender_requirements(tender_id)
        original = await uow.requirements.get_for_update(requirement_id)
        if original is None:
            raise RequirementNotFoundError()
        if original.rejected_at is not None:
            raise RequirementRejectedError([original.display_code])

        before = {
            "split_from": {
                "id": str(original.id),
                "display_code": original.display_code,
                "requirement_text": original.requirement_text,
                "category": original.category,
                "mandatory": original.mandatory,
                "verified": original.verified,
            }
        }
        first_item, *later_items = new_items
        first_text = first_item["requirement_text"]
        fields: dict[str, object] = {
            "requirement_text": first_text,
            "normalized_text": normalize_text(first_text),
            "category": first_item.get("category", original.category),
            "mandatory": first_item.get("mandatory", original.mandatory),
            "human_reviewed": True,
        }
        if first_text != original.requirement_text:
            fields["verified"] = False
            fields["verification_checked_at"] = None
        first_part = await uow.requirements.update_fields(original.id, **fields)

        created: list[Requirement] = [first_part]
        copied: dict[str, dict[str, list[str]]] = {}
        for item in later_items:
            text = item["requirement_text"]
            item_category = item.get("category", original.category)
            item_mandatory = item.get("mandatory", original.mandatory)
            # A person wrote this text, so no extraction ever matches it, and
            # its key need only be unique (the user's review of PR 39): built
            # from the text, two splits of rows from one chunk into a part with
            # the same words collided on uq_requirement_fingerprint (a 500).
            fingerprint = _fingerprint(str(tender_id), "split", str(uuid.uuid4()))
            part = await uow.requirements.create(
                tender_id=tender_id,
                display_code=await _next_display_code(uow, tender_id, item_category),
                requirement_text=text,
                normalized_text=normalize_text(text),
                category=item_category,
                mandatory=item_mandatory,
                source_chunk_id=original.source_chunk_id,
                content_fingerprint=fingerprint,
            )
            part = await uow.requirements.update_fields(part.id, human_reviewed=True)
            citations = await uow.requirements.copy_citations([original.id], part.id)
            claims = await uow.proposal.copy_claim_links([original.id], part.id)
            roles = await uow.talent.copy_role_sources([original.id], part.id)
            copied[str(part.id)] = {
                "citations_added": [str(i) for i in citations],
                "claims_linked": [str(i) for i in claims],
                "roles_sourced": [str(i) for i in roles],
            }
            created.append(part)

        await uow.audit.create(
            organization_id=organization_id,
            actor_type="user",
            actor_id=str(actor_user_id),
            action="requirement_split",
            tender_id=tender_id,
            target_type="requirement",
            target_id=first_part.id,
            before=before,
            after={
                "split_into": [str(r.id) for r in created],
                "kept_in_place": str(first_part.id),
                "copied_to_new_parts": copied,
            },
        )
        # New nodes and edges, and the first part's flags may have changed:
        # the graph changed (plan item 95).
        await uow.tenders.bump_state_version(tender_id)
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
