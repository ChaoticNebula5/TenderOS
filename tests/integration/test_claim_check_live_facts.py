"""Plan items 89 and 91 (user decisions Q3 and addition 1): the graph's
unsupported-grounded-claim check (D6) judges each cited evidence by its
fact now, with today passed in, and reads the Evidence node's stored copy
only for evidence with no fact. Built on real Neo4j nodes under one test
tender id each, the shapes the projector writes (a fact PROVIDES an
Evidence, which is CITED_IN a ProposalClaim)."""

import uuid
from datetime import date

import pytest
from tenderos_backend.graph_projection import queries
from tenderos_backend.graph_projection.driver import get_session as get_neo4j_session

pytestmark = pytest.mark.asyncio

TODAY = date(2026, 11, 18)


async def _flagged(evidence: dict, fact: tuple[str, dict] | None) -> bool:
    """Whether a grounded claim citing this one evidence is flagged."""
    tender_id = str(uuid.uuid4())
    async with get_neo4j_session() as session:
        try:
            await session.run(
                "CREATE (c:ProposalClaim {id: $cid, tender_id: $tid, grounding_status: 'grounded'}) "
                "CREATE (e:Evidence {id: $eid, tender_id: $tid}) SET e += $evidence "
                "CREATE (e)-[:CITED_IN]->(c)",
                cid=str(uuid.uuid4()),
                eid=str(uuid.uuid4()),
                tid=tender_id,
                evidence=evidence,
            )
            if fact is not None:
                label, props = fact
                await session.run(
                    f"MATCH (e:Evidence {{tender_id: $tid}}) CREATE (f:{label}) "
                    "SET f = $props, f.pr17_tender = $tid CREATE (f)-[:PROVIDES]->(e)",
                    tid=tender_id,
                    props=props,
                )
            rows = await queries.find_unsupported_grounded_claim_contradictions(
                session, tender_id, today=TODAY
            )
        finally:
            await session.run(
                "MATCH (n) WHERE n.tender_id = $tid OR n.pr17_tender = $tid DETACH DELETE n",
                tid=tender_id,
            )
    return bool(rows)


STORED_VALID = {"verified": True, "validity_status": "valid"}
STORED_INVALID = {"verified": False, "validity_status": "expired"}


@pytest.mark.parametrize(
    ("evidence", "fact", "flagged"),
    [
        (
            STORED_VALID,
            ("Certification", {"status": "active", "valid_until": date(2027, 1, 20)}),
            False,
        ),
        (STORED_VALID, ("Certification", {"status": "active", "valid_until": TODAY}), False),
        (
            STORED_VALID,
            ("Certification", {"status": "active", "valid_until": date(2026, 11, 17)}),
            True,
        ),
        (STORED_VALID, ("Certification", {"status": "expired"}), True),
        (STORED_VALID, ("Certification", {"status": "unverified"}), True),
        (STORED_VALID, ("Certification", {"status": "archived"}), True),
        (STORED_INVALID, ("Certification", {"status": "active"}), False),
        (
            STORED_VALID,
            ("Employee", {"status": "active", "profile_review_status": "verified"}),
            False,
        ),
        (STORED_VALID, ("Employee", {"status": "active", "profile_review_status": "stale"}), True),
        (STORED_VALID, None, False),
        (STORED_INVALID, None, True),
    ],
    ids=[
        "in date",
        "its last day",
        "date passed",
        "expired status",
        "unverified",
        "withdrawn",
        "stored invalid, fact valid now",
        "employee verified",
        "employee stale",
        "excerpt stored valid",
        "excerpt stored invalid",
    ],
)
async def test_the_claim_check_judges_the_fact_now(evidence, fact, flagged):
    assert await _flagged(evidence, fact) is flagged
