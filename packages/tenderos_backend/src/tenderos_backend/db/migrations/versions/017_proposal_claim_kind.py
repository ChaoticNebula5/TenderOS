"""proposal_claims.claim_kind: current vs. commitment, orthogonal to is_factual

Revision ID: 017_proposal_claim_kind
Revises: 016_workflow_run_retry_count
Create Date: 2026-09-03 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "017_proposal_claim_kind"
down_revision: str | None = "016_workflow_run_retry_count"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # KNOWN_LIMITATIONS.md's [B9/B16] entry: is_factual answers "is this
    # specific and checkable" - it says nothing about WHEN the claim is
    # checkable against (something the company already has/has done, vs.
    # a promise for this specific contract, e.g. "will support SSO"). Both
    # are equally is_factual=true and equally get held to
    # enforce_claim_grounding's existing unsupported/grounded rule -
    # claim_kind does not change that function at all, it only tags which
    # bucket a citation-correctness measurement should count a claim into,
    # so a promise that can never have backward-looking evidence stops
    # permanently dragging that metric's denominator while staying exactly
    # as visible as it is today (still is_factual=true, still surfaces as
    # "unsupported" if it has no evidence - never silently exempted like a
    # narrative claim).
    op.add_column(
        "proposal_claims",
        sa.Column("claim_kind", sa.Text(), nullable=False, server_default="current"),
    )
    op.create_check_constraint(
        "ck_proposal_claim_kind",
        "proposal_claims",
        "claim_kind IN ('current','commitment')",
    )
    # Backfill: every claim drafted before this column existed was drafted
    # under a prompt with no concept of "commitment" at all - there is no
    # reliable way to retroactively tell which historical claims would have
    # been tagged 'commitment' without re-running the LLM against text that
    # may since have changed. 'current' (the server_default above already
    # applies it) is the honest default: it does not misclassify anything
    # as a commitment, it just leaves old rows exactly as measurable as
    # they always were, which is a correct, named limitation of this
    # backfill rather than a silent one.


def downgrade() -> None:
    op.drop_constraint("ck_proposal_claim_kind", "proposal_claims", type_="check")
    op.drop_column("proposal_claims", "claim_kind")
