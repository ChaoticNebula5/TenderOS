import logging
import uuid
from dataclasses import dataclass
from enum import StrEnum

from tenderos_backend.agents.knowledge.dedup import normalize_employee_name
from tenderos_backend.agents.llm.client import LLMClient, LLMError, LLMResult, call_with_repair
from tenderos_backend.agents.prompts.loader import bundle_version, load_prompt
from tenderos_backend.agents.schemas.knowledge import (
    ExtractedProjectMention,
    RelationshipExtractionOutput,
)
from tenderos_backend.db.models.document import DocumentChunk
from tenderos_backend.db.unit_of_work import UnitOfWork

logger = logging.getLogger(__name__)

_VERIFIED_CONFIDENCE = 0.85
_REVIEW_CONFIDENCE = 0.60
_TOKEN_OVERLAP_THRESHOLD = 0.75

_GENERIC_PROJECT_WORDS = {
    "project",
    "program",
    "programme",
    "implementation",
    "services",
    "solution",
    "system",
}


@dataclass(frozen=True)
class ProjectCandidateRef:
    """One candidate project shown to the LLM, and the real ID it resolves
    back to after parsing - mirrors agents/requirements/batch.py's ChunkRef
    label -> real-ID pattern, for the same "no fabricated IDs" reason.
    """

    label: str
    knowledge_item_id: uuid.UUID
    name: str
    client: str | None = None
    year: str | None = None


@dataclass(frozen=True)
class WorkedOnCandidate:
    """One LLM-proposed WORKED_ON edge, resolved back to a real project -
    the raw material resolve_worked_on_verification (below) adjudicates."""

    project: ProjectCandidateRef
    llm_confidence: float
    llm_reasoning: str


@dataclass(frozen=True)
class CapabilityCandidateRef:
    """DEMONSTRATES' analog of ProjectCandidateRef above - same label ->
    real-ID resolution pattern, just for capabilities instead of projects.
    """

    label: str
    knowledge_item_id: uuid.UUID
    name: str
    description: str | None = None


@dataclass(frozen=True)
class DemonstratesCandidate:
    """One LLM-proposed DEMONSTRATES edge (Project -> Capability), resolved
    back to a real capability - the source project is identified by its
    knowledge_item_id at the call site (import_demonstrates_relationship),
    not carried here, since a DEMONSTRATES call is already scoped to one
    project the same way a WORKED_ON call is already scoped to one
    employee."""

    capability: CapabilityCandidateRef
    llm_confidence: float
    llm_reasoning: str


async def _write_trace(
    *,
    agent_name: str,
    organization_id: uuid.UUID,
    model: str,
    status: str,
    result: LLMResult | None = None,
    error_code: str | None = None,
) -> None:
    async with UnitOfWork() as uow:
        await uow.agent_traces.create(
            organization_id=organization_id,
            agent_name=agent_name,
            provider=result.provider if result else None,
            model=model,
            prompt_version=bundle_version(),
            latency_ms=result.latency_ms if result else None,
            input_tokens=result.input_tokens if result else None,
            output_tokens=result.output_tokens if result else None,
            estimated_cost_usd=result.estimated_cost_usd if result else None,
            status=status,
            error_code=error_code,
        )
        await uow.commit()


def _meaningful_tokens(value: str) -> set[str]:
    normalized = normalize_employee_name(value)
    return {token for token in normalized.split() if token not in _GENERIC_PROJECT_WORDS}


def _render_candidates(candidates: list[ProjectCandidateRef]) -> str:
    lines = []
    for candidate in candidates:
        detail = candidate.name
        if candidate.client:
            detail += f" - {candidate.client}"
        if candidate.year:
            detail += f" ({candidate.year})"
        lines.append(f"[{candidate.label}] {detail}")
    return "\n".join(lines)


def _plausibly_same_label(left: str, right: str) -> bool:
    normalized_left = normalize_employee_name(left)
    normalized_right = normalize_employee_name(right)

    if normalized_left == normalized_right:
        return True

    left_tokens = _meaningful_tokens(left)
    right_tokens = _meaningful_tokens(right)

    if not left_tokens or not right_tokens:
        return False

    shared_tokens = left_tokens & right_tokens
    overlap = len(shared_tokens) / min(
        len(left_tokens),
        len(right_tokens),
    )

    return overlap >= _TOKEN_OVERLAP_THRESHOLD


