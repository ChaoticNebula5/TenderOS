"""Plan item 75: json_safe turns Neo4j's temporal values into ones a JSON
response can carry, at any depth, and leaves everything else alone.

The route-level case that failed (a tender's own node, with its submission
date) is in tests/route/test_graph_routes.py; this file pins each of the
four types without a database.
"""

import datetime as dt
from typing import Any

from neo4j.time import Date, DateTime, Duration, Time
from pydantic import TypeAdapter
from tenderos_backend.graph_projection.serialize import json_safe


def test_each_temporal_type_becomes_a_value_pydantic_can_serialize():
    row = {
        "submission_date": Date(2027, 3, 31),
        "selected_at": DateTime(2026, 11, 13, 9, 30, 15, 123456789, tzinfo=dt.UTC),
        "opens_at": Time(9, 30, 0),
        "notice_period": Duration(months=1, days=2, seconds=3),
    }

    safe = json_safe(row)

    assert safe == {
        "submission_date": dt.date(2027, 3, 31),
        # Python keeps microseconds; Neo4j's nanoseconds lose their last
        # three digits (the docstring's stated trade).
        "selected_at": dt.datetime(2026, 11, 13, 9, 30, 15, 123456, tzinfo=dt.UTC),
        "opens_at": dt.time(9, 30, 0),
        "notice_period": "P1M2DT3S",
    }
    # The thing that answered 500: serializing the row as the routes'
    # dict[str, Any] response models do.
    assert TypeAdapter(dict[str, Any]).dump_python(safe, mode="json") == {
        "submission_date": "2027-03-31",
        "selected_at": "2026-11-13T09:30:15.123456Z",
        "opens_at": "09:30:00",
        "notice_period": "P1M2DT3S",
    }


def test_nested_rows_are_converted_at_every_depth():
    # The trace's shape: the node, then a list of neighbors, each a dict
    # holding another node.
    trace = {
        "n": {"id": "t1", "submission_date": Date(2027, 3, 31)},
        "node_labels": ["Tender"],
        "neighbors": [
            {
                "relationship": "HAS_CERT",
                "neighbor": {"valid_until": Date(2026, 1, 1)},
                "neighbor_labels": ("Certification",),
            }
        ],
    }

    safe = json_safe(trace)

    assert safe["n"]["submission_date"] == dt.date(2027, 3, 31)
    assert safe["neighbors"][0]["neighbor"]["valid_until"] == dt.date(2026, 1, 1)
    assert safe["neighbors"][0]["neighbor_labels"] == ["Certification"]


def test_values_that_are_not_temporal_are_returned_unchanged():
    row = {"id": "x", "hops": 2, "selected": True, "score": 0.5, "note": None, "tags": ["a"]}

    assert json_safe(row) == row
    assert json_safe([]) == []
    assert json_safe("2027-03-31") == "2027-03-31"
