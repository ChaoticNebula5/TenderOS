import logging
import uuid
from dataclasses import dataclass
from datetime import date

from tenderos_backend.agents.llm.client import LLMClient
from tenderos_backend.agents.schemas.talent import CandidateScoring, RoleExtractionOutput
from tenderos_backend.agents.talent.extract_roles import RequirementRef, extract_roles
from tenderos_backend.agents.talent.score_candidates import (
    CandidateProfileSummary,
    TalentEvidenceCandidateRef,
    score_candidate,
)
from tenderos_backend.core.hashing import sha256_hex
from tenderos_backend.db.models.knowledge import Employee
from tenderos_backend.db.models.talent import RoleMatch, RoleRequirement
from tenderos_backend.db.unit_of_work import UnitOfWork


logger = logging.getLogger(__name__)


class TenderNotFoundError(Exception):
    pass


class RoleRequirementNotFoundError(Exception):
    pass


class RoleMatchNotFoundError(Exception):
    pass


class CrossOrganizationRoleError(Exception):
    """Raised when the caller's authorized organization_id doesn't match
    role_requirement_id's own tender's organization. Not currently
    reachable via the one write path that exists today (role_matches rows
    are only ever created by evaluate_role_match, scoped to
    find_candidate_pool(organization_id) - same-org candidates only, so a
    cross-org role_match can't exist in the DB yet) - but that safety comes
    from a DIFFERENT function's own scoping, two layers away, not from
    anything set_candidate_selection itself enforces. Same shape as
    CrossOrganizationTenderError in knowledge_service.py (which WAS
    reachable): a guard that looked complete while silently depending on
    something it never checked. Any future second write path into
    role_matches (bulk admin tooling, an import, a fix-up script) makes
    this live with no code change needed here at all - the vulnerability
    is already written, only the input path was missing.
    """


def _fingerprint(*parts: str) -> str:
    return sha256_hex("|".join(parts).encode("utf-8"))


async def _evidence_validity_status(
    uow: UnitOfWork, knowledge_item_id: uuid.UUID, today: date
) -> str:
    """Same freshly-computed validity_status compliance_service.py's own
    _compute_candidate_signals derives for compliance evidence - a
    knowledge_item's status/valid_until, not a value evaluate_role_match's
    own create_evidence_link call left unset (defaulting to the column's
    "unknown", which _facts_from_team's validity_status=="valid" filter can
    never match - see KNOWN_LIMITATIONS.md's B0-B11 catch-up review).
    """
    knowledge_item = await uow.knowledge.get_knowledge_item(knowledge_item_id)
    if knowledge_item is None:
        return "unknown"
    if knowledge_item.valid_until is not None and knowledge_item.valid_until < today:
        return "expired"
    if knowledge_item.status == "archived":
        return "invalid"
    return "valid"


# ----------------------------------------------------------------------
# Role extraction
# ----------------------------------------------------------------------


async def persist_extracted_roles(
    tender_id: uuid.UUID, extraction: RoleExtractionOutput, refs: list[RequirementRef]
) -> list[RoleRequirement]:
    """Resolves extract_roles.py's label-based output back to real
    requirement_ids and persists role_requirements + role_requirement_sources.
    Idempotent via role_requirements' own UNIQUE(tender_id,
    content_fingerprint) - re-running extraction for a tender updates
    nothing for a role whose fingerprint already exists, matching
    persist_compliance_decision's own "retry does not duplicate" shape.
    A role that resolves to zero real source requirements (every ref label
    it cited was unresolvable) is dropped, not persisted with an empty
    source list - same "drop rather than fabricate provenance" posture
    extract.py's own unresolvable source_chunk_ref handling already
    established for requirement extraction.
    """
    by_label = {ref.label: ref for ref in refs}
    persisted: list[RoleRequirement] = []

    async with UnitOfWork() as uow:
        for role in extraction.roles:
            resolved_refs = [by_label[label] for label in role.source_requirement_refs if label in by_label]
            if not resolved_refs:
                continue

            fingerprint = _fingerprint(
                str(tender_id), role.role_name, ",".join(sorted(r.label for r in resolved_refs))
            )
            existing = await uow.talent.get_role_requirement_by_fingerprint(tender_id, fingerprint)
            if existing is not None:
                persisted.append(existing)
                continue

            # role_requirements.source_chunk_id is a single nullable pointer
            # (Part C), not a list - the full one-to-many linkage lives in
            # role_requirement_sources below. Using the first resolved
            # source's chunk gives this role a direct traceability anchor
            # consistent with how requirements.source_chunk_id itself works,
            # rather than leaving it null when a real chunk is available.
            role_requirement = await uow.talent.create_role_requirement(
                tender_id=tender_id,
                role_name=role.role_name,
                content_fingerprint=fingerprint,
                min_experience_years=role.min_experience_years,
                required_skills=role.required_skills,
                required_certs=role.required_certs,
                source_chunk_id=resolved_refs[0].source_chunk_id,
            )
            for ref in resolved_refs:
                await uow.talent.link_role_requirement_source(role_requirement.id, ref.requirement_id)
            persisted.append(role_requirement)

        await uow.commit()
    return persisted


