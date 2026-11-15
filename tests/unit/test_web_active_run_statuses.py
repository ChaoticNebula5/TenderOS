"""Plan item 78: the web app's copy of the active run statuses cannot drift
from the backend's.

The web app decides which runs it still follows (the Documents page's run,
the gate screens' streams) from ACTIVE_RUN_STATUSES in
apps/web/src/status/runStatuses.ts. TypeScript cannot import the Python
tuple, so that file holds a copy. Item 87 was this kind of quiet mismatch
on the backend (a hand-written list missing "changes_requested"); this test
reads the web copy's one array literal and fails CI's test job if it is not
exactly db/models/workflow.py's ACTIVE_RUN_STATUSES.
"""

import re
from pathlib import Path

from tenderos_backend.db.models.workflow import ACTIVE_RUN_STATUSES

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WEB_COPY = REPO_ROOT / "apps" / "web" / "src" / "status" / "runStatuses.ts"
_DECLARATION = re.compile(r"export const ACTIVE_RUN_STATUSES = \[([^\]]*)\] as const;")


def _web_active_run_statuses() -> list[str]:
    """The quoted values of the web copy's one declaration, on one line or
    wrapped over several (prettier may wrap it). Fails, rather than reading
    nothing, if the declaration is missing, renamed, repeated, or holds
    anything but double-quoted strings."""
    source = WEB_COPY.read_text(encoding="utf-8")
    declarations = _DECLARATION.findall(source)
    assert len(declarations) == 1, (
        f"{WEB_COPY.name}: expected exactly one "
        "`export const ACTIVE_RUN_STATUSES = [...] as const;` (one line or wrapped), "
        f"found {len(declarations)}"
    )
    values = re.findall(r'"([^"]*)"', declarations[0])
    leftover = re.sub(r'"[^"]*"', "", declarations[0]).replace(",", "").strip()
    assert leftover == "", f"{WEB_COPY.name}: unexpected text in the array: {leftover!r}"
    return values


def test_the_web_copy_of_active_run_statuses_matches_the_backend():
    web = _web_active_run_statuses()
    assert len(web) == len(set(web)), f"{WEB_COPY.name} repeats a status: {web}"
    assert set(web) == set(ACTIVE_RUN_STATUSES), (
        f"{WEB_COPY.name} has {sorted(web)}, the backend has {sorted(ACTIVE_RUN_STATUSES)}"
    )
