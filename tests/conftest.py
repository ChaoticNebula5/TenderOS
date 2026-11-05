"""Suite-wide guard: a database connection dropped without being returned to
its pool fails the test it belongs to, and the failure says where that
connection was checked out.

Why (Phase 4 plan item 29): CI on cc74b1a (run 39775453912) passed with
`SAWarning: The garbage collector is trying to clean up non-checked-in
connection`, raised during test_readiness_service.py::test_clean_tender_is_ready.
SQLAlchemy only notices a dropped connection when the object holding it is
freed, and a plain warning never fails a run.

How a leak fails a test:
  - SQLAlchemy warns from inside a garbage-collection callback, where an
    exception cannot reach the test. pytest reports such an exception as
    "unraisable" at the end of the test phase it happened in, and
    pyproject.toml makes both SAWarning and that report errors.
  - A connection dropped in a plain local variable is freed by reference
    counting as soon as its test's coroutine finishes, so it fails that
    test. One kept alive by a reference cycle waits for the cyclic
    collector, which runs whenever allocation triggers it, in whatever
    test is running then (the CI case: the warning came from inside neo4j
    code). The fixture below runs the collector after every test, so such a
    leak fails at the end of the test that left it, or earlier.
  - A connection still referenced by something alive (a pending task, a
    module-level object) is not garbage yet; it is reported when that
    holder lets go, in a later test.

Where it came from: SQLAlchemy logs an ERROR on its pool logger just
before it warns, and pytest prints captured log records with every failure.
A filter on those loggers appends the stack (this repo's frames only) that
checked the connection out, recorded through SQLAlchemy's public pool
"checkout" event.
"""

import gc
import logging
import os
import re
import sys
import traceback
from pathlib import Path

import greenlet
import pytest
import pytest_asyncio
from models_schema import rebuild_from_models
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool, NullPool, Pool, QueuePool, StaticPool
from tenderos_backend.core.config import settings
from tenderos_backend.graph_projection.driver import ensure_constraints

_LEAK_TEXT = "non-checked-in connection"
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
_VENV = f"{os.sep}.venv{os.sep}"
_ADDRESS = re.compile(r"object at (0x[0-9a-fA-F]+)")
# Checkout stacks of connections currently out of their pool, keyed by the
# id of the driver connection, which is the address the warning prints.
_checked_out: dict[int, list[tuple[str, int, str]]] = {}


def _driver_id(dbapi_connection) -> int:
    return id(getattr(dbapi_connection, "driver_connection", dbapi_connection))


@event.listens_for(Pool, "checkout")
def _remember_checkout(dbapi_connection, connection_record, connection_proxy) -> None:
    # With an async engine, SQLAlchemy runs this inside a greenlet whose own
    # stack holds only SQLAlchemy frames; the code that asked for the
    # connection is in the parent greenlet, suspended at this moment, so its
    # frames are read from there. A sync engine has no parent: plain stack.
    parent = greenlet.getcurrent().parent
    frame = (
        parent.gr_frame if parent is not None and parent.gr_frame is not None else sys._getframe(1)
    )
    # This repo's frames only, innermost twelve, as (file, line, function):
    # cheap to collect on every checkout. Source lines are read only when a
    # leak is reported.
    ours: list[tuple[str, int, str]] = []
    while frame is not None and len(ours) < 12:
        filename = frame.f_code.co_filename
        if filename.startswith(_REPO_ROOT) and _VENV not in filename and filename != __file__:
            ours.append((filename, frame.f_lineno, frame.f_code.co_name))
        frame = frame.f_back
    _checked_out[_driver_id(dbapi_connection)] = ours[::-1]


@event.listens_for(Pool, "checkin")
def _forget_checkin(dbapi_connection, connection_record) -> None:
    if dbapi_connection is not None:
        _checked_out.pop(_driver_id(dbapi_connection), None)


class _AddCheckoutStack(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = str(record.msg)
        if _LEAK_TEXT in message:
            address = _ADDRESS.search(message)
            frames = _checked_out.pop(int(address.group(1), 16), None) if address else None
            stack = "".join(
                traceback.format_list([traceback.FrameSummary(*f) for f in frames or []])
            )
            record.msg = message + (
                f"\nChecked out at (this repo's frames):\n{stack}"
                if stack
                else "\nChecked out at: not recorded"
            )
        return True


# SQLAlchemy's pool loggers are named after each pool class; a filter only
# applies to records logged on the logger it is attached to.
for _pool_class in (AsyncAdaptedQueuePool, QueuePool, NullPool, StaticPool):
    logging.getLogger(f"{_pool_class.__module__}.{_pool_class.__name__}").addFilter(
        _AddCheckoutStack()
    )


@pytest.fixture(scope="session", autouse=True)
def _freeze_objects_from_before_the_tests():
    # A full collection walks every live object, and after collection the
    # heap holds torch, transformers and the whole app: about 0.26 s per
    # collection here (measured: the suite took 495 s with one per test,
    # 302 s without). Objects that exist before the first test cannot be a
    # test's leak, so they are moved to the permanent generation that
    # collections skip; everything a test creates is still collected.
    gc.collect()
    gc.freeze()
    yield
    gc.unfreeze()


@pytest.fixture(autouse=True)
def _collect_after_each_test(_freeze_objects_from_before_the_tests):
    yield
    gc.collect()


@pytest_asyncio.fixture(scope="session")
async def _graph_constraints():
    # Phase 4 plan item 49: Neo4j's constraints, as `migrate` applies them in the
    # stack, so a fixture writing one key twice fails here as production would.
    # Requested with _models_schema, by the same conftests. They outlive the run
    # on the shared Neo4j, which is what the stack wants too; DETACH DELETE
    # leaves them alone.
    await ensure_constraints()


@pytest_asyncio.fixture(scope="session")
async def _models_schema():
    # Phase 4 plan item 42: the test database is emptied and built from the
    # models (tests/models_schema.py, guarded on this same connection). Not
    # autouse, so unit tests never touch the database: the integration and
    # route conftests each request it from their own autouse _schema. A
    # session fixture defined here is one instance for the whole run, so a
    # run covering both directories rebuilds once, not once per directory.
    engine = create_async_engine(settings.postgres_dsn)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(rebuild_from_models)
    finally:
        await engine.dispose()
