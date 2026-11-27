import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import func, literal, select, text, update
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tenderos_backend.db.models.requirement import (
    ClarificationItem,
    Requirement,
    RequirementCitation,
    RequirementCodeCounter,
    RequirementKey,
    SubmissionItem,
)

# The first key of the two-key advisory lock on a tender's set of
# requirements (plan item 126). Distinct from refresh_tokens.py's 3401,
# graph_projection.py's 3402 and documents.py's 3403
# (tests/unit/test_advisory_lock_classes.py).
_REQUIREMENT_SET_LOCK_CLASS = 3404


@dataclass(frozen=True)
class VerdictWrite:
    """What RequirementRepository.set_verdict_for_text did: "changed" (the
    verdict and its check time written, `verified` moved), "unchanged" (the
    same verdict again: only its check time written, since PR 21),
    "text_changed" (refused, nothing written: the text is no longer the one
    judged) or "not_found" (deleted meanwhile). tender_id is the
    requirement's bid, None only when not found."""

    outcome: Literal["changed", "unchanged", "text_changed", "not_found"]
    tender_id: uuid.UUID | None


class RequirementRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, requirement_id: uuid.UUID) -> Requirement | None:
        return await self._session.get(Requirement, requirement_id)

    async def get_for_update(self, requirement_id: uuid.UUID) -> Requirement | None:
        """The requirement, its row locked until this transaction ends, for an
        edit that takes its "before" from it (plan item 59, D4): a second
        edit waits here and reads the first one's committed values.

        FOR NO KEY UPDATE, the lock the edit's own UPDATE takes, so the write
        that follows upgrades nothing, and an insert of a row that references
        the requirement (a compliance entry, a claim's link) is not made to
        wait. populate_existing: a row this session already holds is read
        again, not served from the identity map."""
        return await self._session.get(
            Requirement,
            requirement_id,
            with_for_update={"key_share": True},
            populate_existing=True,
        )

    async def list_for_tender(
        self, tender_id: uuid.UUID, include_rejected: bool = False
    ) -> list[Requirement]:
        stmt = select(Requirement).where(Requirement.tender_id == tender_id)
        if not include_rejected:
            stmt = stmt.where(Requirement.rejected_at.is_(None))
        result = await self._session.execute(stmt.order_by(Requirement.display_code))
        return list(result.scalars().all())

    async def lock_tender_requirements(self, tender_id: uuid.UUID) -> None:
        """Take the tender's transaction-scoped advisory lock on its set of
        requirements; it is released when the transaction ends (plan item
        126). Extraction's persist, merge and split take it first, so they run
        one at a time per bid: an extraction never finds a requirement by a
        key that a merge then moves, or cites a row a merge then deletes.

        An advisory lock, not the tender's row: an edit locks its requirement
        and then the tender (the bump), so a merge locking the tender before
        the originals could deadlock with it. Edits, verification and
        compliance writes do not take it; they change no row's existence and
        no key. Lock order: this lock, then compliance entries, then
        requirements, then the tender. hashtext() can map two tenders to one
        key; they then wait on each other briefly."""
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_class, hashtext(:tender_id))"),
            {"lock_class": _REQUIREMENT_SET_LOCK_CLASS, "tender_id": str(tender_id)},
        )

    async def lock_for_merge(self, requirement_ids: list[uuid.UUID]) -> list[Requirement]:
        """The requirements, their rows locked FOR UPDATE in id order until
        this transaction ends (plan item 126): merge and split update one and
        delete the others, and a delete needs this lock. Id order, so two
        merges sharing a requirement never wait on each other in a cycle.
        populate_existing: values read before the lock are read again."""
        if not requirement_ids:
            return []
        result = await self._session.execute(
            select(Requirement)
            .where(Requirement.id.in_(requirement_ids))
            .order_by(Requirement.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return list(result.scalars().all())

    async def get_by_fingerprint(
        self, tender_id: uuid.UUID, content_fingerprint: str
    ) -> Requirement | None:
        """The tender's requirement this extraction key finds (plan item
        126): read from requirement_keys, so a key merge or split moved finds
        the row it now names, and an overlap copy's key finds its row."""
        result = await self._session.execute(
            select(Requirement)
            .join(RequirementKey, RequirementKey.requirement_id == Requirement.id)
            .where(
                Requirement.tender_id == tender_id,
                RequirementKey.content_fingerprint == content_fingerprint,
            )
        )
        return result.scalar_one_or_none()

    async def next_display_number(self, tender_id: uuid.UUID, prefix: str) -> int:
        """The bid's next display code number for this prefix (migration 046):
        one statement that creates the counter at 1 or adds 1 to it, and
        returns the number. Never a count of rows, so a number a merge's
        deleted row held is never handed out again. The counter's row lock
        is held until the transaction ends, after the bid's requirement-set
        lock that every caller takes first."""
        counter = RequirementCodeCounter.__table__
        result = await self._session.execute(
            pg_insert(RequirementCodeCounter)
            .values(tender_id=tender_id, prefix=prefix, last_number=1)
            .on_conflict_do_update(
                index_elements=["tender_id", "prefix"],
                set_={"last_number": counter.c.last_number + 1},
            )
            .returning(RequirementCodeCounter.last_number)
        )
        return int(result.scalar_one())

    async def add_key(self, requirement_id: uuid.UUID, content_fingerprint: str) -> bool:
        """Record that this extraction key finds the requirement (plan item
        126). True when the key is new; a key already recorded is left with
        the requirement it names."""
        result = await self._session.execute(
            pg_insert(RequirementKey)
            .values(content_fingerprint=content_fingerprint, requirement_id=requirement_id)
            .on_conflict_do_nothing(index_elements=["content_fingerprint"])
            .returning(RequirementKey.content_fingerprint)
        )
        return result.scalar_one_or_none() is not None

    async def list_keys(self, requirement_id: uuid.UUID) -> list[str]:
        result = await self._session.execute(
            select(RequirementKey.content_fingerprint)
            .where(RequirementKey.requirement_id == requirement_id)
            .order_by(RequirementKey.created_at, RequirementKey.content_fingerprint)
        )
        return list(result.scalars().all())

    async def move_keys(self, from_ids: list[uuid.UUID], to_id: uuid.UUID) -> list[str]:
        """Point every key of these requirements at `to_id` (a merge's
        survivor), so the next extraction finds it for any original's text.
        Returns the keys moved."""
        if not from_ids:
            return []
        result = await self._session.execute(
            update(RequirementKey)
            .where(RequirementKey.requirement_id.in_(from_ids))
            .values(requirement_id=to_id)
            .returning(RequirementKey.content_fingerprint)
            .execution_options(synchronize_session=False)
        )
        return sorted(result.scalars().all())

    async def copy_citations(self, from_ids: list[uuid.UUID], to_id: uuid.UUID) -> list[uuid.UUID]:
        """Give `to_id` every citation of these requirements it lacks (a
        merge's survivor, a split's later parts). Returns the chunks added."""
        if not from_ids:
            return []
        source = (
            select(literal(to_id, UUID(as_uuid=True)), RequirementCitation.chunk_id)
            .where(RequirementCitation.requirement_id.in_(from_ids))
            .distinct()
        )
        result = await self._session.execute(
            pg_insert(RequirementCitation)
            .from_select(["requirement_id", "chunk_id"], source)
            .on_conflict_do_nothing(index_elements=["requirement_id", "chunk_id"])
            .returning(RequirementCitation.chunk_id)
        )
        return sorted(result.scalars().all())

    async def move_clarifications(
        self, from_ids: list[uuid.UUID], to_id: uuid.UUID
    ) -> list[uuid.UUID]:
        """Link every clarification of these requirements to `to_id` (a
        merge's survivor). Each keeps its fingerprint, which names the
        original; see merge_requirements for why. Returns the ones moved."""
        if not from_ids:
            return []
        result = await self._session.execute(
            update(ClarificationItem)
            .where(ClarificationItem.requirement_id.in_(from_ids))
            .values(requirement_id=to_id)
            .returning(ClarificationItem.id)
            .execution_options(synchronize_session=False)
        )
        return sorted(result.scalars().all())

    async def create(
        self,
        tender_id: uuid.UUID,
        display_code: str,
        requirement_text: str,
        normalized_text: str,
        category: str,
        mandatory: bool,
        source_chunk_id: uuid.UUID,
        content_fingerprint: str,
        confidence: float | None = None,
    ) -> Requirement:
        requirement = Requirement(
            tender_id=tender_id,
            display_code=display_code,
            requirement_text=requirement_text,
            normalized_text=normalized_text,
            category=category,
            mandatory=mandatory,
            source_chunk_id=source_chunk_id,
            content_fingerprint=content_fingerprint,
            confidence=confidence,
        )
        self._session.add(requirement)
        await self._session.flush()
        # Plan item 125: every requirement's primary citation is also in
        # requirement_citations, so "every chunk it was found in" is one
        # query (extraction, merge and split all create through here).
        await self.add_citation(requirement.id, source_chunk_id)
        # Plan item 126: and its own key is in requirement_keys, where
        # extraction looks keys up.
        await self.add_key(requirement.id, content_fingerprint)
        return requirement

    async def add_citation(self, requirement_id: uuid.UUID, chunk_id: uuid.UUID) -> bool:
        """Record that the requirement was found in this chunk (plan item 125).
        True when the citation is new; a citation already recorded is left
        as it is."""
        result = await self._session.execute(
            pg_insert(RequirementCitation)
            .values(requirement_id=requirement_id, chunk_id=chunk_id)
            .on_conflict_do_nothing(index_elements=["requirement_id", "chunk_id"])
            .returning(RequirementCitation.requirement_id)
        )
        return result.scalar_one_or_none() is not None

    async def list_citations(
        self, requirement_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[uuid.UUID]]:
        """Each requirement's cited chunks, the primary citation first, then
        the others in the order they were found (plan item 125)."""
        if not requirement_ids:
            return {}
        result = await self._session.execute(
            select(
                RequirementCitation.requirement_id,
                RequirementCitation.chunk_id,
                Requirement.source_chunk_id,
            )
            .join(Requirement, Requirement.id == RequirementCitation.requirement_id)
            .where(RequirementCitation.requirement_id.in_(requirement_ids))
            .order_by(RequirementCitation.created_at, RequirementCitation.chunk_id)
        )
        cited: dict[uuid.UUID, list[uuid.UUID]] = {rid: [] for rid in requirement_ids}
        for requirement_id, chunk_id, primary in result.all():
            if chunk_id == primary:
                cited[requirement_id].insert(0, chunk_id)
            else:
                cited[requirement_id].append(chunk_id)
        return cited

    async def list_by_normalized_text(
        self, tender_id: uuid.UUID, normalized_text: str
    ) -> list[Requirement]:
        """The tender's requirements, rejected ones included, whose normalized
        text is exactly this: the candidates for a draft's overlap copy (plan
        item 125). Rejected rows are included so a rejected requirement is not
        brought back by its copy in the next chunk."""
        result = await self._session.execute(
            select(Requirement)
            .where(
                Requirement.tender_id == tender_id,
                Requirement.normalized_text == normalized_text,
            )
            .order_by(Requirement.created_at, Requirement.id)
        )
        return list(result.scalars().all())

    async def update_fields(self, requirement_id: uuid.UUID, **fields: object) -> Requirement:
        requirement = await self._session.get(Requirement, requirement_id)
        if requirement is None:
            raise ValueError(f"Requirement {requirement_id} not found")
        for key, value in fields.items():
            setattr(requirement, key, value)
        await self._session.flush()
        # updated_at's value comes from the server (onupdate=func.now()) - a
        # plain flush() doesn't pull it back into the Python object, so
        # reading .updated_at after this UnitOfWork's session has closed
        # (every route does, via response-model serialization) raises
        # DetachedInstanceError. refresh() while the session is still open
        # avoids that.
        await self._session.refresh(requirement)
        return requirement

    async def set_verdict_for_text(
        self, requirement_id: uuid.UUID, verified: bool, judged_text: str
    ) -> VerdictWrite:
        """Write a verification verdict only if the requirement's text is
        still `judged_text`, the text the model judged (plan item 59, fix 6),
        with the time it was checked (Q5, PR 21).

        The model call takes seconds outside any transaction, and a person's
        edit of the text can commit meanwhile; a verdict on the old text must
        not land on the new one. So the row is read locked (get_for_update,
        FOR NO KEY UPDATE, the lock the UPDATE below takes): an edit still
        open is waited for, and its text is then compared.

        When the text is the judged one, the verdict and
        verification_checked_at (the database clock) are written whether or
        not `verified` changes: a re-check with the same verdict still
        records when it ran. "changed" or "unchanged" says whether `verified`
        moved, the only part the graph shows, so the caller bumps on
        "changed" only. The old value is read under the lock, because an
        UPDATE's RETURNING gives only the new one."""
        requirement = await self.get_for_update(requirement_id)
        if requirement is None:
            return VerdictWrite("not_found", None)
        if requirement.requirement_text != judged_text:
            return VerdictWrite("text_changed", requirement.tender_id)
        changed = requirement.verified != verified
        await self._session.execute(
            update(Requirement)
            .where(Requirement.id == requirement_id)
            .values(verified=verified, verification_checked_at=func.now())
            .execution_options(synchronize_session=False)
        )
        return VerdictWrite("changed" if changed else "unchanged", requirement.tender_id)

    async def reject(self, requirement_id: uuid.UUID) -> Requirement:
        return await self.update_fields(
            requirement_id, rejected_at=datetime.now(UTC), human_reviewed=True
        )

    async def delete(self, requirement_id: uuid.UUID) -> None:
        # Used by merge/split to remove superseded rows outright rather than
        # soft-delete them - unlike a human's single-item reject, these rows
        # are being replaced within the same request, not withdrawn from
        # review, so there's no "did a reviewer mean to discard this"
        # audit question a rejected_at timestamp needs to answer.
        requirement = await self._session.get(Requirement, requirement_id)
        if requirement is not None:
            await self._session.delete(requirement)
            await self._session.flush()

    async def get_submission_item(self, item_id: uuid.UUID) -> SubmissionItem | None:
        return await self._session.get(SubmissionItem, item_id)

    async def create_submission_item(
        self,
        tender_id: uuid.UUID,
        name: str,
        mandatory: bool,
        content_fingerprint: str,
        source_chunk_id: uuid.UUID | None = None,
    ) -> SubmissionItem:
        item = SubmissionItem(
            tender_id=tender_id,
            name=name,
            mandatory=mandatory,
            source_chunk_id=source_chunk_id,
            content_fingerprint=content_fingerprint,
        )
        self._session.add(item)
        await self._session.flush()
        return item

    async def get_submission_item_by_fingerprint(
        self, tender_id: uuid.UUID, content_fingerprint: str
    ) -> SubmissionItem | None:
        result = await self._session.execute(
            select(SubmissionItem).where(
                SubmissionItem.tender_id == tender_id,
                SubmissionItem.content_fingerprint == content_fingerprint,
            )
        )
        return result.scalar_one_or_none()

    async def list_submission_items_for_tender(self, tender_id: uuid.UUID) -> list[SubmissionItem]:
        result = await self._session.execute(
            select(SubmissionItem).where(SubmissionItem.tender_id == tender_id)
        )
        return list(result.scalars().all())

    async def update_submission_item(self, item_id: uuid.UUID, **fields: object) -> SubmissionItem:
        # Same **fields shape as ComplianceRepository.update_compliance_entry/
        # TalentRepository.update_role_match - no write path exists yet for
        # flipping status (e.g. missing -> present once a matching document
        # is uploaded, or -> needs_review), same gap those two had before
        # their own first caller needed them.
        item = await self._session.get(SubmissionItem, item_id)
        if item is None:
            raise ValueError(f"SubmissionItem {item_id} not found")
        for key, value in fields.items():
            setattr(item, key, value)
        await self._session.flush()
        return item

    async def create_clarification_item(
        self,
        tender_id: uuid.UUID,
        question: str,
        content_fingerprint: str,
        requirement_id: uuid.UUID | None = None,
        reason: str | None = None,
    ) -> ClarificationItem:
        item = ClarificationItem(
            tender_id=tender_id,
            requirement_id=requirement_id,
            question=question,
            reason=reason,
            content_fingerprint=content_fingerprint,
        )
        self._session.add(item)
        await self._session.flush()
        return item

    async def get_clarification_item_by_fingerprint(
        self, tender_id: uuid.UUID, content_fingerprint: str
    ) -> ClarificationItem | None:
        result = await self._session.execute(
            select(ClarificationItem).where(
                ClarificationItem.tender_id == tender_id,
                ClarificationItem.content_fingerprint == content_fingerprint,
            )
        )
        return result.scalar_one_or_none()

    async def list_clarification_items_for_tender(
        self, tender_id: uuid.UUID, include_rejected: bool = False
    ) -> list[ClarificationItem]:
        """Plan item 97: a clarification linked to a rejected requirement is
        out of scope for the bid, as the requirement is, so the requirement
        gate's clarification_count and readiness's open_clarifications leave it
        out. One with no requirement link stays. Nothing is deleted; the filter
        reads rejected_at at query time."""
        stmt = select(ClarificationItem).where(ClarificationItem.tender_id == tender_id)
        if not include_rejected:
            stmt = stmt.outerjoin(
                Requirement, Requirement.id == ClarificationItem.requirement_id
            ).where(Requirement.rejected_at.is_(None))
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
