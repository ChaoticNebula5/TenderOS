"""Phase 4 plan item 46: the api, every worker and the outbox dispatcher
say which LLM provider they are on, once, as they start, before they touch
Redis or Postgres; and the line never carries a key."""

import logging
import os
import subprocess
import sys

import pytest
from tenderos_backend.agents.llm import factory
from tenderos_backend.core.config import settings
from tenderos_worker import outbox_dispatcher
from tenderos_worker import worker as worker_module

FAKE_LINE = "llm provider fake: every LLM call is answered by FakeLLMClient"
LIVE_LINE = "llm provider gemini: LLM calls go to the live provider and cost money"


class _StopBeforeRedis(Exception):
    pass


def _messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records]


@pytest.mark.parametrize(("provider", "line"), [("fake", FAKE_LINE), ("gemini", LIVE_LINE)])
def test_the_line_names_the_provider_and_never_a_key(monkeypatch, caplog, provider, line):
    monkeypatch.setattr(settings, "llm_provider", provider)
    monkeypatch.setattr(settings, "gemini_api_key", "item46-not-a-real-key")
    with caplog.at_level(logging.INFO, logger=factory.__name__):
        factory.log_llm_provider()
    assert _messages(caplog) == [line]
    assert "item46-not-a-real-key" not in caplog.text


def test_a_worker_logs_its_provider_before_it_connects_to_redis(monkeypatch, caplog):
    def stop(*args, **kwargs):
        raise _StopBeforeRedis

    monkeypatch.setattr(worker_module, "worker_connection", stop)
    monkeypatch.setattr("sys.argv", ["tenderos_worker.worker", "maintenance"])
    with caplog.at_level(logging.INFO, logger=factory.__name__), pytest.raises(_StopBeforeRedis):
        worker_module.main()
    assert _messages(caplog).count(FAKE_LINE) == 1


def test_the_outbox_dispatcher_logs_its_provider_before_its_loop(monkeypatch, caplog):
    async def stop() -> None:
        raise _StopBeforeRedis

    monkeypatch.setattr(outbox_dispatcher, "_run_until_terminated", stop)
    with caplog.at_level(logging.INFO, logger=factory.__name__), pytest.raises(_StopBeforeRedis):
        outbox_dispatcher.main()
    assert _messages(caplog).count(FAKE_LINE) == 1


def test_the_api_logs_its_provider_once_when_it_is_imported():
    """The api logs it at import time (main.py, beside configure_logging()),
    as uvicorn imports it once per process: so a fresh interpreter, not this
    test session, which has imported main.py already."""
    result = subprocess.run(
        [sys.executable, "-c", "import tenderos_api.main"],
        env={**os.environ, "LLM_PROVIDER": "fake"},
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert (result.stdout + result.stderr).count(FAKE_LINE) == 1
