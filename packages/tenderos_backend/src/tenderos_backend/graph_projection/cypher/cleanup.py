"""D3 step 5: delete only this tender's own tender-scoped nodes before
recreating them fresh each projection run.

This is the single most safety-critical query in the whole block. The WHERE
clause's label filter is what makes it safe: it matches ONLY the 7
tender-scoped labels (Tender, Requirement, Evidence, Compliance, Role,
ProposalClaim, ProposalSection), every one of which carries a tender_id
property set at creation time. Employee/Certification/Project/Capability
carry no tender_id property at all (they're organization-scoped, D1), so
even if this query's label filter were ever accidentally dropped, those 4
labels still could not match `n.tender_id = $tenderId` - there is no such
property on them to compare. DETACH DELETE also removes every relationship
attached to a deleted node, including USED_IN edges pointing OUT from a
surviving shared node - those get recreated fresh in the same run by
merge_used_in, so this is not a loss (see projector.py's step ordering).
"""

from neo4j import AsyncSession


async def delete_tender_scoped_subgraph(session: AsyncSession, tender_id: str) -> None:
    await session.run(
        "MATCH (n) "
        "WHERE n.tender_id = $tenderId "
        "AND ("
        "  n:Tender OR n:Requirement OR n:Evidence OR n:Compliance "
        "  OR n:Role OR n:ProposalClaim OR n:ProposalSection"
        ") "
        "DETACH DELETE n",
        tenderId=tender_id,
    )
