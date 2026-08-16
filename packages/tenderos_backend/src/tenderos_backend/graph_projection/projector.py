"""D3's projection algorithm (steps 3-9). Step 1 (read tender + state_version)
and step 2 (create the graph_projection_runs row) happen in
domain/services/graph_service.py before this is called; steps 10-11 (verify
counts, mark the run completed) happen there afterward. This file owns only
the actual graph write - reading from Postgres via UnitOfWork, writing to
Neo4j via the cypher/ modules.

Tenant safety (the reason this file exists as written, not some other way):
shared knowledge nodes (Employee/Certification/Project/Capability) are only
ever MERGE'd, never deleted here - cleanup.delete_tender_scoped_subgraph's
own label filter structurally cannot match them (they carry no tender_id
property). Only the 7 tender-scoped labels get deleted-and-recreated each
run. USED_IN is MERGE'd every run including selected=false rows - never
produced by a delete.

Reachable-shared-node scoping: rather than MERGE every knowledge item in the
whole organization on every single tender's projection (correct under MERGE's
idempotency, but wastefully re-touching unrelated tenders' unrelated data
every run), this walks outward from what THIS tender's own rows actually
reference: tender_knowledge_selections, evidence source knowledge items,
human-selected role match candidates, and claim-named employees - plus, for
any certification pulled in that way, its holder employee (needed for HOLDS
to resolve both endpoints).
"""

import uuid

from neo4j import AsyncSession as Neo4jSession

from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.graph_projection import queries
from tenderos_backend.graph_projection.cypher import cleanup, nodes, relationships


