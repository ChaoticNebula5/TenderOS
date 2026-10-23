"""Phase 4 plan item 34: an expired access token gets its 401 before the
Idempotency-Key is claimed, on every route that takes a key.

The web app now refreshes the token on a 401 and retries the same POST
with the same key (apps/web/src/api/client.ts). That retry is safe only if
the first attempt never claimed the key: a claimed key would answer the
retry with IDEMPOTENCY_IN_PROGRESS for the 60 s lease.

Counting idempotency rows after the 401 would not prove it: a key claimed
first would be released again when the 401 is thrown into the dependency
(idempotency.py, the release on any exception), leaving no row either way.
So the claim itself is replaced by a spy that answers 418, and the test
asserts the spy is never reached. The control shows the spy sits on the
live path: with a valid token the same request reaches it.

The routes are found by walking the app's dependency tree, not listed by
hand, so a route that later takes a key is covered without editing this
file; the expected list below makes an addition visible in the diff.
"""

import importlib
import pkgutil
import re
import uuid
from datetime import timedelta

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from tenderos_api import idempotency
from tenderos_api import routes as routes_package
from tenderos_backend.core import security

_EXPECTED = {
    ("POST", "/organizations/{org_id}/tenders"),
    ("POST", "/gates/{gate_id}/decision"),
    ("POST", "/tenders/{tender_id}/graph/project"),
    ("PUT", "/tenders/{tender_id}/knowledge-selections/{knowledge_item_id}"),
    ("POST", "/tenders/{tender_id}/proposal/generate"),
    ("POST", "/roles/{role_id}/select-candidate"),
    ("POST", "/tenders/{tender_id}/workflow-runs"),
}


def _takes_a_key(dependant) -> bool:
    for sub in dependant.dependencies:
        qualname = getattr(sub.call, "__qualname__", "")
        if qualname.startswith("get_idempotency_context.<locals>"):
            return True
        if _takes_a_key(sub):
            return True
    return False


def _routes_taking_a_key() -> list[tuple[str, str]]:
    """Read from each module's own router under tenderos_api.routes, not
    from app.routes: FastAPI 0.148 keeps an included router as one private
    _IncludedRouter entry there, so app.routes holds no APIRoute of ours
    (a first version of this walk found none). main.py includes every
    router with no extra prefix, so these paths are the served ones; a
    router left out of main.py would answer 404 below, not 401."""
    found = []
    for info in pkgutil.iter_modules(routes_package.__path__):
        router = getattr(
            importlib.import_module(f"{routes_package.__name__}.{info.name}"), "router", None
        )
        for route in getattr(router, "routes", []):
            if isinstance(route, APIRoute) and _takes_a_key(route.dependant):
                for method in sorted(route.methods or ()):
                    found.append((method, route.path))
    return sorted(found)


_ROUTES = _routes_taking_a_key()


def test_the_routes_found_are_the_seven_expected():
    assert set(_ROUTES) == _EXPECTED


@pytest.fixture
def claim_spy(monkeypatch):
    """Stands in for idempotency._claim_or_replay, which the dependency
    looks up at call time. Records the call and answers 418, a status no
    route uses, so a response says which side of the claim it came from."""
    calls: list[str] = []

    async def _spy(request, operation, required):
        calls.append(operation)
        raise HTTPException(status_code=418, detail="claim reached")

    monkeypatch.setattr(idempotency, "_claim_or_replay", _spy)
    return calls


def _fill(path: str, **known: str) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: known.get(m.group(1), str(uuid.uuid4())), path)


@pytest.mark.parametrize(("method", "path"), _ROUTES, ids=[f"{m} {p}" for m, p in _ROUTES])
def test_an_expired_token_is_rejected_before_the_key_is_claimed(
    client: TestClient, register_actor, claim_spy, method: str, path: str
):
    _, user_id, org_id = register_actor()
    expired = security._create_token(uuid.UUID(user_id), "access", timedelta(seconds=-60))

    response = client.request(
        method,
        _fill(path, org_id=org_id),
        json={},
        headers={"Authorization": f"Bearer {expired}", "Idempotency-Key": str(uuid.uuid4())},
    )

    assert response.status_code == 401, response.text
    assert claim_spy == []


def test_control_a_valid_token_reaches_the_claim(client: TestClient, register_actor, claim_spy):
    headers, _, org_id = register_actor()

    response = client.post(
        f"/organizations/{org_id}/tenders",
        json={"name": "Control"},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )

    assert response.status_code == 418, response.text
    assert claim_spy == ["create_tender"]
