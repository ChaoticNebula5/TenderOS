"""Phase 4 plan item 31: who closes a Neo4j driver or session, and what
happens if nobody does.

Neither driver 5.28 nor 6 closes an async driver or session when it is
collected: both only warn, and Python hides that warning by default. So the
API closes its shared driver on shutdown, and pyproject.toml's third
filterwarnings line turns the warning into a test failure.

No test here reaches a server: creating a driver or a session opens no
connection until the first query.
"""

import gc
import sys

import pytest
import tenderos_api.main as api_main
from fastapi.testclient import TestClient
from neo4j import AsyncGraphDatabase
from neo4j.exceptions import DriverError
from tenderos_backend.graph_projection import driver as driver_module

_NEVER_CONTACTED = "bolt://192.0.2.1:7687"


def _new_driver():
    return AsyncGraphDatabase.driver(_NEVER_CONTACTED, auth=("neo4j", "unused"))


@pytest.fixture
def unraisable(monkeypatch) -> list:
    """What a finalizer raised. A warning turned into an error inside __del__
    cannot propagate, so it reaches sys.unraisablehook instead. Replacing the
    hook for one test lets the test read what would otherwise fail it."""
    seen: list = []
    monkeypatch.setattr(sys, "unraisablehook", seen.append)
    return seen


def _resource_warnings(seen: list) -> list[str]:
    return [str(u.exc_value) for u in seen if isinstance(u.exc_value, ResourceWarning)]


def test_api_shutdown_closes_the_shared_driver(monkeypatch):
    # Patched, not real: closing the real singleton would break every later
    # test that uses it, because a closed driver cannot be reused.
    closed: list[str] = []

    async def record_close() -> None:
        closed.append("closed")

    monkeypatch.setattr(api_main, "close_driver", record_close)
    with TestClient(api_main.app):
        assert closed == [], "must stay open while the app serves"
    assert closed == ["closed"]


@pytest.mark.asyncio
async def test_close_driver_closes_the_shared_driver(monkeypatch):
    shared = _new_driver()
    monkeypatch.setattr(driver_module, "driver", shared)

    await driver_module.close_driver()

    with pytest.raises(DriverError):
        shared.session()


def test_a_driver_collected_unclosed_fails_the_test(unraisable):
    leaked = _new_driver()
    del leaked
    gc.collect()

    warnings = _resource_warnings(unraisable)
    assert len(warnings) == 1, unraisable
    assert "Driver" in warnings[0]


@pytest.mark.asyncio
async def test_a_session_collected_unclosed_fails_the_test(unraisable):
    driver = _new_driver()
    leaked = driver.session()
    del leaked
    gc.collect()

    warnings = _resource_warnings(unraisable)
    assert len(warnings) == 1, unraisable
    assert "AsyncSession" in warnings[0]
    await driver.close()


@pytest.mark.asyncio
async def test_a_driver_and_session_closed_properly_fail_nothing(unraisable):
    driver = _new_driver()
    async with driver.session():
        pass
    await driver.close()
    del driver
    gc.collect()

    assert _resource_warnings(unraisable) == []
