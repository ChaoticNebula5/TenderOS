import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tenderos_backend.db.models.requirement import (
    ClarificationItem,
    Requirement,
    SubmissionItem,
)


class RequirementRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, requirement_id: uuid.UUID) -> Requirement | None:
        return await self._session.get(Requirement, requirement_id)

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

    async def set_verified(self, requirement_id: uuid.UUID, verified: bool) -> Requirement:
        return await self.update_fields(requirement_id, verified=verified)

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
        self, tender_id: uuid.UUID
    ) -> list[ClarificationItem]:
        result = await self._session.execute(
            select(ClarificationItem).where(ClarificationItem.tender_id == tender_id)
        )
        return list(result.scalars().all())
