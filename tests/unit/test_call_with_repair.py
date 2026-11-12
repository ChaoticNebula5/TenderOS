"""agents/llm/client.py::call_with_repair - I4's rule for malformed
structured output: ask for a repair once, and fail the task if the repair
is malformed too. Shared by every agent, and
until now had zero direct test coverage anywhere in this project - only
exercised incidentally, through agent-level tests asserting on downstream
degradation (e.g. test_proposal_drafting_node.py), never proving the
one-retry-then-propagate contract itself. Real mechanism, real gap in
what verifies it - the same "capability exists, nothing exercises it"
shape this project keeps finding, not assumed correct from the code
reading alone.
"""

import pytest
from pydantic import BaseModel
from tenderos_backend.agents.llm.client import (
    LLMResult,
    MalformedOutputError,
    call_with_repair,
)

pytestmark = pytest.mark.asyncio


class _Schema(BaseModel):
    value: str


def _result(value: str) -> LLMResult[_Schema]:
    return LLMResult(
        parsed=_Schema(value=value),
        provider="fake",
        model="fake-model",
        input_tokens=1,
        output_tokens=1,
        latency_ms=1,
        estimated_cost_usd=None,
    )


class _StubClient:
    """Minimal LLMClient Protocol implementation - not FakeLLMClient,
    which scripts successful responses per agent_name and has no
    mechanism to raise on a specific call in a sequence. Tracks calls
    directly so the test can assert exactly how many happened and what
    the repair prompt looked like, not just the final outcome.
    """

    def __init__(self, raise_count: int) -> None:
        self.raise_count = raise_count
        self.calls: list[str] = []

    async def complete_structured(
        self, *, agent_name, model, system_prompt, user_prompt, response_schema
    ):
        self.calls.append(user_prompt)
        if len(self.calls) <= self.raise_count:
            raise MalformedOutputError(f"call {len(self.calls)}: schema mismatch")
        return _result("ok")


async def test_one_malformed_response_then_success_returns_the_repaired_result():
    client = _StubClient(raise_count=1)

    result = await call_with_repair(
        client,
        agent_name="test_agent",
        model="fake-model",
        system_prompt="system",
        user_prompt="original prompt",
        response_schema=_Schema,
    )

    assert result.parsed.value == "ok"
    assert len(client.calls) == 2, "exactly one repair attempt, not a retry loop"
    assert client.calls[0] == "original prompt"
    assert "did not match the required schema" in client.calls[1]
    assert "call 1: schema mismatch" in client.calls[1], (
        "the repair prompt must carry the actual validation error, not a generic message"
    )


async def test_two_malformed_responses_propagates_not_a_third_attempt():
    client = _StubClient(raise_count=2)

    with pytest.raises(MalformedOutputError):
        await call_with_repair(
            client,
            agent_name="test_agent",
            model="fake-model",
            system_prompt="system",
            user_prompt="original prompt",
            response_schema=_Schema,
        )

    assert len(client.calls) == 2, (
        "I4: repair once, then fail the task - a second failure must propagate, "
        "not trigger a third call"
    )
