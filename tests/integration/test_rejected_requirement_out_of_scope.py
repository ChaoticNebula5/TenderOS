"""Plan item 97: a rejected requirement is out of scope for its bid (user
decision, option c). Its compliance entry is left out of the compliance
listing (the matrix, the compliance gate, readiness, drafting, the graph); a
role whose every source requirement is rejected is left out of the role
listing (the role screen, the role gate, readiness, the red team, drafting,
the graph); the projector leaves the requirement out; a reject bumps. Nothing
is deleted. A claim stays and stays checked; one whose requirement links are
all to rejected requirements gets a minor finding (Q1 b). A clarification
linked to a rejected requirement leaves the requirement gate's and readiness's
counts (the PR's review).

Real Postgres and real Neo4j. The red team runs with a fake critique that
finds nothing, so every finding here comes from a deterministic or graph
check.
"""

import uuid
from datetime import date
from typing import Any

import pytest
import pytest_asyncio
from tenderos_backend.agents.llm.fake_client import FakeLLMClient
from tenderos_backend.agents.schemas.redteam import RedteamCritique
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import (
    gate_summary_service,
    graph_service,
    proposal_service,
    readiness_service,
    redteam_checks,
    redteam_service,
    requirement_service,
    talent_service,
)
from tenderos_backend.domain.services.auth_service import register
from tenderos_backend.domain.services.tender_service import create_tender
from tenderos_backend.graph_projection.driver import get_session as get_neo4j_session

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _clean_neo4j():
    yield
    async with get_neo4j_session() as session:
        await session.run("MATCH (n) DETACH DELETE n")


async def _bid() -> dict[str, Any]:
    """A bid whose mandatory requirement R has an approved fully_compliant
    entry backed by an expired certificate, a factual unsupported claim that
    addresses only R, and a role sourced only from R with no pick: the
    reproduction in the item 97 design. Plus a live requirement L."""
    registered = await register(
        email=f"{uuid.uuid4().hex}@example.com",
        password=uuid.uuid4().hex,
        organization_name="Item 97 Org",
    )
    org_id, actor_id = registered.organization.id, registered.user.id
    tender = await create_tender(
        organization_id=org_id,
        name="Item 97 Bid",
        created_by=actor_id,
        submission_date=date(2026, 12, 1),
    )
    ids: dict[str, Any] = {"org": org_id, "actor": actor_id, "tender": tender.id}
    async with UnitOfWork() as uow:
        document = await uow.documents.create(
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
            document_id=document.id,
            chunk_index=0,
            chunk_text="Ten years of experience.",
            normalized_text="ten years of experience.",
            content_hash=uuid.uuid4().hex,
        )

        async def requirement(code: str, text: str) -> uuid.UUID:
            row = await uow.requirements.create(
                tender_id=tender.id,
                display_code=code,
                requirement_text=text,
                normalized_text=text.lower(),
                category="experience",
                mandatory=True,
                source_chunk_id=chunk.id,
                content_fingerprint=uuid.uuid4().hex,
            )
            return row.id

        ids["requirement"] = await requirement("EXP-001", "Ten years of experience.")
        ids["live"] = await requirement("EXP-002", "Five delivery leads.")
        holder, _ = await uow.knowledge.create_employee(
            organization_id=org_id,
            name="Holder",
            content_fingerprint=uuid.uuid4().hex,
            title="Holder",
        )
        cert, _ = await uow.knowledge.create_certification(
            organization_id=org_id,
            title="Expired Certification",
            content_fingerprint=uuid.uuid4().hex,
            holder_employee_item_id=holder.id,
            valid_until=date(2020, 1, 1),
        )
        ids["certificate"] = cert.id
        await uow.knowledge.upsert_selection(tender.id, cert.id, True, actor_id)
        link = await uow.compliance.create_evidence_link(
            tender_id=tender.id,
            source_kind="certification",
            content_fingerprint=uuid.uuid4().hex,
            knowledge_item_id=cert.id,
            verified=True,
            validity_status="valid",
        )
        entry = await uow.compliance.create_compliance_entry(
            requirement_id=ids["requirement"], status="fully_compliant"
        )
        await uow.compliance.link_evidence(entry.id, link.id)
        await uow.compliance.update_compliance_entry(entry.id, human_approved=True)
        ids["entry"] = entry.id
        section = await uow.proposal.create_proposal_section(
            tender_id=tender.id, section_name="Experience"
        )
        ids["section"] = section.id
        claim = await uow.proposal.create_proposal_claim(
            section_id=section.id,
            claim_text="We have ten years of experience.",
            claim_type="experience",
            is_factual=True,
            grounding_status="unsupported",
            content_fingerprint=uuid.uuid4().hex,
        )
        await uow.proposal.link_claim_requirement(claim.id, ids["requirement"])
        ids["claim"] = claim.id
        role = await uow.talent.create_role_requirement(
            tender_id=tender.id, role_name="Delivery Lead", content_fingerprint=uuid.uuid4().hex
        )
        await uow.talent.link_role_requirement_source(role.id, ids["requirement"])
        ids["role"] = role.id
        await uow.commit()
    return ids


