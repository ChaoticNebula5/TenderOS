import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime

from tenderos_backend.agents.compliance.propose import EvidenceCandidateRef, propose_compliance
from tenderos_backend.agents.knowledge.retrieval import KnowledgeSearchResult, search_company_knowledge
from tenderos_backend.agents.llm.client import LLMClient
from tenderos_backend.agents.schemas.compliance import ComplianceProposal
from tenderos_backend.core.hashing import sha256_hex
from tenderos_backend.db.models.compliance import ComplianceEntry, EvidenceLink
from tenderos_backend.db.unit_of_work import UnitOfWork


class RequirementNotFoundError(Exception):
    pass


class ComplianceEntryNotFoundError(Exception):
    pass


def _fingerprint(*parts: str) -> str:
    return sha256_hex("|".join(parts).encode("utf-8"))


# ----------------------------------------------------------------------
# Evidence candidate preparation (retrieval result -> LLM-facing labels)
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class _CandidateSignals:
    """Mechanically-derived trust signals for one retrieval result, computed
    before the LLM ever sees it - not a judgment call, just reading two
    existing fields the same way search_company_knowledge's own valid_only
    filter already does (B6's retrieval.py: knowledge_item.valid_until vs.
    today). Kept separate from EvidenceCandidateRef because the LLM only
    ever sees the derived validity_status (part of its prompt context, so
    it can reason about an expired certification instead of being kept
    blind to it - Build Guide: "Invalid/expired evidence excluded" is a
    pipeline test, not license to hide expired evidence from the model),
    while `verified` is used later, only by the deterministic
    evidence-sufficiency check, never shown in the prompt at all.
    """

    validity_status: str
    verified: bool


async def _compute_candidate_signals(
    uow: UnitOfWork, result: KnowledgeSearchResult, today: date
) -> _CandidateSignals:
    knowledge_item = await uow.knowledge.get_knowledge_item(result.knowledge_item_id)
    if knowledge_item is None:
        return _CandidateSignals(validity_status="unknown", verified=False)

    if knowledge_item.valid_until is not None and knowledge_item.valid_until < today:
        validity_status = "expired"
    elif knowledge_item.status == "archived":
        validity_status = "invalid"
    else:
        validity_status = "valid"

    if result.item_type == "employee":
        employee = await uow.knowledge.get_employee(result.knowledge_item_id)
        verified = employee is not None and employee.profile_review_status == "verified"
    else:
        # Non-employee knowledge_items (certifications, past_projects,
        # capabilities, company_documents) have no separate per-item review
        # step in B6 - they're trusted at ingestion time against the org's
        # own uploaded documents. knowledge_items.status carries an
        # 'unverified' value in its own CHECK constraint that B6 never
        # populates (no writer sets it), so this reduces to "not
        # archived/expired" for every item type except employee, which has
        # a real review workflow behind profile_review_status.
        verified = knowledge_item.status == "active"

    return _CandidateSignals(validity_status=validity_status, verified=verified)


@dataclass(frozen=True)
class PreparedEvidence:
    """One retrieval result, labeled for the prompt and carrying the
    signals the deterministic sufficiency check (below) needs - the single
    object both propose_compliance (prompt-building) and
    materialize_evidence (persistence) key off of, so the two never drift
    out of sync about what "evidence_3" actually referred to.
    """

    label: str
    result: KnowledgeSearchResult
    signals: _CandidateSignals


async def prepare_evidence_candidates(
    results: list[KnowledgeSearchResult], today: date | None = None
) -> list[PreparedEvidence]:
    today = today or date.today()
    prepared: list[PreparedEvidence] = []
    async with UnitOfWork() as uow:
        for i, result in enumerate(results, start=1):
            signals = await _compute_candidate_signals(uow, result, today)
            prepared.append(PreparedEvidence(label=f"evidence_{i}", result=result, signals=signals))
    return prepared


