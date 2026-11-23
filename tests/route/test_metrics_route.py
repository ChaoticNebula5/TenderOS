"""Observability PR 31 (A): the api's /metrics and its request counters.

Labels are the matched route's template, never the path; a path no route
matches shares one label; a method outside the fixed list is "other"; the
status is its exact code, under the label `code`. Values are read from the process registry
(core/metrics.py) as before-and-after deltas, since it lives for the whole
test session.
"""

import asyncio
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families
from tenderos_api import main as api_main
from tenderos_backend.core.metrics import REGISTRY, UNMATCHED_ROUTE
from tenderos_backend.domain.services import readiness_service

pytestmark = pytest.mark.asyncio

_REQUESTS = "tenderos_http_requests_total"
_DURATION_COUNT = "tenderos_http_request_duration_seconds_count"
_READINESS_TEMPLATE = "/tenders/{tender_id}/readiness"


def _count(route: str, method: str, code: str) -> float:
    labels = {"route": route, "method": method, "code": code}
    return REGISTRY.get_sample_value(_REQUESTS, labels) or 0.0


def _codes(text: str, route: str, method: str) -> dict[str, float]:
    """Every `code` value counted for one route and method, parsed from
    /metrics' own text, so the label is checked as Prometheus reads it."""
    return {
        sample.labels["code"]: sample.value
        for family in text_string_to_metric_families(text)
        if family.name == "tenderos_http_requests"
        for sample in family.samples
        if sample.name == _REQUESTS
        and sample.labels["route"] == route
        and sample.labels["method"] == method
    }


def _route_labels(text: str) -> set[str]:
    return {
        sample.labels["route"]
        for family in text_string_to_metric_families(text)
        if family.name == "tenderos_http_requests"
        for sample in family.samples
    }


async def test_metrics_route_serves_the_registry_in_prometheus_text(client: TestClient):
    client.get("/health/live")

    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    names = {family.name for family in text_string_to_metric_families(response.text)}
    assert {
        "tenderos_http_requests",
        "tenderos_http_request_duration_seconds",
        "tenderos_rate_limiter_redis_unavailable",
        "tenderos_refresh_token_reuse",
    } <= names


async def test_a_tender_route_is_counted_under_its_template_not_its_id(client: TestClient):
    tender_id = uuid.uuid4()
    before = _count(_READINESS_TEMPLATE, "GET", "401")
    duration_before = REGISTRY.get_sample_value(
        _DURATION_COUNT, {"route": _READINESS_TEMPLATE, "method": "GET"}
    )

    response = client.get(f"/tenders/{tender_id}/readiness")

    assert response.status_code == 401
    assert _count(_READINESS_TEMPLATE, "GET", "401") == before + 1
    duration_after = REGISTRY.get_sample_value(
        _DURATION_COUNT, {"route": _READINESS_TEMPLATE, "method": "GET"}
    )
    assert duration_after == (duration_before or 0.0) + 1
    assert str(tender_id) not in client.get("/metrics").text


async def test_paths_no_route_matches_share_one_label(client: TestClient):
    labels_before = _route_labels(client.get("/metrics").text)
    before = _count(UNMATCHED_ROUTE, "GET", "404")
    paths = [f"/no-such-route-{uuid.uuid4().hex}", f"/tenders/{uuid.uuid4()}/no-such-child"]

    for path in paths:
        assert client.get(path).status_code == 404

    text = client.get("/metrics").text
    assert _count(UNMATCHED_ROUTE, "GET", "404") == before + 2
    assert _route_labels(text) - labels_before <= {UNMATCHED_ROUTE, "/metrics"}
    for path in paths:
        assert path not in text


async def test_a_method_outside_the_list_is_other(client: TestClient):
    before = _count("/health/live", "other", "405")

    response = client.request("BREW", "/health/live")

    assert response.status_code == 405
    assert _count("/health/live", "other", "405") == before + 1


async def test_a_429_and_a_404_are_counted_under_their_own_codes(
    client: TestClient, monkeypatch, _cleanup_redis
):
    """Fold-in 1 of PR 31's review: the exact code, so PR 32 can alert on a
    429 surge without a 404 scan in the same series. The limiter answers
    before the router, so its 429 has no matched route and is counted
    under UNMATCHED_ROUTE with the 404s; the code alone tells them apart."""
    probe = "/health/live"
    lost = f"/no-such-route-{uuid.uuid4().hex}"
    before = _codes(client.get("/metrics").text, UNMATCHED_ROUTE, "GET")
    live_before = _codes(client.get("/metrics").text, probe, "GET")

    assert client.get(lost).status_code == 404
    monkeypatch.setattr(api_main.settings, "rate_limit_enabled", True)
    monkeypatch.setattr(api_main, "GLOBAL_LIMIT", 2)
    assert client.get(probe).status_code == 200
    assert client.get(probe).status_code == 200
    assert client.get(probe).status_code == 429
    monkeypatch.setattr(api_main.settings, "rate_limit_enabled", False)

    text = client.get("/metrics").text
    after = _codes(text, UNMATCHED_ROUTE, "GET")
    live_after = _codes(text, probe, "GET")
    assert after.get("404", 0.0) == before.get("404", 0.0) + 1
    assert after.get("429", 0.0) == before.get("429", 0.0) + 1
    assert live_after.get("200", 0.0) == live_before.get("200", 0.0) + 2
    assert "429" not in live_after
    for code in set(after) | set(live_after):
        assert code.isdigit() and 100 <= int(code) <= 599, code


