import uuid

from tenderos_backend.agents.llm.client import LLMClient, LLMError, LLMResult, call_with_repair
from tenderos_backend.agents.prompts.loader import bundle_version, load_prompt
from tenderos_backend.agents.schemas.commercial import FindingExplanation
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services.pricing_checks import PricingFindingDraft

_AGENT_NAME = "commercial_explanation"


def _render_finding(finding: PricingFindingDraft) -> str:
    lines = [
        f"Finding type: {finding.finding_type}",
        f"Cells involved: {', '.join(finding.cell_refs)}",
    ]
    if finding.expected_value is not None:
        lines.append("Expected value: known, computed deterministically")
    if finding.actual_value is not None:
        lines.append("Actual value: known, read directly from the workbook")
    if finding.delta is not None:
        lines.append("A non-zero difference exists between the two")
    lines.append(f"Note: {finding.note}")
    lines.append(f"Suggested default severity: {finding.default_severity}")
    return "\n".join(lines)


async def _write_trace(
    *,
    tender_id: uuid.UUID,
    workflow_run_id: uuid.UUID | None,
    task_id: uuid.UUID | None,
    model: str,
    status: str,
    result: LLMResult[FindingExplanation] | None = None,
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


async def explain_finding(
    client: LLMClient,
    model: str,
    finding: PricingFindingDraft,
    tender_id: uuid.UUID,
    workflow_run_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
) -> FindingExplanation:
    """E6's explanation call - one deterministic finding, one call, mirrors
    propose_compliance/score_candidate's one-call-per-target shape. Note
    that _render_finding deliberately never puts the actual numbers into
    the prompt text itself, only says a value "is known" - the model
    couldn't invent expected_value/actual_value/delta even if it tried,
    since it never has them to paraphrase. commercial_service.py combines
    this output with the PricingFindingDraft it was generated from when
    persisting - this function does not touch the database.
    """
    system_prompt = load_prompt(_AGENT_NAME)
    user_prompt = _render_finding(finding)
    try:
        llm_result = await call_with_repair(
            client,
            agent_name=_AGENT_NAME,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=FindingExplanation,
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
