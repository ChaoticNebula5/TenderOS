"""Every advisory-lock class in the backend is distinct (phase 4 plan item 49).

Two features using one class would share a key space: a tender's projection could
block on, or block, a refresh-token family that happens to hash to the same
second key, with nothing in the code to explain it. Reads the source, so a new
lock is covered without anyone remembering to add it here.
"""

import re
from pathlib import Path

from tenderos_backend.db.repositories.graph_projection import _PROJECTION_LOCK_CLASS
from tenderos_backend.db.repositories.refresh_tokens import _FAMILY_LOCK_CLASS

REPO_ROOT = Path(__file__).resolve().parents[2]
_CLASS_ASSIGNMENT = re.compile(r"^(_\w*LOCK_CLASS)\s*=\s*(\d+)\s*$", re.MULTILINE)


def _lock_classes() -> list[tuple[str, str, int]]:
    found = []
    for root in ("packages", "apps", "scripts"):
        for path in (REPO_ROOT / root).rglob("*.py"):
            if ".venv" in path.parts or "node_modules" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            found += [(path.name, name, int(value)) for name, value in _CLASS_ASSIGNMENT.findall(text)]
    return found


def test_the_two_known_classes_are_what_they_were_chosen_to_be():
    assert _FAMILY_LOCK_CLASS == 3401
    assert _PROJECTION_LOCK_CLASS == 3402


def test_every_advisory_lock_class_in_the_repo_is_distinct():
    classes = _lock_classes()
    values = [value for _file, _name, value in classes]

    assert len(classes) >= 2, f"the search found too little to mean anything: {classes}"
    assert len(values) == len(set(values)), f"two locks share a class: {classes}"


def test_every_advisory_lock_call_uses_the_two_key_form_with_a_class():
    # A one-key call lives in another key space, so a class constant would not
    # protect it; and an inline number would escape the search above.
    calls = []
    for root in ("packages", "apps", "scripts"):
        for path in (REPO_ROOT / root).rglob("*.py"):
            if ".venv" in path.parts or "node_modules" in path.parts:
                continue
            calls += re.findall(r"pg_(?:try_)?advisory\w*\(([^)]*\)?)", path.read_text(encoding="utf-8"))

    assert calls, "the search found no advisory lock calls"
    assert all(call.startswith(":lock_class,") for call in calls), calls
