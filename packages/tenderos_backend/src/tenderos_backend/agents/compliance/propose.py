import uuid
from dataclasses import dataclass

from tenderos_backend.agents.llm.client import LLMClient, LLMError, LLMResult, call_with_repair
from tenderos_backend.agents.prompts.loader import bundle_version, load_prompt
from tenderos_backend.agents.schemas.compliance import ComplianceProposal
from tenderos_backend.db.unit_of_work import UnitOfWork

_AGENT_NAME = "compliance_proposal"


@dataclass(frozen=True)
class EvidenceCandidateRef:
    """One retrieved evidence candidate shown to the LLM, and enough of its
    own metadata (not yet a persisted evidence_link - retrieval results are
    not evidence until something actually cites them) for the model to
    judge it. Same label -> real-object resolution pattern B6's
    ProjectCandidateRef/CapabilityCandidateRef already established: the
    model only ever sees `label`, never a UUID, and the caller
    (compliance_service.py) resolves cited labels back to the underlying
    KnowledgeSearchResult after parsing, to materialize real evidence_links
    only for what was actually used.
    """

    label: str
    knowledge_item_id: uuid.UUID
    source_kind: str
    excerpt: str
    validity_status: str


async def _write_trace(
    *,
    tender_id: uuid.UUID,
    workflow_run_id: uuid.UUID | None,
    task_id: uuid.UUID | None,
    model: str,
    status: str,
    result: LLMResult[ComplianceProposal] | None = None,
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


def _format_candidates(candidates: list[EvidenceCandidateRef]) -> str:
    if not candidates:
        return "(no candidate evidence was retrieved for this requirement)"
    lines = []
    for c in candidates:
        lines.append(
            f"- {c.label} [{c.source_kind}, validity={c.validity_status}]: {c.excerpt}"
        )
    return "\n".join(lines)


async def propose_compliance(
    client: LLMClient,
    model: str,
    requirement_text: str,
    candidates: list[EvidenceCandidateRef],
    tender_id: uuid.UUID,
    workflow_run_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
) -> ComplianceProposal:
    """E4's compliance-proposal call - one requirement, its retrieved
    evidence candidates, one call. Never asked to echo requirement_id back
    (same omission VerificationOutput already established), and never given
    real evidence_link UUIDs to cite, only labels - this function returns
    the LLM's raw proposal unmodified; it does not adjudicate it against
    PRD 8.4's evidence-sufficiency invariant or persist anything. That
    happens one layer up, in compliance_service.py, deliberately kept
    separate so the LLM-calling code stays a thin, swappable-provider layer
    and the invariant enforcement lives in one place regardless of which
    provider answered.
    """
    system_prompt = load_prompt(_AGENT_NAME)
    user_prompt = (
        f"REQUIREMENT:\n{requirement_text}\n\n"
        f"CANDIDATE EVIDENCE:\n{_format_candidates(candidates)}"
    )
    try:
        llm_result = await call_with_repair(
            client,
            agent_name=_AGENT_NAME,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=ComplianceProposal,
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