def to_llm_candidates(prepared: list[PreparedEvidence]) -> list[EvidenceCandidateRef]:
    return [
        EvidenceCandidateRef(
            label=p.label,
            knowledge_item_id=p.result.knowledge_item_id,
            source_kind=p.result.item_type,
            excerpt=p.result.excerpt,
            validity_status=p.signals.validity_status,
        )
        for p in prepared
    ]


# ----------------------------------------------------------------------
# Evidence materialization
# ----------------------------------------------------------------------


async def materialize_evidence(
    tender_id: uuid.UUID, prepared: list[PreparedEvidence], cited_labels: list[str]
) -> list[EvidenceLink]:
    """Turns the LLM's cited labels back into real, persisted evidence_link
    rows - only for what was actually cited, not every retrieval candidate
    (a compliance decision's evidence should reflect what backed it, not
    everything that was merely considered). Idempotent via
    uq_evidence_link_fingerprint: re-running compliance analysis for the
    same requirement against the same retrieval result reuses the existing
    row rather than raising or duplicating.
    """
    by_label = {p.label: p for p in prepared}
    materialized: list[EvidenceLink] = []
    async with UnitOfWork() as uow:
        for label in cited_labels:
            p = by_label.get(label)
            if p is None:
                # The model cited a label that was never in its own prompt -
                # a malformed-output-shaped failure that call_with_repair's
                # schema validation can't catch (evidence_refs is just
                # list[str], any string is schema-valid). Skip rather than
                # fabricate a row for something that was never retrieved.
                continue
            fingerprint = _fingerprint(
                str(tender_id), str(p.result.knowledge_item_id), str(p.result.chunk_id)
            )
            existing = await uow.compliance.get_evidence_link_by_fingerprint(tender_id, fingerprint)
            if existing is not None:
                materialized.append(existing)
                continue
            link = await uow.compliance.create_evidence_link(
                tender_id=tender_id,
                source_kind=p.result.item_type,
                content_fingerprint=fingerprint,
                knowledge_item_id=p.result.knowledge_item_id,
                source_chunk_id=p.result.chunk_id,
                excerpt=p.result.excerpt,
                confidence=p.result.rerank_score,
                verified=p.signals.verified,
                validity_status=p.signals.validity_status,
            )
            materialized.append(link)
        await uow.commit()
    return materialized


# ----------------------------------------------------------------------
# Deterministic evidence-sufficiency enforcement (PRD 8.4)
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class SufficiencyResult:
    status: str
    downgraded: bool
    reason: str


# PRD 8.4's evidence-sufficiency policy: the one place a fully_compliant/
# partially_compliant status stops being a hope about model behavior and
# becomes a fact about what actually gets persisted - unconditional, not
# advisory (unlike B6's disagreement-routing, there is no human step
# between this function and the database row).
def enforce_evidence_sufficiency(
    proposal: ComplianceProposal, materialized_evidence: list[EvidenceLink]
) -> SufficiencyResult:
    if proposal.status not in {"fully_compliant", "partially_compliant"}:
        return SufficiencyResult(status=proposal.status, downgraded=False, reason="evidence sufficiency does not apply to this compliance status")
    
    sufficient_evidence = [
        evidence for evidence in materialized_evidence
        if evidence.verified and evidence.validity_status=="valid"
    ]
    
    if not sufficient_evidence:
        if not materialized_evidence:
            reason = "no materialized supporting evidence was available"
        else:
            rejected_details = ", ".join(
               f"{e.id}: verified={e.verified}, validity={e.validity_status}" 
               for e in materialized_evidence
            )
            reason = f"all supporting evidence was insufficient: {rejected_details}"
        
        return SufficiencyResult(
            status="evidence_unavailable",
            downgraded=True,
            reason=reason
        )
        
    all_evidence_sufficient = (len(sufficient_evidence) == len(materialized_evidence))
    if proposal.status == "fully_compliant" and not all_evidence_sufficient:
        sufficient_ids = ", ".join(str(e.id) for e in sufficient_evidence)
        insufficient_ids = ", ".join(
            str(e.id)
            for e in materialized_evidence
            if e not in sufficient_evidence
        )
        return SufficiencyResult(
            status="partially_compliant",
            downgraded=True,
            reason=(
                f"only part of the cited evidence was verified and valid; "
                f"sufficient=[{sufficient_ids}], "
                f"insufficient=[{insufficient_ids}]"
            )
        )
    sufficient_ids = ", ".join(str(e.id) for e in sufficient_evidence)

    return SufficiencyResult(
        status=proposal.status,
        downgraded=False,
        reason=(
            f"{len(sufficient_evidence)} verified, valid evidence item(s) "
            f"supported the compliance status: [{sufficient_ids}]"
        ),
    )


