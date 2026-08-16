"""graph_projection/ + domain/services/graph_service.py against real
Postgres AND real Neo4j - the 10 required graph tests from the Build Guide's
own B10 list, verbatim, plus an 11th "no extra facts" completeness test the
stop-gate's second sentence ("do not proceed if Neo4j contains a fact that
cannot be reconstructed from PostgreSQL") demands but the 10 don't directly
assert, plus the real-mutation staleness test from the B10 plan's freshness
section.

tests/integration/conftest.py's autouse _clean_tables fixture only wipes
Postgres - it has no idea Neo4j exists. Without _clean_neo4j below, every
test after the first would see leftover nodes from previous test tenders,
which is exactly the kind of environment-state trap KNOWN_LIMITATIONS.md
already tracks (B9's test-teardown incident) - closing it here before it
becomes a fourth instance instead of after.
"""

import uuid
from datetime import date

import pytest
import pytest_asyncio
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import compliance_service, graph_service
from tenderos_backend.graph_projection import queries
from tenderos_backend.graph_projection.driver import get_session as get_neo4j_session

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _clean_neo4j():
    yield
    async with get_neo4j_session() as session:
        await session.run("MATCH (n) DETACH DELETE n")


# ----------------------------------------------------------------------
# Fixture helpers - direct repository calls, no S3/parsing pipeline: these
# tests only need valid rows to satisfy FKs, not real parsed document
# content.
# ----------------------------------------------------------------------


async def _make_user() -> uuid.UUID:
    # selected_by/deselected_by (tender_knowledge_selections) are real FKs
    # to users - a random uuid4() satisfies typed parameters but not this
    # constraint, unlike the looser columns other integration tests' own
    # actor_user_id=uuid.uuid4() shortcuts get away with.
    async with UnitOfWork() as uow:
        user = await uow.users.create(
            email=f"{uuid.uuid4().hex}@example.test", password_hash="x"
        )
        await uow.commit()
        return user.id


async def _org_and_tender(name: str) -> tuple[uuid.UUID, uuid.UUID]:
    async with get_session() as session:
        org = Organization(name=f"{name} Org")
        session.add(org)
        await session.flush()
        tender = Tender(organization_id=org.id, name=f"{name} Tender", submission_date=date(2026, 12, 1))
        session.add(tender)
        await session.flush()
        await session.commit()
        return org.id, tender.id


async def _chunk(tender_id: uuid.UUID, label: str) -> uuid.UUID:
    async with UnitOfWork() as uow:
        document = await uow.documents.create(
            side="client",
            doc_type="rfp",
            filename=f"{label}.txt",
            storage_key=f"test/{uuid.uuid4()}",
            sha256=uuid.uuid4().hex,
            mime_type="text/plain",
            file_size_bytes=10,
            tender_id=tender_id,
        )
        chunk = await uow.documents.create_chunk(
            document_id=document.id,
            chunk_index=0,
            chunk_text=label,
            normalized_text=label.lower(),
            content_hash=uuid.uuid4().hex,
        )
        await uow.commit()
        return chunk.id


async def _requirement(tender_id: uuid.UUID, chunk_id: uuid.UUID, category: str = "experience") -> uuid.UUID:
    async with UnitOfWork() as uow:
        requirement = await uow.requirements.create(
            tender_id=tender_id,
            display_code=f"REQ-{uuid.uuid4().hex[:6]}",
            requirement_text="Sample requirement text.",
            normalized_text="sample requirement text.",
            category=category,
            mandatory=True,
            source_chunk_id=chunk_id,
            content_fingerprint=uuid.uuid4().hex,
        )
        await uow.commit()
        return requirement.id


async def _employee(org_id: uuid.UUID, name: str) -> uuid.UUID:
    async with UnitOfWork() as uow:
        knowledge_item, _employee = await uow.knowledge.create_employee(
            organization_id=org_id,
            name=name,
            content_fingerprint=uuid.uuid4().hex,
            title=name,
        )
        await uow.commit()
        return knowledge_item.id


