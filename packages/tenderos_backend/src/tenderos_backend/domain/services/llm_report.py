"""The LLM measurement report (PR 28, observability phase): cost and latency
of the calls recorded in agent_traces. scripts/llm_report.py is its command
line; the Gemini run's write-up reads cost per tender and latency per agent
from it.

What it reports, for one scope (a run, a tender, an organization, a start
date, or everything):
- cost per organization, tender, run and agent;
- latency p50, p95 and max per agent, provider and model;
- timeouts and retries per agent, provider and model.

Its rules, each also printed in the report's notes:
- latency_ms is each call's whole wall time, retries and backoff included,
  and attempts the provider requests it covered (0 for a cache hit; I4's
  repair call counts).
- A cost is unknown when any call in the group has no recorded cost: rows
  from before the price table, failed calls with no usage, an unpriced
  model, cache hits written before migration 036. The group then shows
  "unknown" with the known part beside it, never 0. The cost-budget gate
  sums the same column, so the two agree on every known cost.
- A cache hit (attempts 0) made no provider request: since PR 28 its cost
  is recorded as 0.0. It is counted beside the latency percentiles, never
  in them.
- Latency p50, p95 and max cover provider calls only (attempts 1 or more):
  cache hits and rows with no attempts (before migration 036) are counted
  beside them, so cached answers do not pull the percentiles towards 0.
- Fake rows (provider 'fake') cost 0 by design, with tokens estimated from
  characters; the report marks them, so a fake 0 is never read as a price.

Read only: it runs SELECTs, and the command line sets its transaction READ
ONLY. Every value it filters on is a bound parameter.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# agents/llm/client.py's LLMTimeoutError, as the agents record it in
# error_code (the exception's class name). Named here, not imported: domain
# services do not depend on the agents layer.
_TIMEOUT_ERROR_CODE = "LLMTimeoutError"

_FROM = "FROM agent_traces AS t LEFT JOIN tenders AS tn ON tn.id = t.tender_id"
_ORG = "COALESCE(t.organization_id, tn.organization_id)"

# One group's cost columns. Unknown means no recorded cost, nothing else: a
# cache hit records 0.0 (it made no request), and a row from before
# migration 036 with no cost stays unknown, as the user decided.
_COST_COLUMNS = """
    count(*) AS calls,
    count(*) FILTER (WHERE t.status = 'failed') AS failed,
    array_agg(DISTINCT COALESCE(t.provider, 'unknown')
              ORDER BY COALESCE(t.provider, 'unknown')) AS providers,
    bool_and(COALESCE(t.provider = 'fake', false)) AS fake,
    COALESCE(sum(t.estimated_cost_usd), 0) AS known_cost_usd,
    count(*) FILTER (WHERE t.estimated_cost_usd IS NULL) AS unknown_cost_calls,
    count(*) FILTER (WHERE t.attempts = 0) AS cache_hits
