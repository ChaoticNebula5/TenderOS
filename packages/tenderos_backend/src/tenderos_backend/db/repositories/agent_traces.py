import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from tenderos_backend.db.models.agent_trace import AgentTrace


class AgentTraceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        tender_id: uuid.UUID,
        agent_name: str,
        status: str,
        workflow_run_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
        provider: str | None = None,
        model: str | None = None,
        prompt_version: str | None = None,
        input_refs: dict | None = None,
        output_refs: dict | None = None,
        tool_names: list | None = None,
        latency_ms: int | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        estimated_cost_usd: float | None = None,
        error_code: str | None = None,
    ) -> AgentTrace:
        trace = AgentTrace(
            tender_id=tender_id,
            workflow_run_id=workflow_run_id,
            task_id=task_id,
            agent_name=agent_name,
            provider=provider,
            model=model,
            prompt_version=prompt_version,
            input_refs=input_refs or {},
            output_refs=output_refs or {},
            tool_names=tool_names or [],
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=estimated_cost_usd,
            status=status,
            error_code=error_code,
        )
        self._session.add(trace)
        await self._session.flush()
        return trace
