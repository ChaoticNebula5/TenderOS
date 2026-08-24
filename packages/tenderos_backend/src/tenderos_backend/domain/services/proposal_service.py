import uuid
from dataclasses import dataclass

from tenderos_backend.agents.llm.client import LLMClient
from tenderos_backend.agents.proposal.draft_section import GroundedFactRef, draft_section
from tenderos_backend.agents.schemas.proposal import ProposalClaimDraft, ProposalSectionDraft
from tenderos_backend.core.hashing import sha256_hex
from tenderos_backend.db.models.compliance import EvidenceLink
from tenderos_backend.db.models.proposal import ProposalClaim, ProposalSection
from tenderos_backend.db.unit_of_work import UnitOfWork

# Compliance statuses PRD 8.4/E4 treat as an actual affirmative compliance
# position - anything else (non_compliant, evidence_unavailable,
# clarification_required) is not a position a claim_type='compliance' claim
# may agree with, regardless of how much evidence backs the claim's wording.
_AFFIRMATIVE_COMPLIANCE_STATUSES = frozenset({"fully_compliant", "partially_compliant"})


class TenderNotFoundError(Exception):
    pass


class ProposalSectionNotFoundError(Exception):
    pass


class ProposalClaimNotFoundError(Exception):
    pass


def _fingerprint(*parts: str) -> str:
    return sha256_hex("|".join(parts).encode("utf-8"))


# ----------------------------------------------------------------------
# Section templates (build-list item "section templates" - unspecified by
# either the PRD or the Build Guide; six templates chosen here, each tied to
# requirements.category or a specialist's own approved output, skipped
# entirely when it has zero underlying approved facts to ground against -
# see KNOWN_LIMITATIONS.md "[B9] Section templates are an interpretation,
# not a locked list" for the scope this leaves out.
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class SectionTemplate:
    name: str
    guidance: str
    always_on: bool = False


SECTION_TEMPLATES: tuple[SectionTemplate, ...] = (
    SectionTemplate(
        "Company Overview",
        "General introduction to the company's suitability for this tender, written "
        "narratively. Do not assert specific company capabilities, certifications, "
        "or client references unless they appear in AVAILABLE FACTS.",
        always_on=True,
    ),
    SectionTemplate(
        "Relevant Experience",
        "Describe the company's relevant past delivery experience against this "
        "tender's experience-related requirements.",
    ),
    SectionTemplate(
        "Technical Approach",
        "Describe the proposed technical approach addressing this tender's "
        "technical requirements.",
    ),
    SectionTemplate(
        "Proposed Team",
        "Introduce the team members selected for this tender's staffing roles.",
    ),
    SectionTemplate(
        "Compliance and Eligibility",
        "Summarize the company's compliance and eligibility position against this "
        "tender's eligibility, legal, and security requirements.",
    ),
    SectionTemplate(
        "Commercial Summary",
        "Summarize the commercial position for this tender, including the status "
        "of any pricing validation already carried out.",
    ),
)

# category sets keyed by section name, for the five compliance-sourced
# templates ("Proposed Team" and "Company Overview" are handled separately
# below - team facts come from role_matches, not compliance_entries, and
# Company Overview is deliberately not data-gated at all).
_COMPLIANCE_CATEGORIES_BY_SECTION: dict[str, frozenset[str]] = {
    "Relevant Experience": frozenset({"experience"}),
    "Technical Approach": frozenset({"technical"}),
    "Compliance and Eligibility": frozenset({"eligibility", "legal", "security"}),
    "Commercial Summary": frozenset({"commercial"}),
}


# ----------------------------------------------------------------------
# Grounded context builder (PRD 10.11 / 12.6 - draws only on facts already
# approved by a human elsewhere in the product: approved compliance_entries,
# selected role_matches, resolved pricing_findings. No fresh retrieval
# happens here.)
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class PreparedFact:
    """One already-approved fact offered to draft_section, carrying both
    the label the LLM sees and the real row(s) it resolves to - the single
    object both draft_section's prompt-building and this module's own
    materialize_claim_links key off of, mirroring PreparedEvidence's role in
    compliance_service.py. Exactly one of the three *_id fields is set for
    any given fact (a requirement fact, an evidence fact, or an employee
    fact never overlap) except a pricing-finding-derived fact, which sets
    none of them - see _facts_from_pricing's own docstring for why.
    """

    label: str
    kind: str  # "requirement" | "evidence" | "employee"
    excerpt: str
    requirement_id: uuid.UUID | None = None
    evidence_link_id: uuid.UUID | None = None
    employee_knowledge_item_id: uuid.UUID | None = None


