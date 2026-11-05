"""talent: role_requirements, role_requirement_sources, role_matches,
role_match_evidence

Revision ID: 007_talent
Revises: 006_compliance
Create Date: 2026-08-13 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "007_talent"
down_revision: str | None = "006_compliance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Part C, lines 1368-1411 - transcribed verbatim.
    op.create_table(
        "role_requirements",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tender_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role_name", sa.Text(), nullable=False),
        sa.Column("min_experience_years", sa.Numeric(), nullable=True),
        sa.Column(
            "required_skills",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "required_certs",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "source_chunk_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_chunks.id"),
            nullable=True,
        ),
        sa.Column("content_fingerprint", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "tender_id", "content_fingerprint", name="uq_role_requirement_fingerprint"
        ),
    )

    op.create_table(
        "role_requirement_sources",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "role_requirement_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("role_requirements.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "requirement_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("requirements.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "role_requirement_id", "requirement_id", name="uq_role_requirement_source"
        ),
    )

    op.create_table(
        "role_matches",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "role_requirement_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("role_requirements.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "employee_knowledge_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("score", sa.REAL(), nullable=True),
        sa.Column(
            "strengths",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "gaps",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("human_selected", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "role_requirement_id",
            "employee_knowledge_item_id",
            name="uq_role_match_candidate",
        ),
    )

    op.create_table(
        "role_match_evidence",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "role_match_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("role_matches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "evidence_link_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("evidence_links.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("criterion", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "role_match_id",
            "evidence_link_id",
            "criterion",
            name="uq_role_match_evidence",
        ),
    )


def downgrade() -> None:
    op.drop_table("role_match_evidence")
    op.drop_table("role_matches")
    op.drop_table("role_requirement_sources")
    op.drop_table("role_requirements")