async def _certification(
    org_id: uuid.UUID, holder_id: uuid.UUID, valid_until: date | None = None
) -> uuid.UUID:
    async with UnitOfWork() as uow:
        knowledge_item, _cert = await uow.knowledge.create_certification(
            organization_id=org_id,
            title="Sample Certification",
            content_fingerprint=uuid.uuid4().hex,
            holder_employee_item_id=holder_id,
            valid_until=valid_until,
        )
        await uow.commit()
        return knowledge_item.id


async def _select(tender_id: uuid.UUID, knowledge_item_id: uuid.UUID, selected: bool = True) -> None:
    actor_user_id = await _make_user()
    async with UnitOfWork() as uow:
        await uow.knowledge.upsert_selection(
            tender_id=tender_id,
            knowledge_item_id=knowledge_item_id,
            selected=selected,
            actor_user_id=actor_user_id,
        )
        await uow.commit()


async def _evidence_link(
    tender_id: uuid.UUID,
    knowledge_item_id: uuid.UUID | None,
    verified: bool = True,
    validity_status: str = "valid",
) -> uuid.UUID:
    # ck_evidence_link_has_source requires knowledge_item_id OR
    # source_chunk_id - a document-excerpt-only evidence link (the
    # knowledge_item_id=None case) still needs a real chunk to satisfy it.
    source_chunk_id = None if knowledge_item_id else await _chunk(tender_id, "excerpt")
    async with UnitOfWork() as uow:
        link = await uow.compliance.create_evidence_link(
            tender_id=tender_id,
            source_kind="certification" if knowledge_item_id else "document_excerpt",
            content_fingerprint=uuid.uuid4().hex,
            knowledge_item_id=knowledge_item_id,
            source_chunk_id=source_chunk_id,
            verified=verified,
            validity_status=validity_status,
        )
        await uow.commit()
        return link.id


async def _compliance_entry(
    requirement_id: uuid.UUID,
    evidence_ids: list[uuid.UUID],
    status: str = "fully_compliant",
    human_approved: bool = True,
) -> uuid.UUID:
    async with UnitOfWork() as uow:
        entry = await uow.compliance.create_compliance_entry(
            requirement_id=requirement_id, status=status
        )
        for evidence_id in evidence_ids:
            await uow.compliance.link_evidence(entry.id, evidence_id)
        if human_approved:
            await uow.compliance.update_compliance_entry(entry.id, human_approved=True)
        await uow.commit()
        return entry.id


async def _role_with_match(
    tender_id: uuid.UUID,
    requirement_id: uuid.UUID,
    employee_id: uuid.UUID,
    human_selected: bool = True,
) -> tuple[uuid.UUID, uuid.UUID]:
    async with UnitOfWork() as uow:
        role = await uow.talent.create_role_requirement(
            tender_id=tender_id, role_name="Solution Architect", content_fingerprint=uuid.uuid4().hex
        )
        await uow.talent.link_role_requirement_source(role.id, requirement_id)
        match = await uow.talent.create_role_match(
            role_requirement_id=role.id, employee_knowledge_item_id=employee_id
        )
        if human_selected:
            await uow.talent.update_role_match(match.id, human_selected=True)
        await uow.commit()
        return role.id, match.id


async def _proposal_claim(
    tender_id: uuid.UUID,
    section_name: str,
    requirement_id: uuid.UUID | None = None,
    evidence_id: uuid.UUID | None = None,
    employee_id: uuid.UUID | None = None,
    grounding_status: str = "grounded",
) -> uuid.UUID:
    async with UnitOfWork() as uow:
        section = await uow.proposal.create_proposal_section(tender_id=tender_id, section_name=section_name)
        claim = await uow.proposal.create_proposal_claim(
            section_id=section.id,
            claim_text="Sample claim text.",
            claim_type="experience",
            is_factual=True,
            grounding_status=grounding_status,
            content_fingerprint=uuid.uuid4().hex,
        )
        if requirement_id is not None:
            await uow.proposal.link_claim_requirement(claim.id, requirement_id)
        if evidence_id is not None:
            await uow.proposal.link_claim_evidence(claim.id, evidence_id)
        if employee_id is not None:
            await uow.proposal.link_claim_employee(claim.id, employee_id)
        await uow.commit()
        return claim.id