def to_llm_facts(prepared: list[PreparedFact]) -> list[GroundedFactRef]:
    return [GroundedFactRef(label=p.label, kind=p.kind, excerpt=p.excerpt) for p in prepared]


def _prepare_evidence_facts(
    links: list[EvidenceLink],
    seen_evidence_ids: set[uuid.UUID],
    start_index: int,
) -> list[PreparedFact]:
    """The one place that turns a candidate list of real EvidenceLink rows
    into citable PreparedFacts - shared by _facts_from_compliance (evidence
    attached to approved compliance_entries), _facts_from_team (evidence
    attached to selected role_matches), and get_proposal_trace's
    currently_in_scope_evidence (same filter, re-run at view time via
    _build_grounded_context instead of a second copy of this logic). One
    source for "what counts as offerable evidence" - see
    KNOWN_LIMITATIONS.md's "a reader trusts a column its writer never keeps
    current" standing pattern for why this isn't duplicated per caller.
    `seen_evidence_ids` is mutated in place so callers share one dedup set
    across multiple calls (e.g. per compliance entry, per role match).
    """
    prepared: list[PreparedFact] = []
    for link in links:
        # Re-filtered here, not trusted from the caller's own decision-time
        # signals - evidence can expire between an approval/selection and a
        # later drafting or trace-viewing run.
        if not (link.verified and link.validity_status == "valid"):
            continue
        if link.id in seen_evidence_ids:
            continue
        seen_evidence_ids.add(link.id)
        prepared.append(
            PreparedFact(
                label=f"evidence_{start_index + len(prepared)}",
                kind="evidence",
                excerpt=link.excerpt or "(no excerpt available)",
                evidence_link_id=link.id,
            )
        )
    return prepared


async def _facts_from_compliance(
    uow: UnitOfWork, tender_id: uuid.UUID, categories: frozenset[str]
) -> list[PreparedFact]:
    entries = await uow.compliance.list_compliance_entries_for_tender(tender_id)
    prepared: list[PreparedFact] = []
    seen_evidence_ids: set[uuid.UUID] = set()
    req_i = 0
    ev_i = 0
    for entry in entries:
        if not entry.human_approved:
            continue
        requirement = await uow.requirements.get(entry.requirement_id)
        if requirement is None or requirement.category not in categories:
            continue

        req_i += 1
        prepared.append(
            PreparedFact(
                label=f"requirement_{req_i}",
                kind="requirement",
                excerpt=requirement.requirement_text,
                requirement_id=requirement.id,
            )
        )

        links = await uow.compliance.list_evidence_for_compliance_entry(entry.id)
        new_evidence = _prepare_evidence_facts(links, seen_evidence_ids, start_index=ev_i + 1)
        prepared.extend(new_evidence)
        ev_i += len(new_evidence)
    return prepared


async def _facts_from_team(uow: UnitOfWork, tender_id: uuid.UUID) -> list[PreparedFact]:
    roles = await uow.talent.list_role_requirements_for_tender(tender_id)
    prepared: list[PreparedFact] = []
    seen_evidence_ids: set[uuid.UUID] = set()
    emp_i = 0
    ev_i = 0
    for role in roles:
        matches = await uow.talent.list_role_matches_for_role(role.id)
        for match in matches:
            if not match.human_selected:
                continue
            employee = await uow.knowledge.get_employee(match.employee_knowledge_item_id)
            emp_i += 1
            title = employee.current_title if employee else "role unspecified"
            prepared.append(
                PreparedFact(
                    label=f"employee_{emp_i}",
                    kind="employee",
                    excerpt=f"{title}, selected for role: {role.role_name}",
                    employee_knowledge_item_id=match.employee_knowledge_item_id,
                )
            )
            link_rows = await uow.talent.list_evidence_for_role_match(match.id)
            links = []
            for link_row in link_rows:
                if link_row.evidence_link_id in seen_evidence_ids:
                    continue
                link = await uow.compliance.get_evidence_link(link_row.evidence_link_id)
                if link is not None:
                    links.append(link)
            new_evidence = _prepare_evidence_facts(links, seen_evidence_ids, start_index=ev_i + 1)
            prepared.extend(new_evidence)
            ev_i += len(new_evidence)
    return prepared


