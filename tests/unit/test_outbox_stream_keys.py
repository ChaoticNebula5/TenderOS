"""Plan item 55 (decision 0082): the SSE stream writes its payload key into
the SQL as a literal, so migration 026's expression indexes match it under a
generic plan, and it takes that key only from an allowlist.

Compiled for asyncpg, the dialect the app runs, without a database.
"""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from tenderos_api.outbox_stream import STREAM_PAYLOAD_KEYS, _matching
from tenderos_backend.db.models.outbox import OutboxEvent


def _compiled(key: str):
    query = select(OutboxEvent.id).where(*_matching(["SomeEvent"], key, uuid.uuid4()))
    return query.compile(dialect=postgresql.asyncpg.dialect())


def test_the_allowlist_is_exactly_the_two_stream_keys():
    assert STREAM_PAYLOAD_KEYS == {"workflow_run_id", "operation_id"}


@pytest.mark.parametrize("key", sorted(STREAM_PAYLOAD_KEYS))
def test_an_allowed_key_is_in_the_sql_as_a_literal_and_the_id_stays_a_parameter(key):
    compiled = _compiled(key)
    sql = str(compiled)
    assert f"outbox_events.payload ->> '{key}'" in sql
    assert key not in compiled.params.values()
    assert any(isinstance(value, str) and len(value) == 36 for value in compiled.params.values())


@pytest.mark.parametrize(
    "key",
    ["tender_id", "WORKFLOW_RUN_ID", "", "workflow_run_id' OR '1'='1", "operation_id "],
)
def test_any_other_key_is_refused_before_a_statement_exists(key):
    with pytest.raises(ValueError, match="not a stream payload key"):
        _matching(["SomeEvent"], key, uuid.uuid4())
