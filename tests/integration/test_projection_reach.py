"""Plan item 95: the bump rule's two ties to the graph projector.

1. What the projector writes is exactly what tests/projection_inputs.py
   declares (the guard's map cannot drift from the projector).
2. KnowledgeRepository.list_tender_ids_projecting answers "which bids' graphs
   include this knowledge item" exactly as the projector decides it: for a
   fully populated bid, the lookup returns the bid for the knowledge items the
   projector puts in its graph, and for no other. Each of the five reach
   paths is the only path for at least one item here, so dropping any one of
   them fails this test.
3. A rejected requirement is out of scope (plan item 97, PROJECTION_SCOPE):
   the projector leaves out its node, its compliance node, the edges into it
   and a role whose every source is rejected, and the reach lookup leaves out
   an employee picked only on such a role.

The projector writes into a recording stand-in for the Neo4j session, so
these tests read exactly the rows it would send, with no graph store.
"""

import uuid
from typing import Any

import pytest
from projection_inputs import PROJECTED_PROPERTIES, PROJECTION_SCOPE
from tenderos_backend.db.repositories.tenders import TenderRepository
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import knowledge_service
from tenderos_backend.domain.services.auth_service import register
from tenderos_backend.domain.services.tender_service import create_tender
from tenderos_backend.graph_projection import projector

pytestmark = pytest.mark.asyncio

_FACT_LABELS = ("Employee", "Certification", "Project", "Capability")


class _Count:
    async def single(self) -> dict[str, int]:
        return {"c": 0}


