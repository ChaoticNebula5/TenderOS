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
from sqlalchemy import text
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import compliance_service, graph_service
from tenderos_backend.graph_projection import queries
from tenderos_backend.graph_projection.driver import get_session as get_neo4j_session
from tenderos_backend.graph_projection.projector import ProjectionIntegrityError

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
        user = await uow.users.create(email=f"{uuid.uuid4().hex}@example.test", password_hash="x")
        await uow.commit()
        return user.id


async def _org_and_tender(name: str) -> tuple[uuid.UUID, uuid.UUID]:
    async with get_session() as session:
        org = Organization(name=f"{name} Org")
        session.add(org)
        await session.flush()
        tender = Tender(
            organization_id=org.id, name=f"{name} Tender", submission_date=date(2026, 12, 1)
        )
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


async def _requirement(
    tender_id: uuid.UUID, chunk_id: uuid.UUID, category: str = "experience"
) -> uuid.UUID:
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


async def _select(
    tender_id: uuid.UUID, knowledge_item_id: uuid.UUID, selected: bool = True
) -> None:
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
            tender_id=tender_id,
            role_name="Solution Architect",
            content_fingerprint=uuid.uuid4().hex,
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
        section = await uow.proposal.create_proposal_section(
            tender_id=tender_id, section_name=section_name
        )
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
    good_evidence_id = await _evidence_link(
        tender_id, employee_id, verified=True, validity_status="valid"
    )
    bad_evidence_id = await _evidence_link(
        tender_id, None, verified=False, validity_status="invalid"
    )
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
    only_evidence_id = await _evidence_link(
        tender_id, employee_id, verified=True, validity_status="valid"
    )
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
    await _requirement(tender_id, chunk_id)
    employee_id = await _employee(org_id, "Cited Employee")
    await _select(tender_id, employee_id)
    evidence_id = await _evidence_link(tender_id, employee_id)
    await _proposal_claim(tender_id, "Relevant Experience", evidence_id=evidence_id)

    await graph_service.project_tender(tender_id)

    impact = await graph_service.preview_impact(tender_id, employee_id)
    labels_seen = {label for row in impact for label in row["labels"]}
    assert "ProposalClaim" in labels_seen


