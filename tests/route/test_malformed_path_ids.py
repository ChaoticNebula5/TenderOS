"""Phase 4 plan item 76: a malformed id in any route's path answers a 4xx,
never a 500.

Every route types its ids as UUID, but FastAPI runs a route's dependencies
before it validates the route's own path parameters, and the permission
dependency (dependencies._resolve_organization_id) parsed the raw path
string with UUID(...). So `POST /gates/not-a-uuid/decision` answered 500
from a ValueError (observed in the item 66 walk).

The routes are found by walking each module's router under
tenderos_api.routes, as test_idempotency_after_auth.py does, so a route
added later is covered without editing this file. Each path parameter is
tried on its own: the malformed value in that one place, a fresh UUID in
any other. The caller is a real registered user with a valid token, so the
request gets past authentication to the code that reads the path.
"""

import importlib
import pkgutil
import re
import uuid

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from tenderos_api import routes as routes_package
from tenderos_api.main import app

_MALFORMED = "not-a-uuid"

# Every path parameter name the walk finds. A new one shows up here in the
# diff; the walk covers it either way.
_EXPECTED_PARAMETER_NAMES = {
    "batch_id",
    "claim_id",
    "compliance_id",
    "document_id",
    "finding_id",
    "gate_id",
    "item_id",
    "knowledge_item_id",
    "operation_id",
    "org_id",
    "pricing_document_id",
    "pricing_finding_id",
    "requirement_id",
    "role_id",
    "run_id",
    "section_id",
    "submission_item_id",
    "tender_id",
}


def _cases() -> list[tuple[str, str, str]]:
    """(method, path, parameter) for every path parameter of every route."""
    found = []
    for info in pkgutil.iter_modules(routes_package.__path__):
        router = getattr(
            importlib.import_module(f"{routes_package.__name__}.{info.name}"), "router", None
        )
        for route in getattr(router, "routes", []):
            if not isinstance(route, APIRoute):
                continue
            for parameter in re.findall(r"\{(\w+)\}", route.path):
                for method in sorted(route.methods or ()):
                    found.append((method, route.path, parameter))
    return sorted(found)


_CASES = _cases()


def test_the_walk_finds_every_id_parameter():
    # 77 routes carried path parameters when this was written (78 cases,
    # one route has two). The floor guards against a walk that silently
    # finds nothing, which is how a first version of the idempotency walk
    # failed.
    assert len(_CASES) >= 78
    assert {parameter for _, _, parameter in _CASES} == _EXPECTED_PARAMETER_NAMES


def _fill(path: str, malformed: str) -> str:
    return re.sub(
        r"\{(\w+)\}",
        lambda m: _MALFORMED if m.group(1) == malformed else str(uuid.uuid4()),
        path,
    )


@pytest.mark.parametrize(
    ("method", "path", "parameter"), _CASES, ids=[f"{m} {p} [{n}]" for m, p, n in _CASES]
)
def test_a_malformed_id_answers_a_4xx_not_a_500(
    register_actor, method: str, path: str, parameter: str
):
    headers, _, _ = register_actor()
    # Its own client: the shared one re-raises a server error inside the
    # test, which would hide the status this test is about.
    client = TestClient(app, headers={"X-TenderOS-Client": "web"}, raise_server_exceptions=False)

    response = client.request(
        method,
        _fill(path, parameter),
        json={} if method in ("POST", "PUT", "PATCH") else None,
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )

    assert 400 <= response.status_code < 500, (response.status_code, response.text)


def test_without_a_token_a_malformed_id_still_gets_the_401_first():
    client = TestClient(app, headers={"X-TenderOS-Client": "web"})
    response = client.post(f"/gates/{_MALFORMED}/decision", json={"decision": "approve"})
    assert response.status_code == 401


def test_a_malformed_gate_id_with_a_token_answers_422_naming_the_parameter(register_actor):
    """The observed case from the item 66 walk, with the exact answer."""
    headers, _, _ = register_actor()
    client = TestClient(app, headers={"X-TenderOS-Client": "web"})
    response = client.post(
        f"/gates/{_MALFORMED}/decision",
        json={"decision": "approve"},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "Malformed gate_id in path: not a UUID"
