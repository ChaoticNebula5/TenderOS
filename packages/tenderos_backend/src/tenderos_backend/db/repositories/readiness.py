import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tenderos_backend.db.models.readiness import ReadinessReport


class ReadinessRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        tender_id: uuid.UUID,
        tender_state_version: int,
        counts: dict,
        blockers: list,
        ready: bool,
    ) -> ReadinessReport:
        report = ReadinessReport(
            tender_id=tender_id,
            tender_state_version=tender_state_version,
            counts=counts,
            blockers=blockers,
            ready=ready,
        )
        self._session.add(report)
        await self._session.flush()
        return report

    async def get(self, report_id: uuid.UUID) -> ReadinessReport | None:
        return await self._session.get(ReadinessReport, report_id)

    async def get_by_version(
        self, tender_id: uuid.UUID, tender_state_version: int
    ) -> ReadinessReport | None:
        # evaluate_readiness's own idempotency check (B12 plan design notes):
        # a report already exists for this tender_id/tender_state_version
        # pair means nothing has changed since it was generated - return the
        # existing row rather than inserting a duplicate against the
        # uq_readiness_report_version constraint.
        result = await self._session.execute(
            select(ReadinessReport).where(
                ReadinessReport.tender_id == tender_id,
                ReadinessReport.tender_state_version == tender_state_version,
            )
        )
        return result.scalar_one_or_none()

    async def get_latest_for_tender(self, tender_id: uuid.UUID) -> ReadinessReport | None:
        # G11's GET /tenders/{tender_id}/readiness - the human-facing read,
        # same "most recent by version" shape
        # graph_projection.get_latest_completed_run already uses for graph
        # freshness. tender_state_version only ever increases (bump_state_
        # version, never decremented), so ordering by it is equivalent to
        # ordering by generated_at but doesn't depend on clock precision.
        result = await self._session.execute(
            select(ReadinessReport)
            .where(ReadinessReport.tender_id == tender_id)
            .order_by(ReadinessReport.tender_state_version.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