async def extract_and_persist_roles(
    client: LLMClient,
    model: str,
    tender_id: uuid.UUID,
    workflow_run_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
) -> list[RoleRequirement]:
    async with UnitOfWork() as uow:
        requirements = await uow.requirements.list_for_tender(tender_id)
    staffing = [r for r in requirements if r.category == "staffing"]
    if not staffing:
        return []

    refs = [
        RequirementRef(
            label=f"req_{i}",
            requirement_id=r.id,
            requirement_text=r.requirement_text,
            source_chunk_id=r.source_chunk_id,
        )
        for i, r in enumerate(staffing, start=1)
    ]
    extraction = await extract_roles(
        client, model, refs, tender_id, workflow_run_id=workflow_run_id, task_id=task_id
    )
    return await persist_extracted_roles(tender_id, extraction, refs)


# ----------------------------------------------------------------------
# Candidate signals (mechanical, pre-LLM - mirrors compliance_service.py's
# _compute_candidate_signals)
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class CertFinding:
    required_cert: str
    matched: bool
    validity_status: str  # "valid" / "expired" / "unverified" (no match found)


@dataclass(frozen=True)
class CandidateTalentSignals:
    cert_findings: list[CertFinding]
    profile_stale_or_unreviewed: bool
    availability_unknown: bool


async def _compute_candidate_talent_signals(
    uow: UnitOfWork, employee: Employee, required_certs: list[str], today: date
) -> CandidateTalentSignals:
    certifications = await uow.knowledge.list_certifications_for_employee(employee.knowledge_item_id)
    cert_items = []
    for cert in certifications:
        item = await uow.knowledge.get_knowledge_item(cert.knowledge_item_id)
        if item is not None:
            cert_items.append(item)

    cert_findings: list[CertFinding] = []
    for required in required_certs:
        # Same normalized-substring match B6's employee dedup already uses
        # and KNOWN_LIMITATIONS.md already documents as a known scope
        # boundary (not fuzzy similarity) - reused here rather than
        # inventing a second matching heuristic for the same class of
        # problem.
        match = next(
            (item for item in cert_items if required.lower() in item.title.lower()), None
        )
        if match is None:
            cert_findings.append(
                CertFinding(required_cert=required, matched=False, validity_status="unverified")
            )
        elif match.valid_until is not None and match.valid_until < today:
            cert_findings.append(
                CertFinding(required_cert=required, matched=True, validity_status="expired")
            )
        else:
            cert_findings.append(
                CertFinding(required_cert=required, matched=True, validity_status="valid")
            )

    return CandidateTalentSignals(
        cert_findings=cert_findings,
        profile_stale_or_unreviewed=employee.profile_review_status in ("stale", "needs_review"),
        availability_unknown=employee.availability_status == "unknown",
    )