async def project_tender(
    uow: UnitOfWork, neo4j_session: Neo4jSession, tender_id: uuid.UUID
) -> tuple[int, int]:
    tender = await uow.tenders.get(tender_id)
    if tender is None:
        raise ValueError(f"Tender {tender_id} not found")
    tid = str(tender_id)
    org_id = str(tender.organization_id)

    # ------------------------------------------------------------------
    # Gather every tender-scoped row up front (all reads happen before any
    # write, so a read failure never leaves a half-written graph).
    # ------------------------------------------------------------------
    requirements = await uow.requirements.list_for_tender(tender_id, include_rejected=True)
    evidence_links = await uow.compliance.list_evidence_links_for_tender(tender_id)
    compliance_entries = await uow.compliance.list_compliance_entries_for_tender(tender_id)
    role_requirements = await uow.talent.list_role_requirements_for_tender(tender_id)
    role_matches_by_role = {
        rr.id: await uow.talent.list_role_matches_for_role(rr.id) for rr in role_requirements
    }
    role_sources_by_role = {
        rr.id: await uow.talent.list_sources_for_role(rr.id) for rr in role_requirements
    }
    sections = await uow.proposal.list_proposal_sections_for_tender(tender_id)
    claims_by_section = {
        s.id: await uow.proposal.list_proposal_claims_for_section(s.id) for s in sections
    }
    all_claims = [c for claims in claims_by_section.values() for c in claims]
    claim_evidence_by_claim = {
        c.id: await uow.proposal.list_evidence_for_claim(c.id) for c in all_claims
    }
    claim_requirements_by_claim = {
        c.id: await uow.proposal.list_requirements_for_claim(c.id) for c in all_claims
    }
    claim_employees_by_claim = {
        c.id: await uow.proposal.list_employees_for_claim(c.id) for c in all_claims
    }
    compliance_evidence_by_entry = {
        entry.id: await uow.compliance.list_evidence_for_compliance_entry(entry.id)
        for entry in compliance_entries
    }
    selections = await uow.knowledge.list_selections_for_tender(tender_id)

    # ------------------------------------------------------------------
    # Reachable shared knowledge_item_id set.
    # ------------------------------------------------------------------
    reachable_ids: set[uuid.UUID] = {s.knowledge_item_id for s in selections}
    reachable_ids.update(
        ev.knowledge_item_id for ev in evidence_links if ev.knowledge_item_id is not None
    )
    for matches in role_matches_by_role.values():
        reachable_ids.update(
            m.employee_knowledge_item_id for m in matches if m.human_selected
        )
    for links in claim_employees_by_claim.values():
        reachable_ids.update(link.employee_knowledge_item_id for link in links)

    # Resolve each reachable id to its knowledge_item + typed child row, and
    # pull in certification holders as a second pass (needed for HOLDS).
    resolved: dict[uuid.UUID, tuple[str, object, object]] = {}
    holder_ids: set[uuid.UUID] = set()
    for kid in reachable_ids:
        item = await uow.knowledge.get_knowledge_item(kid)
        if item is None:
            continue
        if item.item_type == "employee":
            child = await uow.knowledge.get_employee(kid)
        elif item.item_type == "certification":
            child = await uow.knowledge.get_certification(kid)
            if child is not None and child.holder_employee_item_id is not None:
                holder_ids.add(child.holder_employee_item_id)
        elif item.item_type == "past_project":
            child = await uow.knowledge.get_past_project(kid)
        elif item.item_type == "capability":
            child = await uow.knowledge.get_capability(kid)
        else:
            continue
        if child is not None:
            resolved[kid] = (item.item_type, item, child)

    for hid in holder_ids - resolved.keys():
        item = await uow.knowledge.get_knowledge_item(hid)
        if item is not None and item.item_type == "employee":
            employee = await uow.knowledge.get_employee(hid)
            if employee is not None:
                resolved[hid] = ("employee", item, employee)

    # ------------------------------------------------------------------
    # Step 3-4: MERGE shared nodes + organization-scoped relationships.
    # ------------------------------------------------------------------
    employee_rows, certification_rows, project_rows, capability_rows = [], [], [], []
    for kid, (item_type, item, child) in resolved.items():
        # status/valid_from/valid_until live on the parent KnowledgeItem row
        # for all 4 shared types (Part C), not just certifications - D7's
        # evidence-coverage check ("knowledge item active", "validity
        # dates") needs these uniformly on whichever fact type backs a given
        # evidence link, not only when that fact happens to be a
        # certification.
        base = {
            "knowledge_item_id": str(kid),
            "organization_id": str(item.organization_id),
            "status": item.status,
            "valid_from": item.valid_from,
            "valid_until": item.valid_until,
        }
        if item_type == "employee":
            employee_rows.append(
                base
                | {
                    "name": child.name,
                    "current_title": child.current_title,
                    "eligible_for_bid": child.eligible_for_bid,
                    "profile_review_status": child.profile_review_status,
                    "availability_status": child.availability_status,
                }
            )
        elif item_type == "certification":
            certification_rows.append(
                base
                | {
                    "title": item.title,
                    "issuer": child.issuer,
                    "credential_id": child.credential_id,
                }
            )
        elif item_type == "past_project":
            project_rows.append(
                base
                | {
                    "title": item.title,
                    "client_name": child.client_name,
                    "start_date": child.start_date,
                    "end_date": child.end_date,
                }
            )
        elif item_type == "capability":
            capability_rows.append(
                base | {"title": item.title, "category": child.category}
            )

    await nodes.merge_employee_nodes(neo4j_session, employee_rows)
    await nodes.merge_certification_nodes(neo4j_session, certification_rows)
    await nodes.merge_project_nodes(neo4j_session, project_rows)
    await nodes.merge_capability_nodes(neo4j_session, capability_rows)

    holds_rows = [
        {"employee_id": str(child.holder_employee_item_id), "certification_id": str(kid), "props": {}}
        for kid, (item_type, _item, child) in resolved.items()
        if item_type == "certification" and child.holder_employee_item_id in resolved
    ]
    await relationships.merge_holds(neo4j_session, holds_rows)

    org_relationships = await uow.knowledge.list_relationships_for_org(
        tender.organization_id, verified_only=True
    )
    worked_on_rows, demonstrates_rows = [], []
    for rel in org_relationships:
        if rel.source_knowledge_item_id not in resolved or rel.target_knowledge_item_id not in resolved:
            continue
        props = {"confidence": rel.confidence, "verified": rel.verified}
        row = {
            "employee_id" if rel.relationship_type == "WORKED_ON" else "project_id": str(
                rel.source_knowledge_item_id
            ),
            "project_id" if rel.relationship_type == "WORKED_ON" else "capability_id": str(
                rel.target_knowledge_item_id
            ),
            "props": props,
        }
        (worked_on_rows if rel.relationship_type == "WORKED_ON" else demonstrates_rows).append(row)
    await relationships.merge_worked_on(neo4j_session, worked_on_rows)
    await relationships.merge_demonstrates(neo4j_session, demonstrates_rows)

    # ------------------------------------------------------------------
    # Step 5: delete this tender's own tender-scoped subgraph.
    # ------------------------------------------------------------------
    await cleanup.delete_tender_scoped_subgraph(neo4j_session, tid)

    # ------------------------------------------------------------------
    # Step 6: recreate tender-scoped nodes.
    # ------------------------------------------------------------------
    await nodes.merge_tender_nodes(
        neo4j_session,
        [
            {
                "id": tid,
                "tender_id": tid,
                "organization_id": org_id,
                "name": tender.name,
                "status": tender.status,
                "state_version": tender.state_version,
                # D6's expired-certification contradiction check compares
                # cert.valid_until against tender.submission_date - missing
                # this property meant that check could never fire, caught
                # live by test_expired_certification_contradiction_found.
                "submission_date": tender.submission_date,
            }
        ],
    )
    await nodes.merge_requirement_nodes(
        neo4j_session,
        [
            {
                "id": str(r.id),
                "tender_id": tid,
                "display_code": r.display_code,
                "category": r.category,
                "mandatory": r.mandatory,
                "verified": r.verified,
            }
            for r in requirements
        ],
    )
    await nodes.merge_evidence_nodes(
        neo4j_session,
        [
            {
                "id": str(ev.id),
                "tender_id": tid,
                "source_kind": ev.source_kind,
                "verified": ev.verified,
                "validity_status": ev.validity_status,
                "confidence": ev.confidence,
            }
            for ev in evidence_links
        ],
    )
    requirement_by_id = {r.id: r for r in requirements}
    compliance_rows = []
    for entry in compliance_entries:
        requirement = requirement_by_id.get(entry.requirement_id)
        if requirement is None:
            continue
        compliance_rows.append(
            {
                "id": str(entry.id),
                "tender_id": tid,
                "status": entry.status,
                "human_approved": entry.human_approved,
                "confidence": entry.confidence,
            }
        )
    await nodes.merge_compliance_nodes(neo4j_session, compliance_rows)
    await nodes.merge_role_nodes(
        neo4j_session,
        [
            {
                "id": str(rr.id),
                "tender_id": tid,
                "role_name": rr.role_name,
                "min_experience_years": (
                    float(rr.min_experience_years) if rr.min_experience_years is not None else None
                ),
            }
            for rr in role_requirements
        ],
    )
    await nodes.merge_proposal_section_nodes(
        neo4j_session,
        [
            {
                "id": str(s.id),
                "tender_id": tid,
                "section_name": s.section_name,
                "status": s.status,
                "version": s.version,
            }
            for s in sections
        ],
    )
    await nodes.merge_proposal_claim_nodes(
        neo4j_session,
        [
            {
                "id": str(c.id),
                "tender_id": tid,
                "claim_type": c.claim_type,
                "is_factual": c.is_factual,
                "grounding_status": c.grounding_status,
            }
            for c in all_claims
        ],
    )

    # ------------------------------------------------------------------
    # Step 7: recreate tender-specific relationships.
    # ------------------------------------------------------------------
    await relationships.merge_satisfies(
        neo4j_session,
        [
            {"role_id": str(role_id), "requirement_id": str(src.requirement_id)}
            for role_id, sources in role_sources_by_role.items()
            for src in sources
        ],
    )
    provides_rows = [
        {"knowledge_item_id": str(ev.knowledge_item_id), "evidence_id": str(ev.id)}
        for ev in evidence_links
        if ev.knowledge_item_id is not None
    ]
    await relationships.merge_provides(neo4j_session, provides_rows)
    await relationships.merge_supports(
        neo4j_session,
        [
            # list_evidence_for_compliance_entry returns EvidenceLink rows
            # directly (already joined through compliance_evidence), unlike
            # list_evidence_for_claim which returns the junction row itself
            # - so `link` here IS the evidence link; .id, not
            # .evidence_link_id.
            {"evidence_id": str(link.id), "compliance_id": str(entry_id)}
            for entry_id, links in compliance_evidence_by_entry.items()
            for link in links
        ],
    )
    await relationships.merge_decides(
        neo4j_session,
        [
            {"compliance_id": str(entry.id), "requirement_id": str(entry.requirement_id)}
            for entry in compliance_entries
            if entry.requirement_id in requirement_by_id
        ],
    )
    await relationships.merge_cited_in(
        neo4j_session,
        [
            {"evidence_id": str(link.evidence_link_id), "claim_id": str(claim_id)}
            for claim_id, links in claim_evidence_by_claim.items()
            for link in links
        ],
    )
    await relationships.merge_addresses(
        neo4j_session,
        [
            {"claim_id": str(claim_id), "requirement_id": str(link.requirement_id)}
            for claim_id, links in claim_requirements_by_claim.items()
            for link in links
        ],
    )
    await relationships.merge_named_in(
        neo4j_session,
        [
            {"employee_id": str(link.employee_knowledge_item_id), "claim_id": str(claim_id)}
            for claim_id, links in claim_employees_by_claim.items()
            for link in links
        ],
    )
    await relationships.merge_part_of(
        neo4j_session,
        [
            {"claim_id": str(c.id), "section_id": str(s.id)}
            for s in sections
            for c in claims_by_section[s.id]
        ],
    )
    fills_rows = [
        {"employee_id": str(m.employee_knowledge_item_id), "role_id": str(role_id), "props": {}}
        for role_id, matches in role_matches_by_role.items()
        for m in matches
        if m.human_selected
    ]
    await relationships.merge_fills(neo4j_session, fills_rows)

    # ------------------------------------------------------------------
    # Step 8: MERGE persistent USED_IN, including selected=false.
    # ------------------------------------------------------------------
    used_in_rows = [
        {
            "knowledge_item_id": str(sel.knowledge_item_id),
            "tender_id": tid,
            "props": {
                "selected": sel.selected,
                "selected_at": sel.selected_at,
                "deselected_at": sel.deselected_at,
            },
        }
        for sel in selections
        if sel.knowledge_item_id in resolved
    ]
    await relationships.merge_used_in(neo4j_session, used_in_rows)

    # ------------------------------------------------------------------
    # Step 10: verify basic projection counts - read the ACTUAL post-write
    # state back from Neo4j rather than trusting that the MERGE calls above
    # wrote what was intended. This is what makes node_count/
    # relationship_count a fact about Neo4j, not an echo of Python-side
    # input sizes.
    # ------------------------------------------------------------------
    return await queries.count_tender_subgraph(neo4j_session, tid)
