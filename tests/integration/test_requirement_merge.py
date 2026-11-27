"""Plan item 126 (user decisions 2026-11-27: Q1 a, Q2 a refined, Q3 a, Q4 a,
Q5 a): a manual merge or split keeps what the originals had, and the next
extraction does not undo it.

Merge: the first id survives in place; the others' compliance entries (by
the agree rule), claim links, role sources, clarifications, citations and
extraction keys move to it; differing decisions refuse. Split: the original
is the first part, in place; the later parts get its citations, claim links
and role sources. The next projection rebuilds the graph from these rows.

Real Postgres and real Neo4j. Requirements are made through extraction's
own persist, so their keys and citations are the ones extraction writes.
"""

import logging
import uuid
from datetime import date
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from tenderos_backend.agents.llm.fake_client import FakeLLMClient
from tenderos_backend.agents.requirements import verify as verify_module
from tenderos_backend.agents.schemas.requirements import VerificationOutput
from tenderos_backend.db.models.audit import AuditEvent
from tenderos_backend.db.models.compliance import ComplianceEntry, ComplianceEvidence
from tenderos_backend.db.models.outbox import OutboxEvent
from tenderos_backend.db.models.proposal import ProposalClaimRequirement
from tenderos_backend.db.models.requirement import (
    ClarificationItem,
    Requirement,
    RequirementCitation,
    RequirementKey,
)
from tenderos_backend.db.models.talent import RoleRequirementSource
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import graph_service, requirement_service
from tenderos_backend.domain.services.auth_service import register
from tenderos_backend.domain.services.requirement_service import (
    MergeComplianceConflictError,
    RepeatedRequirementError,
    RequirementDraft,
    RequirementRejectedError,
    persist_extraction_results,
)
from tenderos_backend.domain.services.tender_service import create_tender
from tenderos_backend.graph_projection.driver import get_session as get_neo4j_session
from tenderos_worker.jobs import verification as verification_job

pytestmark = pytest.mark.asyncio

FIRST = "The bidder shall hold a valid tax clearance certificate."
SECOND = "The bidder shall be registered for VAT."
THIRD = "The bidder shall hold a tax clearance certificate on the closing date."


@pytest_asyncio.fixture(autouse=True)
async def _clean_neo4j():
    yield
    async with get_neo4j_session() as session:
        await session.run("MATCH (n) DETACH DELETE n")


async def _bid() -> dict[str, Any]:
    """A bid with one document: chunks 0 and 1 carry FIRST between them,
    chunk 5 holds SECOND and THIRD. FIRST is extracted from both its chunks
    (one row, two citations, two keys); SECOND and THIRD from chunk 5."""
    registered = await register(
        email=f"{uuid.uuid4().hex}@example.com",
        password=uuid.uuid4().hex,
        organization_name="Item 126 Org",
    )
    org_id, actor_id = registered.organization.id, registered.user.id
    tender = await create_tender(
        organization_id=org_id,
        name="Item 126 Bid",
        created_by=actor_id,
        submission_date=date(2026, 12, 1),
    )
    async with UnitOfWork() as uow:
        document = await uow.documents.create(
            side="client",
            doc_type="rfp",
            filename="rfp.docx",
            storage_key=f"test/{uuid.uuid4()}",
            sha256=uuid.uuid4().hex,
            mime_type="application/octet-stream",
            file_size_bytes=10,
            tender_id=tender.id,
        )
        chunks = {}
        for index, lines in (
            (0, ["Section 2: Eligibility.", FIRST]),
            (1, [FIRST, "Bids are valid for ninety days."]),
            (5, ["Section 9.", SECOND, THIRD]),
        ):
            body = "\n".join(lines)
            chunk = await uow.documents.create_chunk(
                document_id=document.id,
                chunk_index=index,
                chunk_text=body,
                normalized_text=" ".join(body.split()),
                content_hash=uuid.uuid4().hex,
                page_num=1,
            )
            chunks[index] = chunk.id
        await uow.commit()
    ids: dict[str, Any] = {
        "org": org_id,
        "actor": actor_id,
        "tender": tender.id,
        "document": document.id,
        "chunks": chunks,
    }
    first, second, third = await _extract(ids, (FIRST, 0), (FIRST, 1), (SECOND, 5), (THIRD, 5))
    ids.update(first=first, second=second, third=third)
    return ids


