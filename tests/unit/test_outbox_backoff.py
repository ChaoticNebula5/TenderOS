"""Phase 4 blocker 3: the outbox retry delay for errors that consume an
attempt (outbox_dispatcher._retry_delay_seconds). Redis being unreachable
does not consume an attempt and has no per-event delay at all; see
tests/integration/test_outbox_dispatcher.py for that half."""

import pytest
from tenderos_worker import outbox_dispatcher
from tenderos_worker.outbox_dispatcher import (
    MAX_ATTEMPTS,
    RETRY_BASE_SECONDS,
    RETRY_CAP_SECONDS,
    RETRY_JITTER_LOW,
    _retry_delay_seconds,
)


def _without_jitter(monkeypatch: pytest.MonkeyPatch, factor: float = 1.0) -> None:
    monkeypatch.setattr(outbox_dispatcher.random, "uniform", lambda low, high: factor)


def test_delays_double_from_the_base_and_stop_at_the_cap(monkeypatch):
    _without_jitter(monkeypatch)
    delays = [_retry_delay_seconds(n) for n in range(1, MAX_ATTEMPTS)]
    assert delays == [30.0, 60.0, 120.0, 240.0, 300.0, 300.0, 300.0]
    assert delays[0] == RETRY_BASE_SECONDS
    assert max(delays) == RETRY_CAP_SECONDS


def test_jitter_never_raises_a_wait_above_the_cap(monkeypatch):
    _without_jitter(monkeypatch, factor=1.0)
    assert _retry_delay_seconds(50) == RETRY_CAP_SECONDS
    _without_jitter(monkeypatch, factor=RETRY_JITTER_LOW)
    assert _retry_delay_seconds(50) == RETRY_CAP_SECONDS * RETRY_JITTER_LOW


def test_jitter_stays_inside_its_band():
    for n in range(1, MAX_ATTEMPTS):
        nominal = min(RETRY_CAP_SECONDS, RETRY_BASE_SECONDS * 2 ** (n - 1))
        for _ in range(50):
            delay = _retry_delay_seconds(n)
            assert nominal * RETRY_JITTER_LOW <= delay <= nominal


def test_all_retries_together_span_roughly_15_to_30_minutes(monkeypatch):
    _without_jitter(monkeypatch, factor=RETRY_JITTER_LOW)
    shortest = sum(_retry_delay_seconds(n) for n in range(1, MAX_ATTEMPTS))
    _without_jitter(monkeypatch, factor=1.0)
    longest = sum(_retry_delay_seconds(n) for n in range(1, MAX_ATTEMPTS))
    assert 15 * 60 <= shortest <= longest <= 30 * 60
