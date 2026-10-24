"""The migration history has exactly one head.

Two branches that each add a revision on top of the same parent (plan item
34 option 3's 021 and batch 3's reanalysis guard both started from 020)
merge into a history with two heads, and `alembic upgrade head` then fails.
The rule (phase 4 plan, item 34 option 3) is that whichever merges second
renumbers onto the first; this test is what makes forgetting it fail by
name, with no database, instead of as an upgrade error later.
"""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_the_migration_history_has_exactly_one_head():
    script = ScriptDirectory.from_config(Config(str(REPO_ROOT / "alembic.ini")))

    heads = script.get_heads()

    assert len(heads) == 1, f"expected one Alembic head, found {sorted(heads)}"
