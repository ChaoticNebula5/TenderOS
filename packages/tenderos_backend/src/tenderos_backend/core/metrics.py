"""Observability PR 31: the api process's own metrics (decision Q3 a).

One registry for the process, served by the api's GET /metrics (main.py).
The api is one uvicorn process (apps/api/Dockerfile, no --workers), so one
in-process registry holds every count; prometheus_client's multiprocess mode
is not needed. Worker state (queues, heartbeats, runs, dead letters) is not
here: the metrics_exporter reads it from Postgres and Redis
(tenderos_worker/metrics_exporter.py), so RQ's forked work horses never
need a registry of their own.

In the backend package, not the api's, because one of the two event
counters is incremented in a backend service: auth_service's refresh-token
reuse. The api's request middleware (tenderos_api/middleware/metrics.py)
records into the same registry.

Labels are bounded on purpose: a route's template (`/tenders/{tender_id}`),
never its path; a method from a fixed list; the exact status code, 100 to
599, anything else OTHER_STATUS (at most 501 values, and in practice the
dozen the app answers). A path that matches no route shares UNMATCHED_ROUTE,
so a scan of random paths adds no label values. So does every response a
middleware gives before the router runs: the rate limiter's 429s and
login's 503 when its Redis is gone, the body limit's 413s, and CORS's
answer to a preflight. Their codes tell them apart from a 404 scan.

The status label is `code`, the name Prometheus's own HTTP instrumentation
uses (client_golang's promhttp, prometheus_http_requests_total), so a query
reads `code="429"`. The exact code, not its class, so an alert can tell a
429 surge (every user behind a host's proxy in one rate-limit bucket),
login's 503s (the limiter's Redis gone) and the final gate's 409s apart
from a 404 scan, which a class would sum together.
"""

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram
from prometheus_client.gc_collector import GCCollector
from prometheus_client.platform_collector import PlatformCollector
from prometheus_client.process_collector import ProcessCollector

UNMATCHED_ROUTE = "unmatched"
OTHER_METHOD = "other"
OTHER_STATUS = "other"
KNOWN_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})

# Its own registry, not prometheus_client's global one: nothing a library
# registers globally ends up on /metrics unseen, and a test can read this
# registry's values without other processes' state.
REGISTRY = CollectorRegistry(auto_describe=True)
ProcessCollector(registry=REGISTRY)
PlatformCollector(registry=REGISTRY)
GCCollector(registry=REGISTRY)

HTTP_REQUESTS = Counter(
    "tenderos_http_requests",
    "api requests answered, by route template, method and status code",
    ["route", "method", "code"],
    registry=REGISTRY,
)

# Seconds from the request reaching the api to its last response byte. An
# SSE stream's duration is how long it stayed open, so streams sit in the
# top buckets by design.
HTTP_REQUEST_DURATION = Histogram(
    "tenderos_http_request_duration_seconds",
    "api request duration, by route template and method",
    ["route", "method"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
    registry=REGISTRY,
)

# The two events that were log lines only (design section 2): each request
# the rate limiter let through because Redis could not be asked (main.py's
# fail-open branch; the WARNING is rate-limited to one a minute, this counts
# every one), and each refresh token presented again after it was rotated
# (auth_service.rotate_refresh_token, which revokes the family).
RATE_LIMITER_REDIS_UNAVAILABLE = Counter(
    "tenderos_rate_limiter_redis_unavailable",
    "requests the rate limiter let through (or login refused) because Redis was unavailable",
    registry=REGISTRY,
)
REFRESH_TOKEN_REUSE = Counter(
    "tenderos_refresh_token_reuse",
    "refresh tokens presented again after rotation (each revokes its family)",
    registry=REGISTRY,
)


# Observability PR 32 (answer A): what /health/ready answers, as a metric, so
# an alert can see a dependency down (Neo4j above all: its outage shows as
# graph routes answering 500, and no other metric carries it). Set by the
# api's /metrics handler, which runs the same four checks before each scrape
# (main.py); 1 for up, 0 for down or no answer within the check's cap.
DEPENDENCY_UP = Gauge(
    "tenderos_dependency_up",
    "whether the api reaches each dependency, checked at each scrape (1 up, 0 down or too slow)",
    ["dependency"],
    registry=REGISTRY,
)


def method_label(method: str) -> str:
    return method if method in KNOWN_METHODS else OTHER_METHOD


def status_code_label(status_code: int) -> str:
    """'429' for 429; anything outside 100 to 599 is OTHER_STATUS, so a
    nonsense value a handler might send cannot add label values."""
    if 100 <= status_code <= 599:
        return str(status_code)
    return OTHER_STATUS
