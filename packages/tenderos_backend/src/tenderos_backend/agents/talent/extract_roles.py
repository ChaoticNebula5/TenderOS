import uuid
from dataclasses import dataclass

from tenderos_backend.agents.llm.client import LLMClient, LLMError, LLMResult, call_with_repair
from tenderos_backend.agents.prompts.loader import bundle_version, load_prompt
from tenderos_backend.agents.prompts.untrusted_content import wrap_untrusted_content
from tenderos_backend.agents.schemas.talent import RoleExtractionOutput
from tenderos_backend.db.unit_of_work import UnitOfWork

_AGENT_NAME = "role_extraction"


@dataclass(frozen=True)
class RequirementRef:
    """One staffing-category requirement shown to the model, labeled the
    same way batch.py labels document chunks for requirement extraction -
    the model cites `label`, never the real requirement_id, and
    extract_roles.py resolves cited labels back to real UUIDs after
    parsing.
    """

    label: str
    requirement_id: uuid.UUID
    requirement_text: str
    source_chunk_id: uuid.UUID


def _render_prompt(refs: list[RequirementRef]) -> str:
    # B15: requirement_text is tender-document-sourced, same as
    # requirements/extract.py's own chunk text.
    return "\n\n".join(
        wrap_untrusted_content(ref.label, ref.requirement_text) for ref in refs
    )


async def _write_trace(
    *,
    tender_id: uuid.UUID,
    workflow_run_id: uuid.UUID | None,
    task_id: uuid.UUID | None,
    model: str,
    status: str,
    result: LLMResult[RoleExtractionOutput] | None = None,
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


async def extract_roles(
    client: LLMClient,
    model: str,
    requirement_refs: list[RequirementRef],
    tender_id: uuid.UUID,
    workflow_run_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
) -> RoleExtractionOutput:
    """One call per tender, given every staffing-category requirement at
    once (unlike requirement extraction's per-batch loop - a tender's
    staffing requirements are few enough, and the whole point of role
    extraction is merging requirements that reference the same role, which
    is only possible if the model sees them together). Returns the model's
    raw output unresolved - talent_service.py resolves
    source_requirement_refs labels back to real requirement_ids and
    decides what to do with a role that resolves to zero requirements,
    the same "thin call, invariant enforcement lives one layer up" split
    propose_compliance/compliance_service.py already established.
    """
    system_prompt = load_prompt(_AGENT_NAME)
    user_prompt = _render_prompt(requirement_refs)
    try:
        llm_result = await call_with_repair(
            client,
            agent_name=_AGENT_NAME,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=RoleExtractionOutput,
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
