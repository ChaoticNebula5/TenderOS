"""PR 28: every place the backend writes an agent_traces row passes the
call's measurements through trace_measurements(...).

A failed call's provider, latency and attempts reach its row only because
the agent hands the LLMError to trace_measurements; a call site that spelt
the fields out itself would go back to writing None for them on failure, and
nothing else would notice. So this scans the backend package's source (not
tests, which write rows directly on purpose) for `agent_traces.create(`
calls and requires a `**trace_measurements(...)` argument in each.
"""

import ast
from pathlib import Path

_PACKAGE = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "tenderos_backend"
    / "src"
    / "tenderos_backend"
)


def _trace_create_calls() -> list[tuple[str, int, ast.Call]]:
    found = []
    for path in sorted(_PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "create"
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "agent_traces"
            ):
                found.append((str(path.relative_to(_PACKAGE)), node.lineno, node))
    return found


def _passes_trace_measurements(call: ast.Call) -> bool:
    return any(
        kw.arg is None
        and isinstance(kw.value, ast.Call)
        and isinstance(kw.value.func, ast.Name)
        and kw.value.func.id == "trace_measurements"
        for kw in call.keywords
    )


def test_the_scan_finds_every_agent_call_site():
    # 10 today, one in each agent module's _write_trace. A lower number means
    # the scan stopped seeing them (a rename), not that they went away.
    assert len(_trace_create_calls()) >= 10


def test_every_trace_row_takes_the_calls_measurements():
    missing = [
        f"{path}:{line}"
        for path, line, call in _trace_create_calls()
        if not _passes_trace_measurements(call)
    ]

    assert not missing, (
        "agent_traces.create(...) without **trace_measurements(outcome): " + ", ".join(missing)
    )


def test_no_call_site_also_spells_out_a_measured_field():
    measured = {
        "provider",
        "latency_ms",
        "attempts",
        "input_tokens",
        "output_tokens",
        "estimated_cost_usd",
    }
    doubled = [
        f"{path}:{line} {kw.arg}"
        for path, line, call in _trace_create_calls()
        for kw in call.keywords
        if kw.arg in measured
    ]

    assert not doubled, "set by trace_measurements, not by hand: " + ", ".join(doubled)
