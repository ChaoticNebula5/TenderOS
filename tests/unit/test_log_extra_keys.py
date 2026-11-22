"""Observability PR 31 (F): no `extra=` key that logging itself reserves.

`Logger.makeRecord` raises KeyError inside the logging call when an `extra`
key is one a LogRecord already has (`exc_text`, `args`, `name`, ...), or
`message` or `asctime`. PR 30's record factory adds `log_context` to every
record, so that name is taken too. The error happens before any formatter
runs, so PR 30's `extra_` prefix (which renames keys that clash with the
JSON output's own keys) does not help: the line is lost and the caller gets
an exception instead. Observed in item 117's probe (2026-11-22), where an
`exc_text` extra turned a raising read into a KeyError.

This scans every `extra=` dict literal in the backend, api and worker
sources. The reserved set is built at test time from a real LogRecord, so it
follows the running Python's own attributes.
"""

import ast
import logging
from pathlib import Path

from tenderos_backend.core.log_context import RECORD_ATTRIBUTE

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOTS = (
    REPO_ROOT / "packages/tenderos_backend/src",
    REPO_ROOT / "apps/api/src",
    REPO_ROOT / "apps/worker/src",
)


def reserved_extra_keys() -> set[str]:
    record = logging.LogRecord("guard", logging.INFO, __file__, 1, "message", None, None)
    return set(vars(record)) | {"message", "asctime", RECORD_ATTRIBUTE}


def extra_literal_keys() -> list[tuple[str, int, str]]:
    """(file, line, key) for each string key of each `extra={...}` literal."""
    found = []
    for root in SOURCE_ROOTS:
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                for keyword in node.keywords:
                    if keyword.arg == "extra" and isinstance(keyword.value, ast.Dict):
                        for key in keyword.value.keys:
                            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                                relative = path.relative_to(REPO_ROOT).as_posix()
                                found.append((relative, key.lineno, key.value))
    return found


def test_the_reserved_set_is_logging_s_own():
    reserved = reserved_extra_keys()
    # The names makeRecord refuses, checked on the real thing: each raises.
    for key in ("exc_text", "args", "name", "message", "asctime"):
        assert key in reserved, key
        try:
            logging.getLogger("guard").makeRecord(
                "guard", logging.INFO, __file__, 1, "m", None, None, extra={key: 1}
            )
        except KeyError:
            continue
        raise AssertionError(f"makeRecord accepted {key!r}; the guard's premise is wrong")
    assert RECORD_ATTRIBUTE in reserved


def test_no_extra_literal_uses_a_key_logging_reserves():
    found = extra_literal_keys()
    # Not vacuous: the two places that log with extras today (PR 30's
    # stale-projection line, PR 31's gate refusal line) are both seen.
    keys = {key for _, _, key in found}
    assert {"stale_reason", "readiness_check"} <= keys, sorted(keys)
    reserved = reserved_extra_keys()
    clashes = [f"{file}:{line}: extra key {key!r}" for file, line, key in found if key in reserved]
    assert not clashes, "keys logging reserves (makeRecord raises KeyError):\n" + "\n".join(clashes)