async def _gather_candidate_evidence(
    uow: UnitOfWork, employee: Employee
) -> list[TalentEvidenceCandidateRef]:
    """Evidence candidates shown to the scoring LLM. Certifications and
    past projects/capabilities have no per-employee link in this schema
    except certifications' own holder_employee_item_id (Part C) - so the
    realistic evidence set for one candidate is their own employee
    knowledge_item (skills/experience, from the resume-derived profile)
    plus each certification they actually hold, not a broader semantic
    search the way compliance evidence retrieval needed (there is no
    "search query" here - the candidate pool is already a fixed, small,
    already-identified set of employees, not something to retrieve).
    """
    refs: list[TalentEvidenceCandidateRef] = [
        TalentEvidenceCandidateRef(
            label="evidence_1",
            knowledge_item_id=employee.knowledge_item_id,
            source_kind="employee",
            excerpt=(
                f"{employee.current_title or 'Role unspecified'}, "
                f"{employee.years_experience if employee.years_experience is not None else 'unknown'} "
                "years of experience."
            ),
        )
    ]
    certifications = await uow.knowledge.list_certifications_for_employee(employee.knowledge_item_id)
    for i, cert in enumerate(certifications, start=2):
        item = await uow.knowledge.get_knowledge_item(cert.knowledge_item_id)
        if item is None:
            continue
        expiry = f", expires {item.valid_until}" if item.valid_until else ""
        refs.append(
            TalentEvidenceCandidateRef(
                label=f"evidence_{i}",
                knowledge_item_id=item.id,
                source_kind="certification",
                excerpt=f"{item.title} (issuer: {cert.issuer or 'unknown'}{expiry})",
            )
        )
    return refs


# ----------------------------------------------------------------------
# Deterministic gap/warning enforcement (PRD 10.9 / B8A's own Tests)
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class EnforcedScoring:
    strengths: list[dict]
    gaps: list[dict]
    enforced_notes: list[str]


# Keyword heuristic, not semantic detection - same known-limitation class as
# B6's non-fuzzy employee-name matching (KNOWN_LIMITATIONS.md). A single
# "availab" substring check missed phrasings like "ready to start
# immediately" that don't contain that root at all; this widens coverage to
# common ways a model might phrase an availability claim without being
# asked to. Still not exhaustive - a sufficiently unusual phrasing can still
# slip through - but the availability gap below is added unconditionally
# regardless of what survives here, so the caveat is never silently absent
# even when a stray strength does.
_AVAILABILITY_KEYWORDS = (
    "availab",  # available, availability, unavailable
    "ready to start",
    "ready to begin",
    "immediate start",
    "start immediately",
    "can start",
    "on the bench",
    "deployable",
)


def _mentions_availability(criterion: str) -> bool:
    normalized = criterion.strip().casefold()
    return any(keyword in normalized for keyword in _AVAILABILITY_KEYWORDS)


