import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tenderos_backend.db.models.graph_projection import GraphProjectionRun


class GraphProjectionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_run(
        self, tender_id: uuid.UUID, source_state_version: int
    ) -> GraphProjectionRun:
        run = GraphProjectionRun(
            tender_id=tender_id,
            source_state_version=source_state_version,
            status="running",
        )
        self._session.add(run)
        await self._session.flush()
        return run

    async def mark_completed(
        self, run_id: uuid.UUID, node_count: int, relationship_count: int
    ) -> GraphProjectionRun:
        run = await self._session.get(GraphProjectionRun, run_id)
        if run is None:
            raise ValueError(f"GraphProjectionRun {run_id} not found")
        run.status = "completed"
        run.node_count = node_count
        run.relationship_count = relationship_count
        run.completed_at = datetime.now(UTC)
        await self._session.flush()
        return run

    async def mark_failed(self, run_id: uuid.UUID, error_message: str) -> GraphProjectionRun:
        run = await self._session.get(GraphProjectionRun, run_id)
        if run is None:
            raise ValueError(f"GraphProjectionRun {run_id} not found")
        run.status = "failed"
        run.error_message = error_message
        run.completed_at = datetime.now(UTC)
        await self._session.flush()
        return run

    async def get_latest_completed_run(
        self, tender_id: uuid.UUID
    ) -> GraphProjectionRun | None:
        # The freshness invariant (Build Guide, "Graph freshness invariant")
        # compares tenders.state_version against the LATEST SUCCESSFUL run's
        # source_state_version - a 'running' or 'failed' run must never count
        # as the current graph state, since either one means Neo4j doesn't
        # yet (or doesn't correctly) reflect that source_state_version.
        # idx_graph_projection_latest is (tender_id, source_state_version
        # DESC), which is exactly this query's access path.
        result = await self._session.execute(
            select(GraphProjectionRun)
            .where(
                GraphProjectionRun.tender_id == tender_id,
                GraphProjectionRun.status == "completed",
            )
            .order_by(GraphProjectionRun.source_state_version.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
