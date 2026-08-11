import uuid

from tenderos_backend.agents.llm.client import LLMClient, LLMError, LLMResult, call_with_repair
from tenderos_backend.agents.prompts.loader import bundle_version, load_prompt
from tenderos_backend.agents.schemas.requirements import VerificationOutput
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services.requirement_service import RequirementNotFoundError

_AGENT_NAME = "requirement_verification"


async def _write_trace(
    *,
    tender_id: uuid.UUID,
    workflow_run_id: uuid.UUID | None,
    task_id: uuid.UUID | None,
    model: str,
    status: str,
    result: LLMResult[VerificationOutput] | None = None,
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


async def verify_requirement(
    client: LLMClient,
    model: str,
    requirement_text: str,
    source_chunk_text: str,
    tender_id: uuid.UUID,
    workflow_run_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
) -> VerificationOutput:
    """The narrow extract-then-verify call (Build Guide 10.4) - one
    requirement, one source chunk, one call. Never asked to echo
    requirement_id back (E1: no fabricated IDs) since the caller already
    knows it.
    """
    system_prompt = load_prompt("requirement_verification")
    user_prompt = (
        f"Extracted requirement:\n{requirement_text}\n\n"
        f"Source chunk text:\n{source_chunk_text}"
    )
    try:
        llm_result = await call_with_repair(
            client,
            agent_name=_AGENT_NAME,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=VerificationOutput,
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


async def verify_and_persist(
    client: LLMClient,
    model: str,
    requirement_id: uuid.UUID,
    tender_id: uuid.UUID,
    workflow_run_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
) -> VerificationOutput:
    """Shared by two callers: the extraction node's automatic pass over
    every mandatory requirement, and apps/worker/jobs/verification.py's
    on-demand job (G5's POST /requirements/{id}/verify). Both need the same
    load -> call -> persist sequence, so it lives here once rather than
    twice.
    """
    async with UnitOfWork() as uow:
        requirement = await uow.requirements.get(requirement_id)
        if requirement is None:
            raise RequirementNotFoundError()
        chunk = await uow.documents.get_chunk(requirement.source_chunk_id)
        if chunk is None:
            raise RequirementNotFoundError()
        requirement_text = requirement.requirement_text
        chunk_text = chunk.chunk_text

    output = await verify_requirement(
        client,
        model,
        requirement_text,
        chunk_text,
        tender_id,
        workflow_run_id,
        task_id,
    )

    verified = output.supported_by_source and output.mandatory_language_supported
    async with UnitOfWork() as uow:
        await uow.requirements.set_verified(requirement_id, verified)
        await uow.commit()

    return output