# ----------------------------------------------------------------------
# Persistence
# ----------------------------------------------------------------------


async def persist_compliance_decision(
    requirement_id: uuid.UUID,
    proposal: ComplianceProposal,
    sufficiency: SufficiencyResult,
    materialized_evidence: list[EvidenceLink],
) -> ComplianceEntry:
    """Writes (or re-writes, on retry) the one compliance_entries row a
    requirement is allowed to have (requirement_id UNIQUE), attaches every
    materialized evidence link, and generates a ClarificationItem when the
    proposal flagged one - independent of status per E4's own example
    (a partially_compliant proposal can still need clarification on one
    point). Idempotent: re-running this for the same requirement updates
    the existing row in place rather than violating the UNIQUE constraint,
    and link_evidence/create_clarification_item are both themselves
    idempotent (ON CONFLICT DO NOTHING / fingerprint lookup), so a retry
    after a crash produces the same end state, not duplicates - satisfying
    the stop-gate's "retry does not duplicate evidence or compliance" test.
    """
    async with UnitOfWork() as uow:
        requirement = await uow.requirements.get(requirement_id)
        if requirement is None:
            raise RequirementNotFoundError()
        tender_id = requirement.tender_id

        existing = await uow.compliance.get_compliance_entry_by_requirement(requirement_id)
        # Computed before either branch writes anything, so it reflects
        # "did this call actually change something" rather than "did a
        # write statement run" - a brand-new entry is always a real change;
        # a reevaluation of an existing one only is if its status differs
        # or it attaches evidence that wasn't already linked (link_evidence
        # is itself an ON CONFLICT DO NOTHING upsert, so re-attaching
        # already-linked evidence is a legitimate no-op, not new evidence).
        materially_changed = existing is None
        if existing is not None:
            already_linked_ids = {
                e.id for e in await uow.compliance.list_evidence_for_compliance_entry(existing.id)
            }
            new_evidence_ids = {e.id for e in materialized_evidence}
            materially_changed = (
                existing.status != sufficiency.status
                or not new_evidence_ids.issubset(already_linked_ids)
            )

        if existing is None:
            entry = await uow.compliance.create_compliance_entry(
                requirement_id=requirement_id,
                status=sufficiency.status,
                reasoning_summary=proposal.reasoning_summary,
                confidence=proposal.confidence,
            )
        else:
            update_fields: dict[str, object] = {
                "status": sufficiency.status,
                "reasoning_summary": proposal.reasoning_summary,
                "confidence": proposal.confidence,
                "last_evaluated_at": datetime.now(UTC),
            }
            # A human's approval is an approval of the STATUS this entry
            # showed at the time - proposal_service.py's _facts_from_
            # compliance trusts human_approved unconditionally, and B12's
            # own required blockers name "compliant decision without valid
            # evidence". An automated reevaluation that flips the status
            # (e.g. fully_compliant -> non_compliant, because the evidence
            # backing it changed) must clear that approval rather than let
            # it survive attached to a decision the human never actually
            # saw - otherwise a stale approval grounds a claim inside a
            # readiness report asserting everything checks out. An
            # IDENTICAL status is treated as the same semantic decision the
            # human already approved and deliberately preserves
            # human_approved - re-running analysis against unchanged
            # underlying data isn't a new decision to re-approve.
            if existing.human_approved and existing.status != sufficiency.status:
                update_fields["human_approved"] = False
                tender = await uow.tenders.get(tender_id)
                if tender is not None:
                    await uow.audit.create(
                        organization_id=tender.organization_id,
                        actor_type="system",
                        actor_id="compliance_reevaluation",
                        action="compliance_approval_cleared_on_reevaluation",
                        tender_id=tender_id,
                        target_type="compliance_entry",
                        target_id=existing.id,
                        before={"status": existing.status, "human_approved": True},
                        after={"status": sufficiency.status, "human_approved": False},
                    )
            entry = await uow.compliance.update_compliance_entry(existing.id, **update_fields)

        for evidence in materialized_evidence:
            await uow.compliance.link_evidence(entry.id, evidence.id)

        # Part C's state-version invariant, same "compliance change"/
        # "evidence change" pair approve_compliance_entry/override_compliance_entry
        # already bump (KNOWN_LIMITATIONS.md: "tenders.state_version was never
        # bumped") - this is the automated origination path for that same data
        # (materialize_evidence + this function, called from
        # evaluate_compliance_for_requirement), which B8's fix never covered.
        # One bump per decision write, not per evidence link, matching that
        # same precedent. Gated on materially_changed (computed above,
        # before either write branch ran) - every consumer of state_version
        # (graph freshness, targeted-replan no-progress checks) reads a
        # bump as "something real changed," so an identical semantic retry
        # (same status, no new evidence) bumping anyway would falsely mark
        # an otherwise-fresh graph stale for no actual change.
        if materially_changed:
            await uow.tenders.bump_state_version(tender_id)

        if proposal.clarification_required:
            question = proposal.clarification_question or (
                f"Please clarify: {proposal.reasoning_summary}"
            )
            fingerprint = _fingerprint(str(tender_id), str(requirement_id), question)
            existing_clarification = await uow.requirements.get_clarification_item_by_fingerprint(
                tender_id, fingerprint
            )
            if existing_clarification is None:
                await uow.requirements.create_clarification_item(
                    tender_id=tender_id,
                    question=question,
                    content_fingerprint=fingerprint,
                    requirement_id=requirement_id,
                    reason=f"Raised by compliance analysis: {sufficiency.reason}",
                )

        await uow.commit()
        return entry


