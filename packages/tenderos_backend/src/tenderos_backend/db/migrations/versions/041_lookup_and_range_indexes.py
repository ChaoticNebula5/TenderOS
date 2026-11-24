"""Indexes for three paths measured to scan whole tables that grow (plan item 90)

PR 33, the observability phase's fixes. Measured with EXPLAIN ANALYZE on two
seeded scratch databases (one busy organization's year, and ten of them:
pr33-plan.md and the decision record), each index only where the plan showed
a scan that grows with the data on a path that runs often:

- The knowledge reach lookup (KnowledgeRepository.list_tender_ids_projecting,
  plan items 86, 88 and 95): every graph-relevant knowledge edit, and every
  changed row of an employee-master import, unions four lookups by
  knowledge item. Each table's existing unique index leads with its other
  column (tender_id, role_requirement_id, proposal_claim_id), so each branch
  was a sequential scan:
  evidence_links (knowledge_item_id), tender_knowledge_selections
  (knowledge_item_id), role_matches (employee_knowledge_item_id),
  proposal_claim_employees (employee_knowledge_item_id).
- The metrics exporter's runs-ended-by-the-system count, every 15 s
  (migration 039's observability.runs_ended_by_system view): a partial index
  on audit_events holding only the two actions the view selects. Its
  predicate is the view's, so the planner can match it.
- The LLM dashboard's time range over every tender (migration 040's
  observability.llm_calls): agent_traces (created_at). The existing
  (tender_id, created_at) index serves one tender's range only.

Plain CREATE INDEX inside the migration's transaction: there is no
production data yet, and migrate runs before the app starts. On a host with
real data, an index on a large table belongs in its own non-transactional
step with CREATE INDEX CONCURRENTLY (as migration 026 did), so it does not
block writes while it builds (plan note, 2026-11-23).

The test database is built from the models, which declare the same indexes
(test_schema_diff.py compares the two).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "041_lookup_and_range_indexes"  # alembic_version holds 32 characters
down_revision: str | None = "040_observability_grafana_views"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# name -> (table, column)
_LOOKUPS = {
    "idx_evidence_links_knowledge_item": ("evidence_links", "knowledge_item_id"),
    "idx_tender_knowledge_selections_item": ("tender_knowledge_selections", "knowledge_item_id"),
    "idx_role_matches_employee_item": ("role_matches", "employee_knowledge_item_id"),
    "idx_proposal_claim_employees_item": (
        "proposal_claim_employees",
        "employee_knowledge_item_id",
    ),
    "idx_agent_traces_created_at": ("agent_traces", "created_at"),
}
_RUN_ENDINGS_PREDICATE = "action IN ('workflow_run_reaped', 'workflow_run_timed_out')"


def upgrade() -> None:
    for name, (table, column) in _LOOKUPS.items():
        op.create_index(name, table, [column])
    op.create_index(
        "idx_audit_run_endings",
        "audit_events",
        ["action"],
        postgresql_where=_RUN_ENDINGS_PREDICATE,
    )


def downgrade() -> None:
    op.drop_index("idx_audit_run_endings", table_name="audit_events")
    for name, (table, _column) in reversed(_LOOKUPS.items()):
        op.drop_index(name, table_name=table)
