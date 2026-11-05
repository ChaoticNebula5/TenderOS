"""Phase 4 plan item 31: a Neo4j outage is not the caller's bad input.

GET .../graph/trace catches ValueError to answer 400 for a bad node_label.
Driver 5 raised ValueError when Neo4j's name did not resolve, which is what a
stopped Neo4j container looks like on Docker's network, so an outage came back
as 400 "Cannot resolve address neo4j:7687". Driver 6 raises ServiceUnavailable,
so it reaches the catch-all's 500 and the hostname stays private.

A real driver and a real failed lookup: the name is reserved never to resolve
(RFC 6761). Only the freshness check is patched, because it answers from
Postgres first and this tender was never projected.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient
from neo4j import AsyncGraphDatabase
from tenderos_api.main import app
from tenderos_backend.domain.services import graph_service

pytestmark = pytest.mark.asyncio

_UNRESOLVABLE_HOST = "no-such-host.invalid"


@asynccontextmanager
async def _session_on_an_unresolvable_host() -> AsyncIterator:
    driver = AsyncGraphDatabase.driver(
        f"bolt://{_UNRESOLVABLE_HOST}:7687", auth=("neo4j", "unused")
    )
    try:
        async with driver.session() as session:
            yield session
    finally:
        await driver.close()


async def _no_freshness_check(*_args, **_kwargs) -> None:
    return None


async def test_a_neo4j_outage_on_the_trace_route_is_a_500_not_a_400(monkeypatch, register_actor):
    monkeypatch.setattr(graph_service, "get_neo4j_session", _session_on_an_unresolvable_host)
    monkeypatch.setattr(graph_service, "check_freshness", _no_freshness_check)
    client = TestClient(app, raise_server_exceptions=False)
    headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Graph Outage"}, headers=headers
    )
    assert tender.status_code in (200, 201), tender.text

    response = client.get(
        f"/tenders/{tender.json()['id']}/graph/trace",
        params={"node_label": "Requirement", "node_id": "any"},
        headers=headers,
    )

    assert response.status_code == 500, response.text
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert _UNRESOLVABLE_HOST not in response.text


async def test_a_bad_label_on_the_trace_route_is_still_a_400(register_actor):
    # The other side of the same except clause: narrowing it must not lose this.
    client = TestClient(app, raise_server_exceptions=False)
    headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Graph Bad Label"}, headers=headers
    )

    response = client.get(
        f"/tenders/{tender.json()['id']}/graph/trace",
        params={"node_label": "NotALabel", "node_id": "any"},
        headers=headers,
    )

    assert response.status_code == 400, response.text
