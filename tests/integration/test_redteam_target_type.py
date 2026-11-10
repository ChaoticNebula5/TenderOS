"""A finding's target_type against a real projected graph and real tables
(phase 4 plan item 35).

The label comes from the relationship a contradiction row was reached through
(redteam_checks._TARGET_TYPE_BY_VIA). That stays right only while each
relationship type goes to one label, so these look every id up in the table
its label names, which is what would fail if one gained a second.

The last test is why the migration changes fingerprints at all: after it, a
red-team pass finds the same rows, creates no second one, and leaves a human's
resolution alone.

Real Postgres and real Neo4j, with self-built helpers, as
test_redteam_service.py does.
"""

import importlib.util
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from tenderos_backend.agents.llm.fake_client import FakeLLMClient
from tenderos_backend.agents.schemas.redteam import RedteamCritique
from tenderos_backend.core.config import settings
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import graph_service, redteam_checks, redteam_service
from tenderos_backend.domain.services.redteam_service import finding_fingerprint
from tenderos_backend.graph_projection.driver import get_session as get_neo4j_session

pytestmark = pytest.mark.asyncio

_MIGRATION = (
    Path(__file__).resolve().parent.parent.parent
    / "packages/tenderos_backend/src/tenderos_backend/db/migrations/versions"
    / "025_redteam_target_type.py"
)
_TABLE_BY_LABEL = {
    "compliance_entry": "compliance_entries",
    "proposal_claim": "proposal_claims",
    "role_requirement": "role_requirements",
}
_GRAPH_CATEGORIES = ("deselected_fact_in_use", "removed_employee_committed")
_OLD_LABEL = {"deselected_fact_in_use": "evidence", "removed_employee_committed": "proposal_claim"}


@pytest_asyncio.fixture(autouse=True)
async def _clean_neo4j():
    yield
    async with get_neo4j_session() as session:
        await session.run("MATCH (n) DETACH DELETE n")


async def _seed_every_way_to_reach_a_target() -> tuple[uuid.UUID, dict[str, uuid.UUID]]:
    """One tender where one deselected employee reaches a target through each
    of the four relationships: its evidence SUPPORTS a compliance entry and is
    CITED_IN a claim, and the employee FILLS a role and is NAMED_IN a claim."""
    async with get_session() as session:
        org = Organization(name=f"TargetType {uuid.uuid4().hex[:6]}")
        session.add(org)
        await session.flush()
        tender = Tender(organization_id=org.id, name="TargetType Tender")
        session.add(tender)
        await session.flush()
        await session.commit()
        org_id, tender_id = org.id, tender.id

    async with UnitOfWork() as uow:
        user = await uow.users.create(email=f"{uuid.uuid4().hex}@example.test", password_hash="x")
        employee, _ = await uow.knowledge.create_employee(
            organization_id=org_id,
            name="Removed Employee",
            content_fingerprint=uuid.uuid4().hex,
            title="Removed Employee",
        )
        document = await uow.documents.create(
            side="client",
            doc_type="rfp",
            filename="req.txt",
            storage_key=f"test/{uuid.uuid4()}",
            sha256=uuid.uuid4().hex,
            mime_type="text/plain",
            file_size_bytes=10,
            tender_id=tender_id,
        )
        chunk = await uow.documents.create_chunk(
            document_id=document.id,
            chunk_index=0,
            chunk_text="The bidder must name a project lead.",
            normalized_text="the bidder must name a project lead",
            content_hash=uuid.uuid4().hex,
        )
        requirement = await uow.requirements.create(
            tender_id=tender_id,
            display_code="REQ-001",
            requirement_text="The bidder must name a project lead.",
            normalized_text="the bidder must name a project lead",
            category="staffing",
            mandatory=True,
            source_chunk_id=chunk.id,
            content_fingerprint=uuid.uuid4().hex,
        )
        evidence = await uow.compliance.create_evidence_link(
            tender_id=tender_id,
            source_kind="employee",
            content_fingerprint=uuid.uuid4().hex,
            knowledge_item_id=employee.id,
            verified=True,
            validity_status="valid",
        )
        entry = await uow.compliance.create_compliance_entry(
            requirement_id=requirement.id, status="fully_compliant"
        )
        await uow.compliance.link_evidence(entry.id, evidence.id)
        await uow.compliance.update_compliance_entry(entry.id, human_approved=True)

        role = await uow.talent.create_role_requirement(
            tender_id=tender_id, role_name="Project Lead", content_fingerprint=uuid.uuid4().hex
        )
        match = await uow.talent.create_role_match(
            role_requirement_id=role.id, employee_knowledge_item_id=employee.id
        )
        await uow.talent.update_role_match(match.id, human_selected=True)

        section = await uow.proposal.create_proposal_section(
            tender_id=tender_id, section_name="Team"
        )
        cited = await uow.proposal.create_proposal_claim(
            section_id=section.id,
            claim_text="Cited claim.",
            claim_type="team",
            is_factual=True,
            grounding_status="grounded",
            content_fingerprint=uuid.uuid4().hex,
        )
        await uow.proposal.link_claim_evidence(cited.id, evidence.id)
        named = await uow.proposal.create_proposal_claim(
            section_id=section.id,
            claim_text="Named claim.",
            claim_type="team",
            is_factual=True,
            grounding_status="grounded",
            content_fingerprint=uuid.uuid4().hex,
        )
        await uow.proposal.link_claim_employee(named.id, employee.id)
        await uow.commit()
        user_id = user.id
        ids = {"entry": entry.id, "role": role.id, "cited": cited.id, "named": named.id}
        employee_id = employee.id

    for selected in (True, False):
        async with UnitOfWork() as uow:
            await uow.knowledge.upsert_selection(
                tender_id=tender_id,
                knowledge_item_id=employee_id,
                selected=selected,
                actor_user_id=user_id,
            )
            await uow.commit()

    await graph_service.project_tender(tender_id)
    return tender_id, ids


