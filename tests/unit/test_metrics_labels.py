"""Observability PR 31 (A): the request labels stay bounded, and scrape
requests stay out of uvicorn's access log."""

import logging

import pytest
from tenderos_api.logging_filters import SkipMetricsScrapeFilter
from tenderos_api.middleware.metrics import route_label
from tenderos_backend.core.metrics import (
    OTHER_METHOD,
    OTHER_STATUS,
    UNMATCHED_ROUTE,
    method_label,
    status_code_label,
)


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (100, "100"),
        (200, "200"),
        (204, "204"),
        (301, "301"),
        (404, "404"),
        (409, "409"),
        (429, "429"),
        (499, "499"),
        (500, "500"),
        (503, "503"),
        (599, "599"),
        (0, OTHER_STATUS),
        (99, OTHER_STATUS),
        (600, OTHER_STATUS),
        (-1, OTHER_STATUS),
    ],
)
def test_the_status_label_is_the_exact_code_or_other(code: int, expected: str):
    assert status_code_label(code) == expected


def test_only_known_methods_keep_their_name():
    for method in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
        assert method_label(method) == method
    for method in ("BREW", "get", "PROPFIND", "", "GET " * 50):
        assert method_label(method) == OTHER_METHOD


def test_the_route_label_is_the_template_or_unmatched():
    class _Route:
        path = "/tenders/{tender_id}"

    assert route_label({"route": _Route()}) == "/tenders/{tender_id}"
    assert route_label({}) == UNMATCHED_ROUTE
    assert route_label({"route": object()}) == UNMATCHED_ROUTE


def _access_record(method: str, path: str) -> logging.LogRecord:
    # uvicorn's own call: '%s - "%s %s HTTP/%s" %d' with these args.
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("10.0.0.5:5000", method, path, "1.1", 200),
        None,
    )


def test_a_scrape_has_no_access_line_and_everything_else_keeps_one():
    skip = SkipMetricsScrapeFilter()
    assert skip.filter(_access_record("GET", "/metrics")) is False
    assert skip.filter(_access_record("GET", "/metrics?name[]=x")) is False
    for method, path in (
        ("GET", "/metrics/extra"),
        ("GET", "/metricsx"),
        ("POST", "/metrics"),
        ("GET", "/health/live"),
        ("GET", "/api/metrics"),
    ):
        assert skip.filter(_access_record(method, path)) is True, (method, path)
    plain = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, "no args", None, None)
    assert skip.filter(plain) is True


def test_the_api_installs_the_filter_on_uvicorns_access_logger():
    import tenderos_api.main  # noqa: F401 - the import installs it

    access = logging.getLogger("uvicorn.access")
    assert sum(isinstance(f, SkipMetricsScrapeFilter) for f in access.filters) == 1
