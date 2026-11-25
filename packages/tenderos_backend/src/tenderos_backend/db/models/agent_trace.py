import uuid
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Numeric
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from tenderos_backend.db.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class AgentTrace(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "agent_traces"
    __table_args__ = (
        # B6: tender_id was NOT NULL until organization-scoped agent calls
        # (resume/certification extraction, WORKED_ON/DEMONSTRATES) existed.
        # Exactly one of tender_id/organization_id is set per row, mirroring
        # documents.ck_document_scope.
        CheckConstraint(
            "(tender_id IS NOT NULL AND organization_id IS NULL) "
            "OR (tender_id IS NULL AND organization_id IS NOT NULL)",
            name="ck_agent_trace_scope",
        ),
        # 036_agent_trace_attempts.py
        CheckConstraint("attempts IS NULL OR attempts >= 0", name="ck_agent_trace_attempts"),
        # 037_agent_trace_owners_thinking.py: a call is owned by at most
        # one of a workflow run, an async operation and a reanalysis claim.
        CheckConstraint(
            "num_nonnulls(workflow_run_id, operation_id, reanalysis_claim_id) <= 1",
            name="ck_agent_trace_one_owner",
        ),
        CheckConstraint(
            "thinking_tokens IS NULL OR thinking_tokens >= 0",
            name="ck_agent_trace_thinking_tokens",
        ),
        # 042_agent_trace_rate_limits.py: neither can be negative, and the
        # 429s are among the requests counted in attempts.
        CheckConstraint(
            "rate_limited_requests IS NULL OR rate_limited_requests >= 0",
            name="ck_agent_trace_rate_limited_requests",
        ),
        CheckConstraint(
            "rate_limit_wait_ms IS NULL OR rate_limit_wait_ms >= 0",
            name="ck_agent_trace_rate_limit_wait_ms",
        ),
        CheckConstraint(
            "rate_limited_requests IS NULL OR attempts IS NULL "
            "OR rate_limited_requests <= attempts",
            name="ck_agent_trace_rate_limited_within_attempts",
        ),
        # 004_requirements.py
        Index("idx_agent_traces_tender_time", "tender_id", "created_at"),
        # 041_lookup_and_range_indexes.py (plan item 90): the LLM
        # dashboard's time range over every tender.
        Index("idx_agent_traces_created_at", "created_at"),
    )

    tender_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), nullable=True
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_runs.id", ondelete="SET NULL"), nullable=True
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_tasks.id", ondelete="SET NULL"), nullable=True
    )
    # PR 29: the other two owners (ck_agent_trace_one_owner). An async
    # operation's calls, and the reanalysis job's, were written with no owner.
    operation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("async_operations.id", ondelete="SET NULL"), nullable=True
    )
    reanalysis_claim_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reanalysis_claims.id", ondelete="SET NULL"), nullable=True
    )
    agent_name: Mapped[str] = mapped_column(nullable=False)
    provider: Mapped[str | None] = mapped_column(nullable=True)
    model: Mapped[str | None] = mapped_column(nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(nullable=True)
    input_refs: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    output_refs: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    tool_names: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    # The whole call's wall time, retries and backoff included (PR 28).
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # PR 28: the provider requests that time covered (0 for a cache hit; the
    # repair call counts). NULL on rows written before migration 036, and
    # when a request's count is not known.
    attempts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # The answer's tokens only; thinking tokens are counted beside them.
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # PR 29: tokens the model spent thinking, billed at the output rate and
    # included in estimated_cost_usd. NULL before migration 037.
    thinking_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # PR 34: requests the provider answered 429, and the time the adapter
    # waited on them (included in latency_ms). NULL before migration 042.
    rate_limited_requests: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rate_limit_wait_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    status: Mapped[str] = mapped_column(nullable=False)
    error_code: Mapped[str | None] = mapped_column(nullable=True)