async def extract_worked_on(
    client: LLMClient,
    model: str,
    resume_chunks: list[DocumentChunk],
    candidate_projects: list[ProjectCandidateRef],
    organization_id: uuid.UUID,
) -> list[WorkedOnCandidate]:
    """One call per employee, shown the organization's full canonical
    project list as candidates - not batched the way B5's tender-wide
    extraction is, since an organization's project count is small, unlike a
    whole tender's worth of chunks. Returns proposed edges only; verifying
    them (resolve_worked_on_verification below) is a separate step, exactly
    mirroring B5's extract-then-verify split.
    """
    system_prompt = load_prompt("worked_on_extraction")
    resume_text = "\n\n".join(chunk.chunk_text for chunk in resume_chunks)
    user_prompt = (
        f"RESUME:\n{resume_text}\n\nCANDIDATE PROJECTS:\n{_render_candidates(candidate_projects)}"
    )

    agent_name = "worked_on_extraction"
    try:
        llm_result = await call_with_repair(
            client,
            agent_name=agent_name,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=RelationshipExtractionOutput,
        )
    except LLMError as exc:
        await _write_trace(
            agent_name=agent_name,
            organization_id=organization_id,
            model=model,
            status="failed",
            error_code=type(exc).__name__,
        )
        raise

    await _write_trace(
        agent_name=agent_name,
        organization_id=organization_id,
        model=model,
        status="completed",
        result=llm_result,
    )

    by_label = {candidate.label: candidate for candidate in candidate_projects}
    resolved: list[WorkedOnCandidate] = []
    for item in llm_result.parsed.relationships:
        candidate = by_label.get(item.target_ref)
        if candidate is None:
            # Same "fabricated/mistyped label -> drop this one item, not the
            # whole call" reasoning as extract.py's unresolvable
            # source_chunk_ref handling.
            logger.warning(
                "worked_on extraction: unresolvable target_ref %r, dropping item",
                item.target_ref,
            )
            continue
        resolved.append(
            WorkedOnCandidate(
                project=candidate, llm_confidence=item.confidence, llm_reasoning=item.reasoning
            )
        )
    return resolved


def _render_capability_candidates(candidates: list[CapabilityCandidateRef]) -> str:
    lines = []
    for candidate in candidates:
        detail = candidate.name
        if candidate.description:
            detail += f" - {candidate.description}"
        lines.append(f"[{candidate.label}] {detail}")
    return "\n".join(lines)


async def extract_demonstrates(
    client: LLMClient,
    model: str,
    project_name: str,
    project_client: str | None,
    project_description: str | None,
    candidate_capabilities: list[CapabilityCandidateRef],
    organization_id: uuid.UUID,
) -> list[DemonstratesCandidate]:
    """One call per project, shown the organization's full canonical
    capability list as candidates - same one-call-per-source-item shape as
    extract_worked_on above, just Project -> Capability instead of
    Employee -> Project. Takes the project's own fields directly (not
    chunks) since by this point a project is already a persisted
    past_projects row with its own name/client/description, not raw
    document text needing re-reading.
    """
    system_prompt = load_prompt("demonstrates_extraction")
    project_lines = [f"Name: {project_name}"]
    if project_client:
        project_lines.append(f"Client: {project_client}")
    if project_description:
        project_lines.append(f"Description: {project_description}")
    user_prompt = (
        f"PROJECT:\n{chr(10).join(project_lines)}\n\n"
        f"CANDIDATE CAPABILITIES:\n{_render_capability_candidates(candidate_capabilities)}"
    )

    agent_name = "demonstrates_extraction"
    try:
        llm_result = await call_with_repair(
            client,
            agent_name=agent_name,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=RelationshipExtractionOutput,
        )
    except LLMError as exc:
        await _write_trace(
            agent_name=agent_name,
            organization_id=organization_id,
            model=model,
            status="failed",
            error_code=type(exc).__name__,
        )
        raise

    await _write_trace(
        agent_name=agent_name,
        organization_id=organization_id,
        model=model,
        status="completed",
        result=llm_result,
    )

    by_label = {candidate.label: candidate for candidate in candidate_capabilities}
    resolved: list[DemonstratesCandidate] = []
    for item in llm_result.parsed.relationships:
        candidate = by_label.get(item.target_ref)
        if candidate is None:
            logger.warning(
                "demonstrates extraction: unresolvable target_ref %r, dropping item",
                item.target_ref,
            )
            continue
        resolved.append(
            DemonstratesCandidate(
                capability=candidate, llm_confidence=item.confidence, llm_reasoning=item.reasoning
            )
        )
    return resolved


