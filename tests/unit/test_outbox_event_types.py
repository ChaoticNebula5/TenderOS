"""Observability PR 32 (change 2): ALL_EVENT_TYPES is every outbox event
type, so the metrics_exporter's dead-letter series exist at 0 before the
first dead letter of each type (DeadLetter fires on a rise from the hour's
minimum, which a series born at 1 never shows).

The list is gathered from the module's EVENT_* names; this guard keeps every
outbox write using one of them, so no event type can be written that the
list does not hold.
"""

import ast
from pathlib import Path

from tenderos_backend.domain.events import outbox_events
from tenderos_backend.domain.events.outbox_events import ALL_EVENT_TYPES

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCES = [
    REPO_ROOT / "packages/tenderos_backend/src",
    REPO_ROOT / "apps/api/src",
    REPO_ROOT / "apps/worker/src",
]
# Files where event_type= passes a value through rather than naming a type:
# the repository's own create(), and the re-drive command's log field.
PASS_THROUGH = {
    "tenderos_backend/db/repositories/outbox.py",
    "tenderos_backend/domain/services/outbox_redrive.py",
}


def test_all_event_types_is_every_event_constant():
    constants = {value for name, value in vars(outbox_events).items() if name.startswith("EVENT_")}
    assert set(ALL_EVENT_TYPES) == constants
    assert list(ALL_EVENT_TYPES) == sorted(ALL_EVENT_TYPES)
    assert len(ALL_EVENT_TYPES) == len(constants) == 17


def _event_type_arguments(path: Path) -> list[tuple[int, ast.expr]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        (node.lineno, keyword.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg == "event_type"
    ]


def test_every_outbox_write_names_an_event_constant():
    found = 0
    problems = []
    for root in SOURCES:
        for path in root.rglob("*.py"):
            relative = path.relative_to(root).as_posix()
            if relative in PASS_THROUGH:
                continue
            for line, value in _event_type_arguments(path):
                found += 1
                name = (
                    value.attr if isinstance(value, ast.Attribute) else getattr(value, "id", None)
                )
                if not (isinstance(name, str) and name.startswith("EVENT_")):
                    problems.append(f"{relative}:{line}: {ast.unparse(value)}")
    assert found > 10  # the scan found the writes it guards
    assert problems == []
