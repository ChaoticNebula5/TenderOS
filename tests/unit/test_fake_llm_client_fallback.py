"""B17: FakeLLMClient's own strict-queue-by-default, fallback-opt-in-per-pair
contract - see fake_client.py's own docstring for why this exists (an E2E
run against real synthetic documents can't pre-know exact call counts the
way every pytest integration test's own fixture-driven scripting can).
No integration test in this repo has ever exercised FakeLLMClient's own
behavior directly - every existing test exercises it through real
application code instead. This is that direct test.
"""

import pytest
from pydantic import BaseModel
from tenderos_backend.agents.llm.client import MalformedOutputError
from tenderos_backend.agents.llm.fake_client import FakeLLMClient


class _Response(BaseModel):
    value: str


async def _call(client: FakeLLMClient, agent_name: str = "agent", user_prompt: str = "u") -> str:
    result = await client.complete_structured(
        agent_name=agent_name,
        model="m",
        system_prompt="s",
        user_prompt=user_prompt,
        response_schema=_Response,
    )
    return result.parsed.value


@pytest.mark.asyncio
async def test_strict_queue_is_still_the_default_and_raises_on_underrun():
    client = FakeLLMClient()
    client.script("agent", _Response, [_Response(value="one")])

    assert await _call(client) == "one"
    with pytest.raises(AssertionError, match="no scripted response left"):
        await _call(client)


@pytest.mark.asyncio
async def test_fallback_answers_indefinitely_once_set():
    client = FakeLLMClient()
    client.set_fallback("agent", _Response, _Response(value="default"))

    for _ in range(5):
        assert await _call(client) == "default"


@pytest.mark.asyncio
async def test_scripted_queue_drains_before_fallback_is_used():
    client = FakeLLMClient()
    client.script("agent", _Response, [_Response(value="first"), _Response(value="second")])
    client.set_fallback("agent", _Response, _Response(value="default"))

    assert await _call(client) == "first"
    assert await _call(client) == "second"
    assert await _call(client) == "default"
    assert await _call(client) == "default"


@pytest.mark.asyncio
async def test_fallback_is_scoped_to_its_own_agent_name_and_schema_pair():
    client = FakeLLMClient()
    client.set_fallback("agent-a", _Response, _Response(value="a"))

    assert await _call(client, agent_name="agent-a") == "a"
    with pytest.raises(AssertionError, match="no scripted response left"):
        await _call(client, agent_name="agent-b")


@pytest.mark.asyncio
async def test_callable_fallback_is_invoked_fresh_on_every_call():
    # Found necessary empirically, not designed in speculatively - see
    # fake_client.py's own docstring: a static fallback gave 4 real,
    # distinct imported résumés the identical name, which real duplicate-
    # adjudication logic then collapsed into 1 employee. A callable lets a
    # caller give each call a distinct identity without pre-knowing the
    # call count.
    client = FakeLLMClient()
    counter = {"n": 0}

    def _next(_user_prompt: str) -> _Response:
        counter["n"] += 1
        return _Response(value=f"call-{counter['n']}")

    client.set_fallback("agent", _Response, _next)

    assert await _call(client) == "call-1"
    assert await _call(client) == "call-2"
    assert await _call(client) == "call-3"


@pytest.mark.asyncio
async def test_callable_fallback_receives_the_real_user_prompt():
    # The second empirical fix, not designed in speculatively either - see
    # fake_client.py's own docstring, instance 2: a static "proposal_
    # drafting" fallback gave every section, including one real code
    # deliberately excludes from a downstream check, the identical claim.
    # A callable needs to be able to tell sections apart the same way real
    # drafting implicitly does - by what's actually in its own prompt.
    client = FakeLLMClient()

    def _fallback(user_prompt: str) -> _Response:
        return _Response(value="matched" if "SECTION: Overview" in user_prompt else "other")

    client.set_fallback("agent", _Response, _fallback)

    assert await _call(client, user_prompt="SECTION: Overview\nsome guidance") == "matched"
    assert await _call(client, user_prompt="SECTION: Technical Approach\nmore") == "other"


@pytest.mark.asyncio
async def test_fallback_still_supports_script_malformed_via_the_queue():
    # Malformed-output scripting is queue-only (script_malformed appends the
    # sentinel to the same deque set_fallback never touches) - a fallback
    # existing alongside it must not change that: the queued malformed
    # entry is still consumed first, in order.
    client = FakeLLMClient()
    client.script_malformed("agent", _Response)
    client.set_fallback("agent", _Response, _Response(value="default"))

    with pytest.raises(MalformedOutputError):
        await _call(client)
    assert await _call(client) == "default"