def _drafts(ids: dict[str, Any], *found: tuple[str, int]) -> list[RequirementDraft]:
    return [
        RequirementDraft(
            requirement_text=wording,
            category="eligibility",
            mandatory=True,
            source_chunk_id=ids["chunks"][index],
            document_id=ids["document"],
        )
        for wording, index in found
    ]


async def _extract(ids: dict[str, Any], *found: tuple[str, int]) -> list[uuid.UUID]:
    return [r.id for r in await persist_extraction_results(ids["tender"], _drafts(ids, *found))]


async def _entry(
    requirement_id: uuid.UUID,
    status: str = "fully_compliant",
    approved: bool = True,
    decided_by: str = "analysis",
    evidence: tuple[uuid.UUID, ...] = (),
) -> uuid.UUID:
    async with UnitOfWork() as uow:
        entry = await uow.compliance.create_compliance_entry(
            requirement_id=requirement_id, status=status
        )
        await uow.compliance.update_compliance_entry(
            entry.id, human_approved=approved, decided_by=decided_by
        )
        for link in evidence:
            await uow.compliance.link_evidence(entry.id, link)
        await uow.commit()
        return entry.id


async def _evidence(ids: dict[str, Any]) -> uuid.UUID:
    async with UnitOfWork() as uow:
        link = await uow.compliance.create_evidence_link(
            tender_id=ids["tender"],
            source_kind="document_excerpt",
            content_fingerprint=uuid.uuid4().hex,
            source_chunk_id=ids["chunks"][5],
            excerpt="A tax clearance certificate is attached.",
            verified=True,
            validity_status="valid",
        )
        await uow.commit()
        return link.id


async def _claim(ids: dict[str, Any], *requirement_ids: uuid.UUID) -> uuid.UUID:
    async with UnitOfWork() as uow:
        if "section" not in ids:
            section = await uow.proposal.create_proposal_section(
                tender_id=ids["tender"], section_name="Eligibility"
            )
            ids["section"] = section.id
        claim = await uow.proposal.create_proposal_claim(
            section_id=ids["section"],
            claim_text=f"We comply. {uuid.uuid4().hex[:6]}",
            claim_type="compliance",
            is_factual=True,
            grounding_status="grounded",
            content_fingerprint=uuid.uuid4().hex,
        )
        for requirement_id in requirement_ids:
            await uow.proposal.link_claim_requirement(claim.id, requirement_id)
        await uow.commit()
        return claim.id


async def _role(ids: dict[str, Any], *requirement_ids: uuid.UUID) -> uuid.UUID:
    async with UnitOfWork() as uow:
        role = await uow.talent.create_role_requirement(
            tender_id=ids["tender"],
            role_name=f"Lead {uuid.uuid4().hex[:6]}",
            content_fingerprint=uuid.uuid4().hex,
        )
        for requirement_id in requirement_ids:
            await uow.talent.link_role_requirement_source(role.id, requirement_id)
        await uow.commit()
        return role.id


async def _clarification(ids: dict[str, Any], requirement_id: uuid.UUID) -> uuid.UUID:
    async with UnitOfWork() as uow:
        item = await uow.requirements.create_clarification_item(
            tender_id=ids["tender"],
            question=f"Which certificate? {uuid.uuid4().hex[:6]}",
            content_fingerprint=uuid.uuid4().hex,
            requirement_id=requirement_id,
        )
        await uow.commit()
        return item.id


async def _merge(ids: dict[str, Any], order: list[uuid.UUID], wording: str = "Merged.") -> Any:
    return await requirement_service.merge_requirements(
        order, wording, "eligibility", True, ids["org"], ids["actor"]
    )


async def _column(statement: Any) -> set:
    async with get_session() as session:
        return set((await session.execute(statement)).scalars().all())


async def _requirement_ids(tender_id: uuid.UUID) -> set[uuid.UUID]:
    return await _column(select(Requirement.id).where(Requirement.tender_id == tender_id))


async def _version(tender_id: uuid.UUID) -> int:
    async with UnitOfWork() as uow:
        tender = await uow.tenders.get(tender_id)
        assert tender is not None
        return tender.state_version


