import uuid

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tenderos_backend.db.models.readiness import GENERATION_SEQUENCE, ReadinessReport


class ReadinessRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_report(
        self,
        tender_id: uuid.UUID,
        tender_state_version: int,
        content_hash: str,
        counts: dict,
        blockers: list,
        ready: bool,
    ) -> ReadinessReport:
        """Plan item 93: store this content at this version, or reuse the row
        that already has it, and either way make it the newest report. One
        statement (INSERT ... ON CONFLICT DO UPDATE on
        uq_readiness_report_content), so two concurrent generates of the
        same content end on one row, and a reuse can never be left older
        than a report with different content: content that goes X, Y, X
        makes X's row current again. clock_timestamp(), not now(): the time
        of this statement, not of its transaction's start.

        Plan item 120: what makes it the newest is its generation, taken
        from the sequence on insert (the column's default) and again on a
        reuse, never the time. The caller holds the tender's row lock
        (readiness_service), so one tender's generations follow commit
        order."""
        stamp = func.clock_timestamp()
        statement = (
            pg_insert(ReadinessReport)
            .values(
                tender_id=tender_id,
                tender_state_version=tender_state_version,
                content_hash=content_hash,
                counts=counts,
                blockers=blockers,
                ready=ready,
                last_generated_at=stamp,
            )
            .on_conflict_do_update(
                constraint="uq_readiness_report_content",
                set_={"last_generated_at": stamp, "generation": GENERATION_SEQUENCE.next_value()},
            )
            .returning(ReadinessReport.id)
        )
        report_id = (await self._session.execute(statement)).scalar_one()
        report = await self._session.get(ReadinessReport, report_id, populate_existing=True)
        assert report is not None
        return report

    async def get(self, report_id: uuid.UUID) -> ReadinessReport | None:
        return await self._session.get(ReadinessReport, report_id)

    async def get_latest_for_tender(self, tender_id: uuid.UUID) -> ReadinessReport | None:
        # The current report: the one most recently generated or reused
        # (plan item 93). Every reader of "the latest report" comes here:
        # GET /tenders/{tender_id}/readiness (and the web's Readiness page
        # through it) and the final gate's approval check (routes/gates.py).
        # The Work dashboard's stale-graph panel picks each tender's newest
        # report from the observability view by the same column.
        # Not by version: several reports can exist at one version, and the
        # newest content is current. Plan item 120: by generation, not by
        # last_generated_at, whose database clock steps back (item 117), so
        # an older report could sort first. Unique per tender, so no tie.
        result = await self._session.execute(
            select(ReadinessReport)
            .where(ReadinessReport.tender_id == tender_id)
            .order_by(ReadinessReport.generation.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
