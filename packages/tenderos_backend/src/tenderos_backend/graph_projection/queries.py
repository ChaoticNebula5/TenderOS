"""Read-side graph queries: D5 (impact preview), D6 (4 contradiction checks),
D7 (evidence-coverage data gathering), plus a subgraph-count helper used both
by the projector's own step-10 verification and by the "no extra facts"
completeness test. Called directly by domain/services/graph_service.py and,
from B11 onward, by red-team's deterministic pre-checks (E8) - never by an
LLM via tool-calling (see the B10 plan's own "Internal graph-query tools"
interpretation note).
"""

from typing import Any

from neo4j import AsyncSession

_TENDER_SCOPED_LABELS = (
    "Tender",
    "Requirement",
    "Evidence",
    "Compliance",
    "Role",
    "ProposalClaim",
    "ProposalSection",
)


async def count_tender_subgraph(session: AsyncSession, tender_id: str) -> tuple[int, int]:
    """Actual, post-write counts read back from Neo4j - not an echo of how
    many rows the projector attempted to MERGE. Node count covers this
    tender's own 7 tender-scoped labels; relationship count covers every
    relationship with at least one endpoint in that node set (this correctly
    includes USED_IN edges, whose OTHER endpoint is a shared node with no
    tender_id property of its own).
    """
    node_result = await session.run(
        "MATCH (n) WHERE n.tender_id = $tenderId AND ("
        "n:Tender OR n:Requirement OR n:Evidence OR n:Compliance "
        "OR n:Role OR n:ProposalClaim OR n:ProposalSection"
        ") RETURN count(n) AS c",
        tenderId=tender_id,
    )
    node_count = (await node_result.single())["c"]

    rel_result = await session.run(
        "MATCH (n)-[r]-(m) WHERE n.tender_id = $tenderId AND ("
        "n:Tender OR n:Requirement OR n:Evidence OR n:Compliance "
        "OR n:Role OR n:ProposalClaim OR n:ProposalSection"
        ") RETURN count(DISTINCT r) AS c",
        tenderId=tender_id,
    )
    relationship_count = (await rel_result.single())["c"]
    return node_count, relationship_count


async def is_projection_complete(session: AsyncSession, tender_id: str, state_version: int) -> bool:
    """The Neo4j-side half of check_freshness's freshness check (major
    finding #8) - Postgres's own graph_projection_runs.source_state_
    version comparison only proves a projection was ATTEMPTED for the
    current state_version, not that it finished. True only if a Tender
    node exists for this tender_id, AT this state_version (a stale Tender
    node from an older, still-successful run would otherwise pass), with
    nodes.mark_projection_complete's own property set - the genuinely
    last write of a real, un-crashed project_tender run. See that
    function's own docstring for why reusing the Tender node (rather
    than a separate marker) makes a stale flag from a prior run
    structurally impossible, not just unlikely.
    """
    result = await session.run(
        "MATCH (n:Tender {id: $tenderId}) "
        "RETURN n.state_version = $stateVersion AND n.projection_complete = true AS complete",
        tenderId=tender_id,
        stateVersion=state_version,
    )
    record = await result.single()
    return record is not None and bool(record["complete"])


async def preview_impact(
    session: AsyncSession, tender_id: str, knowledge_item_id: str, max_hops: int
) -> list[dict[str, Any]]:
    """D5's impact preview. max_hops is NEVER derived from request data - the
    caller (graph_service.py) always passes settings.graph_max_hops, a
    server-side config value, so this f-string interpolation of the hop
    bound is safe: it's a validated int from Settings, never raw user input
    (D5's own implementation note; the request schema has no hop field at
    all to begin with).

    B16: the USED_IN match is OPTIONAL, not required - was a hard MATCH,
    which meant a knowledge item that genuinely backs a real compliance
    decision (via PROVIDES, materialized automatically during compliance
    evaluation, independent of selection - see KNOWN_LIMITATIONS.md's
    USED_IN structural-gap entry) but was never explicitly selected/
    deselected for this tender had no USED_IN edge at all and returned
    nothing here, even though it demonstrably affects this tender's own
    decisions. Safe to loosen: `fact` is already anchored to one specific
    node via knowledge_item_id (an indexed lookup, not a scan), and the
    WHERE clause below already does 100% of the cross-tenant safety work
    by filtering the DESTINATION node's own tender_id - traced directly,
    not assumed, and covered by a real cross-org test
    (test_graph_projection.py) alongside the positive case. currentlySelected
    reads null (not true/false) for an item with no USED_IN edge at all -
    a real, meaningful third state ("never selected for this tender"),
    not a defect.
    """
    # B17 Phase 0: the path is DIRECTED (`->`), as D5 (:1955) specifies.
    # Every D2 edge points downstream from the fact, so a directed walk is
    # exactly "what depends on this fact". It was undirected from B10 until
    # B17 Phase 0, which let a fact with no dependents walk back through the
    # shared Tender node into its siblings' evidence (16 vs 0 affected nodes
    # for one employee on real-run data) - see KNOWN_LIMITATIONS.md and
    # test_employee_with_no_evidence_affects_nothing_even_beside_one_that_does.
    result = await session.run(
        "MATCH (fact {knowledge_item_id: $knowledgeItemId}) "
        "OPTIONAL MATCH (fact)-[u:USED_IN]->(:Tender {id: $tenderId}) "
        f"MATCH path = (fact)-[*1..{int(max_hops)}]->(affected) "
        "WHERE affected.tender_id = $tenderId AND ("
        "  affected:Role OR affected:Compliance OR affected:Requirement "
        "  OR affected:ProposalClaim OR affected:ProposalSection"
        ") "
        "RETURN DISTINCT labels(affected) AS labels, affected, "
        "min(length(path)) AS hops, u.selected AS currentlySelected "
        "ORDER BY hops",
        knowledgeItemId=knowledge_item_id,
        tenderId=tender_id,
    )
    return [record.data() async for record in result]