async def test_a_merge_keeps_everything_the_originals_had():
    ids = await _bid()
    first, second = ids["first"], ids["second"]
    async with UnitOfWork() as uow:
        # A citation both originals have, so the survivor's is kept once.
        await uow.requirements.add_citation(second, ids["chunks"][1])
        await uow.commit()
    shared, only_second = await _evidence(ids), await _evidence(ids)
    kept_entry = await _entry(first, evidence=(shared,))
    dropped_entry = await _entry(second, evidence=(shared, only_second))
    both_claim = await _claim(ids, first, second)
    second_claim = await _claim(ids, second)
    both_role = await _role(ids, first, second)
    second_role = await _role(ids, second)
    clarifications = {await _clarification(ids, first), await _clarification(ids, second)}
    async with UnitOfWork() as uow:
        before = await uow.requirements.get(first)
        second_keys = set(await uow.requirements.list_keys(second))
        keys_before = set(await uow.requirements.list_keys(first)) | second_keys
    assert before is not None

    merged = await _merge(ids, [first, second])

    assert merged.id == first and merged.display_code == before.display_code
    assert merged.requirement_text == "Merged." and merged.human_reviewed is True
    assert await _requirement_ids(ids["tender"]) == {first, ids["third"]}
    # One compliance entry, the survivor's own, with both entries' evidence.
    async with get_session() as session:
        found = await session.execute(
            select(ComplianceEntry).where(ComplianceEntry.id.in_([kept_entry, dropped_entry]))
        )
        entries = found.scalars().all()
    assert [(e.id, e.requirement_id) for e in entries] == [(kept_entry, first)]
    assert await _column(
        select(ComplianceEvidence.evidence_link_id).where(
            ComplianceEvidence.compliance_entry_id == kept_entry
        )
    ) == {shared, only_second}
    # Every link once, now the survivor's.
    links = await _column(
        select(ProposalClaimRequirement.proposal_claim_id).where(
            ProposalClaimRequirement.requirement_id == first
        )
    )
    assert links == {both_claim, second_claim}
    assert await _column(
        select(RoleRequirementSource.role_requirement_id).where(
            RoleRequirementSource.requirement_id == first
        )
    ) == {both_role, second_role}
    assert (
        await _column(select(ClarificationItem.id).where(ClarificationItem.requirement_id == first))
        == clarifications
    )
    assert await _column(
        select(RequirementCitation.chunk_id).where(RequirementCitation.requirement_id == first)
    ) == {ids["chunks"][0], ids["chunks"][1], ids["chunks"][5]}
    assert (
        await _column(
            select(RequirementKey.content_fingerprint).where(RequirementKey.requirement_id == first)
        )
        == keys_before
    )
    # The audit records what moved.
    async with get_session() as session:
        event = (
            await session.execute(
                select(AuditEvent).where(
                    AuditEvent.action == "requirement_merge", AuditEvent.target_id == first
                )
            )
        ).scalar_one()
    assert [o["id"] for o in event.before["merged_from"]] == [str(first), str(second)]
    assert event.after["compliance_entry_kept"] == str(kept_entry)
    assert event.after["compliance_entries_dropped"] == [str(dropped_entry)]
    assert event.after["evidence_added"] == [str(only_second)]
    assert event.after["claims_linked"] == [str(second_claim)]
    assert event.after["roles_sourced"] == [str(second_role)]
    assert set(event.after["keys_moved"]) == second_keys


async def test_the_next_extraction_of_either_original_returns_the_survivor():
    ids = await _bid()
    await _merge(ids, [ids["first"], ids["second"]])
    before = await _requirement_ids(ids["tender"])

    returned = await _extract(ids, (FIRST, 0), (FIRST, 1), (SECOND, 5))

    assert returned == [ids["first"]], "each original's text finds the survivor, once"
    assert await _requirement_ids(ids["tender"]) == before


async def test_different_statuses_refuse_the_merge_and_write_nothing():
    ids = await _bid()
    await _entry(ids["first"], status="fully_compliant")
    await _entry(ids["second"], status="non_compliant")
    version = await _version(ids["tender"])

    with pytest.raises(MergeComplianceConflictError) as refused:
        await _merge(ids, [ids["first"], ids["second"]])

    assert [(d.status, d.human_approved) for d in refused.value.decisions] == [
        ("fully_compliant", True),
        ("non_compliant", True),
    ]
    assert await _requirement_ids(ids["tender"]) == {ids["first"], ids["second"], ids["third"]}
    async with UnitOfWork() as uow:
        first = await uow.requirements.get(ids["first"])
    assert first is not None and first.requirement_text == FIRST
    assert await _version(ids["tender"]) == version


async def test_a_different_approval_refuses_the_merge():
    ids = await _bid()
    await _entry(ids["first"], approved=True)
    await _entry(ids["second"], approved=False)

    with pytest.raises(MergeComplianceConflictError):
        await _merge(ids, [ids["first"], ids["second"]])


