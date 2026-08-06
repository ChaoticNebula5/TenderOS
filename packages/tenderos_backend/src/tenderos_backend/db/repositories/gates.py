import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tenderos_backend.db.models.workflow import ApprovalGate


class GateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        gate_id: uuid.UUID,
        tender_id: uuid.UUID,
        workflow_run_id: uuid.UUID,
        gate_key: str,
        gate_type: str,
        request_payload: dict,
    ) -> ApprovalGate:
        # gate_id is caller-generated (not server_default gen_random_uuid())
        # because R5's request_payload includes the gate's own id, and the
        # caller (requirement_gate_node/final_gate_node) needs that id to
        # build the payload before this row exists - same pattern
        # WorkflowRepository.create_run uses for langgraph_thread_id.
        gate = ApprovalGate(
            id=gate_id,
            tender_id=tender_id,
            workflow_run_id=workflow_run_id,
            gate_key=gate_key,
            gate_type=gate_type,
            request_payload=request_payload,
        )
        self._session.add(gate)
        await self._session.flush()
        return gate

    async def get(self, gate_id: uuid.UUID) -> ApprovalGate | None:
        return await self._session.get(ApprovalGate, gate_id)

    async def get_by_key(self, workflow_run_id: uuid.UUID, gate_key: str) -> ApprovalGate | None:
        result = await self._session.execute(
            select(ApprovalGate).where(
                ApprovalGate.workflow_run_id == workflow_run_id,
                ApprovalGate.gate_key == gate_key,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_run(self, workflow_run_id: uuid.UUID) -> list[ApprovalGate]:
        result = await self._session.execute(
            select(ApprovalGate)
            .where(ApprovalGate.workflow_run_id == workflow_run_id)
            .order_by(ApprovalGate.requested_at)
        )
        return list(result.scalars().all())

    async def record_decision(
        self,
        gate_id: uuid.UUID,
        decided_by: uuid.UUID,
        status: str,
        decision_payload: dict,
    ) -> ApprovalGate:
        gate = await self._session.get(ApprovalGate, gate_id)
        if gate is None:
            raise ValueError(f"ApprovalGate {gate_id} not found")
        gate.status = status
        gate.decided_by = decided_by
        gate.decision_payload = decision_payload
        gate.decided_at = datetime.now(UTC)
        await self._session.flush()
        return gate
