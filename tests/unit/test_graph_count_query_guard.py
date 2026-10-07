"""B17 Phase 0b runtime behaviour change (mypy narrowing in
graph_projection/queries.py): count_tender_subgraph used to index
`(await result.single())["c"]` directly. A count with no grouping keys always
returns one row, so a missing row is a driver/server anomaly - which used to
surface as an anonymous TypeError. It now raises UnexpectedEmptyResultError
naming the query.
"""

import asyncio

import pytest
from tenderos_backend.graph_projection.queries import (
    UnexpectedEmptyResultError,
    count_tender_subgraph,
)


class _EmptyResult:
    async def single(self):
        return None


class _CountResult:
    def __init__(self, value: int) -> None:
        self._value = value

    async def single(self):
        return {"c": self._value}


class _Session:
    def __init__(self, *results) -> None:
        self._results = list(results)

    async def run(self, *_args, **_kwargs):
        return self._results.pop(0)


def test_counts_are_returned_from_the_single_row():
    session = _Session(_CountResult(7), _CountResult(11))
    assert asyncio.run(count_tender_subgraph(session, "t-1")) == (7, 11)


def test_a_missing_count_row_raises_a_named_error_not_a_type_error():
    session = _Session(_EmptyResult(), _CountResult(0))
    with pytest.raises(UnexpectedEmptyResultError, match="node count for tender t-1"):
        asyncio.run(count_tender_subgraph(session, "t-1"))