async def test_agreeing_decisions_by_a_reviewer_and_the_analysis_keep_the_reviewers():
    """Who decided may differ (the user's refinement of Q2 a): the
    reviewer's entry is kept, so a re-evaluation still leaves its status in
    place and reopens it (item 121) rather than replacing it."""
    ids = await _bid()
    analysis = await _entry(ids["first"], decided_by="analysis")
    reviewer = await _entry(ids["second"], decided_by="reviewer")

    await _merge(ids, [ids["first"], ids["second"]])

    async with UnitOfWork() as uow:
        entry = await uow.compliance.get_compliance_entry_by_requirement(ids["first"])
        gone = await uow.compliance.get_compliance_entry(analysis)
    assert entry is not None and entry.id == reviewer and entry.decided_by == "reviewer"
    assert gone is None


async def test_an_original_without_a_decision_takes_the_others():
    ids = await _bid()
    decided = await _entry(ids["second"])

    await _merge(ids, [ids["first"], ids["second"]])

    async with UnitOfWork() as uow:
        entry = await uow.compliance.get_compliance_entry_by_requirement(ids["first"])
    assert entry is not None and entry.id == decided


async def test_a_rejected_original_or_a_repeated_id_is_refused():
    ids = await _bid()
    await requirement_service.patch_requirement(
        ids["second"], ids["org"], ids["actor"], action="reject"
    )
    with pytest.raises(RequirementRejectedError):
        await _merge(ids, [ids["first"], ids["second"]])
    with pytest.raises(RepeatedRequirementError):
        await _merge(ids, [ids["first"], ids["first"]])
    assert await _requirement_ids(ids["tender"]) == {ids["first"], ids["second"], ids["third"]}


async def test_three_originals_two_from_one_chunk_leave_the_survivor_both_keys():
    ids = await _bid()
    await _merge(ids, [ids["first"], ids["second"], ids["third"]])

    returned = await _extract(ids, (SECOND, 5), (THIRD, 5))

    assert returned == [ids["first"]]
    assert await _requirement_ids(ids["tender"]) == {ids["first"]}


async def test_the_survivors_verdict_stays_for_the_same_text_and_resets_for_a_new_one():
    ids = await _bid()
    async with get_session() as session:
        await session.execute(
            text(
                "UPDATE requirements SET verified = true, verification_checked_at = now() "
                "WHERE id IN (:a, :b)"
            ),
            {"a": ids["first"], "b": ids["third"]},
        )
        await session.commit()

    same = await _merge(ids, [ids["first"], ids["second"]], wording=FIRST)
    assert same.verified is True and same.verification_checked_at is not None

    new = await _merge(ids, [ids["third"], ids["first"]], wording="A new text.")
    assert new.verified is False and new.verification_checked_at is None


async def test_every_requirements_own_key_is_in_requirement_keys_after_merge_and_split():
    ids = await _bid()
    await _merge(ids, [ids["second"], ids["third"]])
    await requirement_service.split_requirement(
        ids["first"],
        [{"requirement_text": "Part one."}, {"requirement_text": "Part two."}],
        ids["org"],
        ids["actor"],
    )
    async with get_session() as session:
        missing = (
            await session.execute(
                text(
                    "SELECT r.id FROM requirements r WHERE r.tender_id = :t AND NOT EXISTS ("
                    "SELECT 1 FROM requirement_keys k WHERE k.content_fingerprint = "
                    "r.content_fingerprint AND k.requirement_id = r.id)"
                ),
                {"t": ids["tender"]},
            )
        ).all()
    assert len(await _requirement_ids(ids["tender"])) == 3
    assert missing == []


async def _graph(tender_id: uuid.UUID) -> dict[str, set]:
    async with get_neo4j_session() as session:
        nodes = await (
            await session.run(
                "MATCH (n {tender_id: $t}) WHERE n:Requirement OR n:Compliance RETURN n.id AS id",
                t=str(tender_id),
            )
        ).data()
        edges = await (
            await session.run(
                "MATCH (a)-[r]->(q:Requirement {tender_id: $t}) "
                "RETURN type(r) AS type, a.id AS source, q.id AS target",
                t=str(tender_id),
            )
        ).data()
    return {
        "nodes": {row["id"] for row in nodes},
        "edges": {(row["type"], row["source"], row["target"]) for row in edges},
    }


