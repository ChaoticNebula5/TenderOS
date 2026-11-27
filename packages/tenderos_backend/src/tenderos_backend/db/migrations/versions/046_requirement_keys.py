"""requirement_keys: every extraction key that finds a requirement

Plan item 126 (user decisions 2026-11-27, Q3 a).

Extraction finds an existing requirement by its key, sha256(document | chunk
| normalized text). Until now the key was a column, requirements.
content_fingerprint, so a requirement answered to one key only. A manual
merge or split must answer to several: the merged row to each original's
key, the split's first part to the original's. Without that, the next
extraction found no row for an original's text and made the original again.
Since plan item 125 a requirement is also found by an overlap copy, whose
key names the other chunk.

One row per key. The key is the primary key, so it finds one requirement;
requirement_id is indexed, since merge and split move and copy a
requirement's keys. The foreign key cascades: a deleted requirement's keys go
with it (a merge moves them to the survivor first).

Invariant: every requirement's content_fingerprint is in this table, split
parts' included (their fingerprint names the tender, not a document, so no
extraction ever matches it). requirements.content_fingerprint stays, for its
unique constraint and history; extraction no longer reads it.

Upgrade creates the table and its index and backfills:
- every requirement's content_fingerprint;
- for each plan item 125 overlap citation (a citation other than the
  primary), the key that copy would have had: sha256(the citation chunk's
  document | the chunk | the requirement's normalized text). Exact only for a
  row whose text was not edited after the match, since an edit changes
  normalized_text; for an edited row the key names text no extraction
  produced, and matches nothing. Accepted: there is no production data.
A key already present is left with the requirement it names.

Also requirement_code_counters (the user's review of PR 39): a display code's
number is the bid's next for its prefix ("ELIG-004"), from a counter per bid
and prefix, never a count of the category's rows. A count drops when a merge
deletes a row, and the next code then repeats one in use; (tender_id,
display_code) is not unique, so nothing refused it. The backfill sets each
counter to the highest number in use for that prefix, rejected rows included.
A code that is not PREFIX-NNN counts for nothing.

Downgrade drops both tables; content_fingerprint was never changed.

Revision ID: 046_requirement_keys
Revises: 045_requirement_citations
Create Date: 2026-11-27 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "046_requirement_keys"  # alembic_version holds 32 characters
down_revision: str | None = "045_requirement_citations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "requirement_keys",
        sa.Column("content_fingerprint", sa.Text(), nullable=False),
        sa.Column("requirement_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["requirement_id"],
            ["requirements.id"],
            name="requirement_keys_requirement_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("content_fingerprint", name="requirement_keys_pkey"),
    )
    op.create_index("idx_requirement_keys_requirement", "requirement_keys", ["requirement_id"])
    op.execute(
        "INSERT INTO requirement_keys (content_fingerprint, requirement_id) "
        "SELECT content_fingerprint, id FROM requirements"
    )
    # The same formula as requirement_service._fingerprint: the parts joined
    # by "|", UTF-8, SHA-256 as lowercase hex; a uuid's text form is
    # Python's str(uuid).
    op.execute(
        "INSERT INTO requirement_keys (content_fingerprint, requirement_id) "
        "SELECT encode(sha256(convert_to("
        "  chunk.document_id::text || '|' || citation.chunk_id::text || '|' "
        "  || requirement.normalized_text, 'UTF8')), 'hex'), requirement.id "
        "FROM requirement_citations AS citation "
        "JOIN requirements AS requirement ON requirement.id = citation.requirement_id "
        "JOIN document_chunks AS chunk ON chunk.id = citation.chunk_id "
        "WHERE citation.chunk_id <> requirement.source_chunk_id "
        "ON CONFLICT (content_fingerprint) DO NOTHING"
    )

    op.create_table(
        "requirement_code_counters",
        sa.Column("tender_id", UUID(as_uuid=True), nullable=False),
        sa.Column("prefix", sa.Text(), nullable=False),
        sa.Column("last_number", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tender_id"],
            ["tenders.id"],
            name="requirement_code_counters_tender_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tender_id", "prefix", name="requirement_code_counters_pkey"),
    )
    op.execute(
        "INSERT INTO requirement_code_counters (tender_id, prefix, last_number) "
        "SELECT tender_id, substring(display_code FROM '^([A-Z]+)-[0-9]+$'), "
        "max(substring(display_code FROM '^[A-Z]+-([0-9]+)$')::integer) "
        "FROM requirements WHERE display_code ~ '^[A-Z]+-[0-9]+$' "
        "GROUP BY tender_id, substring(display_code FROM '^([A-Z]+)-[0-9]+$')"
    )


def downgrade() -> None:
    op.drop_table("requirement_code_counters")
    op.drop_index("idx_requirement_keys_requirement", table_name="requirement_keys")
    op.drop_table("requirement_keys")