async def _facts_from_pricing(
    uow: UnitOfWork, tender_id: uuid.UUID, start_index: int
) -> list[PreparedFact]:
    """Resolved pricing_findings give the Commercial Summary section real
    narrative context ("no critical pricing discrepancies remain
    unresolved"), but they can never become citable claim evidence: Part
    C's evidence_links.source_kind CHECK constraint has no pricing-sourced
    value, and every evidence_links row requires a real knowledge_item_id or
    source_chunk_id - a pricing_finding is neither. These facts are
    therefore offered with evidence_link_id left unset; if the LLM cites one
    anyway, materialize_claim_links resolves nothing for it (same
    skip-unresolvable posture as an unrecognized label), so a factual claim
    that leans on a pricing finding alone still correctly requires real
    compliance evidence to be marked grounded. See KNOWN_LIMITATIONS.md.
    """
    findings = await uow.commercial.list_pricing_findings_for_tender(tender_id)
    prepared: list[PreparedFact] = []
    for i, finding in enumerate(
        (f for f in findings if f.resolved), start=start_index
    ):
        prepared.append(
            PreparedFact(
                label=f"evidence_{i}",
                kind="evidence",
                excerpt=f"Resolved pricing finding ({finding.finding_type}): {finding.note}",
            )
        )
    return prepared


async def _build_grounded_context(
    uow: UnitOfWork, tender_id: uuid.UUID, template: SectionTemplate
) -> list[PreparedFact]:
    if template.name == "Company Overview":
        return []
    if template.name == "Proposed Team":
        return await _facts_from_team(uow, tender_id)
    if template.name == "Commercial Summary":
        compliance_facts = await _facts_from_compliance(
            uow, tender_id, _COMPLIANCE_CATEGORIES_BY_SECTION[template.name]
        )
        pricing_facts = await _facts_from_pricing(
            uow, tender_id, start_index=len(compliance_facts) + 1
        )
        return compliance_facts + pricing_facts
    return await _facts_from_compliance(
        uow, tender_id, _COMPLIANCE_CATEGORIES_BY_SECTION[template.name]
    )


async def _compliance_status_by_requirement(
    uow: UnitOfWork, tender_id: uuid.UUID
) -> dict[uuid.UUID, str]:
    entries = await uow.compliance.list_compliance_entries_for_tender(tender_id)
    return {entry.requirement_id: entry.status for entry in entries if entry.human_approved}


# ----------------------------------------------------------------------
# Deterministic claim-grounding enforcement (PRD 8.5 / E7 - the claim
# invariant B9's build list names, the third installment of the same
# write-time-downgrade pattern as enforce_evidence_sufficiency (B7) and
# enforce_gap_visibility (B8A))
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class GroundingResult:
    status: str
    reason: str