async def test_the_next_projection_drops_the_originals_and_shows_the_moved_links():
    ids = await _bid()
    first, second = str(ids["first"]), str(ids["second"])
    await _entry(ids["first"])
    dropped = str(await _entry(ids["second"]))
    claim = str(await _claim(ids, ids["second"]))
    role = str(await _role(ids, ids["second"]))
    await graph_service.project_tender(ids["tender"])
    assert {second, dropped} <= (await _graph(ids["tender"]))["nodes"], "the premise"

    await _merge(ids, [ids["first"], ids["second"]])
    await graph_service.project_tender(ids["tender"])

    graph = await _graph(ids["tender"])
    assert not {second, dropped} & graph["nodes"]
    assert ("ADDRESSES", claim, first) in graph["edges"]
    assert ("SATISFIES", role, first) in graph["edges"]
    assert not any(target == second for _type, _source, target in graph["edges"])


async def test_a_split_keeps_the_original_as_its_first_part_with_everything_it_had():
    ids = await _bid()
    original = ids["first"]
    entry = await _entry(original)
    claim = await _claim(ids, original)
    role = await _role(ids, original)
    clarification = await _clarification(ids, original)
    async with get_session() as session:
        await session.execute(
            text(
                "UPDATE requirements SET verified = true, verification_checked_at = now() "
                "WHERE id = :id"
            ),
            {"id": original},
        )
        await session.commit()
    async with UnitOfWork() as uow:
        keys = set(await uow.requirements.list_keys(original))

    first_part, later_part = await requirement_service.split_requirement(
        original,
        [
            {"requirement_text": "The bidder shall hold a tax clearance certificate."},
            {"requirement_text": "The certificate shall be valid.", "mandatory": False},
        ],
        ids["org"],
        ids["actor"],
    )

    assert first_part.id == original
    assert first_part.verified is False, "its text changed, as an edit"
    assert later_part.verified is False and later_part.human_reviewed is True
    async with UnitOfWork() as uow:
        kept = await uow.compliance.get_compliance_entry_by_requirement(original)
        none = await uow.compliance.get_compliance_entry_by_requirement(later_part.id)
        cited = await uow.requirements.list_citations([original, later_part.id])
        first_keys = set(await uow.requirements.list_keys(original))
        later_keys = await uow.requirements.list_keys(later_part.id)
    assert kept is not None and kept.id == entry and none is None
    chunks = [ids["chunks"][0], ids["chunks"][1]]
    assert cited[original] == chunks and cited[later_part.id] == chunks, "primary first"
    assert first_keys == keys
    assert later_keys == [later_part.content_fingerprint]
    for part in (original, later_part.id):
        assert await _column(
            select(ProposalClaimRequirement.proposal_claim_id).where(
                ProposalClaimRequirement.requirement_id == part
            )
        ) == {claim}
        assert await _column(
            select(RoleRequirementSource.role_requirement_id).where(
                RoleRequirementSource.requirement_id == part
            )
        ) == {role}
    assert await _column(
        select(ClarificationItem.requirement_id).where(ClarificationItem.id == clarification)
    ) == {original}


async def test_the_next_extraction_of_the_original_returns_the_first_part():
    ids = await _bid()
    parts = await requirement_service.split_requirement(
        ids["first"],
        [{"requirement_text": "Part one."}, {"requirement_text": "Part two."}],
        ids["org"],
        ids["actor"],
    )
    before = await _requirement_ids(ids["tender"])

    returned = await _extract(ids, (FIRST, 0), (FIRST, 1))

    assert returned == [parts[0].id] == [ids["first"]]
    assert await _requirement_ids(ids["tender"]) == before


async def test_a_rejected_requirement_is_not_split():
    ids = await _bid()
    await requirement_service.patch_requirement(
        ids["first"], ids["org"], ids["actor"], action="reject"
    )
    with pytest.raises(RequirementRejectedError):
        await requirement_service.split_requirement(
            ids["first"],
            [{"requirement_text": "Part one."}, {"requirement_text": "Part two."}],
            ids["org"],
            ids["actor"],
        )
    assert await _requirement_ids(ids["tender"]) == {ids["first"], ids["second"], ids["third"]}


# ----------------------------------------------------------------------
# The user's review of PR 39.
# ----------------------------------------------------------------------


