import uuid
from dataclasses import dataclass

from tenderos_backend.agents.llm.client import LLMClient, LLMError, LLMResult, call_with_repair
from tenderos_backend.agents.prompts.loader import bundle_version, load_prompt
from tenderos_backend.agents.prompts.untrusted_content import wrap_untrusted_content
from tenderos_backend.agents.schemas.proposal import ProposalSectionDraft
from tenderos_backend.db.unit_of_work import UnitOfWork

_AGENT_NAME = "proposal_drafting"


@dataclass(frozen=True)
class GroundedFactRef:
    """One already-approved fact offered to the drafting call, labeled the
    same way EvidenceCandidateRef/RequirementRef already are elsewhere - the
    model only ever sees `label` and `excerpt`, never the real UUID behind
    it. `kind` distinguishes the three ref lists a claim can cite
    (requirement / evidence / employee) so _format_facts can group them
    under the headings proposal_drafting.md's own prompt describes.
    """

    label: str
    kind: str  # "requirement" | "evidence" | "employee"
    excerpt: str


async def _write_trace(
    *,
    tender_id: uuid.UUID,
    workflow_run_id: uuid.UUID | None,
    task_id: uuid.UUID | None,
    model: str,
    status: str,
    result: LLMResult[ProposalSectionDraft] | None = None,
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


def _format_facts(facts: list[GroundedFactRef]) -> str:
    if not facts:
        return "(no approved facts are available to ground this section)"
    lines = []
    for f in facts:
        # B15: f.excerpt is document/knowledge-base-sourced (a requirement,
        # an evidence excerpt, or an employee profile field).
        lines.append(f"- {f.label} [{f.kind}]: {wrap_untrusted_content(f.label, f.excerpt)}")
    return "\n".join(lines)


async def draft_section(
    client: LLMClient,
    model: str,
    section_name: str,
    section_guidance: str,
    facts: list[GroundedFactRef],
    tender_id: uuid.UUID,
    workflow_run_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
) -> ProposalSectionDraft:
    """E7's proposal-drafting call - one section, its own narrow grounded
    fact bundle, one call. Returns the LLM's raw draft unmodified; it does
    not decide grounding_status or persist anything - that split mirrors
    propose_compliance's own division of labor, kept for the same reason
    (a thin, swappable-provider layer here, invariant enforcement one layer
    up in proposal_service.py).
    """
    system_prompt = load_prompt(_AGENT_NAME)
    user_prompt = (
        f"SECTION: {section_name}\n{section_guidance}\n\n"
        f"AVAILABLE FACTS:\n{_format_facts(facts)}"
    )
    try:
        llm_result = await call_with_repair(
            client,
            agent_name=_AGENT_NAME,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=ProposalSectionDraft,
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
