"""domain/services/graph_service.py::get_graph_trace - the node_label
allowlist check the background security review caught missing. Confirms the
check runs BEFORE any database access (no tender needs to exist, no Neo4j
connection needs to succeed, for an unknown label to be rejected) and that
every one of D1's 11 real labels is accepted by the allowlist.
"""

import uuid

import pytest
from tenderos_backend.domain.services.graph_service import _ALLOWED_TRACE_LABELS, get_graph_trace
from tenderos_backend.graph_projection.driver import SHARED_LABELS, TENDER_SCOPED_LABELS

pytestmark = pytest.mark.asyncio


async def test_unknown_label_rejected_before_any_database_access():
    # No tender exists for this random UUID and no Neo4j session is opened -
    # if the label check ran after check_freshness or after the Cypher
    # MATCH, this would fail with a different exception (TenderNotFoundError
    # or a connection error) rather than the intended ValueError.
    with pytest.raises(ValueError, match="Unknown graph node label"):
        await get_graph_trace(uuid.uuid4(), "'; MATCH (n) DETACH DELETE n; //", "some-id")


async def test_every_real_label_is_in_the_allowlist():
    for label in (*TENDER_SCOPED_LABELS, *SHARED_LABELS):
        assert label in _ALLOWED_TRACE_LABELS


async def test_allowlist_has_exactly_11_labels():
    # D1's own count - 7 tender-scoped + 4 shared. A regression that
    # silently drops or duplicates a label would still pass the two tests
    # above; this pins the total.
    assert len(_ALLOWED_TRACE_LABELS) == 11
