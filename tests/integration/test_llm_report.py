"""PR 28: scripts/llm_report.py and the report it prints
(domain/services/llm_report.py), against seeded agent_traces rows in one
fresh organization, so other tests' rows never count.

What the report must get right:
- cost per organization, tender, run and agent, where a row with no cost
  (an older gemini row, a failed call) makes the total unknown, never 0, and
  a cache hit (attempts 0, no provider request, cost 0.0) is known;
- fake rows marked as fake, their 0 cost never read as a real price;
- latency p50, p95 and max per agent, provider and model over provider calls
  only (attempts 1 or more), cache hits and unknown attempts counted beside;
- timeouts and retries per agent, provider and model.
"""

import contextlib
import importlib.util
import io
import json
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services.llm_report import ReportScope, build_report, render_text

pytestmark = pytest.mark.asyncio

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "llm_report_cli", REPO_ROOT / "scripts" / "llm_report.py"
)
assert _spec is not None and _spec.loader is not None
llm_report_cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(llm_report_cli)


async def _seed() -> dict[str, uuid.UUID]:
    async with get_session() as session:
        org = Organization(name="LLM Report Test Org")
        session.add(org)
        await session.flush()
        tender = Tender(organization_id=org.id, name="LLM Report Test Tender")
        session.add(tender)
        await session.commit()
        ids = {"org": org.id, "tender": tender.id}

    async with UnitOfWork() as uow:
        live = await uow.workflow.create_run(
            tender_id=ids["tender"], prompt_bundle_version="test", model_config={}
        )
        # A tender has one active run at a time (uq_workflow_runs_one_active_
        # per_tender): the first is finished before the second starts.
        live.status = "completed"
        await uow.session.flush()
        fake = await uow.workflow.create_run(
            tender_id=ids["tender"], prompt_bundle_version="test", model_config={}
        )
        ids["live_run"], ids["fake_run"] = live.id, fake.id
        rows = [
            # (agent, provider, status, error_code, attempts, latency, cost, run)
            ("extract", "gemini", "completed", None, 1, 100, Decimal("0.010"), live.id),
            ("extract", "gemini", "completed", None, None, 200, None, live.id),  # older row
            # A cache hit, as the adapter writes it since PR 28's review.
            ("extract", "gemini", "completed", None, 0, 2, Decimal("0"), live.id),
            ("verify", "gemini", "failed", "LLMTimeoutError", 1, 180_000, None, live.id),
            ("verify", "gemini", "completed", None, 3, 300, Decimal("0.020"), live.id),
            ("extract", "fake", "completed", None, 1, 0, Decimal("0"), fake.id),
        ]
        for agent, provider, status, error_code, attempts, latency, cost, run_id in rows:
            await uow.agent_traces.create(
                agent_name=agent,
                status=status,
                tender_id=ids["tender"],
                workflow_run_id=run_id,
                provider=provider,
                model="gemini-3.5-flash",
                attempts=attempts,
                latency_ms=latency,
                input_tokens=10,
                output_tokens=5,
                estimated_cost_usd=cost,
                error_code=error_code,
            )
        # One organization-scoped call (a resume extraction): no tender, no run.
        await uow.agent_traces.create(
            agent_name="resume",
            status="completed",
            organization_id=ids["org"],
            provider="gemini",
            model="gemini-3.5-flash",
            attempts=1,
            latency_ms=50,
            estimated_cost_usd=Decimal("0.005"),
        )
        await uow.commit()
    return ids


async def _report(**scope) -> dict:
    async with get_session() as session:
        return await build_report(session, ReportScope(**scope))


def _by(rows: list[dict], **match) -> dict:
    [row] = [r for r in rows if all(r[k] == v for k, v in match.items())]
    return row


async def test_a_row_with_no_cost_makes_the_total_unknown_never_zero():
    ids = await _seed()

    report = await _report(organization_id=ids["org"])

    totals = report["totals"]
    assert totals["calls"] == 7
    assert totals["unknown_cost_calls"] == 2  # the older row and the timeout
    assert totals["cache_hits"] == 1
    assert totals["cost_usd"] is None
    assert totals["known_cost_usd"] == pytest.approx(0.035)

    live = _by(report["cost_by_run"], workflow_run_id=str(ids["live_run"]))
    assert live["cost_usd"] is None
    assert live["known_cost_usd"] == pytest.approx(0.030)
    assert live["unknown_cost_calls"] == 2

    org = _by(report["cost_by_organization"], organization_id=str(ids["org"]))
    assert org["cost_usd"] is None


async def test_fake_rows_are_marked_fake():
    ids = await _seed()

    report = await _report(workflow_run_id=ids["fake_run"])

    run = _by(report["cost_by_run"], workflow_run_id=str(ids["fake_run"]))
    assert run["fake"] is True
    assert run["providers"] == ["fake"]
    assert run["cost_usd"] == 0
    text = render_text(report)
    assert "(fake)" in text


