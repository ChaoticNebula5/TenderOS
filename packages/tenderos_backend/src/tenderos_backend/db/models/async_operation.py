import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, CheckConstraint, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from tenderos_backend.db.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin

# The kinds of work that run as an async operation instead of inside an
# HTTP request (Phase 4: no LLM call inside a request). Stage 1 converts
# redteam_run; proposal_generation and commercial_validation follow.
OPERATION_KINDS = ("redteam_run", "proposal_generation", "commercial_validation")
OPERATION_STATUSES = ("queued", "running", "succeeded", "failed")
ACTIVE_STATUSES = ("queued", "running")
# The partial unique index's predicate, as literal SQL. create_queued's
# ON CONFLICT names the same text: Postgres matches a conflict target to a
# partial index only when it can prove the target's WHERE implies the
# index's, which it cannot do with bound parameters.
ACTIVE_PREDICATE_SQL = "status IN ('queued','running')"


class AsyncOperation(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One request for long-running work on a tender, and its progress.

    queued (requested, waiting for the worker) -> running (claimed by one
    worker, which holds the lease until locked_until) -> succeeded | failed.
    The results themselves live in the feature's own tables (red-team
    findings, proposal sections, pricing findings); this row only tracks
    state, so the API, the worker claim and the UI can treat every kind the
    same way.
    """

    __tablename__ = "async_operations"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('redteam_run','proposal_generation','commercial_validation')",
            name="ck_async_operation_kind",
        ),
        CheckConstraint(
            "status IN ('queued','running','succeeded','failed')",
            name="ck_async_operation_status",
        ),
        # At most one active operation of a kind per tender: a second
        # request while one is queued or running gets the existing one.
        Index(
            "uq_async_operation_active",
            "tender_id",
            "kind",
            unique=True,
            postgresql_where=text(ACTIVE_PREDICATE_SQL),
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(nullable=False)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Safe, user-facing failure details only (a code such as LLM_TIMEOUT and
    # a generic message), never exception text or internals.
    error_code: Mapped[str | None] = mapped_column(nullable=True)
    error_message: Mapped[str | None] = mapped_column(nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    # The running worker's lease (job timeout + 60 s, set at claim from the
    # database clock) and its claim token: only the holder can finish the
    # operation, and the reaper fails one whose lease has lapsed.
    locked_until: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    claim_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
