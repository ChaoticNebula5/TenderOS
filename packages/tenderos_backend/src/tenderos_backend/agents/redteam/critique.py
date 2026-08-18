import uuid
from dataclasses import dataclass

from tenderos_backend.agents.llm.client import LLMClient, LLMError, LLMResult, call_with_repair
from tenderos_backend.agents.prompts.loader import bundle_version, load_prompt
from tenderos_backend.agents.schemas.redteam import RedteamCritique
from tenderos_backend.db.unit_of_work import UnitOfWork

_AGENT_NAME = "redteam_critique"


@dataclass(frozen=True)
class ClaimForCritique:
    """One drafted claim shown to the adversarial reviewer, labeled the
    same way EvidenceCandidateRef labels evidence for the compliance
    agent - the model only ever sees `label`, never claim_id, and
    redteam_service.py resolves a cited label back to the real
    ProposalClaim after parsing.
    """

    label: str
    claim_text: str
    claim_type: str
    grounding_status: str
    requirement_text: str | None


async def _write_trace(
    *,
    tender_id: uuid.UUID,
    workflow_run_id: uuid.UUID | None,
    task_id: uuid.UUID | None,
    model: str,
    status: str,
    result: LLMResult[RedteamCritique] | None = None,
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


def _format_claims(claims: list[ClaimForCritique]) -> str:
    if not claims:
        return "(no proposal claims exist yet for this tender)"
    lines = []
    for c in claims:
        req = c.requirement_text or "(not tied to a specific requirement)"
        lines.append(
            f"- {c.label} [{c.claim_type}, grounding={c.grounding_status}]\n"
            f"  CLAIM: {c.claim_text}\n"
            f"  REQUIREMENT: {req}"
        )
    return "\n".join(lines)


async def critique_proposal(
    client: LLMClient,
    model: str,
    claims: list[ClaimForCritique],
    tender_id: uuid.UUID,
    workflow_run_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
) -> RedteamCritique:
    """E8's adversarial-LLM step - the 5th and last of Red Team's check
    list, run after the 4 deterministic/graph checks (redteam_checks.py)
    have already found everything they can. This call sees only claims,
    never the underlying compliance_entries/role_matches/pricing_findings
    tables those deterministic checks already covered - it exists to catch
    what code can't: overstatement, internal inconsistency between two
    claims, or a claim that technically cites valid evidence but doesn't
    actually satisfy what the requirement asks for.

    Returns RedteamCritique unmodified, same "thin, swappable-provider
    layer" split propose_compliance already established - redteam_service.py
    resolves labels to real IDs, computes fingerprints, and persists.
    """
    system_prompt = load_prompt(_AGENT_NAME)
    user_prompt = f"DRAFTED CLAIMS:\n{_format_claims(claims)}"
    try:
        llm_result = await call_with_repair(
            client,
            agent_name=_AGENT_NAME,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=RedteamCritique,
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
