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


_PRICED = "gemini-3.5-flash-lite"  # in gemini_adapter's price table


def _models(monkeypatch, extractor: str, verifier: str, reasoning: str) -> None:
    monkeypatch.setattr(settings, "extractor_model", extractor)
    monkeypatch.setattr(settings, "verifier_model", verifier)
    monkeypatch.setattr(settings, "reasoning_model", reasoning)


@pytest.mark.parametrize(("provider", "line"), [("fake", FAKE_LINE), ("gemini", LIVE_LINE)])
def test_the_line_names_the_provider_and_never_a_key(monkeypatch, caplog, provider, line):
    monkeypatch.setattr(settings, "llm_provider", provider)
    monkeypatch.setattr(settings, "gemini_api_key", "item46-not-a-real-key")
    # PR 29: priced models, so the live provider logs its one line only (an
    # unpriced one adds a warning, tested below).
    _models(monkeypatch, _PRICED, _PRICED, _PRICED)
    with caplog.at_level(logging.INFO, logger=factory.__name__):
        factory.log_llm_provider()
    assert _messages(caplog) == [line]
    assert "item46-not-a-real-key" not in caplog.text


def test_the_live_provider_warns_once_per_configured_model_with_no_price(monkeypatch, caplog):
    # PR 29 (Q7 a): a model id outside the price table records no cost, so
    # the live provider says so at start, naming the setting and the id.
    monkeypatch.setattr(settings, "llm_provider", "gemini")
    monkeypatch.setattr(settings, "gemini_api_key", "pr29-not-a-real-key")
    _models(monkeypatch, _PRICED, "gemini-9-unpriced", "changeme")
    with caplog.at_level(logging.INFO, logger=factory.__name__):
        factory.log_llm_provider()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert [r.getMessage() for r in warnings] == [
        "llm price: VERIFIER_MODEL=gemini-9-unpriced has no price in the table; "
        "its calls record no cost",
        "llm price: REASONING_MODEL=changeme has no price in the table; its calls record no cost",
    ]
    assert "pr29-not-a-real-key" not in caplog.text


def test_the_fake_provider_never_warns_about_prices(monkeypatch, caplog):
    monkeypatch.setattr(settings, "llm_provider", "fake")
    _models(monkeypatch, "changeme", "changeme", "changeme")
    with caplog.at_level(logging.INFO, logger=factory.__name__):
        factory.log_llm_provider()
    assert _messages(caplog) == [FAKE_LINE]


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
