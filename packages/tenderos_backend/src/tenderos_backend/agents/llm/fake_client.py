from collections import deque
from dataclasses import dataclass, field

from pydantic import BaseModel

from tenderos_backend.agents.llm.client import LLMResult, MalformedOutputError


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
    call site, not as a confusing downstream assertion failure.
    """

    _scripts: dict[tuple[str, type], deque] = field(default_factory=dict)
    calls: list[dict] = field(default_factory=list)

    def script(self, agent_name: str, response_schema: type[BaseModel], responses: list) -> None:
        key = (agent_name, response_schema)
        self._scripts.setdefault(key, deque()).extend(responses)

    def script_malformed(self, agent_name: str, response_schema: type[BaseModel]) -> None:
        # A sentinel the queue can hold alongside real responses, to test
        # MalformedOutputError handling (the one-repair-attempt path in
        # agents/requirements/extract.py) without a real schema violation.
        self.script(agent_name, response_schema, [_MALFORMED_SENTINEL])

    async def complete_structured[T: BaseModel](
        self,
        *,
        agent_name: str,
        model: str,
        system_prompt: str,
        user_prompt: str,
        response_schema: type[T],
    ) -> LLMResult[T]:
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
        if not queue:
            raise AssertionError(
                f"FakeLLMClient has no scripted response left for "
                f"({agent_name!r}, {response_schema.__name__}) - call .script(...) first."
            )
        next_response = queue.popleft()
        if next_response is _MALFORMED_SENTINEL:
            raise MalformedOutputError(f"{agent_name}: scripted malformed output")

        return LLMResult(
            parsed=next_response,
            provider="fake",
            model=model,
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
            estimated_cost_usd=0.0,
        )


_MALFORMED_SENTINEL = object()