# ----------------------------------------------------------------------
# 1. Project same state twice; same graph result.
# ----------------------------------------------------------------------


async def test_projecting_same_state_twice_yields_same_counts():
    org_id, tender_id = await _org_and_tender("Idempotent")
    chunk_id = await _chunk(tender_id, "req")
    requirement_id = await _requirement(tender_id, chunk_id)
    employee_id = await _employee(org_id, "Dana Whitfield")
    await _select(tender_id, employee_id)
    evidence_id = await _evidence_link(tender_id, employee_id)
    await _compliance_entry(requirement_id, [evidence_id])

    first = await graph_service.project_tender(tender_id)
    second = await graph_service.project_tender(tender_id)

    assert first.node_count == second.node_count
    assert first.relationship_count == second.relationship_count
    assert first.node_count > 0


# ----------------------------------------------------------------------
# 2. Delete Neo4j database; rebuild from PostgreSQL.
# ----------------------------------------------------------------------


async def test_rebuild_from_postgres_after_deleting_neo4j():
    org_id, tender_id = await _org_and_tender("Rebuild")
    chunk_id = await _chunk(tender_id, "req")
    requirement_id = await _requirement(tender_id, chunk_id)
    employee_id = await _employee(org_id, "Priya Nair")
    await _select(tender_id, employee_id)
    evidence_id = await _evidence_link(tender_id, employee_id)
    await _compliance_entry(requirement_id, [evidence_id])

    before = await graph_service.project_tender(tender_id)

    async with get_neo4j_session() as session:
        await session.run("MATCH (n) DETACH DELETE n")

    after = await graph_service.project_tender(tender_id)

    assert after.node_count == before.node_count
    assert after.relationship_count == before.relationship_count


# ----------------------------------------------------------------------
# 3. Deselect Project A in Bid A; Bid B remains unchanged.
# ----------------------------------------------------------------------


async def test_deselecting_fact_in_one_tender_does_not_affect_another():
    org_id, tender_a = await _org_and_tender("Bid A")
    _org_id_b, tender_b = await _org_and_tender("Bid B")
    # Same organization's shared employee, used (selected) in BOTH tenders -
    # the only way a cross-tender leakage bug could actually manifest.
    employee_id = await _employee(org_id, "Shared Employee")
    await _select(tender_a, employee_id)
    await _select(tender_b, employee_id)

    await graph_service.project_tender(tender_a)
    await graph_service.project_tender(tender_b)

    async with get_neo4j_session() as session:
        result = await session.run(
            "MATCH (e:Employee {knowledge_item_id: $id})-[u:USED_IN]->(:Tender {id: $tenderId}) "
            "RETURN u.selected AS selected",
            id=str(employee_id),
            tenderId=str(tender_b),
        )
        before_deselect = (await result.single())["selected"]
    assert before_deselect is True

    await _select(tender_a, employee_id, selected=False)
    await graph_service.project_tender(tender_a)

    async with get_neo4j_session() as session:
        result_a = await session.run(
            "MATCH (e:Employee {knowledge_item_id: $id})-[u:USED_IN]->(:Tender {id: $tenderId}) "
            "RETURN u.selected AS selected",
            id=str(employee_id),
            tenderId=str(tender_a),
        )
        selected_a = (await result_a.single())["selected"]
        result_b = await session.run(
            "MATCH (e:Employee {knowledge_item_id: $id})-[u:USED_IN]->(:Tender {id: $tenderId}) "
            "RETURN u.selected AS selected",
            id=str(employee_id),
            tenderId=str(tender_b),
        )
        selected_b = (await result_b.single())["selected"]

    assert selected_a is False
    assert selected_b is True  # Bid B's own USED_IN edge untouched by Bid A's re-projection


# ----------------------------------------------------------------------
# 4. USED_IN(selected=false) still exists.
# ----------------------------------------------------------------------


async def test_used_in_selected_false_edge_persists_not_deleted():
    org_id, tender_id = await _org_and_tender("Deselect")
    employee_id = await _employee(org_id, "Marcus Ondieki")
    await _select(tender_id, employee_id, selected=False)

    await graph_service.project_tender(tender_id)

    async with get_neo4j_session() as session:
        result = await session.run(
            "MATCH (e:Employee {knowledge_item_id: $id})-[u:USED_IN]->(:Tender {id: $tenderId}) "
            "RETURN u.selected AS selected",
            id=str(employee_id),
            tenderId=str(tender_id),
        )
        record = await result.single()

    assert record is not None
    assert record["selected"] is False