async def _reject(ids: dict[str, Any], requirement_key: str = "requirement") -> None:
    await requirement_service.patch_requirement(
        ids[requirement_key], ids["org"], ids["actor"], action="reject"
    )


async def _red_team(tender_id: uuid.UUID) -> None:
    client = FakeLLMClient()
    client.script("redteam_critique", RedteamCritique, [RedteamCritique(findings=[])])
    await redteam_service.run_red_team(client, "fake-model", tender_id)


async def _version(tender_id: uuid.UUID) -> int:
    async with UnitOfWork() as uow:
        tender = await uow.tenders.get(tender_id)
        assert tender is not None
        return tender.state_version


async def _findings(tender_id: uuid.UUID) -> list[Any]:
    async with UnitOfWork() as uow:
        return await uow.redteam.list_for_tender(tender_id)


def _open_on(findings: list[Any], target_id: uuid.UUID) -> set[tuple[str, str]]:
    return {
        (f.finding_category, f.severity)
        for f in findings
        if f.target_id == target_id and not f.resolved
    }


async def test_after_a_reject_a_new_pass_retires_what_reached_it_and_readiness_asks_for_it():
    ids = await _bid()
    tender_id = ids["tender"]
    of_it = {ids["requirement"], ids["entry"], ids["role"]}

    # Before the reject: the reproduction's findings and blockers.
    await graph_service.project_tender(tender_id)
    await _red_team(tender_id)
    before = await _findings(tender_id)
    assert _open_on(before, ids["entry"]) == {("expired_certification", "critical")}
    assert _open_on(before, ids["claim"]) == {("unsupported_claim", "critical")}
    report = await readiness_service.evaluate_readiness(tender_id)
    assert {(b["category"], b["target_id"]) for b in report.blockers} >= {
        ("compliant_decision_without_valid_evidence", str(ids["entry"])),
        ("role_with_no_selected_candidate", str(ids["role"])),
    }

    # The reject bumps, so readiness asks for a new projection and a new pass.
    version = await _version(tender_id)
    await _reject(ids)
    assert await _version(tender_id) == version + 1
    report = await readiness_service.evaluate_readiness(tender_id)
    categories = {b["category"] for b in report.blockers}
    assert {"stale_graph_projection", "red_team_review_incomplete"} <= categories
    expired = next(f for f in before if f.finding_category == "expired_certification")
    assert ("critical_redteam_finding", str(expired.id)) in {
        (b["category"], b["target_id"]) for b in report.blockers
    }, "the earlier finding stays open until a new pass looks again"

    # The new projection and pass.
    await graph_service.project_tender(tender_id)
    await _red_team(tender_id)
    after = await _findings(tender_id)
    retired = next(f for f in after if f.id == expired.id)
    assert retired.resolved and retired.resolution_source == "system"
    assert not any(f.target_id in of_it and not f.resolved for f in after)
    # The claim stays and stays checked (the sentence is still in the bid),
    # and gains the minor finding that it addresses only rejected ones.
    assert _open_on(after, ids["claim"]) == {
        ("unsupported_claim", "critical"),
        ("claim_addresses_rejected_requirement", "minor"),
    }

    report = await readiness_service.evaluate_readiness(tender_id)
    categories = {b["category"] for b in report.blockers}
    assert "stale_graph_projection" not in categories
    assert "red_team_review_incomplete" not in categories
    finding_targets = {str(f.id): f.target_id for f in after}
    for blocker in report.blockers:
        target = blocker["target_id"]
        assert target not in {str(i) for i in of_it}, blocker
        assert finding_targets.get(target) not in of_it, blocker
    open_minor = next(
        f for f in after if f.finding_category == "claim_addresses_rejected_requirement"
    )
    assert str(open_minor.id) not in {b["target_id"] for b in report.blockers}, "minor"

    # The graph and the impact walk no longer reach it; the gates do not count it.
    async with get_neo4j_session() as session:
        record = await (
            await session.run(
                "MATCH (n) WHERE n.id IN $ids RETURN count(n) AS c",
                ids=[str(ids["requirement"]), str(ids["entry"]), str(ids["role"])],
            )
        ).single()
    assert record is not None and record["c"] == 0
    impact = await graph_service.preview_impact(tender_id, ids["certificate"])
    reached = {(row.get("affected") or {}).get("id") for row in impact}
    assert not reached & {str(ids["requirement"]), str(ids["entry"])}
    async with UnitOfWork() as uow:
        compliance = await gate_summary_service.compliance_review_summary(uow, tender_id)
        resources = await gate_summary_service.resource_selection_summary(uow, tender_id)
    assert compliance["compliance_entry_count"] == 0
    assert resources["role_count"] == 0


async def test_the_compliance_listing_leaves_a_rejected_requirements_entry_out_by_default():
    ids = await _bid()
    await _reject(ids)
    async with UnitOfWork() as uow:
        listed = await uow.compliance.list_compliance_entries_for_tender(ids["tender"])
        assert ids["entry"] not in {e.id for e in listed}
        with_rejected = await uow.compliance.list_compliance_entries_for_tender(
            ids["tender"], include_rejected=True
        )
        kept = await uow.compliance.get_compliance_entry_by_requirement(ids["requirement"])
    assert ids["entry"] in {e.id for e in with_rejected}
    assert kept is not None and kept.id == ids["entry"], "nothing is deleted"


