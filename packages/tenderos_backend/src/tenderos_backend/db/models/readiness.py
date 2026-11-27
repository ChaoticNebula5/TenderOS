import uuid
from datetime import datetime

from sqlalchemy import (
    TIMESTAMP,
    BigInteger,
    Boolean,
    ForeignKey,
    Sequence,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from tenderos_backend.db.models.base import Base, UUIDPrimaryKeyMixin

# Plan item 120 (migration 044): the order of reports, never a clock. Taken on
# insert (the column's default) and on every reuse (upsert_report), under the
# tender's row lock (readiness_service), so one tender's generations follow
# the order its reports were committed. Declared on the metadata so the
# models' build (tests/models_schema.py) creates it as the migration does.
GENERATION_SEQUENCE = Sequence("readiness_report_generation_seq", metadata=Base.metadata)


class ReadinessReport(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "readiness_reports"
    __table_args__ = (
        # Plan item 93 (migration 030): one row per distinct content at a
        # version, not one per version. A generate whose content already has
        # a row reuses it and gives it a new generation, so the highest
        # generation is the current report (plan item 120).
        UniqueConstraint(
            "tender_id",
            "tender_state_version",
            "content_hash",
            name="uq_readiness_report_content",
        ),
        # Plan item 120: no two of one tender's reports share a generation,
        # and "latest" reads this key's index.
        UniqueConstraint("tender_id", "generation", name="uq_readiness_report_generation"),
    )

    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    tender_state_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Both JSONB, not two separate tables - readiness is a point-in-time
    # snapshot read as a whole (one GET /readiness call wants the full
    # picture), not queried by individual blocker/count the way
    # redteam_findings needs per-row resolve. counts: the PRD's fuller
    # §10.13 list (visible, non-blocking). blockers: the Build Guide's
    # narrower "required blockers" list (refusal-triggering) - see the B12
    # plan's Q5 for why these are deliberately two different lists sourced
    # from the two different docs, not one merged list.
    counts: Mapped[dict] = mapped_column(JSONB, nullable=False)
    blockers: Mapped[list] = mapped_column(JSONB, nullable=False)
    ready: Mapped[bool] = mapped_column(Boolean, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    # Canonical JSON over ready, blockers and counts, hashed
    # (readiness_service.report_content_hash). Rows from before migration 030
    # hold 'legacy-' plus their id, which no real hash equals.
    content_hash: Mapped[str] = mapped_column(nullable=False)
    # Set on insert and again whenever a generate reuses this row, for display
    # and the final gate's refusal line. Since plan item 120 it orders
    # nothing: the database clock behind it steps back (item 117).
    last_generated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    # Plan item 120: taken from GENERATION_SEQUENCE on insert and again on
    # every reuse, so content that goes X, Y, X makes X current again. "The
    # latest report" is the one with the highest generation.
    generation: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=GENERATION_SEQUENCE.next_value()
    )
