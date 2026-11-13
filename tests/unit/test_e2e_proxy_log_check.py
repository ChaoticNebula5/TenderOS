"""scripts/e2e_proxy_log_check.py (plan item 27): leaked SSE tickets, leaked
refresh cookies (plan item 34, option 2) and CSP violations in one E2E run's
compose log."""

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "e2e_proxy_log_check", REPO_ROOT / "scripts" / "e2e_proxy_log_check.py"
)
assert _spec is not None and _spec.loader is not None
e2e_proxy_log_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(e2e_proxy_log_check)

_T = "2026-11-11T10:00:00.000000000Z"
_WEB_STREAM = (
    f"web-1  | {_T} 10.89.27.1 - [11/Nov/2026:10:00:00 +0000] "
    '"GET /api/workflow-runs/r1/events/stream HTTP/1.1" 200 512 3.2 "Mozilla"'
)
_API_STREAM = (
    f"api-1  | {_T} INFO:     10.89.27.1:40000 - "
    '"GET /workflow-runs/r1/events/stream?ticket=[redacted] HTTP/1.1" 200'
)


def _check(*lines: str) -> list[str]:
    problems, _counts = e2e_proxy_log_check.check(list(lines))
    return problems


def test_a_clean_run_passes():
    assert _check(_WEB_STREAM, _API_STREAM, "postgres-1  | anything ticket=abc") == []


def test_a_ticket_in_the_nginx_log_fails_without_printing_it():
    leaked = _WEB_STREAM.replace("events/stream HTTP", "events/stream?ticket=s3cr3t HTTP")
    problems = _check(leaked, _API_STREAM)
    assert problems == ["line 1: web (nginx) log holds a ticket parameter"]
    assert not any("s3cr3t" in p for p in problems)


def test_a_ticket_in_an_nginx_error_line_fails():
    error = (
        f"web-1  | {_T} 2026/11/11 10:00:01 [error] 29#29: *7 upstream timed out, "
        'request: "GET /api/operations/o1/events/stream?ticket=s3cr3t HTTP/1.1"'
    )
    assert _check(_WEB_STREAM, _API_STREAM, error) == [
        "line 3: web (nginx) log holds a ticket parameter"
    ]


def test_an_unredacted_ticket_in_the_api_log_fails():
    leaked = _API_STREAM.replace("ticket=[redacted]", "ticket=s3cr3t")
    assert _check(_WEB_STREAM, leaked) == ["line 2: api log holds an unredacted ticket"]


def test_a_csp_report_fails():
    report = (
        f'web-1  | {_T} 10.89.27.1 - [11/Nov/2026:10:00:02 +0000] "POST /csp-report HTTP/1.1" 204'
    )
    assert _check(_WEB_STREAM, _API_STREAM, report) == ["line 3: a CSP violation was reported"]


def test_no_stream_traffic_fails_rather_than_passing_vacuously():
    assert _check(_API_STREAM) == [
        "no web stream lines: the check would pass without stream traffic"
    ]
    assert _check(_WEB_STREAM) == [
        "no api stream lines: the check would pass without stream traffic"
    ]


def test_the_refresh_cookie_in_any_service_log_fails_without_printing_it():
    web = _WEB_STREAM.replace('"Mozilla"', '"Mozilla" cookie: tenderos_refresh=s3cr3t')
    worker = f"worker-1  | {_T} Cookie: tenderos_refresh=s3cr3t"
    problems = _check(web, _API_STREAM, worker)
    assert problems == [
        "line 1: web log holds the refresh cookie",
        "line 3: worker log holds the refresh cookie",
    ]
    assert not any("s3cr3t" in p for p in problems)
