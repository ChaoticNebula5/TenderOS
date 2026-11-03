"""A client that disconnects before its request body is read gets one quiet
INFO line and a 499, never an unhandled 500 with a traceback.

Main's CI hit this twice (runs 40116721087 and 40163720306): the E2E
navigated away while POST /tenders/{id}/workflow-runs was in flight, the
idempotency check's body read raised ClientDisconnect, and the catch-all
logged it as an unhandled error. The app is driven here at the ASGI level
with a receive() that answers http.disconnect, the message a server passes
on when the client has gone, through both places that read a body: the
idempotency check (a keyed route with no body parameter) and FastAPI's own
read (a keyed route with a JSON body).
"""

import logging
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from tenderos_api.client_disconnect import CLIENT_CLOSED_REQUEST
from tenderos_api.main import app
from tenderos_backend.db.models.idempotency import IdempotencyRecord
from tenderos_backend.db.unit_of_work import UnitOfWork

pytestmark = pytest.mark.asyncio


async def _post_with_a_disconnect(path: str, headers: dict[str, str]) -> list[dict]:
    """POST through the whole app, the client gone before any body arrives.
    Returns the ASGI messages the app sent."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(name.lower().encode(), value.encode()) for name, value in headers.items()],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }

    async def receive() -> dict:
        return {"type": "http.disconnect"}

    sent: list[dict] = []

    async def send(message: dict) -> None:
        sent.append(message)

    await app(scope, receive, send)
    return sent


async def _idempotency_record_count() -> int:
    async with UnitOfWork() as uow:
        return await uow.session.scalar(select(func.count()).select_from(IdempotencyRecord))


def _create_tender(client: TestClient, headers: dict[str, str], org_id: str) -> str:
    response = client.post(
        f"/organizations/{org_id}/tenders",
        json={"name": f"Disconnect {uuid.uuid4().hex[:8]}"},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _assert_one_quiet_line(caplog: pytest.LogCaptureFixture, path: str) -> None:
    lines = [r for r in caplog.records if r.name == "tenderos_api.client_disconnect"]
    assert [r.getMessage() for r in lines] == [
        f"client disconnected before its request was read: POST {path}"
    ]
    assert lines[0].levelno == logging.INFO
    assert lines[0].exc_info is None
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors == [], [r.getMessage() for r in errors]


async def test_a_disconnect_at_the_idempotency_body_read_is_one_info_line(
    client: TestClient, register_actor, caplog: pytest.LogCaptureFixture
):
    headers, _, org_id = register_actor()
    tender_id = _create_tender(client, headers, org_id)
    records_before = await _idempotency_record_count()
    caplog.set_level(logging.INFO)

    path = f"/tenders/{tender_id}/workflow-runs"
    sent = await _post_with_a_disconnect(
        path,
        {
            **headers,
            "Idempotency-Key": str(uuid.uuid4()),
            "Content-Type": "application/json",
            "Content-Length": "2",
        },
    )

    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == CLIENT_CLOSED_REQUEST
    _assert_one_quiet_line(caplog, path)
    # The body is read before the key is claimed, so nothing is left behind.
    assert await _idempotency_record_count() == records_before


async def test_a_disconnect_while_fastapi_reads_a_json_body_is_one_info_line(
    register_actor, caplog: pytest.LogCaptureFixture
):
    headers, _, org_id = register_actor()
    records_before = await _idempotency_record_count()
    caplog.set_level(logging.INFO)

    path = f"/organizations/{org_id}/tenders"
    sent = await _post_with_a_disconnect(
        path,
        {
            **headers,
            "Idempotency-Key": str(uuid.uuid4()),
            "Content-Type": "application/json",
            "Content-Length": "20",
        },
    )

    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == CLIENT_CLOSED_REQUEST
    _assert_one_quiet_line(caplog, path)
    assert await _idempotency_record_count() == records_before
