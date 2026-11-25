import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import BaseModel

from tenderos_backend.agents.llm.client import (
    CallMeasurement,
    LLMResult,
    MalformedOutputError,
    elapsed_ms,
)


def estimated_tokens(text: str) -> int:
    """PR 28: the fake's token count, characters / 4 rounded up (a common
    rule of thumb for English text, not any tokenizer). It gives the report
    and the panels non-zero, size-shaped numbers on the fake stack; the cost
    stays 0, so a fake row is never read as a real price."""
    return (len(text) + 3) // 4


@dataclass
class FakeLLMClient:
    """Test double for LLMClient - every automated test in this repo runs
    against this, never GeminiClient, so the suite has no network dependency
    and no flake from a live model's non-determinism (CLAUDE.md/B5 plan: the
    stop-gate's real numbers come from a deliberate, separate run against the
    real adapter, not from the test suite).

    Scripted per (agent_name, response_schema) pair: queue up the exact
    parsed objects a test wants returned, in call order. Calling past the end
    of a queue is a test bug, not a runtime scenario to handle gracefully -
    it raises immediately so a missing script entry fails loudly at the
    call site, not as a confusing downstream assertion failure. This
    strict-queue behavior stays the DEFAULT for every (agent_name,
    response_schema) pair that has no fallback set - every existing test's
    behavior is unchanged by the addition below.

    B17: an opt-in fallback mode, per pair via set_fallback(...), for the
    one real caller that can't pre-know its exact call count - a live E2E
    run against real synthetic documents, where batch/requirement/candidate
    counts depend on real parsing of real content, not a test's own fixed
    fixture. Once a pair's own exact-order queue (if any) is exhausted, a
    fallback response set for that pair keeps being returned indefinitely,
    instead of raising - trading "exact, known call count" (this class's
    original and still-default contract) for "however many times you're
    called, always answer something schema-valid," which is what an
    open-ended run needs and no pytest integration test in this repo does.

    A fallback may be a static BaseModel instance (the common case) OR a
    callable taking the call's own user_prompt and returning one, invoked
    fresh on every call that reaches it. Found necessary empirically, not
    designed in speculatively, twice:

    1. A real fake-provider E2E run with a static "resume_extraction"
       fallback gave all 4 imported résumés the identical candidate name,
       which knowledge_service.py's own real duplicate-adjudication logic
       (resolve_employee_duplicate, matching on name/email) then correctly
       treated 3 of the 4 as the SAME person - collapsing 4 employees into
       1, a real downstream consequence of reusing one static object
       across calls that are supposed to represent distinct real-world
       entities, not a fallback-mechanism bug.
    2. A static "proposal_drafting" fallback gave every section - including
       "Company Overview," the one template proposal_service.py's own
       section_is_data_gated deliberately excludes from grounding checks -
       the identical is_factual claim, which real grounding-enforcement
       logic then correctly marked "unsupported" in every section
       identically, including Company Overview. A downstream E2E assertion
       that has always been able to assume "the first unsupported claim
       came from a data-gated section" (true for real content, where an
       overview/intro section doesn't make the same kind of checkable
       claims a technical/experience section does) broke on fake content
       that made no such distinction. This is what user_prompt access is
       for: draft_section.py's own prompt embeds "SECTION: <name>" as its
       first line, so a fallback can behave differently per section the
       same way real drafting implicitly does, without this class needing
       to know anything about proposals specifically.

    The callable receives user_prompt (not the full call), not zero
    arguments - the one thing every fallback needs the chance to inspect,
    without this class parsing agent-specific prompt structure itself.
    """

    _scripts: dict[tuple[str, type], deque] = field(default_factory=dict)
    _fallbacks: dict[tuple[str, type], object] = field(default_factory=dict)
    calls: list[dict] = field(default_factory=list)

    def script(self, agent_name: str, response_schema: type[BaseModel], responses: list) -> None:
        key = (agent_name, response_schema)
        self._scripts.setdefault(key, deque()).extend(responses)

    def script_malformed(self, agent_name: str, response_schema: type[BaseModel]) -> None:
        # A sentinel the queue can hold alongside real responses, to test
        # MalformedOutputError handling (the one-repair-attempt path in
        # agents/requirements/extract.py) without a real schema violation.
        self.script(agent_name, response_schema, [_MALFORMED_SENTINEL])

    def set_fallback(
        self,
        agent_name: str,
        response_schema: type[BaseModel],
        response: BaseModel | Callable[[str], BaseModel],
    ) -> None:
        """Opt-in per (agent_name, response_schema) pair - see this class's
        own docstring, including why `response` may be a callable (taking
        the call's own user_prompt) instead of a fixed instance. Does not
        affect any pair that never calls this; a pair that HAS both a
        scripted queue and a fallback drains the queue first, in order,
        then falls back once it's empty - a caller can still pin specific
        early responses (e.g. to exercise a particular branch first) while
        staying answerable indefinitely after."""
        self._fallbacks[(agent_name, response_schema)] = response

    async def complete_structured[T: BaseModel](
        self,
        *,
        agent_name: str,
        model: str,
        system_prompt: str,
        user_prompt: str,
        response_schema: type[T],
    ) -> LLMResult[T]:
        # PR 28: measured like a real call (wall time from here), so the fake
        # stack's latency is real, if near 0, rather than a fixed 0.
        start = time.monotonic()
        input_tokens = estimated_tokens(system_prompt + user_prompt)
        self.calls.append(
            {
                "agent_name": agent_name,
                "model": model,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "response_schema": response_schema,
            }
        )
        key = (agent_name, response_schema)
        queue = self._scripts.get(key)
        if queue:
            next_response = queue.popleft()
        elif key in self._fallbacks:
            fallback = self._fallbacks[key]
            next_response = fallback(user_prompt) if callable(fallback) else fallback
        else:
            raise AssertionError(
                f"FakeLLMClient has no scripted response left for "
                f"({agent_name!r}, {response_schema.__name__}) - call .script(...) "
                f"(or .set_fallback(...) for an open-ended run) first."
            )
        if next_response is _MALFORMED_SENTINEL:
            error = MalformedOutputError(f"{agent_name}: scripted malformed output")
            error.measurement = CallMeasurement(
                provider="fake",
                latency_ms=elapsed_ms(start),
                attempts=1,
                input_tokens=input_tokens,
                output_tokens=0,
                estimated_cost_usd=0.0,
                thinking_tokens=0,
                rate_limited_requests=0,
                rate_limit_wait_ms=0,
            )
            raise error

        return LLMResult(
            parsed=next_response,
            provider="fake",
            model=model,
            input_tokens=input_tokens,
            output_tokens=estimated_tokens(next_response.model_dump_json()),
            latency_ms=elapsed_ms(start),
            estimated_cost_usd=0.0,
            attempts=1,
            # PR 29: the fake never thinks. PR 34: nor is it rate limited.
            thinking_tokens=0,
            rate_limited_requests=0,
            rate_limit_wait_ms=0,
        )


_MALFORMED_SENTINEL = object()
