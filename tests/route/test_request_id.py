"""PR 30: the api's request id (tenderos_api/middleware/request_id.py) and
the ids the permission dependencies bind (dependencies.py).

Every response carries X-Request-ID: the caller's own when it has the W3C
trace-id shape, a new one otherwise (a malicious value is replaced, never
echoed and never logged). The route's lines carry the request id, the
organization and the path's tender."""

import logging
import uuid

import pytest
from fastapi.testclient import TestClient
from tenderos_api import main as api_main
from tenderos_api.routes import tenders as tenders_routes
from tenderos_backend.core.log_context import (
    RECORD_ATTRIBUTE,
    current_log_context,
    is_valid_request_id,
)

_INCOMING = "ab" * 16  # the W3C shape, low-entropy so it reads as no secret
_ROUTE_LOGGER = "pr30.route_spy"


def _request_id(response) -> str:
    value = response.headers["x-request-id"]
    assert is_valid_request_id(value), value
    return value


def test_a_response_carries_a_new_request_id_when_none_was_sent(client: TestClient):
    first = _request_id(client.get("/health/live"))
    second = _request_id(client.get("/health/live"))
    assert first != second


def test_a_well_formed_incoming_request_id_is_kept(client: TestClient):
    response = client.get("/health/live", headers={"X-Request-ID": _INCOMING})
    assert response.headers["x-request-id"] == _INCOMING


@pytest.mark.parametrize(
    "incoming",
    [
        _INCOMING.upper(),
        _INCOMING + "0",
        _INCOMING[:-1],
        str(uuid.UUID(_INCOMING)),
        "0" * 32,
        "a" * 4096,
        "<script>alert(1)</script>",
        "someone@example.com",
        f"{_INCOMING} injected text",
        "../../etc/passwd",
        "",
    ],
)
def test_a_malformed_incoming_request_id_is_replaced_and_never_logged(
    client: TestClient, caplog, incoming: str
):
    with caplog.at_level(logging.DEBUG):
        response = client.get("/health/live", headers={"X-Request-ID": incoming})
    replaced = _request_id(response)
    assert replaced != incoming
    if incoming:
        assert incoming not in caplog.text


def test_an_error_response_carries_it_too(client: TestClient):
    response = client.get(f"/tenders/{uuid.uuid4()}", headers={"X-Request-ID": _INCOMING})
    assert response.status_code in (401, 403)
    assert response.headers["x-request-id"] == _INCOMING


def test_a_cors_preflight_carries_it(client: TestClient):
    """CORSMiddleware answers a preflight itself; the request id layer is
    outside it, so the preflight still carries the header."""
    response = client.options(
        "/health/live",
        headers={
            "Origin": api_main.settings.web_base_url,
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert "access-control-allow-origin" in response.headers
    _request_id(response)


def _tender(client: TestClient, headers: dict[str, str], org_id: str) -> str:
    response = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Request id bid"}, headers=headers
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_the_routes_own_lines_carry_the_request_organization_and_tender(
    client: TestClient, register_actor, monkeypatch, caplog
):
    headers, _, org_id = register_actor()
    tender_id = _tender(client, headers, org_id)
    seen: list[dict[str, str]] = []
    real_get_tender = tenders_routes.tender_service.get_tender

    async def spying_get_tender(tid):
        seen.append(current_log_context())
        logging.getLogger(_ROUTE_LOGGER).info("reading tender %s", tid)
        return await real_get_tender(tid)

    monkeypatch.setattr(tenders_routes.tender_service, "get_tender", spying_get_tender)
    with caplog.at_level(logging.INFO, logger=_ROUTE_LOGGER):
        response = client.get(
            f"/tenders/{tender_id}", headers={**headers, "X-Request-ID": _INCOMING}
        )
    assert response.status_code == 200, response.text
    # Two calls: the permission dependency's own lookup of the tender's
    # organization (before the check passes, so only the request id is
    # bound), then the route's.
    expected = {"request_id": _INCOMING, "org_id": org_id, "tender_id": tender_id}
    assert seen == [{"request_id": _INCOMING}, expected]
    records = [r for r in caplog.records if r.name == _ROUTE_LOGGER]
    assert [getattr(r, RECORD_ATTRIBUTE) for r in records] == seen
