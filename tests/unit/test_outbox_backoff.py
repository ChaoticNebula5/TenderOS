from tenderos_worker.outbox_dispatcher import MAX_ATTEMPTS, _backoff_seconds


def test_backoff_is_exponential():
    assert _backoff_seconds(0) == 1.0
    assert _backoff_seconds(1) == 2.0
    assert _backoff_seconds(2) == 4.0


def test_backoff_increases_monotonically_up_to_max_attempts():
    values = [_backoff_seconds(n) for n in range(MAX_ATTEMPTS)]
    assert values == sorted(values)
    assert len(set(values)) == len(values)