# ----------------------------------------------------------------------
# 5. Expired cert contradiction found.
# ----------------------------------------------------------------------


async def test_expired_certification_contradiction_found():
    org_id, tender_id = await _org_and_tender("ExpiredCert")
    chunk_id = await _chunk(tender_id, "req")
    requirement_id = await _requirement(tender_id, chunk_id)
    employee_id = await _employee(org_id, "Employee Holding Expired Cert")
    cert_id = await _certification(org_id, employee_id, valid_until=date(2020, 1, 1))
    await _select(tender_id, employee_id)
    await _select(tender_id, cert_id)
    evidence_id = await _evidence_link(tender_id, cert_id)
    await _compliance_entry(requirement_id, [evidence_id], status="fully_compliant")

    await graph_service.project_tender(tender_id)

    contradictions = await graph_service.find_contradictions(tender_id)
    categories = {c["category"] for c in contradictions}
    assert "expired_certification" in categories


# ----------------------------------------------------------------------
# 6. Removed employee still named in claim found.
# ----------------------------------------------------------------------


async def test_removed_employee_still_named_in_claim_found():
    org_id, tender_id = await _org_and_tender("RemovedEmployee")
    employee_id = await _employee(org_id, "Departed Employee")
    await _select(tender_id, employee_id, selected=False)
    await _proposal_claim(tender_id, "Proposed Team", employee_id=employee_id)

    await graph_service.project_tender(tender_id)

    contradictions = await graph_service.find_contradictions(tender_id)
    categories = {c["category"] for c in contradictions}
    assert "removed_employee_committed" in categories


# ----------------------------------------------------------------------
# 7 & 8. Evidence-coverage re-evaluation - alternate evidence remains vs not.
# ----------------------------------------------------------------------


async def test_alternate_evidence_remains_compliance_not_broken():
    org_id, tender_id = await _org_and_tender("AlternateEvidence")
    chunk_id = await _chunk(tender_id, "req")
    requirement_id = await _requirement(tender_id, chunk_id)
    employee_id = await _employee(org_id, "Employee A")
    await _select(tender_id, employee_id)
    good_evidence_id = await _evidence_link(tender_id, employee_id, verified=True, validity_status="valid")
    bad_evidence_id = await _evidence_link(tender_id, None, verified=False, validity_status="invalid")
    entry_id = await _compliance_entry(requirement_id, [good_evidence_id, bad_evidence_id])

    await graph_service.project_tender(tender_id)

    async with get_neo4j_session() as session:
        rows = await queries.evidence_coverage_rows(session, str(entry_id))
    result = graph_service.evaluate_evidence_coverage(rows)

    assert result.valid_path_count == 1
    assert result.recommended_action == "review_optional"


async def test_no_alternate_evidence_requires_reevaluation():
    org_id, tender_id = await _org_and_tender("NoAlternateEvidence")
    chunk_id = await _chunk(tender_id, "req")
    requirement_id = await _requirement(tender_id, chunk_id)
    employee_id = await _employee(org_id, "Employee B")
    await _select(tender_id, employee_id, selected=False)  # deselected - disqualifies the only path
    only_evidence_id = await _evidence_link(tender_id, employee_id, verified=True, validity_status="valid")
    entry_id = await _compliance_entry(requirement_id, [only_evidence_id])

    await graph_service.project_tender(tender_id)

    async with get_neo4j_session() as session:
        rows = await queries.evidence_coverage_rows(session, str(entry_id))
    result = graph_service.evaluate_evidence_coverage(rows)

    assert result.valid_path_count == 0
    assert result.recommended_action == "compliance_re_evaluation_required"


# ----------------------------------------------------------------------
# 9. ProposalClaim appears in affected impact set.
# ----------------------------------------------------------------------


