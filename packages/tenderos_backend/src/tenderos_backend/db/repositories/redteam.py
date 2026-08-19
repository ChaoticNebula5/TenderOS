import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tenderos_backend.db.models.redteam import RedteamFinding


class RedteamRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_finding(
        self,
        tender_id: uuid.UUID,
        target_type: str,
        target_id: uuid.UUID | None,
        finding_category: str,
        severity: str,
        finding_text: str,
        violated_condition: str,
        content_fingerprint: str,
        rebuttal: str | None = None,
        evidence_refs: list | None = None,
    ) -> RedteamFinding:
        finding = RedteamFinding(
            tender_id=tender_id,
            target_type=target_type,
            target_id=target_id,
            finding_category=finding_category,
            severity=severity,
            finding_text=finding_text,
            rebuttal=rebuttal,
            evidence_refs=evidence_refs or [],
            violated_condition=violated_condition,
            content_fingerprint=content_fingerprint,
        )
        self._session.add(finding)
        await self._session.flush()
        return finding

    async def get_by_fingerprint(
        self, tender_id: uuid.UUID, content_fingerprint: str
    ) -> RedteamFinding | None:
        result = await self._session.execute(
            select(RedteamFinding).where(
                RedteamFinding.tender_id == tender_id,
                RedteamFinding.content_fingerprint == content_fingerprint,
            )
        )
        return result.scalar_one_or_none()

    async def get(self, finding_id: uuid.UUID) -> RedteamFinding | None:
        return await self._session.get(RedteamFinding, finding_id)

    async def list_for_tender(self, tender_id: uuid.UUID) -> list[RedteamFinding]:
        result = await self._session.execute(
            select(RedteamFinding).where(RedteamFinding.tender_id == tender_id)
        )
        return list(result.scalars().all())

    async def list_open_for_tender(
        self, tender_id: uuid.UUID, severities: tuple[str, ...]
    ) -> list[RedteamFinding]:
        # The revision loop's own read: "open" (unresolved) findings at one
        # or more severities - route_after_red_team needs critical-only,
        # targeted_replan needs the same set to map categories against, so
        # both go through this one query rather than each filtering
        # list_for_tender's full result independently.
        result = await self._session.execute(
            select(RedteamFinding).where(
                RedteamFinding.tender_id == tender_id,
                RedteamFinding.resolved.is_(False),
                RedteamFinding.severity.in_(severities),
            )
        )
        return list(result.scalars().all())

    async def resolve_finding(
        self, finding_id: uuid.UUID, resolution_note: str | None
    ) -> RedteamFinding:
        finding = await self._session.get(RedteamFinding, finding_id)
        if finding is None:
            raise ValueError(f"RedteamFinding {finding_id} not found")
        finding.resolved = True
        finding.resolution_note = resolution_note
        finding.resolution_source = "human"
        await self._session.flush()
        return finding

    async def reopen_finding(
        self,
        finding_id: uuid.UUID,
        severity: str,
        finding_text: str,
        evidence_refs: list | None = None,
    ) -> RedteamFinding:
        """A system-retired finding (resolution_source='system') whose
        underlying defect has come back - _persist_draft's own upsert calls
        this instead of returning the still-resolved row untouched. Never
        called against a human-resolved finding (that check is the
        caller's responsibility, same as this repository's other methods
        trusting their caller's WHERE-clause-equivalent precondition).

        severity/finding_text/evidence_refs are refreshed from the CURRENT
        detection, not left at whatever the original (now-stale) row had -
        finding_fingerprint deliberately excludes severity/finding_text so
        re-detecting the same underlying condition upserts onto the same
        row even if its wording changed, but that also means a defect that
        reappears at a DIFFERENT severity (e.g. was major, now critical)
        would otherwise stay stored at the old severity - directly
        undermining B11's critical-recall stop-gate and revision routing,
        both of which read the stored column, not the fresh draft.
        target_type/target_id/finding_category/violated_condition are part
        of the fingerprint itself, so they can't have changed between the
        original detection and this reappearance - nothing to refresh there.
        """
        finding = await self._session.get(RedteamFinding, finding_id)
        if finding is None:
            raise ValueError(f"RedteamFinding {finding_id} not found")
        finding.resolved = False
        finding.resolution_note = None
        finding.resolution_source = None
        finding.severity = severity
        finding.finding_text = finding_text
        finding.evidence_refs = evidence_refs or []
        await self._session.flush()
        return finding

    async def auto_retire_undetected_findings(
        self, tender_id: uuid.UUID, still_detected_fingerprints: set[str]
    ) -> list[RedteamFinding]:
        """run_red_team's own check list always covers the tender's ENTIRE
        current state on every pass (all 5 steps re-derive fresh from
        current data, never incrementally) - so an unresolved finding whose
        fingerprint isn't among what THIS pass just detected is provably no
        longer present, not merely unchecked this time. Marks it resolved
        (out of list_open_for_tender's set, same as a human resolution)
        with a system-authored resolution_note distinguishing it from one -
        the row itself is never deleted, so it stays in list_for_tender's
        full history/audit trail. Never touches a finding a human already
        resolved (the WHERE clause below only selects resolved=False rows).
        """
        # SQLAlchemy's notin_() on an empty Python collection compiles
        # inconsistently across dialects (Postgres itself rejects a bare
        # NOT IN ()) - branch explicitly rather than relying on that: an
        # empty still_detected_fingerprints means every open finding is
        # stale, not "match nothing."
        conditions = [
            RedteamFinding.tender_id == tender_id,
            RedteamFinding.resolved.is_(False),
        ]
        if still_detected_fingerprints:
            conditions.append(
                RedteamFinding.content_fingerprint.notin_(still_detected_fingerprints)
            )
        result = await self._session.execute(select(RedteamFinding).where(*conditions))
        stale = list(result.scalars().all())
        for finding in stale:
            finding.resolved = True
            finding.resolution_note = (
                "Auto-retired: no longer detected by the most recent red-team pass."
            )
            finding.resolution_source = "system"
        if stale:
            await self._session.flush()
        return stale