# TODO(human): implement enforce_gap_visibility.
#
# Steps (plain-English, not code):
#   1. Start from the LLM's own scoring.strengths and scoring.gaps
#      (CandidateScoring - each strength/gap is a small object with a
#      `criterion` and either `evidence_ref` (strengths) or `status`
#      (gaps)). You are not replacing these outright - you are checking
#      whether the mechanical signals below are already reflected in them,
#      and adding a gap yourself for anything that isn't.
#   2. For each entry in signals.cert_findings where validity_status is
#      "expired" or "unverified" (i.e. NOT "valid"): check whether
#      scoring.gaps already contains a gap for that required_cert's
#      criterion. If yes, leave it as the LLM wrote it. If no, you must add
#      one yourself - this is the actual invariant this function exists to
#      guarantee (mirrors enforce_evidence_sufficiency's "zero sufficient
#      evidence -> forced downgrade" bullet, just for a different domain:
#      an unverified/expired required certification must NEVER be silently
#      absent from gaps, regardless of what the LLM decided to write).
#   3. If signals.profile_stale_or_unreviewed is True: decide whether this
#      needs its own added gap (e.g. criterion="profile currency",
#      status="stale") the same way, if the LLM's own gaps don't already
#      cover it. Also decide - this is a real policy call, not a lookup -
#      whether a stale/needs-review profile should also cap the candidate's
#      score somehow, or whether surfacing it as a gap is sufficient on its
#      own without touching the score.
#   4. If signals.availability_unknown is True: the Build Guide test is
#      specific here - "unknown availability never rendered as available."
#      Decide how to guarantee that concretely against THIS candidate's
#      strengths list: does an unknown-availability candidate ever get an
#      "available" strength at all, or does availability only ever appear
#      as a gap/note when it's unknown? Whatever you decide, it must hold
#      even if the LLM's own output claims availability as a strength -
#      you may need to strip or override a strength the LLM wrote, not just
#      add a gap alongside it.
#   5. Return an EnforcedScoring with the final strengths/gaps (as plain
#      dicts matching TalentStrength/TalentGap's own field shapes -
#      {"criterion":..., "evidence_ref":...} / {"criterion":...,
#      "status":...}) and `enforced_notes`: a list of short human-readable
#      strings, one per gap/change YOU added or altered that the LLM's own
#      output didn't already have - this is what a human reviewer reads to
#      understand what the deterministic layer changed versus what the
#      model itself said, the same transparency enforce_evidence_
#      sufficiency's own `reason` field gives compliance's write-time
#      downgrades.
#
# What's already available to use:
#   - `scoring: CandidateScoring` - has .score, .strengths (list of
#     TalentStrength: .criterion, .evidence_ref), .gaps (list of TalentGap:
#     .criterion, .status).
#   - `signals: CandidateTalentSignals` - already computed mechanically
#     above: .cert_findings (list of CertFinding: .required_cert, .matched,
#     .validity_status), .profile_stale_or_unreviewed (bool),
#     .availability_unknown (bool). You are not deriving these yourself,
#     just deciding what the persisted row must show given them.
#
# Why this is worth writing by hand: this is B8A's exact analog of B7's
# enforce_evidence_sufficiency - the one place PRD 10.9's promise ("surface
# explicit gaps and stale or unknown profile facts") stops being a hope
# about the model's own diligence and becomes a fact about what
# role_matches actually persists. Get it wrong and either a real
# disqualifying gap (an expired required cert, an unknown-availability
# candidate shown as available) silently reaches the human reviewer's
# selection screen looking fine when it isn't, or an honest candidate gets
# a gap piled onto them that the mechanical signal doesn't actually support.
def enforce_gap_visibility(
    scoring: CandidateScoring, signals: CandidateTalentSignals
) -> EnforcedScoring:
    strengths = [
        strength.model_dump()
        for strength in scoring.strengths
    ]
    gaps = [
        gap.model_dump()
        for gap in scoring.gaps
    ]
    enforced_notes : list[str] = []
    
    existing_gap_criteria = {
        gap["criterion"].strip().casefold()
        for gap in gaps
    }
    
    for finding in signals.cert_findings:
        if finding.validity_status == "valid":
            continue
        
        normalized_criterion = finding.required_cert.strip().casefold()
        if normalized_criterion in existing_gap_criteria:
            continue
        
        gaps.append({
            "criterion": finding.required_cert,
            "status": finding.validity_status
        })
        
        existing_gap_criteria.add(normalized_criterion)
        enforced_notes.append(
            f"Added {finding.validity_status} certification gap: "
            f"{finding.required_cert}"
        )
        
    profile_criterion = "profile currency"

    if (
        signals.profile_stale_or_unreviewed
        and profile_criterion not in existing_gap_criteria
    ):
        gaps.append(
            {
                "criterion": profile_criterion,
                "status": "stale",
            }
        )
        existing_gap_criteria.add(profile_criterion)
        enforced_notes.append(
            "Added profile currency gap because the profile is stale or unreviewed"
        )
    if signals.availability_unknown:
        retained_strengths: list[dict] = []
        
        for strength in strengths:
            if _mentions_availability(strength["criterion"]):
                enforced_notes.append(
                    f"Removed availability strength {strength['criterion']!r} "
                    "because availability is unknown"
                )
                continue
            retained_strengths.append(strength)
        strengths = retained_strengths
        
        availability_criterion = "availability"

        if availability_criterion not in existing_gap_criteria:
            gaps.append(
                {
                    "criterion": availability_criterion,
                    "status": "unknown",
                }
            )
            existing_gap_criteria.add(availability_criterion)
            enforced_notes.append(
                "Added availability gap because availability is unknown"
            )
    return EnforcedScoring(
        strengths=strengths,
        gaps=gaps,
        enforced_notes=enforced_notes,
    )
            


