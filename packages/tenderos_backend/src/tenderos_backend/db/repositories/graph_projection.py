import uuid
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from tenderos_backend.db.models.graph_projection import GraphProjectionRun

# The first key of the two-key advisory lock, so these locks cannot collide
# with any other advisory lock taken in this database (refresh_tokens.py's
# _FAMILY_LOCK_CLASS is 3401; tests/unit/test_advisory_lock_classes.py keeps
# them distinct). Arbitrary; it only has to stay the same.
_PROJECTION_LOCK_CLASS = 3402

# Postgres's lock_not_available, which lock_timeout raises.
_LOCK_NOT_AVAILABLE = "55P03"


class ProjectionLockTimeoutError(Exception):
    """Another projection of this tender held the lock for the whole wait."""


class GraphProjectionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lock_tender_for_projection(self, tender_id: uuid.UUID, timeout_ms: int) -> None:
        """Take the tender's transaction-scoped advisory lock, waiting at most
        timeout_ms; it is released when the transaction ends.

        Projections of one tender run one at a time. Each deletes the tender's
        nodes and writes them back, in statements that commit separately, so two
        at once can leave a graph that one projection's completion marker calls
        finished while the other's delete has removed part of it. Taken before
        the projection's first read, so the waiter projects what is current when
        it gets its turn. hashtext() can map two tenders to one key; they then
        wait on each other briefly, which is harmless.

        lock_timeout is set with set_config(..., true), which lasts for this
        transaction only, and the session is not reused after a failure.
        """
        await self._session.execute(
            text("SELECT set_config('lock_timeout', :timeout, true)"),
            {"timeout": f"{timeout_ms}ms"},
        )
        try:
            await self._session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_class, hashtext(:tender_id))"),
                {"lock_class": _PROJECTION_LOCK_CLASS, "tender_id": str(tender_id)},
            )
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) == _LOCK_NOT_AVAILABLE:
                raise ProjectionLockTimeoutError(
                    f"tender {tender_id}: another projection held the lock for {timeout_ms} ms"
                ) from exc
            raise

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
