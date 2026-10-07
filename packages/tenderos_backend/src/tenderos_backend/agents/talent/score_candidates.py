import uuid
from dataclasses import dataclass, field

from tenderos_backend.agents.llm.client import LLMClient, LLMError, LLMResult, call_with_repair
from tenderos_backend.agents.prompts.loader import bundle_version, load_prompt
from tenderos_backend.agents.prompts.untrusted_content import wrap_untrusted_content
from tenderos_backend.agents.schemas.talent import CandidateScoring
from tenderos_backend.db.unit_of_work import UnitOfWork

_AGENT_NAME = "talent_scoring"


@dataclass(frozen=True)
class TalentEvidenceCandidateRef:
    """Same label -> real-object resolution pattern as compliance's
    EvidenceCandidateRef - the model only ever sees `label`, never a UUID.
    """

    label: str
    knowledge_item_id: uuid.UUID
    source_kind: str
    excerpt: str


@dataclass(frozen=True)
class CandidateProfileSummary:
    """The candidate-side facts shown to the model, including the
    mechanically pre-computed signals (never the model's own judgment)
    talent_service.py's _compute_candidate_talent_signals derives - the
    prompt tells the model these are given facts, not something to assess
    itself, exactly so a gap grounded in one of them can't be talked out
    of existing by the model's own optimism.
    """

    current_title: str | None
    years_experience: float | None
    professional_location: str | None
    availability_status: str
    profile_review_status: str
    signal_notes: list[str] = field(default_factory=list)


def _render_role(
    role_name: str,
    min_experience_years: float | None,
    required_skills: list[str],
    required_certs: list[str],
) -> str:
    lines = [f"Role: {role_name}"]
    if min_experience_years is not None:
        lines.append(f"Minimum experience: {min_experience_years} years")
    if required_skills:
        lines.append(f"Required skills: {', '.join(required_skills)}")
    if required_certs:
        lines.append(f"Required certifications: {', '.join(required_certs)}")
    return "\n".join(lines)


def _render_candidate(
    profile: CandidateProfileSummary, evidence: list[TalentEvidenceCandidateRef]
) -> str:
    years = profile.years_experience if profile.years_experience is not None else "unknown"
    lines = [
        f"Current title: {profile.current_title or 'unknown'}",
        f"Years of experience: {years}",
        f"Professional location: {profile.professional_location or 'unknown'}",
        f"Availability: {profile.availability_status}",
        f"Profile review status: {profile.profile_review_status}",
    ]
    if profile.signal_notes:
        lines.append("Known signals: " + "; ".join(profile.signal_notes))
    lines.append("")
    if evidence:
        lines.append("Evidence:")
        for e in evidence:
            # B15: e.excerpt is knowledge-base-sourced (resume/certification
            # text) - short structured fields above (title, location,
            # availability) are already-normalized labels, not raw document
            # prose, and are left unwrapped to avoid repeating the wrapper's
            # own instruction text on every short field.
            wrapped = wrap_untrusted_content(e.label, e.excerpt)
            lines.append(f"- {e.label} [{e.source_kind}]: {wrapped}")
    else:
        lines.append("Evidence: (no candidate evidence was retrieved)")
    return "\n".join(lines)


async def _write_trace(
    *,
    tender_id: uuid.UUID,
    workflow_run_id: uuid.UUID | None,
    task_id: uuid.UUID | None,
    model: str,
    status: str,
    result: LLMResult[CandidateScoring] | None = None,
    error_code: str | None = None,
) -> None:
    async with UnitOfWork() as uow:
        await uow.agent_traces.create(
            tender_id=tender_id,
            workflow_run_id=workflow_run_id,
            task_id=task_id,
            agent_name=_AGENT_NAME,
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


async def score_candidate(
    client: LLMClient,
    model: str,
    role_name: str,
    min_experience_years: float | None,
    required_skills: list[str],
    required_certs: list[str],
    profile: CandidateProfileSummary,
    evidence: list[TalentEvidenceCandidateRef],
    tender_id: uuid.UUID,
    workflow_run_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
) -> CandidateScoring:
    """E5's per-candidate scoring call - one role, one candidate, one call
    (mirrors propose_compliance's one-requirement-per-call shape). Returns
    the model's raw scoring unmodified; it does not enforce that mechanical
    signals actually survived into the model's own gaps list. That
    enforcement happens one layer up in talent_service.py, deliberately
    kept separate for the same reason compliance's invariant enforcement
    is separate from propose_compliance - the LLM-calling code stays a
    thin, swappable-provider layer.
    """
    system_prompt = load_prompt(_AGENT_NAME)
    role_text = _render_role(role_name, min_experience_years, required_skills, required_certs)
    user_prompt = (
        f"ROLE:\n{role_text}\n\n"
        f"CANDIDATE:\n{_render_candidate(profile, evidence)}"
    )
    try:
        llm_result = await call_with_repair(
            client,
            agent_name=_AGENT_NAME,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=CandidateScoring,
        )
    except LLMError as exc:
        await _write_trace(
            tender_id=tender_id,
            workflow_run_id=workflow_run_id,
            task_id=task_id,
            model=model,
            status="failed",
            error_code=type(exc).__name__,
        )
        raise

    await _write_trace(
        tender_id=tender_id,
        workflow_run_id=workflow_run_id,
        task_id=task_id,
        model=model,
        status="completed",
        result=llm_result,
    )
    return llm_result.parsed