class RelationshipVerdict(StrEnum):
    VERIFIED = "verified"  # write knowledge_relationships.verified=true
    NEEDS_REVIEW = "needs_review"  # write the row, verified=false, surfaced for review
    REJECTED = "rejected"  # don't write a row at all


@dataclass(frozen=True)
class RelationshipResolution:
    verdict: RelationshipVerdict
    reason: str


# TODO(human): implement resolve_worked_on_verification.
#
# Steps (plain-English, not code):
#   1. Look at whether resume_project_mentions (the SAME resume's own
#      structured project_mentions, from extract_employee_profile earlier -
#      a person's resume gets parsed once, for both their profile fields AND
#      their claimed project list) contains an entry that plausibly refers
#      to `candidate.project` - compare on name/client, not exact string
#      equality (people phrase project names differently than the company's
#      own capability-statement wording; you may want to reuse
#      agents/knowledge/dedup.py's normalize_employee_name-style approach,
#      or write a simpler substring/token-overlap check - your call).
#   2. Decide how the LLM's own confidence and this deterministic
#      corroboration check combine into one of the three verdicts. This is
#      the actual design question - not a lookup. Consider:
#        - LLM confident AND corroborated by resume_project_mentions ->
#          the strongest case. VERIFIED seems safe, but is there a
#          confidence floor below which even a corroborated match should
#          still go to review?
#        - LLM confident but NOT corroborated by resume_project_mentions ->
#          the LLM inferred a link the resume's own structured mentions
#          didn't surface. Is that grounds for NEEDS_REVIEW (a human
#          decides) or REJECTED (too speculative to write at all, since an
#          unverified WORKED_ON edge feeding B8's candidate-scoring or B9's
#          claim-grounding later could misrepresent someone's real
#          experience)?
#        - LLM low-confidence but corroborated by resume_project_mentions ->
#          the deterministic signal disagrees with the LLM's own hedge.
#          Worth a different verdict than the low-confidence+uncorroborated
#          case?
#   3. Return a RelationshipResolution with a `reason` a human reviewer (or
#      future-you re-reading this) can actually use - name which signal(s)
#      drove the verdict, not just "review needed."
#
# What's already available to use:
#   - `candidate: WorkedOnCandidate` - has .project (a ProjectCandidateRef:
#     .name, .client, .year, .knowledge_item_id), .llm_confidence (0.0-1.0),
#     .llm_reasoning (the model's own one-sentence justification).
#   - `resume_project_mentions: list[ExtractedProjectMention]` - the SAME
#     employee's resume, already parsed by extract_employee_profile; each
#     has .name, .client, .year, all `str | None` except .name.
#
# Why this is worth writing by hand: this is the second half of the exact
# extract-then-verify pattern B5 built and this file's own extract_worked_on
# implements the first half of - but unlike B5's verify.py (a second LLM
# call asking a narrow yes/no question), this verification is a
# deterministic reconciliation between an LLM judgment and a structured
# signal already sitting in the database, closer in shape to
# agents/knowledge/dedup.py's precedence policy than to B5's verifier. The
# risk asymmetry (a wrongly-VERIFIED WORKED_ON edge could misrepresent real
# experience in a proposal later) is exactly the kind of stakes-aware
# judgment call CLAUDE.md's TODO(human) standard exists for.
def resolve_worked_on_verification(
    candidate: WorkedOnCandidate,
    resume_project_mentions: list[ExtractedProjectMention],
) -> RelationshipResolution:
    matched_mention: ExtractedProjectMention | None = None

    for mention in resume_project_mentions:
        name_matches = _plausibly_same_label(candidate.project.name, mention.name)
        if not name_matches:
            continue

        client_matches = (
            candidate.project.client is None
            or mention.client is None
            or _plausibly_same_label(candidate.project.client, mention.client)
        )
        if client_matches:
            matched_mention = mention
            break

    corroborated = matched_mention is not None
    if corroborated and candidate.llm_confidence >= _VERIFIED_CONFIDENCE:
        return RelationshipResolution(
            verdict=RelationshipVerdict.VERIFIED,
            reason=(
                f"LLM confidence {candidate.llm_confidence:.2f} met the "
                f"verification threshold and résumé mention "
                f"{matched_mention.name!r} corroborated the project"
            ),
        )

    if matched_mention is not None:
        return RelationshipResolution(
            verdict=RelationshipVerdict.NEEDS_REVIEW,
            reason=(
                f"résumé mention {matched_mention.name!r} corroborated the project, "
                f"but LLM confidence {candidate.llm_confidence:.2f} was below "
                f"the {_VERIFIED_CONFIDENCE:.2f} verification threshold"
            ),
        )

    if candidate.llm_confidence >= _REVIEW_CONFIDENCE:
        return RelationshipResolution(
            verdict=RelationshipVerdict.NEEDS_REVIEW,
            reason=(
                f"LLM confidence {candidate.llm_confidence:.2f} met the "
                f"{_REVIEW_CONFIDENCE:.2f} review threshold, but no structured "
                "résumé project mention corroborated the relationship"
            ),
        )

    return RelationshipResolution(
        verdict=RelationshipVerdict.REJECTED,
        reason=(
            f"LLM confidence {candidate.llm_confidence:.2f} was below "
            f"the {_REVIEW_CONFIDENCE:.2f} review threshold and no structured "
            "résumé project mention corroborated the relationship"
        ),
    )


