import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tenderos_backend.db.models.agent_trace import AgentTrace


class AgentTraceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        agent_name: str,
        status: str,
        tender_id: uuid.UUID | None = None,
        organization_id: uuid.UUID | None = None,
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
        # Exactly one of tender_id/organization_id is required - enforced by
        # ck_agent_trace_scope at the DB layer already, checked here too so a
        # caller mistake fails at the call site with a clear message instead
        # of a raw IntegrityError from the flush below.
        if (tender_id is None) == (organization_id is None):
            raise ValueError("AgentTrace requires exactly one of tender_id, organization_id")
        trace = AgentTrace(
            tender_id=tender_id,
            organization_id=organization_id,
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

    async def sum_cost_for_run(self, workflow_run_id: uuid.UUID) -> Decimal:
        """I5's own budget check reads this - accumulated real cost so far
        for one workflow run, not a lifetime-per-tender or per-org total
        (see cost_budget_gate_node's own docstring for why the run is the
        right scope). COALESCE to 0, not NULL: a run with zero traces yet,
        or where every trace's own estimated_cost_usd is still NULL (an
        unmapped model id, or a cache hit - see gemini_adapter.py's own
        _estimate_cost_usd), must read as "$0 spent," not "unknown," so
        the budget check's own `< settings.max_tender_cost_usd` comparison
        never has to special-case None."""
        result = await self._session.execute(
            select(func.coalesce(func.sum(AgentTrace.estimated_cost_usd), 0)).where(
                AgentTrace.workflow_run_id == workflow_run_id
            )
        )
        # COALESCE guarantees a real, non-null value at the SQL level, but
        # SQLAlchemy's own stubs still type scalar_one() as Decimal | None
        # here (func.sum's own nullable type leaks through the wrapper) -
        # the None branch is defensive against that typing gap, not a real
        # runtime case this method expects to hit.
        value = result.scalar_one()
        return value if value is not None else Decimal(0)
