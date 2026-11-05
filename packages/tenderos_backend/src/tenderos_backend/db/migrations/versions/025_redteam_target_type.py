"""redteam_findings: relabel graph findings to the table their target_id is in

Phase 4 plan item 35 (decision 0072). check_graph_contradictions took
target_id from the row's own node and target_type from a fixed per-category
map, so the two disagreed: deselected_fact_in_use said `evidence` while its id
was a compliance entry or a proposal claim, and removed_employee_committed
said `proposal_claim` while its id could be a role requirement.

The fingerprint hashes target_type, so the label and the fingerprint change
together, in one statement. Relabelling alone would make the next detection
insert a second row beside this one, and that row would not carry a human's
resolution.

Row ids, resolutions and timestamps stay: workflow state
(open_critical_finding_ids) and readiness blockers hold finding ids.

A row whose target_id is in none of the tables is left as it is and counted
in the output. A removed_employee_committed row whose id is a proposal claim
already has the right label, so it is left too.

No collision handling, on purpose. A collision needs a row that already has
the new label, and only the fixed code writes those; the compose `migrate`
service runs and exits before any app service starts. If the fixed code did
somehow run first, uq_redteam_finding_fingerprint fails the statement and the
whole migration rolls back with every row unchanged. Merging would delete a
row whose id workflow state may still hold.

Downgrade: the old code wrote one fixed label per category, so the reverse is
deterministic. Every row in the two categories goes back to that label, with
the fingerprint the old code would have written.

The fingerprint formula is written out here rather than imported, so this
migration stays what it was if the app's formula ever changes.
tests/integration/test_migration_025_redteam_target_type.py pins it to today's
finding_fingerprint, with target_id NULL rendered 'None' as str(None) is there.

Revision ID: 025_redteam_target_type
Revises: 024_workflow_run_current_job
Create Date: 2026-11-05 00:00:00

"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "025_redteam_target_type"
down_revision: str | None = "024_workflow_run_current_job"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Alembic's own logger: alembic.ini sends it to the console at INFO, so this
# reaches `alembic upgrade`'s output, and the migrate container's log.
log = logging.getLogger("alembic.runtime.migration")

_DESELECTED = "deselected_fact_in_use"
_REMOVED = "removed_employee_committed"
_OLD_DESELECTED_LABEL = "evidence"
_OLD_REMOVED_LABEL = "proposal_claim"


def _set_label(new_label: str, where: str) -> None:
    op.execute(
        f"""
        UPDATE redteam_findings f
        SET target_type = '{new_label}',
            content_fingerprint = encode(sha256(convert_to(
                f.tender_id::text || '|' || '{new_label}' || '|'
                || COALESCE(f.target_id::text, 'None') || '|'
                || f.finding_category || '|' || f.violated_condition,
                'UTF8')), 'hex')
        WHERE {where}
        """
    )


def _relabel_if_in(category: str, new_label: str, table: str) -> None:
    _set_label(
        new_label,
        f"f.finding_category = '{category}' "
        f"AND EXISTS (SELECT 1 FROM {table} t WHERE t.id = f.target_id)",
    )


def _log_counts() -> None:
    # `alembic upgrade --sql` has no database to count in.
    if op.get_context().as_sql:
        return
    relabelled, unmatched = (
        op.get_bind()
        .execute(
            sa.text(
                f"""
                SELECT
                  count(*) FILTER (WHERE
                    (finding_category = '{_DESELECTED}'
                     AND target_type <> '{_OLD_DESELECTED_LABEL}')
                    OR (finding_category = '{_REMOVED}'
                        AND target_type <> '{_OLD_REMOVED_LABEL}')),
                  count(*) FILTER (WHERE
                    (finding_category = '{_DESELECTED}'
                     AND target_type = '{_OLD_DESELECTED_LABEL}')
                    OR (finding_category = '{_REMOVED}'
                        AND target_type = '{_OLD_REMOVED_LABEL}'
                        AND NOT EXISTS
                          (SELECT 1 FROM proposal_claims c WHERE c.id = f.target_id)))
                FROM redteam_findings f
                """
            )
        )
        .one()
    )
    log.info(
        "025: %d red-team finding(s) relabelled; %d left as they were, target in no table",
        relabelled,
        unmatched,
    )


def upgrade() -> None:
    _relabel_if_in(_DESELECTED, "compliance_entry", "compliance_entries")
    _relabel_if_in(_DESELECTED, "proposal_claim", "proposal_claims")
    _relabel_if_in(_REMOVED, "role_requirement", "role_requirements")
    _log_counts()


def downgrade() -> None:
    for category, old_label in (
        (_DESELECTED, _OLD_DESELECTED_LABEL),
        (_REMOVED, _OLD_REMOVED_LABEL),
    ):
        _set_label(
            old_label, f"f.finding_category = '{category}' AND f.target_type <> '{old_label}'"
        )