async def test_proposal_claim_appears_in_impact_set():
    org_id, tender_id = await _org_and_tender("ImpactClaim")
    chunk_id = await _chunk(tender_id, "req")
    requirement_id = await _requirement(tender_id, chunk_id)
    employee_id = await _employee(org_id, "Cited Employee")
    await _select(tender_id, employee_id)
    evidence_id = await _evidence_link(tender_id, employee_id)
    await _proposal_claim(tender_id, "Relevant Experience", evidence_id=evidence_id)

    await graph_service.project_tender(tender_id)

    impact = await graph_service.preview_impact(tender_id, employee_id)
    labels_seen = {label for row in impact for label in row["labels"]}
    assert "ProposalClaim" in labels_seen


# ----------------------------------------------------------------------
# 10. Stale projection query returns GRAPH_STALE.
# ----------------------------------------------------------------------


async def test_stale_projection_raises_graph_stale_after_real_mutation():
    org_id, tender_id = await _org_and_tender("Staleness")
    chunk_id = await _chunk(tender_id, "req")
    requirement_id = await _requirement(tender_id, chunk_id)
    employee_id = await _employee(org_id, "Employee C")
    await _select(tender_id, employee_id)
    evidence_id = await _evidence_link(tender_id, employee_id)
    entry_id = await _compliance_entry(requirement_id, [evidence_id], human_approved=False)

    await graph_service.project_tender(tender_id)
    status_before = await graph_service.get_graph_status(tender_id)
    assert status_before["fresh"] is True

    # The load-bearing step: a REAL mutation through compliance_service.
    # approve_compliance_entry - the exact call site that bumps
    # tenders.state_version in the same transaction (B7/B8's own fix) - not
    # a manual UPDATE on tenders.state_version in this test fixture.
    await compliance_service.approve_compliance_entry(entry_id, actor_user_id=uuid.uuid4())

    with pytest.raises(graph_service.GraphStaleError):
        await graph_service.preview_impact(tender_id, employee_id)

    await graph_service.project_tender(tender_id)
    status_after = await graph_service.get_graph_status(tender_id)
    assert status_after["fresh"] is True


# ----------------------------------------------------------------------
# 11. No-extra-facts completeness: every node/relationship traces back to a
# specific Postgres row (the stop-gate's second sentence, not covered by the
# 10 tests above).
# ----------------------------------------------------------------------


async def test_no_extra_facts_every_node_traces_to_postgres():
    org_id, tender_id = await _org_and_tender("Completeness")
    chunk_id = await _chunk(tender_id, "req")
    requirement_id = await _requirement(tender_id, chunk_id)
    employee_id = await _employee(org_id, "Traceable Employee")
    await _select(tender_id, employee_id)
    evidence_id = await _evidence_link(tender_id, employee_id)
    await _compliance_entry(requirement_id, [evidence_id])

    await graph_service.project_tender(tender_id)

    async with UnitOfWork() as uow:
        real_requirement_ids = {str(r.id) for r in await uow.requirements.list_for_tender(tender_id, include_rejected=True)}
        real_evidence_ids = {
            str(e.id) for e in await uow.compliance.list_evidence_links_for_tender(tender_id)
        }

    async with get_neo4j_session() as session:
        req_result = await session.run(
            "MATCH (n:Requirement {tender_id: $tenderId}) RETURN n.id AS id", tenderId=str(tender_id)
        )
        graph_requirement_ids = {record["id"] async for record in req_result}

        ev_result = await session.run(
            "MATCH (n:Evidence {tender_id: $tenderId}) RETURN n.id AS id", tenderId=str(tender_id)
        )
        graph_evidence_ids = {record["id"] async for record in ev_result}

    # Every Requirement/Evidence node in this tender's projected subgraph
    # must be a SUBSET of what Postgres actually has - if Neo4j ever held a
    # node with no corresponding Postgres row, this assertion is what would
    # catch it (a hotfix or stray write directly to Neo4j, the exact thing
    # D3's "no agent writes directly to Neo4j" rule exists to prevent).
    assert graph_requirement_ids <= real_requirement_ids
    assert graph_requirement_ids == real_requirement_ids
    assert graph_evidence_ids <= real_evidence_ids
    assert graph_evidence_ids == real_evidence_ids
