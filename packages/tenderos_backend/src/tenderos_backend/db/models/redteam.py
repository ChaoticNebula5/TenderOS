import uuid
from datetime import datetime

from sqlalchemy import (
    TIMESTAMP,
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from tenderos_backend.db.models.base import Base, UUIDPrimaryKeyMixin


class RedteamFinding(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "redteam_findings"
    __table_args__ = (
        CheckConstraint(
            "severity IN ('critical','major','minor')",
            name="ck_redteam_finding_severity",
        ),
        CheckConstraint(
            "resolution_source IN ('system','human')",
            name="ck_redteam_finding_resolution_source",
        ),
        UniqueConstraint("tender_id", "content_fingerprint", name="uq_redteam_finding_fingerprint"),
    )

    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    target_type: Mapped[str] = mapped_column(nullable=False)
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    finding_category: Mapped[str] = mapped_column(nullable=False)
    severity: Mapped[str] = mapped_column(nullable=False)
    finding_text: Mapped[str] = mapped_column(nullable=False)
    rebuttal: Mapped[str | None] = mapped_column(nullable=True)
    evidence_refs: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    violated_condition: Mapped[str] = mapped_column(nullable=False)
    content_fingerprint: Mapped[str] = mapped_column(nullable=False)
    resolved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    resolution_note: Mapped[str | None] = mapped_column(nullable=True)
    # NULL until resolved; 'human' (resolve_finding) or 'system'
    # (auto_retire_undetected_findings) once it is. Lets _persist_draft
    # reopen a system-retired finding that reappears while leaving a
    # human's resolution untouched, without string-matching resolution_note.
    resolution_source: Mapped[str | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


# Plan items 94 and 96 (migration 032): one row per finished red-team pass,
# written by redteam_service.run_red_team for every caller (the run's
# red_team node, the manual redteam_run operation). Readiness reads these
# instead of being told by its caller whether a pass failed: it asks whether
# a complete pass, graph checks included, has seen the bid's current state
# version. A pass that dies mid-way writes no row (and no findings).
REDTEAM_PASS_OUTCOMES = ("completed", "failed")
REDTEAM_GRAPH_CHECKS = ("ran", "skipped")


class RedteamPass(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "redteam_passes"
    __table_args__ = (
        CheckConstraint("outcome IN ('completed','failed')", name="ck_redteam_pass_outcome"),
        CheckConstraint(
            "graph_checks IS NULL OR graph_checks IN ('ran','skipped')",
            name="ck_redteam_pass_graph_checks",
        ),
        # A completed pass says whether its graph checks ran; a failed one
        # says why it failed; a graph version is recorded exactly when the
        # graph checks ran.
        CheckConstraint(
            "(outcome = 'completed' AND graph_checks IS NOT NULL AND error_code IS NULL)"
            " OR (outcome = 'failed' AND graph_checks IS NULL AND error_code IS NOT NULL)",
            name="ck_redteam_pass_shape",
        ),
        CheckConstraint(
            "(graph_checks IS NOT DISTINCT FROM 'ran') = (graph_state_version IS NOT NULL)",
            name="ck_redteam_pass_graph_version",
        ),
        Index("idx_redteam_pass_latest", "tender_id", text("finished_at DESC"), text("id DESC")),
    )

    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_runs.id", ondelete="SET NULL"), nullable=True
    )
    operation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("async_operations.id", ondelete="SET NULL"), nullable=True
    )
    outcome: Mapped[str] = mapped_column(nullable=False)
    graph_checks: Mapped[str | None] = mapped_column(nullable=True)
    # The bid's state version read just before the graph checks: when they
    # ran, check_freshness had confirmed the latest completed projection
    # equals the version at that moment, which is at least this one (the
    # version only rises). So it never claims a newer graph than was read.
    graph_state_version: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # A safe code (the exception's type name), never the message.
    error_code: Mapped[str | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