async def _codes(tender_id: uuid.UUID) -> dict[uuid.UUID, str]:
    async with get_session() as session:
        rows = await session.execute(
            select(Requirement.id, Requirement.display_code).where(
                Requirement.tender_id == tender_id
            )
        )
        return {row[0]: row[1] for row in rows.all()}


async def _split(ids: dict[str, Any], requirement_id: uuid.UUID, *texts: str) -> list[Any]:
    return await requirement_service.split_requirement(
        requirement_id,
        [{"requirement_text": t} for t in texts],
        ids["org"],
        ids["actor"],
    )


async def test_a_number_a_merge_deleted_is_never_handed_out_again():
    """ELIG-003 is merged away. Counting the category's rows, the next
    extraction would get ELIG-003 again, and the next split part ELIG-004
    after it; the counter gives ELIG-004 and ELIG-005."""
    ids = await _bid()
    codes = await _codes(ids["tender"])
    assert [codes[ids[k]] for k in ("first", "second", "third")] == [
        "ELIG-001",
        "ELIG-002",
        "ELIG-003",
    ], "the premise"
    await _merge(ids, [ids["first"], ids["third"]])

    (extracted,) = await _extract(ids, ("The bidder shall name a contact person.", 5))
    _first_part, part = await _split(ids, ids["second"], "Part one.", "Part two.")

    codes = await _codes(ids["tender"])
    assert codes[extracted] == "ELIG-004"
    assert codes[part.id] == "ELIG-005"
    assert len(set(codes.values())) == len(codes), "no code in use twice"


async def test_two_splits_into_parts_with_the_same_words_both_succeed():
    """SECOND and THIRD come from one chunk. A later part's key used to be
    built from the original's chunk and the part's text, so the second split
    failed the fingerprint's unique constraint."""
    ids = await _bid()
    _a, from_second = await _split(ids, ids["second"], "Part A.", "The shared obligation.")
    _b, from_third = await _split(ids, ids["third"], "Part B.", "The shared obligation.")

    assert from_second.content_fingerprint != from_third.content_fingerprint
    for part in (from_second, from_third):
        async with UnitOfWork() as uow:
            assert await uow.requirements.list_keys(part.id) == [part.content_fingerprint]


async def _verification_event(requirement_id: uuid.UUID) -> uuid.UUID:
    await requirement_service.request_verification(requirement_id)
    async with get_session() as session:
        return (
            await session.execute(
                select(OutboxEvent.id).where(OutboxEvent.aggregate_id == requirement_id)
            )
        ).scalar_one()


async def test_a_verification_queued_before_a_merge_deleted_its_requirement_is_a_skip(
    monkeypatch, caplog
):
    """A person's merge must never leave a failed job behind: the job finds
    no requirement, writes nothing, makes no model call and logs at INFO."""
    ids = await _bid()
    event_id = await _verification_event(ids["second"])
    await _merge(ids, [ids["first"], ids["second"]])
    fake = FakeLLMClient()
    monkeypatch.setattr(verification_job, "_build_llm_client", lambda _organization_id: fake)

    with caplog.at_level(logging.INFO, logger=verification_job.__name__):
        await verification_job.verify_requirement_job(str(ids["second"]), str(event_id))

    assert len(fake.calls) == 0
    assert any(
        r.levelno == logging.INFO and "no longer exists; verification skipped" in r.getMessage()
        for r in caplog.records
    )


async def test_a_requirement_merged_away_while_it_is_verified_is_a_skip(monkeypatch, caplog):
    """The merge commits during the model call: the verdict has no row to
    land on, and the job ends at INFO instead of failing."""
    ids = await _bid()
    event_id = await _verification_event(ids["second"])
    monkeypatch.setattr(verification_job, "_build_llm_client", lambda _organization_id: None)

    async def judged_while_merged(*_args: Any, **_kwargs: Any) -> VerificationOutput:
        await _merge(ids, [ids["first"], ids["second"]])
        return VerificationOutput(
            supported_by_source=True,
            mandatory_language_supported=True,
            verification_confidence=0.9,
            reasoning_summary="The source chunk states the obligation.",
        )

    monkeypatch.setattr(verify_module, "verify_requirement", judged_while_merged)

    with caplog.at_level(logging.INFO, logger=verification_job.__name__):
        await verification_job.verify_requirement_job(str(ids["second"]), str(event_id))

    assert ids["second"] not in await _requirement_ids(ids["tender"]), "the premise"
    assert any(
        r.levelno == logging.INFO and "was deleted during verification" in r.getMessage()
        for r in caplog.records
    )
