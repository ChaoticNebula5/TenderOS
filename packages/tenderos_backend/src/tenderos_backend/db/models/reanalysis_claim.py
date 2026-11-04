import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, CheckConstraint, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from tenderos_backend.db.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class ReanalysisClaim(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One delivery of a knowledge-selection reanalysis that has started
    (plan item 18, decision 0036; migration 023).

    The outbox delivers KnowledgeSelectionChanged at least once, and
    reanalyze_knowledge_selection_change makes paid compliance calls and
    bumps the tender's state_version, so a second delivery of the same
    change must do nothing. The job inserts this row before any work; the
    unique key makes a second insert for the same change find the first.
    A claim whose job died is taken over once its lease has lapsed.

    knowledge_item_id has no foreign key on purpose: the row records that a
    delivery ran, so deleting the item must neither be blocked by it nor
    remove it (which would let a late duplicate run again).
    """

    __tablename__ = "reanalysis_claims"
    __table_args__ = (
        UniqueConstraint(
            "tender_id", "knowledge_item_id", "state_version", name="uq_reanalysis_claim"
        ),
        CheckConstraint("status IN ('running','completed')", name="ck_reanalysis_claim_status"),
        CheckConstraint(
            "(status = 'completed') = (completed_at IS NOT NULL)",
            name="ck_reanalysis_claim_completed_pair",
        ),
    )

    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    knowledge_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(nullable=False)
    # A fresh token per claim or takeover: only the holder of the current
    # token may mark the claim completed.
    claim_token: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    locked_until: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