"""

# Latency samples: provider calls only (PR 28 review). A cache hit (0) and a
# row with no attempts (before migration 036, where a cache hit read as a
# 0 ms call) are counted beside the percentiles, never in them.
_SAMPLE = "t.attempts >= 1"

NOTES = [
    "latency_ms is each call's whole wall time, retries and backoff included; "
    "attempts is the provider requests it covered (0 for a cache hit; the repair call counts).",
    "A cost is unknown when any call in the group has no recorded cost (rows from before the "
    "price table, failed calls with no usage, unpriced models): shown as unknown with the known "
    "part beside it, never as 0.",
    "Cache hits made no provider request: their cost is 0 (rows from before migration 036 "
    "recorded none, so they read unknown).",
    "Latency p50, p95 and max cover provider calls only (attempts 1 or more); cache hits and "
    "calls with unknown attempts are counted beside them, not in them.",
    "Fake rows cost 0 by design and their tokens are estimates (characters / 4): marked (fake).",
    "Rows written before migration 036 have no attempts: counted as attempts unknown.",
]


@dataclass(frozen=True)
class ReportScope:
    """Which calls the report covers; every field set narrows it."""

    workflow_run_id: uuid.UUID | None = None
    tender_id: uuid.UUID | None = None
    organization_id: uuid.UUID | None = None
    since: datetime | None = None


def _where(scope: ReportScope) -> tuple[str, dict[str, Any]]:
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if scope.workflow_run_id is not None:
        clauses.append("t.workflow_run_id = :run_id")
        params["run_id"] = scope.workflow_run_id
    if scope.tender_id is not None:
        clauses.append("t.tender_id = :tender_id")
        params["tender_id"] = scope.tender_id
    if scope.organization_id is not None:
        clauses.append(f"{_ORG} = :org_id")
        params["org_id"] = scope.organization_id
    if scope.since is not None:
        clauses.append("t.created_at >= :since")
        params["since"] = scope.since
    return ("WHERE " + " AND ".join(clauses)) if clauses else "", params


def _plain(value: Any) -> Any:
    """A JSON-ready value: ids as strings, money as float."""
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _cost_row(row: dict[str, Any]) -> dict[str, Any]:
    out = {key: _plain(value) for key, value in row.items()}
    # An empty scope aggregates no rows: bool_and and array_agg give NULL.
    out["fake"] = bool(row["fake"])
    out["providers"] = row["providers"] or []
    out["known_cost_usd"] = float(row["known_cost_usd"])
    out["cost_usd"] = out["known_cost_usd"] if row["unknown_cost_calls"] == 0 else None
    return out


async def _rows(session: AsyncSession, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    result = await session.execute(text(sql), params)
    return [dict(row) for row in result.mappings()]


async def _cost_by(
    session: AsyncSession,
    keys: list[tuple[str, str]],
    where: str,
    params: dict[str, Any],
    top: int,
) -> list[dict[str, Any]]:
    """Cost grouped by `keys` ((sql expression, output name) pairs): at most
    `top` groups, the costliest known first."""
    select_keys = ", ".join(f"{expr} AS {name}" for expr, name in keys)
    group = ", ".join(str(i + 1) for i in range(len(keys)))
    sql = (
        f"SELECT {select_keys}, {_COST_COLUMNS} {_FROM} {where} "
        f"GROUP BY {group} ORDER BY known_cost_usd DESC, calls DESC LIMIT :top"
    )
    return [_cost_row(row) for row in await _rows(session, sql, {**params, "top": top})]


async def build_report(session: AsyncSession, scope: ReportScope, *, top: int = 20) -> dict:
    """The whole report as a JSON-ready dict (render_text prints it)."""
    where, params = _where(scope)

    [totals] = await _rows(session, f"SELECT {_COST_COLUMNS} {_FROM} {where}", params)
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "scope": {
            "workflow_run_id": _plain(scope.workflow_run_id),
            "tender_id": _plain(scope.tender_id),
            "organization_id": _plain(scope.organization_id),
            "since": scope.since.isoformat() if scope.since else None,
        },
        "top": top,
        "notes": NOTES,
        "totals": _cost_row(totals),
    }
    report["cost_by_organization"] = await _cost_by(
        session, [(_ORG, "organization_id")], where, params, top
    )
    report["cost_by_tender"] = await _cost_by(
        session, [("t.tender_id", "tender_id"), (_ORG, "organization_id")], where, params, top
    )
    report["cost_by_run"] = await _cost_by(
        session,
        [("t.workflow_run_id", "workflow_run_id"), ("t.tender_id", "tender_id")],
        where,
        params,
        top,
    )
    report["cost_by_agent"] = await _cost_by(
        session, [("t.agent_name", "agent_name")], where, params, top
    )

    group = (
        "t.agent_name AS agent_name, COALESCE(t.provider, 'unknown') AS provider, "
        "COALESCE(t.model, 'unknown') AS model"
    )
    latency = await _rows(
        session,
        f"""
        SELECT {group},
            count(*) AS calls,
            count(t.latency_ms) FILTER (WHERE {_SAMPLE}) AS provider_calls,
            count(*) FILTER (WHERE t.attempts = 0) AS cache_hits,
            count(*) FILTER (WHERE t.attempts IS NULL) AS attempts_unknown,
            percentile_disc(0.5) WITHIN GROUP (ORDER BY t.latency_ms)
                FILTER (WHERE {_SAMPLE}) AS p50_ms,
            percentile_disc(0.95) WITHIN GROUP (ORDER BY t.latency_ms)
                FILTER (WHERE {_SAMPLE}) AS p95_ms,
            max(t.latency_ms) FILTER (WHERE {_SAMPLE}) AS max_ms
        {_FROM} {where}
        GROUP BY 1, 2, 3
        ORDER BY p95_ms DESC NULLS LAST, 1, 2, 3
        """,
        params,
    )
    report["latency"] = [{k: _plain(v) for k, v in row.items()} for row in latency]

    retries = await _rows(
        session,
        f"""
        SELECT {group},
            count(*) AS calls,
            count(*) FILTER (WHERE t.status = 'failed') AS failed,
            count(*) FILTER (WHERE t.error_code = :timeout_code) AS timeouts,
            count(*) FILTER (WHERE t.attempts > 1) AS retried_calls,
            COALESCE(sum(t.attempts - 1) FILTER (WHERE t.attempts > 1), 0) AS extra_attempts,
            count(*) FILTER (WHERE t.attempts IS NULL) AS attempts_unknown
        {_FROM} {where}
        GROUP BY 1, 2, 3
        ORDER BY timeouts DESC, failed DESC, retried_calls DESC, 1, 2, 3
        """,
        {**params, "timeout_code": _TIMEOUT_ERROR_CODE},
    )
    failures = await _rows(
        session,
        f"""
        SELECT {group}, COALESCE(t.error_code, 'unknown') AS error_code, count(*) AS n
        {_FROM} {where} {"AND" if where else "WHERE"} t.status = 'failed'
        GROUP BY 1, 2, 3, 4
        ORDER BY 1, 2, 3, 4
        """,
        params,
    )
    codes: dict[tuple[str, str, str], dict[str, int]] = {}
    for row in failures:
        key = (row["agent_name"], row["provider"], row["model"])
        codes.setdefault(key, {})[row["error_code"]] = row["n"]
    report["timeouts_and_retries"] = [
        {
            **{k: _plain(v) for k, v in row.items()},
            "error_codes": codes.get((row["agent_name"], row["provider"], row["model"]), {}),
        }
        for row in retries
    ]
    return report


# ---------------------------------------------------------------- the console text


def _usd(value: float) -> str:
    return f"${value:.4f}"


def format_cost(row: dict[str, Any]) -> str:
    """A group's cost for the console: a price, a fake 0 marked as such, or
    unknown with the known part beside it."""
    if row["fake"]:
        return f"{_usd(row['known_cost_usd'])} (fake)"
    if row["cost_usd"] is None:
        return (
            f"unknown ({_usd(row['known_cost_usd'])} known, "
            f"{row['unknown_cost_calls']} of {row['calls']} calls unpriced)"
        )
    return _usd(row["cost_usd"])


def _ms(value: int | None) -> str:
    return "-" if value is None else f"{value:,}"


def _table(headers: list[str], rows: list[list[str]], numeric: set[int]) -> list[str]:
    if not rows:
        return ["  (no calls)"]
    widths = [max(len(headers[i]), *(len(r[i]) for r in rows)) for i in range(len(headers))]

    def line(cells: list[str]) -> str:
        return (
            "  "
            + "  ".join(
                cell.rjust(widths[i]) if i in numeric else cell.ljust(widths[i])
                for i, cell in enumerate(cells)
            ).rstrip()
        )

    return [line(headers), line(["-" * w for w in widths]), *(line(r) for r in rows)]


def _cost_section(
    title: str,
    label: str,
    rows: list[dict[str, Any]],
    name: Callable[[dict[str, Any]], str],
) -> list[str]:
    body = [
        [
            name(r),
            str(r["calls"]),
            str(r["failed"]),
            str(r["cache_hits"]),
            ", ".join(r["providers"]),
            format_cost(r),
        ]
        for r in rows
    ]
    headers = [label, "CALLS", "FAILED", "CACHE HITS", "PROVIDERS", "COST"]
    return [title, *_table(headers, body, numeric={1, 2, 3}), ""]


def render_text(report: dict) -> str:
    """The report for a console: one section per question, plain columns."""
    scope = report["scope"]
    named = [f"{key} {value}" for key, value in scope.items() if value is not None]
    totals = report["totals"]
    out = [
        "LLM report",
        f"Scope: {', '.join(named) if named else 'every recorded call'}",
        f"Generated: {report['generated_at']}; lists show up to {report['top']} groups, "
        "costliest known first",
        "",
        "Notes",
        *(f"  - {note}" for note in report["notes"]),
        "",
        "Totals",
        f"  calls {totals['calls']}, failed {totals['failed']}, cache hits "
        f"{totals['cache_hits']}, providers {', '.join(totals['providers']) or '-'}",
        f"  cost {format_cost(totals) if totals['calls'] else '-'}",
        "",
    ]
    out += _cost_section(
        "Cost per organization",
        "ORGANIZATION",
        report["cost_by_organization"],
        lambda r: r["organization_id"] or "-",
    )
    out += _cost_section(
        "Cost per tender",
        "TENDER",
        report["cost_by_tender"],
        lambda r: r["tender_id"] or "(organization-scoped)",
    )
    out += _cost_section(
        "Cost per run",
        "RUN",
        report["cost_by_run"],
        lambda r: r["workflow_run_id"] or "(no run)",
    )
    out += _cost_section(
        "Cost per agent", "AGENT", report["cost_by_agent"], lambda r: r["agent_name"]
    )

    latency = [
        [
            r["agent_name"],
            # The provider column names the fake already; the cost columns
            # carry the (fake) mark, where a 0 could be misread as a price.
            r["provider"],
            r["model"],
            str(r["provider_calls"]),
            _ms(r["p50_ms"]),
            _ms(r["p95_ms"]),
            _ms(r["max_ms"]),
            str(r["cache_hits"]),
            str(r["attempts_unknown"]),
        ]
        for r in report["latency"]
    ]
    out += [
        "Latency per agent and model (ms, provider calls only)",
        *_table(
            [
                "AGENT",
                "PROVIDER",
                "MODEL",
                "PROVIDER CALLS",
                "P50",
                "P95",
                "MAX",
                "CACHE HITS",
                "ATTEMPTS UNKNOWN",
            ],
            latency,
            numeric={3, 4, 5, 6, 7, 8},
        ),
        "",
    ]

    retries = [
        [
            r["agent_name"],
            r["provider"],
            r["model"],
            str(r["calls"]),
            str(r["failed"]),
            str(r["timeouts"]),
            str(r["retried_calls"]),
            str(r["extra_attempts"]),
            str(r["attempts_unknown"]),
            ", ".join(f"{code} {n}" for code, n in r["error_codes"].items()) or "-",
        ]
        for r in report["timeouts_and_retries"]
    ]
    out += [
        "Timeouts and retries per agent and model",
        *_table(
            [
                "AGENT",
                "PROVIDER",
                "MODEL",
                "CALLS",
                "FAILED",
                "TIMEOUTS",
                "RETRIED",
                "EXTRA ATTEMPTS",
                "ATTEMPTS UNKNOWN",
                "FAILURES",
            ],
            retries,
            numeric={3, 4, 5, 6, 7, 8},
        ),
    ]
    return "\n".join(out) + "\n"
