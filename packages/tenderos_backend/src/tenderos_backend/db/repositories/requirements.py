import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tenderos_backend.db.models.requirement import (
    ClarificationItem,
    Requirement,
    SubmissionItem,
)


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

    async def get_by_fingerprint(
        self, tender_id: uuid.UUID, content_fingerprint: str
    ) -> Requirement | None:
        result = await self._session.execute(
            select(Requirement).where(
                Requirement.tender_id == tender_id,
                Requirement.content_fingerprint == content_fingerprint,
            )
        )
        return result.scalar_one_or_none()

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
        return requirement

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
