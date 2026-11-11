"""Plan item 27 (decision 0085): the web container's nginx in front of the api.

The review conditions, checked on the files themselves so CI catches a drift
without a running stack (the live proof is in the item's E2E runs):
- nginx sets X-Forwarded-For to the address it saw, overwriting what a
  browser sent, never appending to it;
- the api trusts that header only from nginx's fixed address, never "*", and
  uvicorn, given that address, ignores the header from anywhere else;
- nginx's body limit lets the api's own limit decide;
- the two SSE streams are unbuffered, with a read timeout above the 1 s
  heartbeat;
- the access log never records a query string (SSE tickets travel in it).
"""

import ipaddress
import re
from pathlib import Path

import pytest
import yaml
from tenderos_backend.core.config import settings as backend_settings
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

REPO_ROOT = Path(__file__).resolve().parents[2]
NGINX = (REPO_ROOT / "apps/web/nginx.conf").read_text(encoding="utf-8")
PROXY_HEADERS = (REPO_ROOT / "apps/web/proxy_headers.inc").read_text(encoding="utf-8")
COMPOSE = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))


def _directives(text: str) -> list[str]:
    """nginx directives, comments removed, one per entry."""
    without_comments = re.sub(r"#[^\n]*", "", text)
    return [d.strip() for d in re.split(r"[;{}]", without_comments) if d.strip()]


def _location(prefix_or_regex: str) -> str:
    """The body of one location block in nginx.conf (blocks are not nested)."""
    start = NGINX.index(f"location {prefix_or_regex} {{")
    return NGINX[start : NGINX.index("\n    }", start)]


def _nginx_address() -> str:
    return COMPOSE["services"]["web"]["networks"]["proxy"]["ipv4_address"]


# --- the forwarded address -----------------------------------------------------


def test_nginx_overwrites_x_forwarded_for_with_the_address_it_saw():
    directives = _directives(PROXY_HEADERS)
    assert "proxy_set_header X-Forwarded-For $remote_addr" in directives
    assert "$proxy_add_x_forwarded_for" not in PROXY_HEADERS
    for block in ("/api/", "~ ^/api/(workflow-runs|operations)/[^/]+/events/stream$"):
        assert "include /etc/nginx/conf.d/proxy_headers.inc;" in _location(block)


def test_the_api_trusts_forwarded_headers_only_from_nginx():
    services = COMPOSE["services"]
    trusted = services["api"]["environment"]["FORWARDED_ALLOW_IPS"]
    assert trusted == _nginx_address()
    assert "*" not in trusted
    # web is on the proxy network only, so it reaches the api from that address.
    assert list(services["web"]["networks"]) == ["proxy"]
    assert "proxy" in services["api"]["networks"]
    subnet = COMPOSE["networks"]["proxy"]["ipam"]["config"][0]["subnet"]
    assert ipaddress.ip_address(_nginx_address()) in ipaddress.ip_network(subnet)
    cmd = (REPO_ROOT / "apps/api/Dockerfile").read_text(encoding="utf-8")
    assert '"--proxy-headers"' in cmd


async def _client_seen(peer: str, forwarded_for: str | None) -> str:
    """The client address the api's app sees for a request from `peer`,
    through uvicorn's proxy-headers middleware configured as compose does."""
    seen: dict = {}

    async def app(scope, receive, send):
        seen["client"] = scope["client"][0]

    middleware = ProxyHeadersMiddleware(app, trusted_hosts=_nginx_address())
    headers = [(b"host", b"api")]
    if forwarded_for is not None:
        headers.append((b"x-forwarded-for", forwarded_for.encode()))
    scope = {
        "type": "http",
        "scheme": "http",
        "client": (peer, 40000),
        "headers": headers,
        "path": "/",
    }
    await middleware(scope, None, None)
    return seen["client"]


@pytest.mark.asyncio
async def test_uvicorn_takes_the_client_from_nginx_and_from_no_one_else():
    browser = "10.89.27.1"
    # Through nginx: the address nginx saw.
    assert await _client_seen(_nginx_address(), browser) == browser
    # Another container on a compose network, sending its own header: ignored.
    assert await _client_seen("172.18.0.9", "203.0.113.7") == "172.18.0.9"
    # The loopback-published api port (Docker's host gateway): ignored too.
    assert await _client_seen("172.18.0.1", "203.0.113.7") == "172.18.0.1"


# --- bodies, streams, the log ------------------------------------------------------


def test_nginx_lets_the_api_decide_on_upload_size():
    """nginx's default (1 MB) would answer uploads between 1 and 50 MB with its
    own 413. Its limit is one above the api's, so the api's 413 is the one sent
    just over the api's limit."""
    limits = re.findall(r"client_max_body_size (\d+)m;", _location("/api/"))
    assert limits == [str(backend_settings.max_upload_size_mb + 1)]


def test_the_streams_are_unbuffered_with_a_timeout_above_the_heartbeat():
    stream = _directives(_location("~ ^/api/(workflow-runs|operations)/[^/]+/events/stream$"))
    assert "proxy_buffering off" in stream
    (timeout,) = [d for d in stream if d.startswith("proxy_read_timeout")]
    assert int(timeout.split()[1].rstrip("s")) >= 60
    # The general /api/ block must not catch the streams first: a regex
    # location wins over a plain prefix, unless the prefix is ^~.
    assert "location ^~ /api/" not in NGINX


def test_the_access_log_never_records_a_query_string():
    formats = dict(re.findall(r"log_format\s+(\w+)\s+((?:'[^']*'\s*)+);", NGINX))
    (used,) = re.findall(r"^\s*access_log\s+\S+\s+(\w+);", NGINX, re.MULTILINE)
    assert used in formats, "the server's access_log uses a format defined here"
    for variable in (
        "$request ",
        '$request"',
        "$request_uri",
        "$args",
        "$query_string",
        "$is_args",
    ):
        assert variable not in formats[used].replace("'", "") + " ", variable
    # The path it does log is $request_uri cut at the first "?".
    assert "$request_path" in formats[used]
    assert re.search(
        r'map \$request_uri \$request_path \{\s*"~\^\(\?<(\w+)>\[\^\?\]\*\)" \$\1;\s*\}', NGINX
    )
