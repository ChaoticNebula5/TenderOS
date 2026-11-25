"""PR 28: the agent_traces row one LLM call writes, through a real agent
(requirements/verify.py) and the real repository.

- a completed call records its provider, latency, attempts and tokens;
- a failed call records the provider, latency and attempts its LLMError
  carries, with error_code the exception's class, so an LLM timeout is its
  own value (LLMTimeoutError);
- attempts can never be negative (ck_agent_trace_attempts, migration 036);
- PR 34: the 429s and the time waited on them are recorded, a daily quota
  is its own error code, and migration 042's three checks hold.
"""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from tenderos_backend.agents.llm.client import (
    CallMeasurement,
    DailyQuotaExhaustedError,
    LLMError,
    LLMTimeoutError,
    ProviderRequestRejectedError,
    RateLimitedError,
)
from tenderos_backend.agents.llm.fake_client import FakeLLMClient
from tenderos_backend.agents.requirements.verify import verify_requirement
from tenderos_backend.agents.schemas.requirements import VerificationOutput
from tenderos_backend.db.models.agent_trace import AgentTrace
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork

pytestmark = pytest.mark.asyncio

_MODEL = "gemini-3.5-flash"


async def _tender() -> uuid.UUID:
    async with get_session() as session:
        org = Organization(name="LLM Trace Rows Test Org")
        session.add(org)
        await session.flush()
        tender = Tender(organization_id=org.id, name="LLM Trace Rows Test Tender")
        session.add(tender)
        await session.commit()
        return tender.id


async def _rows(tender_id: uuid.UUID) -> list[AgentTrace]:
    async with get_session() as session:
        result = await session.execute(select(AgentTrace).where(AgentTrace.tender_id == tender_id))
        return list(result.scalars())


class _Failing:
    """An LLMClient whose every call raises `error`, as an adapter would,
    with its measurement attached."""

    def __init__(self, error: LLMError) -> None:
        self.error = error

    async def complete_structured(self, **kwargs):
        raise self.error


async def _verify(client, tender_id: uuid.UUID) -> VerificationOutput:
    return await verify_requirement(
        client,
        _MODEL,
        requirement_text="Bidders must hold a tax clearance certificate.",
        source_chunk_text="Bidders must hold a valid tax clearance certificate.",
        tender_id=tender_id,
    )


async def test_a_completed_call_records_its_measurements():
    tender_id = await _tender()
    fake = FakeLLMClient()
    fake.script(
        "requirement_verification",
        VerificationOutput,
        [
            VerificationOutput(
                supported_by_source=True,
                mandatory_language_supported=True,
                verification_confidence=0.9,
                reasoning_summary="The source says so.",
            )
        ],
    )

    await _verify(fake, tender_id)

    [row] = await _rows(tender_id)
    assert row.status == "completed"
    assert row.provider == "fake"
    assert row.model == _MODEL
    assert row.attempts == 1
    assert row.latency_ms is not None
    assert row.input_tokens and row.input_tokens > 0
    assert row.output_tokens and row.output_tokens > 0
    assert row.estimated_cost_usd == 0


async def test_a_failed_call_records_provider_latency_and_attempts():
    tender_id = await _tender()
    error = RateLimitedError("429 after retries")
    error.measurement = CallMeasurement(provider="gemini", latency_ms=35_123, attempts=4)

    with pytest.raises(RateLimitedError):
        await _verify(_Failing(error), tender_id)

    [row] = await _rows(tender_id)
    assert row.status == "failed"
    assert row.error_code == "RateLimitedError"
    assert row.provider == "gemini"
    assert row.model == _MODEL
    assert row.latency_ms == 35_123
    assert row.attempts == 4
    assert row.estimated_cost_usd is None


async def test_an_llm_timeout_is_its_own_error_code():
    tender_id = await _tender()
    error = LLMTimeoutError("no answer within the 180 s call deadline")
    error.measurement = CallMeasurement(provider="gemini", latency_ms=180_004, attempts=2)

    with pytest.raises(LLMTimeoutError):
        await _verify(_Failing(error), tender_id)

    [row] = await _rows(tender_id)
    assert row.error_code == "LLMTimeoutError"
    assert row.latency_ms == 180_004
    assert row.attempts == 2


async def test_a_rejected_request_writes_its_row():
    # PR 28 review: before ProviderRequestRejectedError, a 4xx other than 404
    # and 429 left the adapter as the SDK's ClientError and wrote no row.
    tender_id = await _tender()
    error = ProviderRequestRejectedError(400, "INVALID_ARGUMENT")
    error.measurement = CallMeasurement(provider="gemini", latency_ms=812, attempts=1)

    with pytest.raises(ProviderRequestRejectedError):
        await _verify(_Failing(error), tender_id)

    [row] = await _rows(tender_id)
    assert row.status == "failed"
    assert row.error_code == "ProviderRequestRejectedError"
    assert row.provider == "gemini"
    assert row.latency_ms == 812
    assert row.attempts == 1


async def test_attempts_cannot_be_negative():
    tender_id = await _tender()

    with pytest.raises(IntegrityError, match="ck_agent_trace_attempts"):
        async with UnitOfWork() as uow:
            await uow.agent_traces.create(
                agent_name="requirement_verification",
                status="completed",
                tender_id=tender_id,
                attempts=-1,
            )
            await uow.commit()


async def test_a_daily_quota_writes_its_own_error_code_and_its_429s():
    # PR 34: the row says which calls stopped on a daily quota, and how many
    # 429s and how much waiting a call had.
    tender_id = await _tender()
    error = DailyQuotaExhaustedError("429 daily", retry_after_seconds=3600.0)
    error.measurement = CallMeasurement(
        provider="gemini",
        latency_ms=31_240,
        attempts=3,
        rate_limited_requests=3,
        rate_limit_wait_ms=30_000,
    )

    with pytest.raises(DailyQuotaExhaustedError):
        await _verify(_Failing(error), tender_id)

    [row] = await _rows(tender_id)
    assert row.error_code == "DailyQuotaExhaustedError"
    assert row.rate_limited_requests == 3
    assert row.rate_limit_wait_ms == 30_000


async def test_a_completed_fake_call_records_no_rate_limit():
    tender_id = await _tender()
    fake = FakeLLMClient()
    fake.script(
        "requirement_verification",
        VerificationOutput,
        [
            VerificationOutput(
                supported_by_source=True,
                mandatory_language_supported=True,
                verification_confidence=0.9,
                reasoning_summary="The source says so.",
            )
        ],
    )

    await _verify(fake, tender_id)

    [row] = await _rows(tender_id)
    assert (row.rate_limited_requests, row.rate_limit_wait_ms) == (0, 0)


@pytest.mark.parametrize(
    ("fields", "constraint"),
    [
        ({"attempts": 1, "rate_limited_requests": -1}, "ck_agent_trace_rate_limited_requests"),
        ({"attempts": 1, "rate_limit_wait_ms": -1}, "ck_agent_trace_rate_limit_wait_ms"),
        (
            {"attempts": 2, "rate_limited_requests": 3},
            "ck_agent_trace_rate_limited_within_attempts",
        ),
    ],
)
async def test_the_rate_limit_columns_are_checked(fields, constraint):
    tender_id = await _tender()

    with pytest.raises(IntegrityError, match=constraint):
        async with UnitOfWork() as uow:
            await uow.agent_traces.create(
                agent_name="requirement_verification",
                status="completed",
                tender_id=tender_id,
                **fields,
            )
            await uow.commit()
