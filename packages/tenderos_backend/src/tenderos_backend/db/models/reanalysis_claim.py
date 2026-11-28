import uuid
from datetime import datetime

from sqlalchemy import (
    TIMESTAMP,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    UniqueConstraint,
    text,
)
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
    A running claim whose lease lapsed can still be taken over by a second
    delivery of the same change.

    Plan item 124 (migration 047): a claim also ends failed, and then for
    good. The job fails its own claim when it cannot finish (GRAPH_STALE, or
    the exception's class name; reaped_rule null); the dispatcher's reaper
    fails the claim of a job RQ says will never finish, or whose lease
    lapsed (WORKER_LOST, reaped_rule the RQ verdict or "lease"). Readiness
    blocks a bid while an item's latest failed claim has no completed claim
    after it (reanalysis_not_applied).

    knowledge_item_id has no foreign key on purpose: the row records that a
    delivery ran, so deleting the item must neither be blocked by it nor
    remove it (which would let a late duplicate run again).
    """

    __tablename__ = "reanalysis_claims"
    __table_args__ = (
        UniqueConstraint(
            "tender_id", "knowledge_item_id", "state_version", name="uq_reanalysis_claim"
        ),
        CheckConstraint(
            "status IN ('running','completed','failed')", name="ck_reanalysis_claim_status"
        ),
        CheckConstraint(
            "(status = 'completed') = (completed_at IS NOT NULL)",
            name="ck_reanalysis_claim_completed_pair",
        ),
        CheckConstraint(
            "(status = 'failed') = (failed_at IS NOT NULL)",
            name="ck_reanalysis_claim_failed_at_pair",
        ),
        CheckConstraint(
            "(status = 'failed') = (error_code IS NOT NULL)",
            name="ck_reanalysis_claim_error_code_pair",
        ),
        CheckConstraint(
            "reaped_rule IS NULL OR status = 'failed'",
            name="ck_reanalysis_claim_reaped_rule_failed",
        ),
        # The reaper's scan (plan item 124): running claims, oldest first.
        Index(
            "idx_reanalysis_claims_running",
            "created_at",
            postgresql_where=text("status = 'running'"),
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
    failed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(nullable=True)
    reaped_rule: Mapped[str | None] = mapped_column(nullable=True)


# The codes a claim fails with that are not an exception's class name.
REANALYSIS_GRAPH_STALE = "GRAPH_STALE"
REANALYSIS_WORKER_LOST = "WORKER_LOST"
# The reaper's own rule beside RQ's verdicts (domain/run_endings.py JOB_*).
REANALYSIS_RULE_LEASE = "lease"
REANALYSIS_CLAIM_STATUSES = ("running", "completed", "failed")
