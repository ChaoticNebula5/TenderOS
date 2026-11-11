import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tenderos_backend.db.models.workflow import ApprovalGate
from tenderos_backend.db.repositories.workflow import RunNoLongerActiveError, WorkflowRepository

# Plan item 44: a pending gate of a cancelled run is closed with this
# status and payload. "rejected" because every existing check reads a gate
# that is not "pending" as decided (gate_service.record_decision answers a
# new decision with GateAlreadyDecidedError; the web panel shows it
# decided). The payload's decision is None, so no later decision is ever
# taken for a replay of it.
CLOSED_BY_CANCEL_STATUS = "rejected"
CLOSED_BY_CANCEL_PAYLOAD = {"decision": None, "closed_by": "run_cancelled"}


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
        #
        # Plan item 44: only for an active run, checked in this same
        # transaction with the run's row share-locked, so a cancel either
        # committed first (nothing is opened) or waits and then closes this
        # gate with the run's other pending ones.
        if not await WorkflowRepository(self._session).is_active_for_update(workflow_run_id):
            raise RunNoLongerActiveError(workflow_run_id, f"opening a {gate_type} gate")
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

    async def close_pending_for_run(self, workflow_run_id: uuid.UUID) -> list[uuid.UUID]:
        """Close every pending gate of a run (its cancel), and return their
        ids. One UPDATE; a decided gate is left as it was."""
        result = await self._session.execute(
            update(ApprovalGate)
            .where(
                ApprovalGate.workflow_run_id == workflow_run_id,
                ApprovalGate.status == "pending",
            )
            .values(
                status=CLOSED_BY_CANCEL_STATUS,
                decision_payload=CLOSED_BY_CANCEL_PAYLOAD,
                decided_at=func.now(),
            )
            .returning(ApprovalGate.id)
            .execution_options(synchronize_session="fetch")
        )
        return list(result.scalars().all())

    async def record_decision(
        self,
        gate_id: uuid.UUID,
        decided_by: uuid.UUID,
        status: str,
        decision_payload: dict,
    ) -> ApprovalGate | None:
        """Record the decision on a gate that is still pending, and return
        the decided gate; None if it is no longer pending (decided, or
        closed by a cancel, since the caller read it).

        Plan item 57: one conditional UPDATE (compare-and-swap), like
        close_pending_for_run. Of two concurrent decisions, the second waits
        on the row lock, then re-checks status = 'pending' against the row
        the first wrote and matches nothing. Before this, the update had no
        status condition, so the second overwrote the first and was stopped
        only by the outbox's unique idempotency_key, as an IntegrityError the
        route answered with a 500 (observed 2026-11-11: 4 of 5 races)."""
        result = await self._session.execute(
            update(ApprovalGate)
            .where(ApprovalGate.id == gate_id, ApprovalGate.status == "pending")
            .values(
                status=status,
                decided_by=decided_by,
                decision_payload=decision_payload,
                decided_at=datetime.now(UTC),
            )
            .returning(ApprovalGate.id)
            .execution_options(synchronize_session=False)
        )
        if result.scalar_one_or_none() is None:
            return None
        return await self._session.get(ApprovalGate, gate_id, populate_existing=True)
