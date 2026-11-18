"""Plan items 89 and 91 (addition 1): the graph readers' Cypher fragments
agree with core/evidence_validity.py on every case of the shared table
(tests/evidence_validity_cases.py), on real Neo4j nodes: FACT_COUNTS_CYPHER
against evidence_validity(...).counts for a fact node (an Employee node for
the employee cases, a Certification node otherwise), and
STORED_EVIDENCE_COUNTS_CYPHER for evidence with no fact (a document
excerpt), the verified cases included. Each test's nodes carry its own
marker and are deleted after it."""

import uuid

import pytest
from evidence_validity_cases import CASES, TODAY, Case
from tenderos_backend.core.evidence_validity import (
    FACT_COUNTS_CYPHER,
    STORED_EVIDENCE_COUNTS_CYPHER,
    evidence_validity,
)
from tenderos_backend.graph_projection.driver import get_session as get_neo4j_session

pytestmark = pytest.mark.asyncio


async def _fact_counts(case: Case) -> bool:
    marker = uuid.uuid4().hex
    label = "Employee" if case.is_employee else "Certification"
    props = {"pr17_parity": marker, "status": case.status, "valid_until": case.valid_until}
    if case.profile_review_status is not None:
        props["profile_review_status"] = case.profile_review_status
    async with get_neo4j_session() as session:
        try:
            await session.run(f"CREATE (fact:{label}) SET fact = $props", props=props)
            result = await session.run(
                f"MATCH (fact {{pr17_parity: $marker}}) RETURN {FACT_COUNTS_CYPHER} AS counts",
                marker=marker,
                today=TODAY,
            )
            record = await result.single()
        finally:
            await session.run("MATCH (n {pr17_parity: $marker}) DELETE n", marker=marker)
    assert record is not None
    return record["counts"]


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
async def test_the_fact_fragment_agrees_with_the_function(case: Case):
    expected = evidence_validity(
        status=case.status,
        valid_until=case.valid_until,
        today=TODAY,
        is_employee=case.is_employee,
        profile_review_status=case.profile_review_status,
    ).counts
    assert expected is case.counts
    assert await _fact_counts(case) is expected


@pytest.mark.parametrize(
    ("verified", "validity_status", "counts"),
    [
        (True, "valid", True),
        (False, "valid", False),
        (True, "expired", False),
        (True, "invalid", False),
        (True, "unknown", False),
        (None, "valid", False),
        (True, None, False),
    ],
)
async def test_the_stored_copy_fragment_for_evidence_with_no_fact(
    verified, validity_status, counts
):
    marker = uuid.uuid4().hex
    props = {"pr17_parity": marker, "verified": verified, "validity_status": validity_status}
    async with get_neo4j_session() as session:
        try:
            await session.run(
                "CREATE (evidence:Evidence) SET evidence = $props",
                props={k: v for k, v in props.items() if v is not None},
            )
            result = await session.run(
                "MATCH (evidence:Evidence {pr17_parity: $marker}) "
                f"RETURN {STORED_EVIDENCE_COUNTS_CYPHER} AS counts",
                marker=marker,
            )
            record = await result.single()
        finally:
            await session.run("MATCH (n {pr17_parity: $marker}) DELETE n", marker=marker)
    assert record is not None and record["counts"] is counts
