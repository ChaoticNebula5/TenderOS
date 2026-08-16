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
        "UNWIND $rows AS row "
        "MERGE (n:Tender {id: row.id}) "
        "SET n += row",
        rows=rows,
    )


async def merge_requirement_nodes(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    await session.run(
        "UNWIND $rows AS row "
        "MERGE (n:Requirement {id: row.id}) "
        "SET n += row",
        rows=rows,
    )


async def merge_evidence_nodes(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    await session.run(
        "UNWIND $rows AS row "
        "MERGE (n:Evidence {id: row.id}) "
        "SET n += row",
        rows=rows,
    )


async def merge_compliance_nodes(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    await session.run(
        "UNWIND $rows AS row "
        "MERGE (n:Compliance {id: row.id}) "
        "SET n += row",
        rows=rows,
    )


async def merge_role_nodes(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    await session.run(
        "UNWIND $rows AS row "
        "MERGE (n:Role {id: row.id}) "
        "SET n += row",
        rows=rows,
    )


async def merge_proposal_claim_nodes(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    await session.run(
        "UNWIND $rows AS row "
        "MERGE (n:ProposalClaim {id: row.id}) "
        "SET n += row",
        rows=rows,
    )


async def merge_proposal_section_nodes(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    await session.run(
        "UNWIND $rows AS row "
        "MERGE (n:ProposalSection {id: row.id}) "
        "SET n += row",
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
