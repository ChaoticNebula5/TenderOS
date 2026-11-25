"""PR 34: the report's additions for the real Gemini run
(domain/services/llm_report.py), against rows seeded in one fresh
organization, so other tests' rows never count.

- cost per model;
- latency p50 and p95 without the rate-limit waits, over calls whose wait is
  known, with the unknown ones counted;
- the 429s and the time waited on them, beside the retries;
- the largest single call per agent and model, in tokens;
- calls and requests per model per Pacific day (the day Gemini's daily
  quotas reset on), split at midnight Pacific, not UTC.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from tenderos_backend.db.models.agent_trace import AgentTrace
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import get_session
from tenderos_backend.domain.services.llm_report import ReportScope, build_report, render_text

pytestmark = pytest.mark.asyncio

# 2026-11-25 08:00 UTC is midnight Pacific (PST, UTC-8).
_BEFORE_MIDNIGHT_PACIFIC = datetime(2026, 11, 25, 7, 30, tzinfo=UTC)
_AFTER_MIDNIGHT_PACIFIC = datetime(2026, 11, 25, 8, 30, tzinfo=UTC)
_LATER = datetime(2026, 11, 25, 9, 0, tzinfo=UTC)


async def _seed() -> uuid.UUID:
    async with get_session() as session:
        org = Organization(name="LLM Report Rate Limits Test Org")
        session.add(org)
        await session.flush()
        tender = Tender(organization_id=org.id, name="LLM Report Rate Limits Test Tender")
        session.add(tender)
        await session.flush()
        rows = [
            # A call with no 429.
            dict(
                agent_name="extract",
                model="model-a",
                attempts=1,
                latency_ms=1_000,
                rate_limited_requests=0,
                rate_limit_wait_ms=0,
                input_tokens=100,
                output_tokens=50,
                thinking_tokens=10,
                estimated_cost_usd=Decimal("0.010"),
                created_at=_BEFORE_MIDNIGHT_PACIFIC,
            ),
            # Two 429s, 30 s waited, then an answer after 1 s of provider time.
            dict(
                agent_name="extract",
                model="model-a",
                attempts=3,
                latency_ms=31_000,
                rate_limited_requests=2,
                rate_limit_wait_ms=30_000,
                input_tokens=400,
                output_tokens=60,
                thinking_tokens=0,
                estimated_cost_usd=Decimal("0.020"),
                created_at=_AFTER_MIDNIGHT_PACIFIC,
            ),
            # A row from before migration 042: its waits are not known.
            dict(
                agent_name="verify",
                model="model-b",
                attempts=1,
                latency_ms=500,
                rate_limited_requests=None,
                rate_limit_wait_ms=None,
                input_tokens=80,
                output_tokens=5,
                thinking_tokens=None,
                estimated_cost_usd=Decimal("0.005"),
                created_at=_LATER,
            ),
        ]
        for row in rows:
            session.add(
                AgentTrace(tender_id=tender.id, provider="gemini", status="completed", **row)
            )
        await session.commit()
        return org.id


async def _report() -> dict:
    org_id = await _seed()
    async with get_session() as session:
        return await build_report(session, ReportScope(organization_id=org_id))


def _one(rows: list[dict], **match) -> dict:
    [row] = [r for r in rows if all(r[k] == v for k, v in match.items())]
    return row


async def test_cost_per_model():
    report = await _report()

    model_a = _one(report["cost_by_model"], model="model-a")
    assert model_a["provider"] == "gemini"
    assert model_a["calls"] == 2
    assert model_a["cost_usd"] == pytest.approx(0.030)
    assert model_a["input_tokens"] == 500
    assert _one(report["cost_by_model"], model="model-b")["cost_usd"] == pytest.approx(0.005)


async def test_latency_without_the_rate_limit_waits():
    report = await _report()

    extract = _one(report["latency"], agent_name="extract")
    # With the waits: 1,000 and 31,000 ms; without: 1,000 and 1,000.
    assert extract["p95_ms"] == 31_000
    assert extract["p50_no_wait_ms"] == 1_000
    assert extract["p95_no_wait_ms"] == 1_000
    assert extract["waits_unknown"] == 0
    verify = _one(report["latency"], agent_name="verify")
    assert verify["p50_ms"] == 500
    assert verify["p50_no_wait_ms"] is None
    assert verify["waits_unknown"] == 1


async def test_the_429s_and_their_waits_beside_the_retries():
    report = await _report()

    extract = _one(report["timeouts_and_retries"], agent_name="extract")
    assert extract["retried_calls"] == 1
    assert extract["rate_limited_calls"] == 1
    assert extract["rate_limited_requests"] == 2
    assert extract["rate_limit_wait_ms"] == 30_000
    verify = _one(report["timeouts_and_retries"], agent_name="verify")
    assert (verify["rate_limited_calls"], verify["rate_limited_requests"]) == (0, 0)


async def test_the_largest_single_call_per_agent_and_model():
    report = await _report()

    extract = _one(report["largest_calls"], agent_name="extract")
    assert extract["calls_with_tokens"] == 2
    assert extract["max_input_tokens"] == 400
    assert extract["max_output_tokens"] == 60
    assert extract["max_thinking_tokens"] == 10
    # The largest call in total (400 + 60 + 0), not the sum of the maxima.
    assert extract["max_total_tokens"] == 460
    verify = _one(report["largest_calls"], agent_name="verify")
    assert verify["max_thinking_tokens"] is None
    assert verify["max_total_tokens"] == 85


async def test_calls_per_model_per_pacific_day():
    report = await _report()

    days = {(r["pacific_day"], r["model"]): r for r in report["calls_by_model_day"]}
    # 07:30 UTC is still 2026-11-24 in Pacific time; 08:30 UTC is 2026-11-25.
    assert set(days) == {
        ("2026-11-24", "model-a"),
        ("2026-11-25", "model-a"),
        ("2026-11-25", "model-b"),
    }
    before = days[("2026-11-24", "model-a")]
    assert (before["calls"], before["requests"], before["rate_limited_requests"]) == (1, 1, 0)
    after = days[("2026-11-25", "model-a")]
    assert (after["calls"], after["requests"], after["rate_limited_requests"]) == (1, 3, 2)


async def test_the_console_text_shows_the_new_sections():
    text = render_text(await _report())

    assert "Cost per model" in text
    assert "gemini / model-a" in text
    assert "P95 NO WAIT" in text
    assert "429 WAIT S" in text
    assert "30.0" in text
    assert "Largest single call per agent and model (tokens)" in text
    assert "Calls per model per day (Pacific time" in text
    assert "2026-11-24" in text