def _description_supports_capability(description: str | None, capability_name: str) -> bool:
    # Containment, not equality: a capability name ("Systems Integration")
    # is a short label, a project description is several sentences - the
    # question isn't "are these the same string" (that's WORKED_ON's
    # question, comparing two names of the same thing) but "does the
    # capability's own vocabulary actually show up in what the project
    # describes." Same _TOKEN_OVERLAP_THRESHOLD as _plausibly_same_label,
    # applied against the capability's (shorter) token set as the
    # denominator, since a description will always have far more tokens
    # than a capability name and a symmetric ratio would be an unfairly
    # strict bar for a correct match.
    if not description:
        return False
    description_tokens = _meaningful_tokens(description)
    capability_tokens = _meaningful_tokens(capability_name)
    if not capability_tokens:
        return False
    overlap = capability_tokens & description_tokens
    return len(overlap) / len(capability_tokens) >= _TOKEN_OVERLAP_THRESHOLD


def resolve_demonstrates_verification(
    candidate: DemonstratesCandidate, project_description: str | None
) -> RelationshipResolution:
    """DEMONSTRATES' verification step, same decision matrix as
    resolve_worked_on_verification above (VERIFIED needs both a confident
    LLM and a corroborating deterministic signal; either alone downgrades to
    NEEDS_REVIEW; neither is REJECTED) - the only thing that changes between
    WORKED_ON and DEMONSTRATES is what "corroborated" means: there,
    matching the same resume's own structured project_mentions; here,
    the capability's own vocabulary actually appearing in the project's own
    description (_description_supports_capability above). Implemented
    directly rather than scaffolded as a TODO(human), per this block's own
    instruction: the pattern and its risk asymmetry are already established
    and reviewed for WORKED_ON, so re-deriving it here from scratch would
    be redundant, not a fresh design decision.
    """
    corroborated = _description_supports_capability(project_description, candidate.capability.name)

    if corroborated and candidate.llm_confidence >= _VERIFIED_CONFIDENCE:
        return RelationshipResolution(
            verdict=RelationshipVerdict.VERIFIED,
            reason=(
                f"LLM confidence {candidate.llm_confidence:.2f} met the verification "
                f"threshold and the project's own description corroborated "
                f"{candidate.capability.name!r}"
            ),
        )

    if corroborated:
        return RelationshipResolution(
            verdict=RelationshipVerdict.NEEDS_REVIEW,
            reason=(
                f"the project's own description corroborated {candidate.capability.name!r}, "
                f"but LLM confidence {candidate.llm_confidence:.2f} was below the "
                f"{_VERIFIED_CONFIDENCE:.2f} verification threshold"
            ),
        )

    if candidate.llm_confidence >= _REVIEW_CONFIDENCE:
        return RelationshipResolution(
            verdict=RelationshipVerdict.NEEDS_REVIEW,
            reason=(
                f"LLM confidence {candidate.llm_confidence:.2f} met the "
                f"{_REVIEW_CONFIDENCE:.2f} review threshold, but the project's own "
                f"description did not corroborate {candidate.capability.name!r}"
            ),
        )

    return RelationshipResolution(
        verdict=RelationshipVerdict.REJECTED,
        reason=(
            f"LLM confidence {candidate.llm_confidence:.2f} was below the "
            f"{_REVIEW_CONFIDENCE:.2f} review threshold and the project's own description "
            f"did not corroborate {candidate.capability.name!r}"
        ),
    )