async def evaluate_compliance_for_requirement(
    client: LLMClient,
    model: str,
    requirement_id: uuid.UUID,
    workflow_run_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
    today: date | None = None,
) -> ComplianceEntry:
    """The full per-requirement pipeline (retrieve -> propose -> materialize
    -> enforce -> persist) - what compliance_analysis_node calls once per
    requirement in a tender. Kept as one function here (rather than left for
    the orchestration node to assemble) so the same sequence is reachable
    from a test or an on-demand re-evaluation route without duplicating the
    wiring between propose_compliance, materialize_evidence,
    enforce_evidence_sufficiency, and persist_compliance_decision.
    """
    async with UnitOfWork() as uow:
        requirement = await uow.requirements.get(requirement_id)
        if requirement is None:
            raise RequirementNotFoundError()
        tender = await uow.tenders.get(requirement.tender_id)
        if tender is None:
            raise RequirementNotFoundError()
        requirement_text = requirement.requirement_text
        tender_id = requirement.tender_id
        organization_id = tender.organization_id

    # selected_only is deliberately not used here - B7's vertical-slice demo
    # retrieves evidence straight from the org's knowledge base with no
    # prior "select knowledge for this tender" step in between (that's a
    # later-block concern per Part C's tender_knowledge_selections, not
    # something B7's own build list requires as a precondition). valid_only
    # is also False - compliance needs to SEE expired/invalid evidence to
    # honestly propose against it (and cite it in reasoning_summary), not
    # have it silently filtered out of the candidate pool before the model
    # ever sees it.
    results = await search_company_knowledge(organization_id, requirement_text, tender_id=tender_id)
    prepared = await prepare_evidence_candidates(results, today=today)
    llm_candidates = to_llm_candidates(prepared)

    proposal = await propose_compliance(
        client,
        model,
        requirement_text,
        llm_candidates,
        tender_id,
        workflow_run_id=workflow_run_id,
        task_id=task_id,
    )

    materialized = await materialize_evidence(tender_id, prepared, proposal.evidence_refs)
    sufficiency = enforce_evidence_sufficiency(proposal, materialized)

    return await persist_compliance_decision(requirement_id, proposal, sufficiency, materialized)


