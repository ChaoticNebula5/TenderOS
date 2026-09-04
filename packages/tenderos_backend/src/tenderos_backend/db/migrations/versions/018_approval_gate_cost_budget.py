"""approval_gates.gate_type: add cost_budget (I5's gate, built for real)

Revision ID: 018_approval_gate_cost_budget
Revises: 017_proposal_claim_kind
Create Date: 2026-09-03 00:00:00

"""

from collections.abc import Sequence

from alembic import op

revision: str = "018_approval_gate_cost_budget"
down_revision: str | None = "017_proposal_claim_kind"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_TYPES = (
    "requirement_review",
    "compliance_review",
    "resource_selection",
    "commercial_approval",
    "finding_resolution",
    "final_readiness",
)
_NEW_TYPES = (*_OLD_TYPES, "cost_budget")


def upgrade() -> None:
    op.drop_constraint("ck_approval_gate_type", "approval_gates", type_="check")
    op.create_check_constraint(
        "ck_approval_gate_type",
        "approval_gates",
        "gate_type IN (" + ",".join(f"'{t}'" for t in _NEW_TYPES) + ")",
    )


def downgrade() -> None:
    op.drop_constraint("ck_approval_gate_type", "approval_gates", type_="check")
    op.create_check_constraint(
        "ck_approval_gate_type",
        "approval_gates",
        "gate_type IN (" + ",".join(f"'{t}'" for t in _OLD_TYPES) + ")",
    )
