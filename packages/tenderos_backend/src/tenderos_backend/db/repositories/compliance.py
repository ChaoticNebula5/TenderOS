import uuid

from sqlalchemy import delete, literal, or_, select, update
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tenderos_backend.db.models.compliance import ComplianceEntry, ComplianceEvidence, EvidenceLink
from tenderos_backend.db.models.requirement import Requirement


class ComplianceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # evidence_links
    # ------------------------------------------------------------------

    async def create_evidence_link(
        self,
        tender_id: uuid.UUID,
        source_kind: str,
        content_fingerprint: str,
        knowledge_item_id: uuid.UUID | None = None,
        source_chunk_id: uuid.UUID | None = None,
        excerpt: str | None = None,
        confidence: float | None = None,
        verified: bool = False,
        validity_status: str = "unknown",
    ) -> EvidenceLink:
        link = EvidenceLink(
            tender_id=tender_id,
            knowledge_item_id=knowledge_item_id,
            source_chunk_id=source_chunk_id,
            source_kind=source_kind,
            excerpt=excerpt,
            confidence=confidence,
            verified=verified,
            validity_status=validity_status,
            content_fingerprint=content_fingerprint,
        )
        self._session.add(link)
        await self._session.flush()
        return link

    async def get_evidence_link(self, evidence_link_id: uuid.UUID) -> EvidenceLink | None:
        return await self._session.get(EvidenceLink, evidence_link_id)

    async def get_evidence_link_by_fingerprint(
        self, tender_id: uuid.UUID, content_fingerprint: str
    ) -> EvidenceLink | None:
        result = await self._session.execute(
            select(EvidenceLink).where(
                EvidenceLink.tender_id == tender_id,
                EvidenceLink.content_fingerprint == content_fingerprint,
            )
        )
        return result.scalar_one_or_none()

    async def list_evidence_links_for_tender(self, tender_id: uuid.UUID) -> list[EvidenceLink]:
        # B10: the graph projector needs every evidence_links row for the
        # tender (valid, invalid, and unverified alike) - D7's coverage
        # re-evaluation has to see the full set to compute valid_path_count
        # correctly, not just the currently-valid subset.
        result = await self._session.execute(
            select(EvidenceLink).where(EvidenceLink.tender_id == tender_id)
        )
        return list(result.scalars().all())

    async def refresh_evidence_link_signals(
        self, evidence_link_id: uuid.UUID, verified: bool, validity_status: str
    ) -> bool:
        """Plan item 88: rewrite a link's stored verified and validity_status
        to what a compliance evaluation just computed for its source. True
        only when a value actually changed (one conditional UPDATE, so an
        unchanged link writes nothing). The caller must commit this together
        with the bid's state-version bump: the link is projected into the
        bid's graph, so a changed link makes it stale (since plan item 93
        readiness computes on every generate and no longer depends on it)."""
        result = await self._session.execute(
            update(EvidenceLink)
            .where(
                EvidenceLink.id == evidence_link_id,
                or_(
                    EvidenceLink.verified.is_distinct_from(verified),
                    EvidenceLink.validity_status.is_distinct_from(validity_status),
                ),
            )
            .values(verified=verified, validity_status=validity_status)
            .returning(EvidenceLink.id)
        )
        return result.scalar_one_or_none() is not None

    # ------------------------------------------------------------------
    # compliance_entries
    # ------------------------------------------------------------------

    async def create_compliance_entry(
        self,
        requirement_id: uuid.UUID,
        status: str,
        reasoning_summary: str | None = None,
        confidence: float | None = None,
    ) -> ComplianceEntry:
        # A new entry is always the analysis's answer (an override edits an
        # existing one), so its automated_* columns start as its own values
        # and decided_by as 'analysis' (plan item 121).
        entry = ComplianceEntry(
            requirement_id=requirement_id,
            status=status,
            reasoning_summary=reasoning_summary,
            automated_status=status,
            automated_reasoning_summary=reasoning_summary,
            decided_by="analysis",
            confidence=confidence,
        )
        self._session.add(entry)
        await self._session.flush()
        return entry

    async def get_compliance_entry(self, compliance_entry_id: uuid.UUID) -> ComplianceEntry | None:
        return await self._session.get(ComplianceEntry, compliance_entry_id)

    async def get_compliance_entry_for_update(
        self, compliance_entry_id: uuid.UUID
    ) -> ComplianceEntry | None:
        """The entry, its row locked until this transaction ends (plan item
        59). Approve and override read their entry this way, and a
        re-evaluation reads it the same way by requirement
        (get_compliance_entry_by_requirement_for_update), so each judges the
        entry as the others left it. populate_existing: a row already in the
        session is refreshed from the locked read. Lock order: the entry,
        then the tender (the bump)."""
        result = await self._session.execute(
            select(ComplianceEntry)
            .where(ComplianceEntry.id == compliance_entry_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def get_compliance_entry_by_requirement_for_update(
        self, requirement_id: uuid.UUID
    ) -> ComplianceEntry | None:
        """get_compliance_entry_by_requirement with the entry's row locked,
        as get_compliance_entry_for_update. None, with nothing locked, when
        the requirement has no entry yet."""
        result = await self._session.execute(
            select(ComplianceEntry)
            .where(ComplianceEntry.requirement_id == requirement_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def lock_compliance_entries_by_requirements(
        self, requirement_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, ComplianceEntry]:
        """These requirements' entries keyed by requirement, their rows
        locked FOR UPDATE in entry id order (plan item 126). A merge or split
        takes them before the requirements: approve, override and
        re-evaluation lock the entry first too, and a re-evaluation then
        writes rows referencing the requirement, so locking the requirement
        first could wait on it in a cycle."""
        if not requirement_ids:
            return {}
        result = await self._session.execute(
            select(ComplianceEntry)
            .where(ComplianceEntry.requirement_id.in_(requirement_ids))
            .order_by(ComplianceEntry.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return {entry.requirement_id: entry for entry in result.scalars().all()}

    async def copy_evidence(
        self, from_entry_ids: list[uuid.UUID], to_entry_id: uuid.UUID
    ) -> list[uuid.UUID]:
        """Attach to `to_entry_id` every evidence link of these entries it
        lacks (plan item 126: the entry a merge keeps gains the dropped
        entries' evidence). Returns the links added."""
        if not from_entry_ids:
            return []
        source = (
            select(literal(to_entry_id, UUID(as_uuid=True)), ComplianceEvidence.evidence_link_id)
            .where(ComplianceEvidence.compliance_entry_id.in_(from_entry_ids))
            .distinct()
        )
        result = await self._session.execute(
            pg_insert(ComplianceEvidence)
            .from_select(["compliance_entry_id", "evidence_link_id"], source)
            .on_conflict_do_nothing(constraint="uq_compliance_evidence")
            .returning(ComplianceEvidence.evidence_link_id)
        )
        return sorted(result.scalars().all())

    async def delete_compliance_entry(self, compliance_entry_id: uuid.UUID) -> None:
        """Plan item 126: a merge drops the entries it does not keep. Its
        evidence attachments cascade; the evidence links stay (other entries
        and claims may cite them)."""
        await self._session.execute(
            delete(ComplianceEntry)
            .where(ComplianceEntry.id == compliance_entry_id)
            .execution_options(synchronize_session=False)
        )

    async def get_compliance_entry_by_requirement(
        self, requirement_id: uuid.UUID
    ) -> ComplianceEntry | None:
        # requirement_id is UNIQUE on this table (Part C) - this is the
        # lookup the compliance node uses on every run to decide whether a
        # requirement already has an entry (re-evaluate in place) or needs
        # one created, which is what makes retry idempotent at the
        # repository layer rather than relying on the caller to remember.
        result = await self._session.execute(
            select(ComplianceEntry).where(ComplianceEntry.requirement_id == requirement_id)
        )
        return result.scalar_one_or_none()

    async def list_compliance_entries_by_requirement_ids(
        self, requirement_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, ComplianceEntry]:
        # readiness_service.py's own blocker 1 (unresolved mandatory
        # requirement) used to call get_compliance_entry_by_requirement
        # once per requirement in a loop - O(requirements) sequential
        # round-trips, invisible on a small synthetic tender but real cost
        # on hundreds of real requirements, and paid twice per check since
        # evaluate_readiness runs from both the manual generate route and
        # the orchestration node. One IN-list query, keyed by
        # requirement_id (UNIQUE on this table, so the mapping is 1:1)
        # for the caller to look up in memory instead.
        if not requirement_ids:
            return {}
        result = await self._session.execute(
            select(ComplianceEntry).where(ComplianceEntry.requirement_id.in_(requirement_ids))
        )
        return {entry.requirement_id: entry for entry in result.scalars().all()}

    async def list_compliance_entries_for_tender(
        self, tender_id: uuid.UUID, include_rejected: bool = False
    ) -> list[ComplianceEntry]:
        """compliance_entries has no tender_id column of its own (Part C) -
        every entry is reached through its one-to-one requirement, so
        listing "for a tender" means joining through Requirement rather
        than a direct WHERE.

        Plan item 97: an entry whose requirement is rejected is out of scope
        for the bid, as RequirementRepository.list_for_tender leaves the
        requirement itself out. Nothing is deleted; the filter reads
        rejected_at here, so the compliance matrix and gate, readiness,
        drafting and the graph projection all leave the entry out, and a
        requirement brought back would bring its entry back."""
        stmt = (
            select(ComplianceEntry)
            .join(Requirement, Requirement.id == ComplianceEntry.requirement_id)
            .where(Requirement.tender_id == tender_id)
        )
        if not include_rejected:
            stmt = stmt.where(Requirement.rejected_at.is_(None))
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update_compliance_entry(
        self, compliance_entry_id: uuid.UUID, **fields: object
    ) -> ComplianceEntry:
        entry = await self._session.get(ComplianceEntry, compliance_entry_id)
        if entry is None:
            raise ValueError(f"ComplianceEntry {compliance_entry_id} not found")
        for key, value in fields.items():
            setattr(entry, key, value)
        await self._session.flush()
        # Same DetachedInstanceError concern RequirementRepository.update_fields
        # documents - updated_at/last_evaluated_at both carry onupdate/
        # server_default values a plain flush() doesn't pull back into the
        # Python object, and routes serialize this object after the
        # UnitOfWork's session has already closed.
        await self._session.refresh(entry)
        return entry

    # ------------------------------------------------------------------
    # compliance_evidence (junction)
    # ------------------------------------------------------------------

    async def link_evidence(
        self, compliance_entry_id: uuid.UUID, evidence_link_id: uuid.UUID
    ) -> None:
        # ON CONFLICT DO NOTHING on uq_compliance_evidence - re-running
        # compliance analysis for a requirement that already has this exact
        # evidence attached (retry) must not raise IntegrityError, and must
        # not create a second row either. Same idempotent-retry shape B6's
        # relationship import needed for uq_knowledge_relationship_fingerprint.
        stmt = (
            pg_insert(ComplianceEvidence)
            .values(compliance_entry_id=compliance_entry_id, evidence_link_id=evidence_link_id)
            .on_conflict_do_nothing(constraint="uq_compliance_evidence")
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def list_evidence_for_compliance_entry(
        self, compliance_entry_id: uuid.UUID
    ) -> list[EvidenceLink]:
        result = await self._session.execute(
            select(EvidenceLink)
            .join(ComplianceEvidence, ComplianceEvidence.evidence_link_id == EvidenceLink.id)
            .where(ComplianceEvidence.compliance_entry_id == compliance_entry_id)
        )
        return list(result.scalars().all())

    async def list_evidence_for_compliance_entries(
        self, compliance_entry_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[EvidenceLink]]:
        # readiness_service.py's own blocker 2 (compliant decision without
        # valid evidence) - same "one query instead of one per item" fix
        # as list_compliance_entries_by_requirement_ids above, for the
        # sibling loop. Selects the junction's own compliance_entry_id
        # alongside EvidenceLink (unlike the single-entry method above,
        # which doesn't need it) so results can be grouped back in memory -
        # every requested id is present in the returned dict, even with an
        # empty list, so a caller doesn't need `.get(id, [])` at every
        # call site to tell "no evidence" from "id not in the batch."
        result: dict[uuid.UUID, list[EvidenceLink]] = {cid: [] for cid in compliance_entry_ids}
        if not compliance_entry_ids:
            return result
        rows = await self._session.execute(
            select(ComplianceEvidence.compliance_entry_id, EvidenceLink)
            .join(EvidenceLink, EvidenceLink.id == ComplianceEvidence.evidence_link_id)
            .where(ComplianceEvidence.compliance_entry_id.in_(compliance_entry_ids))
        )
        for compliance_entry_id, evidence_link in rows.all():
            result[compliance_entry_id].append(evidence_link)
        return result

    async def unlink_all_evidence(self, compliance_entry_id: uuid.UUID) -> None:
        # Used when a re-evaluation (e.g. an on-demand re-run after the
        # requirement's evidence selection changed) needs to replace the
        # entry's evidence set rather than accumulate stale links from a
        # previous run alongside new ones - delete-then-relink rather than
        # trying to diff old vs. new.
        result = await self._session.execute(
            select(ComplianceEvidence).where(
                ComplianceEvidence.compliance_entry_id == compliance_entry_id
            )
        )
        for row in result.scalars().all():
            await self._session.delete(row)
        await self._session.flush()