# TODO(human): implement enforce_claim_grounding.
#
# Steps (plain-English, not code):
#   1. If `claim.is_factual` is False: the claim is narrative, not a
#      checkable assertion - return GroundingResult(status="not_required",
#      reason=...). This is the ONLY thing allowed to produce
#      "not_required". Do not let which section the claim came from
#      influence this - a factual claim in "Company Overview" (the one
#      template with no pre-gated approved data behind it) must be judged
#      by exactly the same rule as a factual claim anywhere else. That's
#      the specific edge case this function has to get right: it would be
#      easy to accidentally treat "Company Overview" as inherently
#      narrative and skip real enforcement for it, which is exactly the
#      gap an ungrounded factual claim would slip through.
#   2. If `claim.is_factual` is True and `resolved_evidence_link_ids` is
#      empty: return GroundingResult(status="unsupported", reason=...).
#      E7's own rule: an unsupported factual claim stays VISIBLE with this
#      status, it is never silently dropped or downgraded to
#      "not_required" instead.
#   3. If `claim.claim_type == "compliance"` and `resolved_requirement_ids`
#      is non-empty: look up each resolved requirement's status in
#      `compliance_status_by_requirement` (a dict; a requirement_id missing
#      from it means no approved compliance decision exists for it yet -
#      treat that the same as a non-affirmative status, not as "no
#      opinion"). If ANY resolved requirement's status is not in
#      `_AFFIRMATIVE_COMPLIANCE_STATUSES` (imported at module level -
#      {"fully_compliant", "partially_compliant"}), the claim is asserting
#      a compliance position the tender's own approved decision doesn't
#      actually hold - return GroundingResult(status="needs_review",
#      reason=...) explaining which requirement's compliance status
#      disagrees, REGARDLESS of how much evidence the claim cites. This is
#      the "compliance position mismatch flagged" test - a claim cannot be
#      "grounded" just because it cites real evidence if what it's
#      actually claiming contradicts the tender's own recorded decision.
#   4. Otherwise (factual, has evidence, and no compliance-position
#      conflict): return GroundingResult(status="grounded", reason=...)
#      naming how many evidence items backed it.
#
# What's already available to use:
#   - `claim: ProposalClaimDraft` - has .is_factual (bool), .claim_type
#     (one of "capability"/"experience"/"team"/"compliance"/"commercial"/
#     "other"), .claim_text.
#   - `resolved_requirement_ids: list[uuid.UUID]` - the claim's
#     requirement_refs labels already resolved to real requirement rows
#     (unresolvable labels already dropped by the caller).
#   - `resolved_evidence_link_ids: list[uuid.UUID]` - same, for
#     evidence_refs.
#   - `compliance_status_by_requirement: dict[uuid.UUID, str]` - every
#     requirement in this tender with an approved compliance_entries row,
#     mapped to that entry's .status.
#   - `_AFFIRMATIVE_COMPLIANCE_STATUSES` - the frozenset defined above this
#     function.
#
# Why this is worth writing by hand: this is the one place PRD 8.5's
# invariant ("a factual proposal claim cannot be treated as grounded if it
# has no valid supporting evidence") and E7's compliance-position rule stop
# being something the model might get right and become a fact about what
# proposal_claims.grounding_status actually persists. Get step 1 wrong and
# an entire section of the proposal (Company Overview) becomes an
# unenforced back door for ungrounded claims; get step 3 wrong and a
# well-evidenced claim can still assert something the tender's own
# compliance record disagrees with, undetected.
def enforce_claim_grounding(
    claim: ProposalClaimDraft,
    resolved_requirement_ids: list[uuid.UUID],
    resolved_evidence_link_ids: list[uuid.UUID],
    compliance_status_by_requirement: dict[uuid.UUID, str],
) -> GroundingResult:
    if not claim.is_factual:
        return GroundingResult(
            status="not_required",
            reason="Narrative claims do not require factual evidence.",
        )
    if not resolved_evidence_link_ids:
        return GroundingResult(
            status="unsupported",
            reason="Factual claim has no resolved supporting evidence.",
        )
    if claim.claim_type == "compliance" and resolved_requirement_ids:
        for requirement_id in resolved_requirement_ids:
            requirement_status = compliance_status_by_requirement.get(requirement_id)
            
            if requirement_status not in _AFFIRMATIVE_COMPLIANCE_STATUSES:
                return GroundingResult(
                    status="needs_review",
                    reason=(
                        f"Compliance claim references requirement {requirement_id} "
                        f"with non-affirmative status {requirement_status!r}."
                    ),
                )
    return GroundingResult(
        status="grounded",
        reason=f"{len(resolved_evidence_link_ids)} number of evidence items support the audit record.",
    )

# ----------------------------------------------------------------------
# Persistence (E7: "Persist section and claims transactionally")
# ----------------------------------------------------------------------


async def materialize_claim_links(
    prepared: list[PreparedFact], claim: ProposalClaimDraft
) -> tuple[list[uuid.UUID], list[uuid.UUID], list[uuid.UUID]]:
    """Resolves the LLM's cited labels back to real ids, skipping any label
    that isn't in this section's own prepared fact list - a label from
    another section's context (or an invented one) never resolves, the same
    "skip rather than fabricate" posture materialize_evidence already
    established. This is also why "evidence from another organization" is
    structurally rejected: prepared is built fresh per section from only
    this tender's own already-approved rows, so there is no real id a
    cross-org label could ever resolve to.
    """
    by_label = {p.label: p for p in prepared}
    requirement_ids: list[uuid.UUID] = []
    for label in claim.requirement_refs:
        fact = by_label.get(label)
        if fact is not None and fact.requirement_id is not None:
            requirement_ids.append(fact.requirement_id)
    evidence_ids: list[uuid.UUID] = []
    for label in claim.evidence_refs:
        fact = by_label.get(label)
        if fact is not None and fact.evidence_link_id is not None:
            evidence_ids.append(fact.evidence_link_id)
    employee_ids: list[uuid.UUID] = []
    for label in claim.employee_refs:
        fact = by_label.get(label)
        if fact is not None and fact.employee_knowledge_item_id is not None:
            employee_ids.append(fact.employee_knowledge_item_id)
    return requirement_ids, evidence_ids, employee_ids


