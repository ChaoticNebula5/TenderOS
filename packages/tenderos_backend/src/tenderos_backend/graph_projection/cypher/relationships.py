"""Parameterized MERGE statements for D2's 13 relationship types.

Same UNWIND-batch shape as cypher/nodes.py. For USED_IN and PROVIDES, the
"fact" endpoint can be any of the 4 shared labels (Employee/Certification/
Project/Capability) - matched by `knowledge_item_id` alone with no label
filter, same as D5's own conceptual Cypher (`MATCH (fact {knowledge_item_id:
$knowledgeItemId})`). This is safe because knowledge_item_id values are
unique across all 4 shared labels (they all key off the single
knowledge_items.id UUID space - one knowledge item is exactly one item_type).
"""

from typing import Any

from neo4j import AsyncSession


async def merge_holds(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    # Employee -HOLDS-> Certification
    await session.run(
        "UNWIND $rows AS row "
        "MATCH (employee:Employee {knowledge_item_id: row.employee_id}) "
        "MATCH (cert:Certification {knowledge_item_id: row.certification_id}) "
        "MERGE (employee)-[r:HOLDS]->(cert) "
        "SET r += row.props",
        rows=rows,
    )


async def merge_worked_on(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    # Employee -WORKED_ON-> Project. LLM-inferred - carries confidence/verified.
    await session.run(
        "UNWIND $rows AS row "
        "MATCH (employee:Employee {knowledge_item_id: row.employee_id}) "
        "MATCH (project:Project {knowledge_item_id: row.project_id}) "
        "MERGE (employee)-[r:WORKED_ON]->(project) "
        "SET r += row.props",
        rows=rows,
    )


async def merge_demonstrates(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    # Project -DEMONSTRATES-> Capability. LLM-inferred - carries confidence/verified.
    await session.run(
        "UNWIND $rows AS row "
        "MATCH (project:Project {knowledge_item_id: row.project_id}) "
        "MATCH (capability:Capability {knowledge_item_id: row.capability_id}) "
        "MERGE (project)-[r:DEMONSTRATES]->(capability) "
        "SET r += row.props",
        rows=rows,
    )


async def merge_used_in(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    # fact -USED_IN-> Tender. Persistent: MERGE'd every run regardless of
    # `selected`, including selected=false - never deleted by the cleanup
    # step. This is the property-flip pattern, not delete-and-recreate.
    await session.run(
        "UNWIND $rows AS row "
        "MATCH (fact {knowledge_item_id: row.knowledge_item_id}) "
        "MATCH (tender:Tender {id: row.tender_id}) "
        "MERGE (fact)-[r:USED_IN]->(tender) "
        "SET r += row.props",
        rows=rows,
    )


async def merge_fills(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    # Employee -FILLS-> Role. Only human_selected=true role_matches produce
    # this edge at all (projector.py filters before calling this).
    await session.run(
        "UNWIND $rows AS row "
        "MATCH (employee:Employee {knowledge_item_id: row.employee_id}) "
        "MATCH (role:Role {id: row.role_id}) "
        "MERGE (employee)-[r:FILLS]->(role) "
        "SET r += row.props",
        rows=rows,
    )


async def merge_satisfies(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    # Role -SATISFIES-> Requirement
    await session.run(
        "UNWIND $rows AS row "
        "MATCH (role:Role {id: row.role_id}) "
        "MATCH (requirement:Requirement {id: row.requirement_id}) "
        "MERGE (role)-[r:SATISFIES]->(requirement)",
        rows=rows,
    )


async def merge_provides(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    # fact -PROVIDES-> Evidence. Only rows with a non-null knowledge_item_id
    # reach here at all - projector.py filters bare document-excerpt
    # evidence (knowledge_item_id IS NULL) out before calling this, since
    # there's no fact node to draw the edge from.
    await session.run(
        "UNWIND $rows AS row "
        "MATCH (fact {knowledge_item_id: row.knowledge_item_id}) "
        "MATCH (evidence:Evidence {id: row.evidence_id}) "
        "MERGE (fact)-[r:PROVIDES]->(evidence)",
        rows=rows,
    )


async def merge_supports(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    # Evidence -SUPPORTS-> Compliance
    await session.run(
        "UNWIND $rows AS row "
        "MATCH (evidence:Evidence {id: row.evidence_id}) "
        "MATCH (compliance:Compliance {id: row.compliance_id}) "
        "MERGE (evidence)-[r:SUPPORTS]->(compliance)",
        rows=rows,
    )


async def merge_decides(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    # Compliance -DECIDES-> Requirement
    await session.run(
        "UNWIND $rows AS row "
        "MATCH (compliance:Compliance {id: row.compliance_id}) "
        "MATCH (requirement:Requirement {id: row.requirement_id}) "
        "MERGE (compliance)-[r:DECIDES]->(requirement)",
        rows=rows,
    )


async def merge_cited_in(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    # Evidence -CITED_IN-> ProposalClaim
    await session.run(
        "UNWIND $rows AS row "
        "MATCH (evidence:Evidence {id: row.evidence_id}) "
        "MATCH (claim:ProposalClaim {id: row.claim_id}) "
        "MERGE (evidence)-[r:CITED_IN]->(claim)",
        rows=rows,
    )


async def merge_addresses(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    # ProposalClaim -ADDRESSES-> Requirement
    await session.run(
        "UNWIND $rows AS row "
        "MATCH (claim:ProposalClaim {id: row.claim_id}) "
        "MATCH (requirement:Requirement {id: row.requirement_id}) "
        "MERGE (claim)-[r:ADDRESSES]->(requirement)",
        rows=rows,
    )


async def merge_named_in(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    # Employee -NAMED_IN-> ProposalClaim
    await session.run(
        "UNWIND $rows AS row "
        "MATCH (employee:Employee {knowledge_item_id: row.employee_id}) "
        "MATCH (claim:ProposalClaim {id: row.claim_id}) "
        "MERGE (employee)-[r:NAMED_IN]->(claim)",
        rows=rows,
    )


async def merge_part_of(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    # ProposalClaim -PART_OF-> ProposalSection
    await session.run(
        "UNWIND $rows AS row "
        "MATCH (claim:ProposalClaim {id: row.claim_id}) "
        "MATCH (section:ProposalSection {id: row.section_id}) "
        "MERGE (claim)-[r:PART_OF]->(section)",
        rows=rows,
    )