# ----------------------------------------------------------------------
# Human review / override (G7)
# ----------------------------------------------------------------------


async def approve_compliance_entry(
    compliance_entry_id: uuid.UUID, actor_user_id: uuid.UUID
) -> ComplianceEntry:
    async with UnitOfWork() as uow:
        entry = await uow.compliance.get_compliance_entry(compliance_entry_id)
        if entry is None:
            raise ComplianceEntryNotFoundError()
        requirement = await uow.requirements.get(entry.requirement_id)
        if requirement is None:
            raise RequirementNotFoundError()
        tender = await uow.tenders.get(requirement.tender_id)
        if tender is None:
            raise RequirementNotFoundError()

        before = {"human_approved": entry.human_approved, "status": entry.status}
        updated = await uow.compliance.update_compliance_entry(
            compliance_entry_id, human_approved=True
        )
        await uow.audit.create(
            organization_id=tender.organization_id,
            actor_type="user",
            actor_id=str(actor_user_id),
            action="compliance_approve",
            tender_id=tender.id,
            target_type="compliance_entry",
            target_id=compliance_entry_id,
            before=before,
            after={"human_approved": True, "status": updated.status},
        )
        # Part C's State-version invariant: "compliance change" is one of
        # the listed graph-relevant mutations, bumped in the same
        # transaction as the write it accompanies (KNOWN_LIMITATIONS.md:
        # "[B7/B8] tenders.state_version was never bumped" - this call was
        # missing from B7 through this fix in B8).
        await uow.tenders.bump_state_version(tender.id)
        await uow.commit()
        return updated


async def override_compliance_entry(
    compliance_entry_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    status: str | None = None,
    reasoning_summary: str | None = None,
) -> ComplianceEntry:
    """G7's PATCH /compliance/{compliance_id} - a human directly overriding
    the proposed status and/or reasoning. Always audited (Build Guide test:
    "Human override audited") and always sets human_approved=True - an
    entry a human has just edited is, by definition, one they've now taken
    responsibility for, whether or not they separately call the approve
    route too.
    """
    async with UnitOfWork() as uow:
        entry = await uow.compliance.get_compliance_entry(compliance_entry_id)
        if entry is None:
            raise ComplianceEntryNotFoundError()
        requirement = await uow.requirements.get(entry.requirement_id)
        if requirement is None:
            raise RequirementNotFoundError()
        tender = await uow.tenders.get(requirement.tender_id)
        if tender is None:
            raise RequirementNotFoundError()

        before = {
            "status": entry.status,
            "reasoning_summary": entry.reasoning_summary,
            "human_approved": entry.human_approved,
        }
        fields: dict[str, object] = {"human_approved": True}
        if status is not None:
            fields["status"] = status
        if reasoning_summary is not None:
            fields["reasoning_summary"] = reasoning_summary

        updated = await uow.compliance.update_compliance_entry(compliance_entry_id, **fields)
        await uow.audit.create(
            organization_id=tender.organization_id,
            actor_type="user",
            actor_id=str(actor_user_id),
            action="compliance_override",
            tender_id=tender.id,
            target_type="compliance_entry",
            target_id=compliance_entry_id,
            before=before,
            after={
                "status": updated.status,
                "reasoning_summary": updated.reasoning_summary,
                "human_approved": True,
            },
        )
        # See approve_compliance_entry's own comment - same invariant, same fix.
        await uow.tenders.bump_state_version(tender.id)
        await uow.commit()
        return updated