async def persist_section_and_claims(
    tender_id: uuid.UUID,
    section_name: str,
    draft: ProposalSectionDraft,
    prepared: list[PreparedFact],
    compliance_status_by_requirement: dict[uuid.UUID, str],
) -> ProposalSection:
    """Transactional per E7. Idempotent at the claim level via
    proposal_claims' own UNIQUE(section_id, content_fingerprint) - a retry
    that reproduces the same claim text reuses the existing row. Section
    versioning: reuse the latest version in place while it is still
    'draft' (an automated retry, nothing human-touched yet); once a
    section's latest version has moved to 'review'/'approved', a re-draft
    creates version+1 instead, leaving the reviewed/approved row untouched -
    the "auditable before/after" half of E7's own versioning rule. Bumps
    tenders.state_version ("proposal claim change", Part C) only when a
    claim was actually created or its grounding_status actually changed by
    this call, not on a pure no-op retry.
    """
    async with UnitOfWork() as uow:
        tender = await uow.tenders.get(tender_id)
        if tender is None:
            raise TenderNotFoundError()

        existing_section = await uow.proposal.get_latest_proposal_section(tender_id, section_name)
        if existing_section is None:
            section = await uow.proposal.create_proposal_section(
                tender_id=tender_id, section_name=section_name, content=draft.content, version=1
            )
        elif existing_section.status == "draft":
            section = await uow.proposal.update_proposal_section(
                existing_section.id, content=draft.content
            )
        else:
            section = await uow.proposal.create_proposal_section(
                tender_id=tender_id,
                section_name=section_name,
                content=draft.content,
                version=existing_section.version + 1,
            )

        claims_changed = False
        for claim_draft in draft.claims:
            requirement_ids, evidence_ids, employee_ids = await materialize_claim_links(
                prepared, claim_draft
            )
            grounding = enforce_claim_grounding(
                claim_draft, requirement_ids, evidence_ids, compliance_status_by_requirement
            )

            fingerprint = _fingerprint(
                str(section.id), claim_draft.claim_text, claim_draft.claim_type
            )
            existing_claim = await uow.proposal.get_proposal_claim_by_fingerprint(
                section.id, fingerprint
            )
            claim: ProposalClaim
            if existing_claim is None:
                claim = await uow.proposal.create_proposal_claim(
                    section_id=section.id,
                    claim_text=claim_draft.claim_text,
                    claim_type=claim_draft.claim_type,
                    is_factual=claim_draft.is_factual,
                    grounding_status=grounding.status,
                    content_fingerprint=fingerprint,
                )
                claims_changed = True
            else:
                claim = existing_claim
                if claim.grounding_status != grounding.status:
                    claim = await uow.proposal.update_proposal_claim(
                        claim.id, grounding_status=grounding.status
                    )
                    claims_changed = True

            for requirement_id in requirement_ids:
                await uow.proposal.link_claim_requirement(claim.id, requirement_id)
            for evidence_link_id in evidence_ids:
                await uow.proposal.link_claim_evidence(claim.id, evidence_link_id)
            for employee_id in employee_ids:
                await uow.proposal.link_claim_employee(claim.id, employee_id)

        if claims_changed:
            # Part C's State-version invariant: "proposal claim change" is
            # one of the listed graph-relevant mutations.
            await uow.tenders.bump_state_version(tender.id)
        await uow.commit()
        return section


# ----------------------------------------------------------------------
# Full per-tender drafting pipeline
# ----------------------------------------------------------------------