async def find_expired_certification_contradictions(
    session: AsyncSession, tender_id: str
) -> list[dict[str, Any]]:
    # D6: expired certification still backing an active compliant decision.
    result = await session.run(
        "MATCH (cert:Certification)-[:PROVIDES]->(evidence:Evidence)"
        "-[:SUPPORTS]->(compliance:Compliance)-[:DECIDES]->(requirement:Requirement) "
        "WHERE compliance.tender_id = $tenderId "
        "AND compliance.status IN ['fully_compliant','partially_compliant'] "
        "AND cert.valid_until IS NOT NULL "
        "MATCH (tender:Tender {id: $tenderId}) "
        "WHERE tender.submission_date IS NOT NULL AND cert.valid_until < tender.submission_date "
        "RETURN cert, evidence, compliance, requirement",
        tenderId=tender_id,
    )
    return [record.data() async for record in result]


async def find_deselected_fact_still_used_contradictions(
    session: AsyncSession, tender_id: str
) -> list[dict[str, Any]]:
    # D6: USED_IN.selected=false but the fact still provides evidence that
    # supports a compliance decision or is cited in a proposal claim.
    result = await session.run(
        "MATCH (fact)-[u:USED_IN {selected: false}]->(:Tender {id: $tenderId}) "
        "MATCH (fact)-[:PROVIDES]->(evidence:Evidence) "
        "WHERE evidence.tender_id = $tenderId "
        "MATCH (evidence)-[rel:SUPPORTS|CITED_IN]->(target) "
        "RETURN fact, evidence, type(rel) AS via, target",
        tenderId=tender_id,
    )
    return [record.data() async for record in result]


async def find_removed_employee_still_committed_contradictions(
    session: AsyncSession, tender_id: str
) -> list[dict[str, Any]]:
    # D6: employee deselected but still FILLS a role or NAMED_IN a claim.
    result = await session.run(
        "MATCH (employee:Employee)-[u:USED_IN {selected: false}]->(:Tender {id: $tenderId}) "
        "MATCH (employee)-[rel:FILLS|NAMED_IN]->(target) "
        "WHERE target.tender_id = $tenderId "
        "RETURN employee, type(rel) AS via, target",
        tenderId=tender_id,
    )
    return [record.data() async for record in result]


async def find_unsupported_grounded_claim_contradictions(
    session: AsyncSession, tender_id: str
) -> list[dict[str, Any]]:
    # D6: a claim persisted as grounding_status='grounded' with no valid,
    # verified CITED_IN evidence path remaining (evidence can have expired or
    # been invalidated after the claim was drafted).
    result = await session.run(
        "MATCH (claim:ProposalClaim {tender_id: $tenderId, grounding_status: 'grounded'}) "
        "OPTIONAL MATCH (claim)<-[:CITED_IN]-(evidence:Evidence "
        "{verified: true, validity_status: 'valid'}) "
        "WITH claim, count(evidence) AS valid_evidence_count "
        "WHERE valid_evidence_count = 0 "
        "RETURN claim",
        tenderId=tender_id,
    )
    return [record.data() async for record in result]


async def evidence_coverage_rows(
    session: AsyncSession, compliance_entry_id: str
) -> list[dict[str, Any]]:
    """D7 step 1-3, read side only: every evidence link attached to a
    compliance decision, with the fields evaluate_evidence_coverage
    (domain/services/graph_service.py) needs to decide valid_path_count -
    verified, validity_status, and (for shared-node-backed evidence) the
    knowledge item's own status/valid_until and current USED_IN.selected.
    Document-excerpt evidence with no knowledge_item_id has no fact node, so
    those 3 fields come back null - still a valid (if permanently unowned)
    evidence row for the decision function to see.
    """
    result = await session.run(
        "MATCH (evidence:Evidence)-[:SUPPORTS]->(compliance:Compliance {id: $complianceId}) "
        "OPTIONAL MATCH (fact)-[:PROVIDES]->(evidence) "
        "OPTIONAL MATCH (fact)-[u:USED_IN]->(:Tender {id: compliance.tender_id}) "
        "RETURN evidence.id AS evidence_id, evidence.verified AS verified, "
        "evidence.validity_status AS validity_status, "
        "fact.status AS knowledge_item_status, fact.valid_until AS valid_until, "
        "u.selected AS selected",
        complianceId=compliance_entry_id,
    )
    return [record.data() async for record in result]