class _RecordingSession:
    """Stands in for a neo4j AsyncSession: records every statement's rows."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def run(self, query: str, **params: Any) -> _Count:
        self.calls.append((query, params))
        return _Count()

    def node_rows(self, label: str) -> list[dict[str, Any]]:
        return [
            row
            for query, params in self.calls
            if f"MERGE (n:{label} " in query
            for row in params.get("rows", [])
        ]

    def relationship_rows(self, rel_type: str) -> list[dict[str, Any]]:
        return [
            row
            for query, params in self.calls
            if f"[r:{rel_type}]" in query
            for row in params.get("rows", [])
        ]

    def relationship_props(self, rel_type: str) -> list[dict[str, Any]]:
        return [
            row["props"]
            for query, params in self.calls
            if f"[r:{rel_type}]" in query
            for row in params.get("rows", [])
            if "props" in row
        ]


async def _project(tender_id: uuid.UUID) -> _RecordingSession:
    session = _RecordingSession()
    async with UnitOfWork() as uow:
        await projector.project_tender(uow, session, tender_id)  # type: ignore[arg-type]
    return session


async def _populated_bid() -> dict[str, Any]:
    """One bid with every node label and every property-carrying edge, and
    knowledge items reaching it by exactly one path each, plus items the
    projector leaves out."""
    registered = await register(
        email=f"{uuid.uuid4().hex}@example.com",
        password=uuid.uuid4().hex,
        organization_name="Reach Org",
    )
    org_id, actor_id = registered.organization.id, registered.user.id
    tender = await create_tender(organization_id=org_id, name="Reach Bid", created_by=actor_id)
    other = await create_tender(organization_id=org_id, name="Other Bid", created_by=actor_id)
    ids: dict[str, Any] = {"org": org_id, "actor": actor_id, "tender": tender.id, "other": other.id}

    async with UnitOfWork() as uow:

        async def employee(name: str) -> uuid.UUID:
            item, _ = await uow.knowledge.create_employee(
                organization_id=org_id,
                name=name,
                content_fingerprint=uuid.uuid4().hex,
                title=name,
                current_title="Engineer",
            )
            return item.id

        # Path 3 only: a human-selected role match.
        ids["selected_employee"] = await employee("Selected Employee")
        # Path 4 only: named in a claim.
        ids["named_employee"] = await employee("Named Employee")
        # Path 5 only: holds a certificate the bid cites.
        ids["holder_employee"] = await employee("Holder Employee")
        # Left out: matched but not selected.
        ids["matched_only_employee"] = await employee("Matched Only")
        # Left out: holds a certificate nothing reaches.
        ids["unreached_holder"] = await employee("Unreached Holder")
        # Left out: reaches only the other bid.
        ids["other_bid_employee"] = await employee("Other Bid Employee")
        # Left out: selected only on a role whose every source is rejected.
        ids["out_of_scope_employee"] = await employee("Out Of Scope Employee")

        cert, _ = await uow.knowledge.create_certification(
            organization_id=org_id,
            title="ISO 27001",
            content_fingerprint=uuid.uuid4().hex,
            holder_employee_item_id=ids["holder_employee"],
            issuer="BSI",
            credential_id="C-1",
        )
        ids["cited_cert"] = cert.id  # path 2 only: an evidence link
        unreached_cert, _ = await uow.knowledge.create_certification(
            organization_id=org_id,
            title="Unreached Cert",
            content_fingerprint=uuid.uuid4().hex,
            holder_employee_item_id=ids["unreached_holder"],
        )
        ids["unreached_cert"] = unreached_cert.id
        project, _ = await uow.knowledge.create_past_project(
            organization_id=org_id,
            title="Metro Rollout",
            content_fingerprint=uuid.uuid4().hex,
            client_name="City",
        )
        ids["selected_project"] = project.id  # path 1 only: a selection row
        capability, _ = await uow.knowledge.create_capability(
            organization_id=org_id,
            title="24x7 NOC",
            content_fingerprint=uuid.uuid4().hex,
            category="operations",
        )
        ids["deselected_capability"] = capability.id  # path 1: a selected=false row
        document = await uow.documents.create(
            side="company",
            doc_type="company_profile",
            filename="profile.pdf",
            storage_key=f"test/{uuid.uuid4()}",
            sha256=uuid.uuid4().hex,
            mime_type="application/pdf",
            file_size_bytes=10,
            organization_id=org_id,
        )
        company_doc, _ = await uow.knowledge.create_company_document(
            organization_id=org_id,
            title="Company Profile",
            content_fingerprint=uuid.uuid4().hex,
            document_id=document.id,
            doc_subtype="profile",
        )
        ids["company_document"] = company_doc.id  # selected, but never projected

        # Tender-scoped rows.
        chunk_document = await uow.documents.create(
            side="client",
            doc_type="rfp",
            filename="rfp.txt",
            storage_key=f"test/{uuid.uuid4()}",
            sha256=uuid.uuid4().hex,
            mime_type="text/plain",
            file_size_bytes=10,
            tender_id=tender.id,
        )
        chunk = await uow.documents.create_chunk(
            document_id=chunk_document.id,
            chunk_index=0,
            chunk_text="rfp",
            normalized_text="rfp",
            content_hash=uuid.uuid4().hex,
        )
        requirement = await uow.requirements.create(
            tender_id=tender.id,
            display_code="EXP-001",
            requirement_text="Ten years of experience.",
            normalized_text="ten years of experience.",
            category="experience",
            mandatory=True,
            source_chunk_id=chunk.id,
            content_fingerprint=uuid.uuid4().hex,
        )
        evidence = await uow.compliance.create_evidence_link(
            tender_id=tender.id,
            source_kind="certification",
            content_fingerprint=uuid.uuid4().hex,
            knowledge_item_id=ids["cited_cert"],
            verified=True,
            validity_status="valid",
            confidence=0.9,
        )
        entry = await uow.compliance.create_compliance_entry(
            requirement_id=requirement.id, status="fully_compliant"
        )
        await uow.compliance.link_evidence(entry.id, evidence.id)
        role = await uow.talent.create_role_requirement(
            tender_id=tender.id,
            role_name="Lead",
            content_fingerprint=uuid.uuid4().hex,
            min_experience_years=5,
        )
        await uow.talent.link_role_requirement_source(role.id, requirement.id)
        selected = await uow.talent.create_role_match(role.id, ids["selected_employee"])
        await uow.talent.update_role_match(selected.id, human_selected=True)
        await uow.talent.create_role_match(role.id, ids["matched_only_employee"])
        section = await uow.proposal.create_proposal_section(
            tender_id=tender.id, section_name="Team"
        )
        claim = await uow.proposal.create_proposal_claim(
            section_id=section.id,
            claim_text="Our team has done this.",
            claim_type="experience",
            is_factual=True,
            grounding_status="grounded",
            content_fingerprint=uuid.uuid4().hex,
        )
        await uow.proposal.link_claim_requirement(claim.id, requirement.id)
        await uow.proposal.link_claim_evidence(claim.id, evidence.id)
        # Plan item 97: a rejected requirement with a compliance entry, a role
        # sourced only from it (with a pick), a role sourced from it and from
        # the live one, and a link from the claim. Its entry cites the same
        # evidence as the live entry, so only SUPPORTS to the live one stays.
        rejected = await uow.requirements.create(
            tender_id=tender.id,
            display_code="EXP-002",
            requirement_text="Twenty years of experience.",
            normalized_text="twenty years of experience.",
            category="experience",
            mandatory=True,
            source_chunk_id=chunk.id,
            content_fingerprint=uuid.uuid4().hex,
        )
        await uow.requirements.reject(rejected.id)
        rejected_entry = await uow.compliance.create_compliance_entry(
            requirement_id=rejected.id, status="fully_compliant"
        )
        await uow.compliance.link_evidence(rejected_entry.id, evidence.id)
        out_of_scope_role = await uow.talent.create_role_requirement(
            tender_id=tender.id, role_name="Rejected Lead", content_fingerprint=uuid.uuid4().hex
        )
        await uow.talent.link_role_requirement_source(out_of_scope_role.id, rejected.id)
        picked = await uow.talent.create_role_match(
            out_of_scope_role.id, ids["out_of_scope_employee"]
        )
        await uow.talent.update_role_match(picked.id, human_selected=True)
        mixed_role = await uow.talent.create_role_requirement(
            tender_id=tender.id, role_name="Mixed Lead", content_fingerprint=uuid.uuid4().hex
        )
        await uow.talent.link_role_requirement_source(mixed_role.id, requirement.id)
        await uow.talent.link_role_requirement_source(mixed_role.id, rejected.id)
        await uow.proposal.link_claim_requirement(claim.id, rejected.id)
        ids.update(
            requirement=requirement.id,
            entry=entry.id,
            role=role.id,
            claim=claim.id,
            rejected=rejected.id,
            rejected_entry=rejected_entry.id,
            out_of_scope_role=out_of_scope_role.id,
            mixed_role=mixed_role.id,
        )
        await uow.proposal.link_claim_employee(claim.id, ids["named_employee"])
        for item_id, chosen in (
            (ids["selected_project"], True),
            (ids["deselected_capability"], False),
            (ids["company_document"], True),
        ):
            await uow.knowledge.upsert_selection(tender.id, item_id, chosen, actor_id)
        # Verified relationships between projected items carry properties.
        await uow.knowledge.create_relationship(
            organization_id=org_id,
            source_knowledge_item_id=ids["selected_employee"],
            relationship_type="WORKED_ON",
            target_knowledge_item_id=ids["selected_project"],
            content_fingerprint=uuid.uuid4().hex,
            confidence=0.8,
            verified=True,
        )
        await uow.knowledge.create_relationship(
            organization_id=org_id,
            source_knowledge_item_id=ids["selected_project"],
            relationship_type="DEMONSTRATES",
            target_knowledge_item_id=ids["deselected_capability"],
            content_fingerprint=uuid.uuid4().hex,
            confidence=0.7,
            verified=True,
        )
        # The other bid reaches only its own employee.
        await uow.knowledge.upsert_selection(other.id, ids["other_bid_employee"], True, actor_id)
        await uow.commit()
    return ids


async def test_the_projector_writes_exactly_the_declared_properties():
    ids = await _populated_bid()
    session = await _project(ids["tender"])

    for label, declared in PROJECTED_PROPERTIES.items():
        if label in ("USED_IN", "WORKED_ON", "DEMONSTRATES"):
            rows = session.relationship_props(label)
        else:
            rows = session.node_rows(label)
        assert rows, f"the populated bid wrote no {label}; the test bid must cover it"
        written = set().union(*(row.keys() for row in rows))
        assert written == declared, (
            f"{label}: the projector writes {sorted(written)}, tests/projection_inputs.py "
            f"declares {sorted(declared)}. Update the declaration, and decide whether the "
            "writers of a newly projected field bump (plan item 95)."
        )


async def test_the_reach_lookup_matches_the_fact_nodes_the_projector_writes():
    ids = await _populated_bid()
    session = await _project(ids["tender"])
    projected = {
        uuid.UUID(row["knowledge_item_id"])
        for label in _FACT_LABELS
        for row in session.node_rows(label)
    }
    reach_paths = {
        "selected_employee",  # path 3
        "named_employee",  # path 4
        "holder_employee",  # path 5
        "cited_cert",  # path 2
        "selected_project",  # path 1, selected
        "deselected_capability",  # path 1, selected=false
    }
    left_out = {
        "matched_only_employee",
        "unreached_holder",
        "unreached_cert",
        "other_bid_employee",
        "company_document",
        "out_of_scope_employee",
    }
    assert projected == {ids[name] for name in reach_paths}, "the test bid's own premise"

    async with UnitOfWork() as uow:
        for name in reach_paths | left_out:
            bids = await uow.knowledge.list_tender_ids_projecting(ids[name])
            assert (ids["tender"] in bids) == (name in reach_paths), (
                f"{name}: the lookup {'misses' if name in reach_paths else 'wrongly returns'} "
                "the bid the projector "
                f"{'puts it in' if name in reach_paths else 'leaves it out of'}"
            )
        assert await uow.knowledge.list_tender_ids_projecting(ids["other_bid_employee"]) == [
            ids["other"]
        ]


async def test_a_rejected_requirement_and_what_hangs_off_it_are_not_projected():
    """Plan item 97: every PROJECTION_SCOPE column has its case here."""
    assert set(PROJECTION_SCOPE) == {"Requirement.rejected_at"}, "a new scope column needs a case"
    ids = await _populated_bid()
    session = await _project(ids["tender"])

    def node_ids(label: str) -> set[str]:
        return {row["id"] for row in session.node_rows(label)}

    def edges(rel_type: str, start: str, end: str) -> set[tuple[str, str]]:
        return {(row[start], row[end]) for row in session.relationship_rows(rel_type)}

    live, rejected = str(ids["requirement"]), str(ids["rejected"])
    assert node_ids("Requirement") == {live}
    assert node_ids("Compliance") == {str(ids["entry"])}
    assert node_ids("Role") == {str(ids["role"]), str(ids["mixed_role"])}
    assert node_ids("ProposalClaim") == {str(ids["claim"])}, "the claim stays"
    assert edges("DECIDES", "compliance_id", "requirement_id") == {(str(ids["entry"]), live)}
    assert edges("SATISFIES", "role_id", "requirement_id") == {
        (str(ids["role"]), live),
        (str(ids["mixed_role"]), live),
    }
    assert edges("ADDRESSES", "claim_id", "requirement_id") == {(str(ids["claim"]), live)}
    assert {compliance for _, compliance in edges("SUPPORTS", "evidence_id", "compliance_id")} == {
        str(ids["entry"])
    }
    fills = {row["role_id"] for row in session.relationship_rows("FILLS")}
    assert str(ids["out_of_scope_role"]) not in fills
    sent = [
        str(value)
        for _query, params in session.calls
        for row in params.get("rows", [])
        for value in row.values()
    ]
    assert rejected not in sent, "nothing the projector sends names the rejected requirement"


async def test_a_knowledge_edit_bumps_its_bids_once_each_in_sorted_order(monkeypatch):
    """Addition 4: several bids are bumped in sorted tender order (each takes
    the bid row's lock), so two knowledge edits reaching overlapping bids
    cannot take the locks in opposite orders."""
    registered = await register(
        email=f"{uuid.uuid4().hex}@example.com",
        password=uuid.uuid4().hex,
        organization_name="Order Org",
    )
    org_id, actor_id = registered.organization.id, registered.user.id
    bids = [
        (await create_tender(organization_id=org_id, name=f"Bid {n}", created_by=actor_id)).id
        for n in range(4)
    ]
    async with UnitOfWork() as uow:
        cert, _ = await uow.knowledge.create_certification(
            organization_id=org_id, title="Shared Cert", content_fingerprint=uuid.uuid4().hex
        )
        for bid in reversed(bids):
            await uow.knowledge.upsert_selection(bid, cert.id, True, actor_id)
        await uow.commit()

    order: list[uuid.UUID] = []
    real = TenderRepository.increment_state_version

    async def recording(self: TenderRepository, tender_id: uuid.UUID) -> None:
        order.append(tender_id)
        await real(self, tender_id)

    monkeypatch.setattr(TenderRepository, "increment_state_version", recording)
    await knowledge_service.patch_knowledge_item(cert.id, org_id, actor_id, status="expired")
    assert order == sorted(bids)