async def draft_proposal_for_tender(
    client: LLMClient,
    model: str,
    tender_id: uuid.UUID,
    workflow_run_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
) -> list[ProposalSection]:
    """The full pipeline: for every section template that has grounding
    data (or is always_on), build its grounded context, draft it, enforce
    the claim invariant, and persist. Mirrors evaluate_compliance_for_
    requirement / extract_and_persist_roles's own "one function assembles
    the whole call chain" shape.
    """
    async with UnitOfWork() as uow:
        compliance_status = await _compliance_status_by_requirement(uow, tender_id)

    sections: list[ProposalSection] = []
    for template in SECTION_TEMPLATES:
        async with UnitOfWork() as uow:
            prepared = await _build_grounded_context(uow, tender_id, template)

        if not prepared and not template.always_on:
            # Mirrors R2 §B2.2's own precedent (commercial analysis skipped
            # tender-wide when no supported pricing documents exist): no
            # underlying approved facts, no LLM call, no section row.
            continue

        facts = to_llm_facts(prepared)
        draft = await draft_section(
            client,
            model,
            template.name,
            template.guidance,
            facts,
            tender_id,
            workflow_run_id=workflow_run_id,
            task_id=task_id,
        )
        section = await persist_section_and_claims(
            tender_id, template.name, draft, prepared, compliance_status
        )
        sections.append(section)
    return sections


# ----------------------------------------------------------------------
# Human review / editing (G10)
# ----------------------------------------------------------------------


async def list_proposal_sections_for_tender(tender_id: uuid.UUID) -> list[ProposalSection]:
    async with UnitOfWork() as uow:
        return await uow.proposal.list_proposal_sections_for_tender(tender_id)


async def list_claims_for_section(section_id: uuid.UUID) -> list[ProposalClaim]:
    """B13 addition: the repository method (`list_proposal_claims_for_
    section`) has existed since B9, but nothing above it ever exposed a way
    to discover a section's claim ids - Part G's own G10 never documented a
    list-claims route, and get_proposal_trace needs a claim_id handed to it
    rather than discovering one. Screen 13 needs exactly this list to let a
    human click a claim into its trace (screen 16), so it's wired the rest
    of the way here rather than left as a repository-only capability.
    """
    async with UnitOfWork() as uow:
        return await uow.proposal.list_proposal_claims_for_section(section_id)


async def update_section_content(
    section_id: uuid.UUID, content: str, actor_user_id: uuid.UUID
) -> ProposalSection:
    """G10's PATCH /proposal/sections/{section_id} - a human directly
    editing drafted content. Always audited, mirrors override_compliance_
    entry's shape. Does not touch claims or grounding_status - editing the
    prose is not the same act as re-grounding it, and a human's edit is not
    itself a new LLM draft to re-run enforce_claim_grounding against.
    """
    async with UnitOfWork() as uow:
        section = await uow.proposal.get_proposal_section(section_id)
        if section is None:
            raise ProposalSectionNotFoundError()
        tender = await uow.tenders.get(section.tender_id)
        if tender is None:
            raise TenderNotFoundError()

        before = {"content": section.content, "status": section.status}
        updated = await uow.proposal.update_proposal_section(
            section_id, content=content, status="review"
        )
        await uow.audit.create(
            organization_id=tender.organization_id,
            actor_type="user",
            actor_id=str(actor_user_id),
            action="proposal_section_edit",
            tender_id=tender.id,
            target_type="proposal_section",
            target_id=section_id,
            before=before,
            after={"content": updated.content, "status": updated.status},
        )
        await uow.commit()
        return updated


# ----------------------------------------------------------------------
# Traceability (G10: GET /proposal/claims/{claim_id}/trace)
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class ClaimEvidenceTraceEntry:
    evidence_link_id: uuid.UUID
    source_kind: str
    excerpt: str | None
    verified: bool
    validity_status: str
    knowledge_item_id: uuid.UUID | None
    knowledge_item_title: str | None
    document_id: uuid.UUID | None
    document_filename: str | None


@dataclass(frozen=True)
class ProposalClaimTrace:
    claim_id: uuid.UUID
    claim_text: str
    claim_type: str
    is_factual: bool
    grounding_status: str
    section_id: uuid.UUID
    section_name: str
    requirement_ids: list[uuid.UUID]
    employee_knowledge_item_ids: list[uuid.UUID]
    evidence: list[ClaimEvidenceTraceEntry]
    # Re-derived at view time from the claim's own section, reusing exactly
    # the same _build_grounded_context call the drafting node made (not a
    # second, potentially-diverging copy of its scoping logic) - what's
    # CURRENTLY in scope for this section, after whatever
    # approvals/validity changes happened since drafting ran. Not a record
    # of what was true when the model ran, and not filtered to a "relevant"
    # claim_type either - evidence_links.source_kind and
    # proposal_claims.claim_type are unrelated vocabularies with no mapping
    # between them (see KNOWN_LIMITATIONS.md). Excludes anything already in
    # `evidence` above, so cited and in-scope-but-uncited never overlap.
    currently_in_scope_evidence: list[ClaimEvidenceTraceEntry]
    # False for "Company Overview" (SECTION_TEMPLATES' one template that's
    # deliberately never data-gated - KNOWN_LIMITATIONS.md's "[B9] Section
    # templates are an interpretation" entry) and for any section name that
    # doesn't match a known template. Lets the drawer render "this section
    # isn't data-gated" instead of an unexplained empty list.
    section_is_data_gated: bool