async def get_compliance_entry(compliance_entry_id: uuid.UUID) -> ComplianceEntry | None:
    async with UnitOfWork() as uow:
        return await uow.compliance.get_compliance_entry(compliance_entry_id)


async def list_compliance_entries_for_tender(tender_id: uuid.UUID) -> list[ComplianceEntry]:
    async with UnitOfWork() as uow:
        return await uow.compliance.list_compliance_entries_for_tender(tender_id)


# ----------------------------------------------------------------------
# Traceability (G7: GET /compliance/{compliance_id}/trace)
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceTraceEntry:
    evidence_link_id: uuid.UUID
    source_kind: str
    excerpt: str | None
    verified: bool
    validity_status: str
    knowledge_item_id: uuid.UUID | None
    knowledge_item_title: str | None
    document_id: uuid.UUID | None
    document_filename: str | None
    page_num: int | None
    heading_path: list | None


@dataclass(frozen=True)
class ComplianceTrace:
    requirement_id: uuid.UUID
    requirement_text: str
    compliance_entry_id: uuid.UUID
    status: str
    reasoning_summary: str | None
    human_approved: bool
    evidence: list[EvidenceTraceEntry]


async def get_compliance_trace(compliance_entry_id: uuid.UUID) -> ComplianceTrace:
    """B7's own build-list wording, walked literally: requirement ->
    compliance -> evidence -> source. One evidence_link resolves to a
    source in one of two ways (Part C: knowledge_item_id and/or
    source_chunk_id) - a knowledge_item name/type, and/or the document
    chunk (page/heading) and document (filename) it was drawn from, both
    populated when both are present rather than one crowding out the other.
    """
    async with UnitOfWork() as uow:
        entry = await uow.compliance.get_compliance_entry(compliance_entry_id)
        if entry is None:
            raise ComplianceEntryNotFoundError()
        requirement = await uow.requirements.get(entry.requirement_id)
        if requirement is None:
            raise RequirementNotFoundError()

        links = await uow.compliance.list_evidence_for_compliance_entry(entry.id)
        evidence_entries: list[EvidenceTraceEntry] = []
        for link in links:
            knowledge_item = None
            if link.knowledge_item_id is not None:
                knowledge_item = await uow.knowledge.get_knowledge_item(link.knowledge_item_id)

            document_id = None
            document_filename = None
            page_num = None
            heading_path = None
            if link.source_chunk_id is not None:
                chunk = await uow.documents.get_chunk(link.source_chunk_id)
                if chunk is not None:
                    page_num = chunk.page_num
                    heading_path = chunk.heading_path
                    document_id = chunk.document_id
                    document = await uow.documents.get(chunk.document_id)
                    if document is not None:
                        document_filename = document.filename

            evidence_entries.append(
                EvidenceTraceEntry(
                    evidence_link_id=link.id,
                    source_kind=link.source_kind,
                    excerpt=link.excerpt,
                    verified=link.verified,
                    validity_status=link.validity_status,
                    knowledge_item_id=link.knowledge_item_id,
                    knowledge_item_title=knowledge_item.title if knowledge_item else None,
                    document_id=document_id,
                    document_filename=document_filename,
                    page_num=page_num,
                    heading_path=heading_path,
                )
            )

    return ComplianceTrace(
        requirement_id=requirement.id,
        requirement_text=requirement.requirement_text,
        compliance_entry_id=entry.id,
        status=entry.status,
        reasoning_summary=entry.reasoning_summary,
        human_approved=entry.human_approved,
        evidence=evidence_entries,
    )