async def _is_in_table(table: str, row_id: uuid.UUID) -> bool:
    async with get_session() as session:
        result = await session.execute(
            text(f"SELECT 1 FROM {table} WHERE id = :id"),  # noqa: S608 - names from a fixed map
            {"id": row_id},
        )
        return result.first() is not None


async def _red_team_pass(tender_id: uuid.UUID) -> None:
    client = FakeLLMClient()
    client.script("redteam_critique", RedteamCritique, [RedteamCritique(findings=[])])
    await redteam_service.run_red_team(client, "fake-model", tender_id)


def _run_migration_025_on_the_test_database() -> None:
    """The real upgrade(), on this session's database, which comes from the
    models and has no alembic history to run it through."""
    spec = importlib.util.spec_from_file_location("migration_025", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = create_engine(settings.postgres_dsn.replace("+asyncpg", "+psycopg", 1))
    try:
        with engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                module.upgrade()
    finally:
        engine.dispose()


async def test_every_id_is_in_the_table_its_label_names():
    tender_id, ids = await _seed_every_way_to_reach_a_target()

    drafts = await redteam_checks.check_graph_contradictions(tender_id)

    found = {(d.finding_category, d.target_type, d.target_id) for d in drafts}
    assert found >= {
        ("deselected_fact_in_use", "compliance_entry", ids["entry"]),
        ("deselected_fact_in_use", "proposal_claim", ids["cited"]),
        ("removed_employee_committed", "role_requirement", ids["role"]),
        ("removed_employee_committed", "proposal_claim", ids["named"]),
    }, f"all four relationships must be reached, got {found}"

    for draft in drafts:
        table = _TABLE_BY_LABEL[draft.target_type]
        assert await _is_in_table(table, draft.target_id), (
            f"{draft.finding_category} labelled {draft.target_type}, "
            f"but {draft.target_id} is not in {table}"
        )


async def test_after_the_migration_a_pass_adds_no_row_and_keeps_a_human_resolution():
    tender_id, _ = await _seed_every_way_to_reach_a_target()
    await _red_team_pass(tender_id)

    # Put the graph findings back as the old code left them, each with the
    # fingerprint the old label gives.
    async with get_session() as session:
        async with UnitOfWork() as uow:
            findings = await uow.redteam.list_for_tender(tender_id)
        graph_findings = [f for f in findings if f.finding_category in _GRAPH_CATEGORIES]
        relabelled = 0
        for finding in graph_findings:
            old = _OLD_LABEL[finding.finding_category]
            if finding.target_type == old:
                continue
            relabelled += 1
            await session.execute(
                text(
                    "UPDATE redteam_findings SET target_type = :label, content_fingerprint = :fp "
                    "WHERE id = :id"
                ),
                {
                    "id": finding.id,
                    "label": old,
                    "fp": finding_fingerprint(
                        tender_id,
                        old,
                        finding.target_id,
                        finding.finding_category,
                        finding.violated_condition,
                    ),
                },
            )
        await session.commit()
    assert relabelled == 3, "the entry, the cited claim and the role carry a wrong old label"

    resolved_id = next(f.id for f in graph_findings if f.target_type == "role_requirement")
    async with UnitOfWork() as uow:
        await uow.redteam.resolve_finding(resolved_id, "accepted, the lead stays on")
        await uow.commit()

    async with UnitOfWork() as uow:
        total_before = len(await uow.redteam.list_for_tender(tender_id))

    _run_migration_025_on_the_test_database()
    await _red_team_pass(tender_id)

    async with UnitOfWork() as uow:
        after = await uow.redteam.list_for_tender(tender_id)
    assert len(after) == total_before, (
        "a detection must land on the migrated row, not insert a second one beside it"
    )
    by_id = {f.id: f for f in after}
    assert {f.id for f in graph_findings} <= by_id.keys(), "every id must survive"
    for finding in graph_findings:
        current = by_id[finding.id]
        assert current.target_type == finding.target_type, "the migration and the code must agree"
        assert current.content_fingerprint == finding.content_fingerprint

    kept = by_id[resolved_id]
    assert kept.resolved is True
    assert kept.resolution_source == "human"
    assert kept.resolution_note == "accepted, the lead stays on"
    still_open = [
        f for f in after if f.finding_category in _GRAPH_CATEGORIES and f.id != resolved_id
    ]
    assert still_open and all(f.resolved is False for f in still_open), (
        "the pass must have re-detected these, not retired them as gone"
    )