# ----------------------------------------------------------------------
# Candidate matching / scoring
# ----------------------------------------------------------------------

async def find_candidate_pool(organization_id: uuid.UUID) -> list[Employee]:
    async with UnitOfWork() as uow:
        return await uow.knowledge.list_candidate_pool_employees(organization_id)


async def evaluate_role_match(
    client: LLMClient,
    model: str,
    role_requirement_id: uuid.UUID,
    employee: Employee,
    today: date | None = None,
) -> RoleMatch:
    """The full per-(role, candidate) pipeline: gather evidence, compute
    mechanical signals, call the LLM, enforce gap visibility, persist.
    Mirrors evaluate_compliance_for_requirement's shape exactly.
    """
    today = today or date.today()
    async with UnitOfWork() as uow:
        role = await uow.talent.get_role_requirement(role_requirement_id)
        if role is None:
            raise RoleRequirementNotFoundError()
        signals = await _compute_candidate_talent_signals(uow, employee, role.required_certs, today)
        evidence = await _gather_candidate_evidence(uow, employee)
        profile = CandidateProfileSummary(
            current_title=employee.current_title,
            years_experience=(
                float(employee.years_experience) if employee.years_experience is not None else None
            ),
            professional_location=employee.professional_location,
            availability_status=employee.availability_status,
            profile_review_status=employee.profile_review_status,
            signal_notes=[
                f"{f.required_cert}: {f.validity_status}"
                for f in signals.cert_findings
                if f.validity_status != "valid"
            ],
        )

    scoring = await score_candidate(
        client,
        model,
        role.role_name,
        role.min_experience_years,
        role.required_skills,
        role.required_certs,
        profile,
        evidence,
        role.tender_id,
    )
    enforced = enforce_gap_visibility(scoring, signals)

    by_label = {e.label: e for e in evidence}
    async with UnitOfWork() as uow:
        existing = await uow.talent.get_role_match_by_candidate(
            role_requirement_id, employee.knowledge_item_id
        )
        if existing is None:
            match = await uow.talent.create_role_match(
                role_requirement_id=role_requirement_id,
                employee_knowledge_item_id=employee.knowledge_item_id,
                score=scoring.score,
                strengths=enforced.strengths,
                gaps=enforced.gaps,
            )
        else:
            match = await uow.talent.update_role_match(
                existing.id, score=scoring.score, strengths=enforced.strengths, gaps=enforced.gaps
            )

        for strength in enforced.strengths:
            ref_label = strength.get("evidence_ref")
            candidate = by_label.get(ref_label) if ref_label else None
            if candidate is None:
                continue
            fingerprint = _fingerprint(
                str(role.tender_id), str(candidate.knowledge_item_id), candidate.source_kind
            )
            link = await uow.compliance.get_evidence_link_by_fingerprint(role.tender_id, fingerprint)
            if link is None:
                validity_status = await _evidence_validity_status(
                    uow, candidate.knowledge_item_id, today
                )
                link = await uow.compliance.create_evidence_link(
                    tender_id=role.tender_id,
                    source_kind=candidate.source_kind,
                    content_fingerprint=fingerprint,
                    knowledge_item_id=candidate.knowledge_item_id,
                    excerpt=candidate.excerpt,
                    verified=True,
                    validity_status=validity_status,
                )
            await uow.talent.link_role_match_evidence(match.id, link.id, strength["criterion"])

        await uow.commit()
        return match


# ----------------------------------------------------------------------
# Human selection (G8)
# ----------------------------------------------------------------------