async def test_cost_by_tender_agent_and_organization_scoped_calls():
    ids = await _seed()

    report = await _report(organization_id=ids["org"])

    tender = _by(report["cost_by_tender"], tender_id=str(ids["tender"]))
    assert tender["calls"] == 6
    assert tender["known_cost_usd"] == pytest.approx(0.030)
    org_scoped = _by(report["cost_by_tender"], tender_id=None)
    assert org_scoped["calls"] == 1
    assert org_scoped["cost_usd"] == pytest.approx(0.005)
    resume = _by(report["cost_by_agent"], agent_name="resume")
    assert resume["cost_usd"] == pytest.approx(0.005)
    verify = _by(report["cost_by_agent"], agent_name="verify")
    assert verify["failed"] == 1
    assert verify["cost_usd"] is None


async def test_latency_percentiles_cover_provider_calls_only():
    ids = await _seed()

    report = await _report(organization_id=ids["org"])

    extract = _by(report["latency"], agent_name="extract", provider="gemini")
    # One provider call (100 ms). The 2 ms cache hit and the 200 ms row with
    # no attempts (before migration 036) are counted beside, not in.
    assert extract["provider_calls"] == 1
    assert extract["cache_hits"] == 1
    assert extract["attempts_unknown"] == 1
    assert extract["p50_ms"] == 100
    assert extract["p95_ms"] == 100
    assert extract["max_ms"] == 100
    verify = _by(report["latency"], agent_name="verify", provider="gemini")
    assert verify["provider_calls"] == 2
    assert verify["max_ms"] == 180_000


async def test_cache_hits_do_not_pull_the_percentiles_towards_zero():
    async with get_session() as session:
        org = Organization(name="LLM Report Cache Test Org")
        session.add(org)
        await session.commit()
        org_id = org.id
    async with UnitOfWork() as uow:
        # Three provider calls and nine cache hits for one agent: counted
        # in, the cache hits would put p50 at 1 ms instead of 600.
        for attempts, latency, cost in [(1, 400, "0.01"), (1, 600, "0.01"), (2, 900, "0.02")] + [
            (0, 1, "0")
        ] * 9:
            await uow.agent_traces.create(
                agent_name="cached_agent",
                status="completed",
                organization_id=org_id,
                provider="gemini",
                model="gemini-3.5-flash",
                attempts=attempts,
                latency_ms=latency,
                estimated_cost_usd=Decimal(cost),
            )
        await uow.commit()

    report = await _report(organization_id=org_id)

    [row] = report["latency"]
    assert row["provider_calls"] == 3
    assert row["cache_hits"] == 9
    assert row["p50_ms"] == 600
    assert row["p95_ms"] == 900
    assert row["max_ms"] == 900
    # The cache hits cost nothing and are known: the total is a price.
    assert report["totals"]["cost_usd"] == pytest.approx(0.04)
    assert report["totals"]["unknown_cost_calls"] == 0


async def test_timeouts_and_retries_per_agent():
    ids = await _seed()

    report = await _report(organization_id=ids["org"])

    verify = _by(report["timeouts_and_retries"], agent_name="verify", provider="gemini")
    assert verify["timeouts"] == 1
    assert verify["failed"] == 1
    assert verify["retried_calls"] == 1
    assert verify["extra_attempts"] == 2
    assert verify["error_codes"] == {"LLMTimeoutError": 1}
    extract = _by(report["timeouts_and_retries"], agent_name="extract", provider="gemini")
    assert extract["attempts_unknown"] == 1
    assert extract["timeouts"] == 0


async def test_the_console_text_says_unknown_where_the_cost_is_unknown():
    ids = await _seed()

    text = render_text(await _report(workflow_run_id=ids["live_run"]))

    # The run's row in "Cost per run": unknown, with the known part beside it.
    assert any(
        str(ids["live_run"]) in line and "unknown" in line and "$0.0300" in line
        for line in text.splitlines()
    )
    assert "$0.0000" not in text.split("Cost per run")[1].split("\n\n")[0]
    assert "latency_ms is each call's whole wall time" in text


async def test_the_command_prints_json_for_one_run(capsys):
    ids = await _seed()

    code = await llm_report_cli.run(["--run", str(ids["live_run"]), "--json"])

    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["scope"]["workflow_run_id"] == str(ids["live_run"])
    assert report["totals"]["calls"] == 5


async def test_the_command_prints_text_by_default(capsys):
    ids = await _seed()

    code = await llm_report_cli.run(["--tender", str(ids["tender"])])

    assert code == 0
    out = capsys.readouterr().out
    assert "Cost per run" in out
    assert "Latency per agent and model" in out


async def test_the_command_refuses_a_bad_id_with_exit_1():
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        code = await llm_report_cli.run(["--run", "not-a-uuid"])

    assert code == 1
    assert "llm_report:" in stderr.getvalue()
