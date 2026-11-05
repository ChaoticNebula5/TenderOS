"""Phase 4 plan items 31 and 49: who closes a Neo4j driver or session, and what
happens if nobody does.

Neither driver 5.28 nor 6 closes an async driver or session when it is
collected: both only warn, and Python hides that warning by default. So the
API closes its shared driver on shutdown, and pyproject.toml's third
filterwarnings line turns the warning into a test failure.

close_driver() also clears the shared driver, so the next get_session() builds a
fresh one and no test has to remember to patch it.
tests/integration/test_graph_constraints.py does this against a real server.

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
    # The real lifespan, not a patched close_driver. monkeypatch puts back
    # whatever driver this session had, so nothing later is disturbed.
    shared = _new_driver()
    monkeypatch.setattr(driver_module, "_driver", shared)

    with TestClient(api_main.app):
        assert driver_module._driver is shared, "must stay open while the app serves"

    assert driver_module._driver is None
    with pytest.raises(DriverError):
        shared.session()


@pytest.mark.asyncio
async def test_close_driver_closes_and_clears_the_shared_driver(monkeypatch):
    shared = _new_driver()
    monkeypatch.setattr(driver_module, "_driver", shared)

    await driver_module.close_driver()

    assert driver_module._driver is None
    with pytest.raises(DriverError):
        shared.session()


@pytest.mark.asyncio
async def test_the_next_session_after_a_close_builds_a_fresh_driver(monkeypatch):
    first = _new_driver()
    monkeypatch.setattr(driver_module, "_driver", first)
    await driver_module.close_driver()

    # A real session object on the new driver; nothing connects until a query.
    async with driver_module.get_session():
        fresh = driver_module._driver
        assert fresh is not None and fresh is not first
    async with driver_module.get_session():
        assert driver_module._driver is fresh, "reused until the next close"
    await driver_module.close_driver()


@pytest.mark.asyncio
async def test_closing_with_no_driver_open_does_nothing(monkeypatch, caplog):
    monkeypatch.setattr(driver_module, "_driver", None)

    await driver_module.close_driver()
    await driver_module.close_driver()

    assert driver_module._driver is None
    assert "closing the Neo4j driver failed" not in caplog.text


@pytest.mark.asyncio
async def test_a_close_that_raises_still_clears_the_driver_and_does_not_propagate(
    monkeypatch, caplog
):
    # What a driver last used on a loop that has since closed does, measured on
    # 6.4.1 (AttributeError), which is what a test's shutdown can run into.
    class _BrokenDriver:
        async def close(self) -> None:
            raise AttributeError("'NoneType' object has no attribute 'send'")

    monkeypatch.setattr(driver_module, "_driver", _BrokenDriver())

    await driver_module.close_driver()

    assert driver_module._driver is None
    assert "closing the Neo4j driver failed" in caplog.text


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