async def _resolve_evidence_trace_entry(
    uow: UnitOfWork, evidence_link_id: uuid.UUID
) -> ClaimEvidenceTraceEntry | None:
    link = await uow.compliance.get_evidence_link(evidence_link_id)
    if link is None:
        return None
    knowledge_item = None
    if link.knowledge_item_id is not None:
        knowledge_item = await uow.knowledge.get_knowledge_item(link.knowledge_item_id)

    document_id = None
    document_filename = None
    if link.source_chunk_id is not None:
        chunk = await uow.documents.get_chunk(link.source_chunk_id)
        if chunk is not None:
            document_id = chunk.document_id
            document = await uow.documents.get(chunk.document_id)
            if document is not None:
                document_filename = document.filename

    return ClaimEvidenceTraceEntry(
        evidence_link_id=link.id,
        source_kind=link.source_kind,
        excerpt=link.excerpt,
        verified=link.verified,
        validity_status=link.validity_status,
        knowledge_item_id=link.knowledge_item_id,
        knowledge_item_title=knowledge_item.title if knowledge_item else None,
        document_id=document_id,
        document_filename=document_filename,
    )


async def get_proposal_trace(claim_id: uuid.UUID) -> ProposalClaimTrace:
    """B9's own build-list wording, walked literally: claim -> evidence ->
    source, plus the requirement(s) and employee(s) the claim cites. Same
    per-evidence-link resolution shape as get_compliance_trace.

    Also answers the question B9's own citation-correctness measurement
    couldn't: for an unsupported claim, was evidence thin, or did evidence
    exist and simply go uncited? currently_in_scope_evidence re-runs
    _build_grounded_context for the claim's own section - the exact
    function the drafting node called - rather than a parallel guess at
    what "available" should mean.
    """
    async with UnitOfWork() as uow:
        claim = await uow.proposal.get_proposal_claim(claim_id)
        if claim is None:
            raise ProposalClaimNotFoundError()
        section = await uow.proposal.get_proposal_section(claim.section_id)
        if section is None:
            raise ProposalSectionNotFoundError()

        requirement_links = await uow.proposal.list_requirements_for_claim(claim.id)
        employee_links = await uow.proposal.list_employees_for_claim(claim.id)
        evidence_links = await uow.proposal.list_evidence_for_claim(claim.id)

        cited_evidence_ids: set[uuid.UUID] = set()
        evidence_entries: list[ClaimEvidenceTraceEntry] = []
        for link_row in evidence_links:
            entry = await _resolve_evidence_trace_entry(uow, link_row.evidence_link_id)
            if entry is None:
                continue
            cited_evidence_ids.add(entry.evidence_link_id)
            evidence_entries.append(entry)

        template = next((t for t in SECTION_TEMPLATES if t.name == section.section_name), None)
        section_is_data_gated = template is not None and template.name != "Company Overview"
        currently_in_scope_entries: list[ClaimEvidenceTraceEntry] = []
        if template is not None:
            facts = await _build_grounded_context(uow, section.tender_id, template)
            in_scope_ids = {
                f.evidence_link_id
                for f in facts
                if f.kind == "evidence" and f.evidence_link_id is not None
            }
            for link_id in in_scope_ids - cited_evidence_ids:
                entry = await _resolve_evidence_trace_entry(uow, link_id)
                if entry is not None:
                    currently_in_scope_entries.append(entry)

    return ProposalClaimTrace(
        claim_id=claim.id,
        claim_text=claim.claim_text,
        claim_type=claim.claim_type,
        is_factual=claim.is_factual,
        grounding_status=claim.grounding_status,
        section_id=section.id,
        section_name=section.section_name,
        requirement_ids=[r.requirement_id for r in requirement_links],
        employee_knowledge_item_ids=[e.employee_knowledge_item_id for e in employee_links],
        evidence=evidence_entries,
        currently_in_scope_evidence=currently_in_scope_entries,
        section_is_data_gated=section_is_data_gated,
    )
