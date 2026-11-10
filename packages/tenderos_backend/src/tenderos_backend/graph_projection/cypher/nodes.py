"""Parameterized MERGE statements for D1's 11 node labels.

Every function takes the Neo4j session and a list of plain dict rows (already
shaped 1:1 from Postgres by projector.py) and MERGEs them in a single
round trip via UNWIND - not one query per row. All values travel as query
parameters ($rows), never interpolated into the Cypher text - only the
label name itself is a fixed Python string literal, never row data.
"""

from typing import Any

from neo4j import AsyncSession


async def merge_tender_nodes(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    await session.run(
        "UNWIND $rows AS row MERGE (n:Tender {id: row.id}) SET n += row",
        rows=rows,
    )


async def mark_projection_complete(session: AsyncSession, tender_id: str) -> None:
    """Major finding #8 (KNOWN_LIMITATIONS.md): a crash partway through
    project_tender leaves real, committed damage (Neo4j auto-commits per
    statement, no transaction spans the whole projection), but Postgres's
    own graph_projection_runs row still names the prior successful run -
    check_freshness had nothing to notice by. This is the fix: the
    genuinely LAST write of project_tender's own write sequence (called
    after merge_used_in, the actual last statement per projector.py),
    setting a property on the SAME Tender node step 6 just recreated. A
    crash anywhere between the delete (step 5) and this call leaves the
    Tender node either absent or present without this property - either
    way, check_freshness's own new Neo4j-side check (queries.
    is_projection_complete) reads it as incomplete, not fresh. Reusing
    the Tender node itself rather than a separate marker node/label is
    deliberate: that node is already deleted-and-recreated every run
    (cleanup.delete_tender_scoped_subgraph's own tender-scoped label
    set includes Tender), so a stale flag surviving from a PRIOR run can
    never be mistaken for this run's own completion - there is no
    "prior" Tender node left to read once the delete has happened.
    """
    await session.run(
        "MATCH (n:Tender {id: $tenderId}) SET n.projection_complete = true",
        tenderId=tender_id,
    )


async def merge_requirement_nodes(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    await session.run(
        "UNWIND $rows AS row MERGE (n:Requirement {id: row.id}) SET n += row",
        rows=rows,
    )


async def merge_evidence_nodes(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    await session.run(
        "UNWIND $rows AS row MERGE (n:Evidence {id: row.id}) SET n += row",
        rows=rows,
    )


async def merge_compliance_nodes(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    await session.run(
        "UNWIND $rows AS row MERGE (n:Compliance {id: row.id}) SET n += row",
        rows=rows,
    )


async def merge_role_nodes(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    await session.run(
        "UNWIND $rows AS row MERGE (n:Role {id: row.id}) SET n += row",
        rows=rows,
    )


async def merge_proposal_claim_nodes(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    await session.run(
        "UNWIND $rows AS row MERGE (n:ProposalClaim {id: row.id}) SET n += row",
        rows=rows,
    )


async def merge_proposal_section_nodes(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    await session.run(
        "UNWIND $rows AS row MERGE (n:ProposalSection {id: row.id}) SET n += row",
        rows=rows,
    )


async def merge_employee_nodes(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    # Shared/organization-scoped - never deleted by a tender-scoped cleanup.
    await session.run(
        "UNWIND $rows AS row "
        "MERGE (n:Employee {knowledge_item_id: row.knowledge_item_id}) "
        "SET n += row",
        rows=rows,
    )


async def merge_certification_nodes(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    await session.run(
        "UNWIND $rows AS row "
        "MERGE (n:Certification {knowledge_item_id: row.knowledge_item_id}) "
        "SET n += row",
        rows=rows,
    )


async def merge_project_nodes(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    await session.run(
        "UNWIND $rows AS row "
        "MERGE (n:Project {knowledge_item_id: row.knowledge_item_id}) "
        "SET n += row",
        rows=rows,
    )


async def merge_capability_nodes(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    await session.run(
        "UNWIND $rows AS row "
        "MERGE (n:Capability {knowledge_item_id: row.knowledge_item_id}) "
        "SET n += row",
        rows=rows,
    )