async def set_candidate_selection(
    role_requirement_id: uuid.UUID,
    employee_knowledge_item_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    organization_id: uuid.UUID,
    selected: bool,
) -> RoleMatch:
    """G8's POST /roles/{role_id}/select-candidate - the route's path only
    carries role_id, so the request body identifies the candidate by
    employee_knowledge_item_id and this resolves it to the real role_match
    row before delegating to _set_human_selected. One locked route handles
    both select and deselect (selected=True/False in the body) rather than
    inventing a second route G8 doesn't define.

    `organization_id` is the org the CALLER was actually authorized for
    (resolved by the route independently of role_requirement_id) - checked
    here against role_requirement_id's own tender, same reasoning
    knowledge_service.set_knowledge_selection's identical parameter
    documents. Not currently reachable through the one write path that
    creates role_matches rows today (evaluate_role_match only ever scores
    same-org candidates), but this function has no way to know that stays
    true - it must not rely on a different function's own scoping to stay
    safe, the exact gap that made the original knowledge-selection guard
    incomplete despite looking finished.
    """
    async with UnitOfWork() as uow:
        role = await uow.talent.get_role_requirement(role_requirement_id)
        if role is None:
            raise RoleMatchNotFoundError()
        tender = await uow.tenders.get(role.tender_id)
        if tender is None:
            raise RoleMatchNotFoundError()
        if tender.organization_id != organization_id:
            logger.warning(
                "cross-organization role-candidate selection attempt: actor=%s "
                "authorized_org=%s role_requirement=%s (org=%s)",
                actor_user_id, organization_id, role_requirement_id, tender.organization_id,
            )
            raise CrossOrganizationRoleError()
        match = await uow.talent.get_role_match_by_candidate(
            role_requirement_id, employee_knowledge_item_id
        )
    if match is None:
        raise RoleMatchNotFoundError()
    return await _set_human_selected(match.id, actor_user_id, selected=selected)


async def select_candidate(role_match_id: uuid.UUID, actor_user_id: uuid.UUID) -> RoleMatch:
    return await _set_human_selected(role_match_id, actor_user_id, selected=True)


async def deselect_candidate(role_match_id: uuid.UUID, actor_user_id: uuid.UUID) -> RoleMatch:
    # Build Guide test: "deselecting employee does not delete employee" -
    # this only ever flips human_selected back to False, never deletes the
    # role_matches row, same "update, never delete" shape Part C's own
    # Selection invariant locks for tender_knowledge_selections.
    return await _set_human_selected(role_match_id, actor_user_id, selected=False)


async def _set_human_selected(
    role_match_id: uuid.UUID, actor_user_id: uuid.UUID, selected: bool
) -> RoleMatch:
    async with UnitOfWork() as uow:
        match = await uow.talent.get_role_match(role_match_id)
        if match is None:
            raise RoleMatchNotFoundError()
        role = await uow.talent.get_role_requirement(match.role_requirement_id)
        if role is None:
            raise RoleRequirementNotFoundError()
        tender = await uow.tenders.get(role.tender_id)
        if tender is None:
            raise TenderNotFoundError()

        before = {"human_selected": match.human_selected}
        updated = await uow.talent.update_role_match(role_match_id, human_selected=selected)
        await uow.audit.create(
            organization_id=tender.organization_id,
            actor_type="user",
            actor_id=str(actor_user_id),
            action="role_select" if selected else "role_deselect",
            tender_id=tender.id,
            target_type="role_match",
            target_id=role_match_id,
            before=before,
            after={"human_selected": selected},
        )
        # Part C's State-version invariant: "role assignment change" is one
        # of the listed graph-relevant mutations.
        await uow.tenders.bump_state_version(tender.id)
        await uow.commit()
        return updated


async def get_role_requirement(role_requirement_id: uuid.UUID) -> RoleRequirement | None:
    async with UnitOfWork() as uow:
        return await uow.talent.get_role_requirement(role_requirement_id)


async def list_role_requirements_for_tender(tender_id: uuid.UUID) -> list[RoleRequirement]:
    async with UnitOfWork() as uow:
        return await uow.talent.list_role_requirements_for_tender(tender_id)


async def list_role_matches_for_role(role_requirement_id: uuid.UUID) -> list[RoleMatch]:
    async with UnitOfWork() as uow:
        return await uow.talent.list_role_matches_for_role(role_requirement_id)
