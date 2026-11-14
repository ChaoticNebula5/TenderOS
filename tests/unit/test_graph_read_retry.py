"""Plan item 81: graph reads retry Neo4j's transient errors, and only those.

Observed in PR 3's E2E run 1: POST /tenders/{id}/graph/impact answered 500
with Neo.TransientError.Transaction.Outdated, a read overlapping a
projection's writes. The reads run auto-commit queries, which the driver
never retries; graph_service._read_with_retry now does, for TransientError
only, as the projection writes already did.

No Neo4j or Postgres here: the session and the queries are replaced at
graph_service's boundary, so each test controls exactly what each attempt
raises. preview_impact stands for every read; they share the one helper.
"""

import types
import uuid
from contextlib import asynccontextmanager

import pytest
from neo4j.exceptions import Neo4jError, ServiceUnavailable, TransientError
from tenderos_backend.domain.services import graph_service

pytestmark = pytest.mark.asyncio

_OUTDATED = "Neo.TransientError.Transaction.Outdated"


def _outdated() -> Neo4jError:
    # The driver's own constructor for a server error, so the class (and so
    # what the retry sees) is decided the same way as on a real answer.
    return Neo4jError._hydrate_neo4j(code=_OUTDATED, message="Database elements were deleted")


class _Calls:
    def __init__(self, outcomes: list[object]) -> None:
        # Each outcome is raised if it is an exception, returned otherwise.
        self.outcomes = outcomes
        self.queries = 0
        self.sessions = 0
        self.sleeps: list[float] = []


@pytest.fixture
def calls(monkeypatch) -> _Calls:
    state = _Calls([])

    @asynccontextmanager
    async def session():
        state.sessions += 1
        yield object()

    async def check_freshness(tender_id, session):
        return None

    async def preview_impact(session, tender_id, knowledge_item_id, max_hops):
        outcome = state.outcomes[state.queries]
        state.queries += 1
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    async def sleep(seconds: float) -> None:
        state.sleeps.append(seconds)

    monkeypatch.setattr(graph_service, "get_neo4j_session", session)
    monkeypatch.setattr(graph_service, "check_freshness", check_freshness)
    monkeypatch.setattr(graph_service.queries, "preview_impact", preview_impact)
    monkeypatch.setattr(graph_service, "asyncio", types.SimpleNamespace(sleep=sleep))
    return state


async def test_the_constructed_error_is_the_drivers_transient_error():
    error = _outdated()
    assert isinstance(error, TransientError)
    assert error.code == _OUTDATED


async def test_an_outdated_read_is_retried_and_the_second_attempt_succeeds(calls):
    rows = [{"node": "Requirement", "hops": 1}]
    calls.outcomes = [_outdated(), rows]

    result = await graph_service.preview_impact(uuid.uuid4(), uuid.uuid4())

    assert result == rows
    assert calls.queries == 2
    # A fresh session for the second attempt, after one short pause.
    assert calls.sessions == 2
    assert len(calls.sleeps) == 1
    assert 0.1 <= calls.sleeps[0] <= 0.5


async def test_an_unreachable_neo4j_is_not_retried(calls):
    # Item 31's fast failure: an outage fails on the first attempt, with no
    # pause, so the driver's own timeouts stay the whole wait.
    calls.outcomes = [ServiceUnavailable("Unable to retrieve routing information"), []]

    with pytest.raises(ServiceUnavailable):
        await graph_service.preview_impact(uuid.uuid4(), uuid.uuid4())

    assert calls.queries == 1
    assert calls.sleeps == []


async def test_a_terminated_transaction_is_not_retried(calls):
    # Neo4j marks this transient code as not worth retrying; driver 6.4.1
    # rewrites it to ClientError (is_retryable() False), so it is raised at
    # once. If a later driver stopped rewriting it, this fails.
    terminated = Neo4jError._hydrate_neo4j(
        code="Neo.TransientError.Transaction.Terminated", message="The transaction was terminated"
    )
    assert terminated.is_retryable() is False
    calls.outcomes = [terminated, []]

    with pytest.raises(Neo4jError) as raised:
        await graph_service.preview_impact(uuid.uuid4(), uuid.uuid4())

    assert raised.value is terminated
    assert calls.queries == 1
    assert calls.sleeps == []


async def test_a_transient_error_on_every_attempt_is_raised_after_three(calls):
    calls.outcomes = [_outdated(), _outdated(), _outdated(), []]

    with pytest.raises(TransientError) as raised:
        await graph_service.preview_impact(uuid.uuid4(), uuid.uuid4())

    assert raised.value.code == _OUTDATED
    assert calls.queries == graph_service._READ_ATTEMPTS == 3
    assert len(calls.sleeps) == 2