async def test_employee_with_no_evidence_affects_nothing_even_beside_one_that_does():
    """B17 Phase 0 regression test for D5's directed traversal (Build Guide
    :1955, `-[*1..$maxHops]->`). Before the fix, preview_impact walked the
    path undirected, so an employee with no downstream edges still "reached"
    everything a sibling employee in the same tender backs: empty -USED_IN-
    Tender -USED_IN- sibling -PROVIDES- Evidence -CITED_IN- ProposalClaim is
    exactly 4 hops. Measured on real-run data before the fix: one such
    employee showed 16 affected nodes undirected, 0 directed
    (KNOWN_LIMITATIONS.md, D5 directionality entry). The sibling is what
    makes this test fail on the old query - without it, both directions
    return nothing and the test would prove nothing.
    """
    org_id, tender_id = await _org_and_tender("DirectedImpact")
    chunk_id = await _chunk(tender_id, "req")
    await _requirement(tender_id, chunk_id)
    backed = await _employee(org_id, "Backed Employee")
    unbacked = await _employee(org_id, "Unbacked Employee")
    await _select(tender_id, backed)
    await _select(tender_id, unbacked)
    evidence_id = await _evidence_link(tender_id, backed)
    await _proposal_claim(tender_id, "Relevant Experience", evidence_id=evidence_id)

    await graph_service.project_tender(tender_id)

    backed_labels = {
        label
        for row in await graph_service.preview_impact(tender_id, backed)
        for label in row["labels"]
    }
    assert "ProposalClaim" in backed_labels, "control: the backed employee does reach its claim"
    assert await graph_service.preview_impact(tender_id, unbacked) == [], (
        "an employee nothing depends on must show zero affected nodes"
    )


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
        real_requirement_ids = {
            str(r.id)
            for r in await uow.requirements.list_for_tender(tender_id, include_rejected=True)
        }
        real_evidence_ids = {
            str(e.id) for e in await uow.compliance.list_evidence_links_for_tender(tender_id)
        }

    async with get_neo4j_session() as session:
        req_result = await session.run(
            "MATCH (n:Requirement {tender_id: $tenderId}) RETURN n.id AS id",
            tenderId=str(tender_id),
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


async def test_a_damaged_graph_is_now_correctly_detected_fixed():
    """B16 reliability scenario ('Neo4j wiped and rebuilt') - was `test_a_
    damaged_graph_still_reads_as_fresh_reproduced_not_fixed`, a deliberate
    reproduction (asserting the BUG - a damaged graph reading as fresh)
    with the fix left open on purpose per that pass's own explicit
    "reproduce only" instruction. Major finding #8 (KNOWN_LIMITATIONS.md)
    is now fixed: `nodes.mark_projection_complete` writes a
    `projection_complete` property on the Tender node as the genuinely
    LAST statement of `project_tender`'s own write sequence (after
    `merge_used_in`), and `check_freshness` now also checks it via
    `queries.is_projection_complete`, not just the two Postgres integers
    it used to compare alone. Same exact reproduction as before (`delete_
    tender_scoped_subgraph` called directly - the real function project_
    tender's own step 5 calls, not a simulated crash), assertion
    inverted: `check_freshness` must now raise `GraphStaleError` with
    `reason="damaged"`, not silently return the stale-but-Postgres-
    matching run.
    """
    org_id, tender_id = await _org_and_tender("DamagedGraphDetected")
    chunk_id = await _chunk(tender_id, "damaged-graph-req")
    await _requirement(tender_id, chunk_id)

    completed_run = await graph_service.project_tender(tender_id)
    async with get_neo4j_session() as session:
        fresh_before = await graph_service.check_freshness(tender_id, session)
        assert fresh_before.id == completed_run.id

        req_before = await session.run(
            "MATCH (n:Requirement {tender_id: $tenderId}) RETURN count(n) AS c",
            tenderId=str(tender_id),
        )
        assert (await req_before.single())["c"] == 1, "sanity check: the real node exists first"

        # The real damage: project_tender's own step 5, called directly -
        # not a simulation of a crash, the actual delete a crashed
        # reprojection would have already committed before failing. The
        # completion marker (step 9) is the Tender node's own property,
        # and delete_tender_scoped_subgraph deletes the Tender node
        # itself along with the other 6 tender-scoped labels - so this
        # single call also destroys the marker, not just the Requirement
        # node checked above.
        from tenderos_backend.graph_projection.cypher.cleanup import (
            delete_tender_scoped_subgraph,
        )

        await delete_tender_scoped_subgraph(session, str(tender_id))

        req_after = await session.run(
            "MATCH (n:Requirement {tender_id: $tenderId}) RETURN count(n) AS c",
            tenderId=str(tender_id),
        )
        assert (await req_after.single())["c"] == 0, "the damage must be real, not a no-op"

        # tender.state_version and graph_projection_runs are both
        # completely untouched by the delete above - it only ever wrote
        # to Neo4j. Postgres alone would still call this fresh; the fix
        # is that check_freshness no longer trusts Postgres alone.
        with pytest.raises(graph_service.GraphStaleError) as exc_info:
            await graph_service.check_freshness(tender_id, session)
        assert exc_info.value.reason == "damaged", (
            "a damaged graph must raise with reason='damaged', distinguishable "
            "from a genuine state_version mismatch"
        )


async def test_get_graph_status_reports_not_fresh_for_a_damaged_graph():
    """Same fix, the other reader: get_graph_status never raises (its own
    contract), so it must fold the same Neo4j-side check into its
    fresh=False result instead - not just re-check Postgres, which alone
    would still say fresh here."""
    org_id, tender_id = await _org_and_tender("DamagedGraphStatus")
    chunk_id = await _chunk(tender_id, "damaged-graph-status-req")
    await _requirement(tender_id, chunk_id)

    await graph_service.project_tender(tender_id)
    status_before = await graph_service.get_graph_status(tender_id)
    assert status_before["fresh"] is True

    async with get_neo4j_session() as session:
        from tenderos_backend.graph_projection.cypher.cleanup import (
            delete_tender_scoped_subgraph,
        )

        await delete_tender_scoped_subgraph(session, str(tender_id))

    status_after = await graph_service.get_graph_status(tender_id)
    assert status_after["fresh"] is False, (
        "get_graph_status must not report fresh=True for a damaged graph just "
        "because Postgres's own state_version still matches"
    )


# ----------------------------------------------------------------------
# 12. preview_impact's USED_IN match is OPTIONAL, not required (B16) -
# scenarios 13/16's own gap. A knowledge item that genuinely backs a real
# compliance decision but was never explicitly selected/deselected has no
# USED_IN edge at all - the fix, plus a real cross-org observation, not
# just an inference from reading the query.
# ----------------------------------------------------------------------


async def test_preview_impact_surfaces_a_never_selected_item_that_backs_real_evidence():
    """The exact scenario 13/16 gap: compliance evidence materializes
    independent of selection (confirmed elsewhere in this file/session),
    so an employee that was never selected/deselected for this tender at
    all - no USED_IN edge, not even selected=false - can still genuinely
    back a real compliance decision. Before this fix, preview_impact's own
    hard MATCH on USED_IN meant this returned nothing; the loosened
    OPTIONAL MATCH must now surface it, with currentlySelected reading
    None (never selected), not False (selected then deselected)."""
    org_id, tender_id = await _org_and_tender("NeverSelectedImpact")
    chunk_id = await _chunk(tender_id, "never-selected-req")
    requirement_id = await _requirement(tender_id, chunk_id)
    employee_id = await _employee(org_id, "Never Selected Employee")
    # Deliberately no _select(...) call at all - this is the exact gap.
    evidence_id = await _evidence_link(tender_id, employee_id)
    await _compliance_entry(requirement_id, [evidence_id])

    await graph_service.project_tender(tender_id)

    impact = await graph_service.preview_impact(tender_id, employee_id)
    assert len(impact) > 0, (
        "a knowledge item backing real compliance evidence must show up in "
        "impact preview even when it was never explicitly selected - this "
        "is the scenario 13/16 gap the OPTIONAL MATCH fix closes"
    )
    assert all(row["currentlySelected"] is None for row in impact), (
        "an item with no USED_IN edge at all must read currentlySelected=None "
        "(never selected for this tender), not False (which would mean "
        "selected then deselected)"
    )


async def test_preview_impact_still_rejects_cross_org_data_with_the_optional_match():
    """Note 1 from the plan approval: the loosened match is the single
    change that touches a cross-tenant-adjacent query, so its safety needs
    a real observation, not only the traced reasoning that the destination
    filter (affected.tender_id = $tenderId) already does the real work
    regardless of whether USED_IN is required. Two separate orgs/tenders,
    each with their own never-selected employee (the exact new code path
    this fix opens up) - querying tender A's impact for org B's employee
    id must return nothing, because there is genuinely no graph path
    connecting a shared knowledge item merged for org B's own tender
    projection to anything with tender A's own tender_id, not because of
    any USED_IN-specific guard."""
    org_a, tender_a = await _org_and_tender("CrossOrgImpactA")
    chunk_a = await _chunk(tender_a, "cross-org-req-a")
    requirement_a = await _requirement(tender_a, chunk_a)
    employee_a = await _employee(org_a, "Org A Never Selected")
    evidence_a = await _evidence_link(tender_a, employee_a)
    await _compliance_entry(requirement_a, [evidence_a])
    await graph_service.project_tender(tender_a)

    org_b, tender_b = await _org_and_tender("CrossOrgImpactB")
    chunk_b = await _chunk(tender_b, "cross-org-req-b")
    requirement_b = await _requirement(tender_b, chunk_b)
    employee_b = await _employee(org_b, "Org B Never Selected")
    evidence_b = await _evidence_link(tender_b, employee_b)
    await _compliance_entry(requirement_b, [evidence_b])
    await graph_service.project_tender(tender_b)

    # Sanity check first: each org's own preview_impact call for its own
    # employee must be non-empty (proves the fixture setup is real,
    # matching the positive test above) before checking the cross-org case
    # actually means something.
    same_org_impact = await graph_service.preview_impact(tender_b, employee_b)
    assert len(same_org_impact) > 0

    cross_org_impact = await graph_service.preview_impact(tender_a, employee_b)
    assert cross_org_impact == [], (
        "org A's own preview_impact must return nothing for org B's employee "
        "id, even with the USED_IN match now optional - a real observation, "
        "not just an inference from the destination-filter reasoning"
    )


async def test_an_unresolvable_knowledge_item_fails_the_projection_loudly_with_its_id():
    """B17 Phase 0b runtime behaviour change (mypy narrowing in projector.py):
    a selected knowledge item whose typed child row is missing used to be
    skipped silently - the projection completed without that node and was
    served as fresh. Now projector.ProjectionIntegrityError names the id, the
    run is marked failed (graph_service.project_tender), and every read path
    refuses the graph as stale instead of answering from an incomplete one.
    """
    org_id, tender_id = await _org_and_tender("IntegrityFailure")
    employee_id = await _employee(org_id, "Half-deleted Employee")
    await _select(tender_id, employee_id)
    async with UnitOfWork() as uow:
        # Remove only the typed child row; the knowledge_items row stays.
        await uow.session.execute(
            text("delete from employees where knowledge_item_id = :k"), {"k": employee_id}
        )
        await uow.commit()

    with pytest.raises(ProjectionIntegrityError, match=str(employee_id)) as excinfo:
        await graph_service.project_tender(tender_id)
    assert excinfo.value.knowledge_item_id == employee_id

    with pytest.raises(graph_service.GraphStaleError):
        await graph_service.preview_impact(tender_id, employee_id)