async def test_drafting_no_longer_offers_a_rejected_requirement_as_a_fact():
    ids = await _bid()
    async with UnitOfWork() as uow:
        offered = await proposal_service._facts_from_compliance(
            uow, ids["tender"], frozenset({"experience"})
        )
    assert ids["requirement"] in {f.requirement_id for f in offered}, "the premise"
    await _reject(ids)
    async with UnitOfWork() as uow:
        offered = await proposal_service._facts_from_compliance(
            uow, ids["tender"], frozenset({"experience"})
        )
        statuses = await proposal_service._compliance_status_by_requirement(uow, ids["tender"])
    assert ids["requirement"] not in {f.requirement_id for f in offered}
    assert ids["requirement"] not in statuses


async def test_a_claim_addressing_only_rejected_requirements_gets_a_minor_finding():
    ids = await _bid()
    async with UnitOfWork() as uow:

        async def claim(text: str, *requirement_ids: uuid.UUID) -> uuid.UUID:
            row = await uow.proposal.create_proposal_claim(
                section_id=ids["section"],
                claim_text=text,
                claim_type="experience",
                is_factual=True,
                grounding_status="grounded",
                content_fingerprint=uuid.uuid4().hex,
            )
            for requirement_id in requirement_ids:
                await uow.proposal.link_claim_requirement(row.id, requirement_id)
            return row.id

        await claim("Ten years and five leads.", ids["requirement"], ids["live"])
        await claim("We are a reliable partner.")
        await uow.commit()

    async with UnitOfWork() as uow:
        drafts = await redteam_checks.check_claims_addressing_rejected_requirements(
            uow, ids["tender"]
        )
    assert drafts == [], "no requirement is rejected yet"

    await _reject(ids)
    async with UnitOfWork() as uow:
        drafts = await redteam_checks.check_claims_addressing_rejected_requirements(
            uow, ids["tender"]
        )
    assert [(d.target_id, d.severity, d.finding_category) for d in drafts] == [
        (ids["claim"], "minor", "claim_addresses_rejected_requirement")
    ], "not the claim with a live link, nor the one with no link"
    assert "EXP-001" in drafts[0].finding_text


async def test_a_role_whose_every_source_is_rejected_is_out_of_scope_and_one_live_source_keeps_it():
    ids = await _bid()
    async with UnitOfWork() as uow:
        mixed = await uow.talent.create_role_requirement(
            tender_id=ids["tender"], role_name="Mixed Lead", content_fingerprint=uuid.uuid4().hex
        )
        await uow.talent.link_role_requirement_source(mixed.id, ids["requirement"])
        await uow.talent.link_role_requirement_source(mixed.id, ids["live"])
        unsourced = await uow.talent.create_role_requirement(
            tender_id=ids["tender"], role_name="Unsourced", content_fingerprint=uuid.uuid4().hex
        )
        await uow.commit()
    await _reject(ids)

    in_scope = {mixed.id, unsourced.id}
    assert {
        r.id for r in await talent_service.list_role_requirements_for_tender(ids["tender"])
    } == (in_scope), "the role screen's listing"
    async with UnitOfWork() as uow:
        summary = await gate_summary_service.resource_selection_summary(uow, ids["tender"])
        every = await uow.talent.list_role_requirements_for_tender(
            ids["tender"], include_rejected=True
        )
    assert summary["role_count"] == 2 and summary["unselected_role_count"] == 2
    assert {r.id for r in every} == in_scope | {ids["role"]}, "nothing is deleted"
    report = await readiness_service.evaluate_readiness(ids["tender"])
    role_blockers = {
        b["target_id"]
        for b in report.blockers
        if b["category"] == "role_with_no_selected_candidate"
    }
    assert role_blockers == {str(mixed.id), str(unsourced.id)}


async def test_a_rejected_requirements_clarification_leaves_the_gate_and_readiness_counts():
    ids = await _bid()
    async with UnitOfWork() as uow:
        for requirement_id in (ids["requirement"], ids["live"], None):
            await uow.requirements.create_clarification_item(
                tender_id=ids["tender"],
                question=f"Which one? {uuid.uuid4().hex}",
                content_fingerprint=uuid.uuid4().hex,
                requirement_id=requirement_id,
            )
        await uow.commit()

    async def counts() -> tuple[int, int]:
        async with UnitOfWork() as uow:
            summary = await gate_summary_service.requirement_review_summary(uow, ids["tender"])
        report = await readiness_service.evaluate_readiness(ids["tender"])
        return summary["clarification_count"], report.counts["open_clarifications"]

    assert await counts() == (3, 3), "the premise"
    await _reject(ids)
    assert await counts() == (2, 2), "the live one and the unlinked one stay"
    async with UnitOfWork() as uow:
        every = await uow.requirements.list_clarification_items_for_tender(
            ids["tender"], include_rejected=True
        )
    assert len(every) == 3, "nothing is deleted"