def _dependency_up(text: str) -> dict[str, float]:
    return {
        sample.labels["dependency"]: sample.value
        for family in text_string_to_metric_families(text)
        if family.name == "tenderos_dependency_up"
        for sample in family.samples
    }


async def test_each_scrape_records_which_dependencies_the_api_reaches(
    client: TestClient, monkeypatch
):
    """Observability PR 32 (answer A): /metrics runs /health/ready's four
    checks and sets tenderos_dependency_up. Up only for a check that answered
    True; a refusal, an exception and a check slower than its cap all read
    0, and the four run at once, so two hung checks cost one cap, not two."""
    monkeypatch.setattr(api_main, "DEPENDENCY_CHECK_TIMEOUT_SECONDS", 0.3)

    async def _up() -> bool:
        return True

    async def _refused() -> bool:
        return False

    async def _raises() -> bool:
        raise OSError("connection refused")

    async def _hangs() -> bool:
        await asyncio.sleep(30)
        return True

    monkeypatch.setattr(api_main, "_check_postgres", _up)
    monkeypatch.setattr(api_main, "_check_redis", _refused)
    monkeypatch.setattr(api_main, "_check_neo4j", _hangs)
    monkeypatch.setattr(api_main, "_check_object_storage", _raises)

    started = time.perf_counter()
    response = client.get("/metrics")
    elapsed = time.perf_counter() - started

    assert response.status_code == 200
    assert _dependency_up(response.text) == {
        "postgres": 1.0,
        "redis": 0.0,
        "neo4j": 0.0,
        "object_storage": 0.0,
    }
    assert elapsed < 2.0, elapsed


async def test_the_checks_run_at_once_so_two_hung_checks_cost_one_cap(
    client: TestClient, monkeypatch
):
    monkeypatch.setattr(api_main, "DEPENDENCY_CHECK_TIMEOUT_SECONDS", 0.5)

    async def _hangs() -> bool:
        await asyncio.sleep(30)
        return True

    async def _up() -> bool:
        return True

    for name in ("_check_postgres", "_check_redis"):
        monkeypatch.setattr(api_main, name, _up)
    monkeypatch.setattr(api_main, "_check_neo4j", _hangs)
    monkeypatch.setattr(api_main, "_check_object_storage", _hangs)

    started = time.perf_counter()
    response = client.get("/metrics")
    elapsed = time.perf_counter() - started

    assert _dependency_up(response.text)["neo4j"] == 0.0
    assert _dependency_up(response.text)["object_storage"] == 0.0
    # One cap (0.5 s) plus the request; one after another would be 1.0 s.
    assert 0.5 <= elapsed < 0.95, elapsed


async def test_a_dependency_that_recovers_reads_up_at_the_next_scrape(
    client: TestClient, monkeypatch
):
    answers = iter([False, True])

    async def _flaky() -> bool:
        return next(answers)

    async def _up() -> bool:
        return True

    for name in ("_check_postgres", "_check_redis", "_check_object_storage"):
        monkeypatch.setattr(api_main, name, _up)
    monkeypatch.setattr(api_main, "_check_neo4j", _flaky)

    assert _dependency_up(client.get("/metrics").text)["neo4j"] == 0.0
    assert _dependency_up(client.get("/metrics").text)["neo4j"] == 1.0


async def test_an_unhandled_error_is_counted_as_500(
    client: TestClient, register_actor, monkeypatch
):
    headers, _, org_id = register_actor()
    created = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Metrics"}, headers=headers
    )
    assert created.status_code == 201, created.text

    async def _broken(_tender_id):
        raise RuntimeError("broken on purpose")

    monkeypatch.setattr(readiness_service, "get_latest_readiness", _broken)
    before = _count(_READINESS_TEMPLATE, "GET", "500")

    response = client.get(f"/tenders/{created.json()['id']}/readiness", headers=headers)

    assert response.status_code == 500
    assert _count(_READINESS_TEMPLATE, "GET", "500") == before + 1
