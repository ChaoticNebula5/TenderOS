"""Readiness reads saved facts, not facts its caller hands it (plan items 94,
96 and 99), against real Postgres and Neo4j.

- run_red_team records every finished pass (redteam_passes): completed with
  its graph checks run at a version, completed with them skipped, or failed;
  a completed pass in the same transaction as its findings.
- Blocker 8 asks one question: has a complete pass, graph checks included,
  seen the bid's current state version? Otherwise it says why, from the
  newest pass.
- Blocker 9 blocks every tender document that is not parsed, and a bid with
  no documents.
"""

import uuid
from datetime import date

import pytest
import pytest_asyncio
from readiness_facts import add_parsed_document, project_and_review, record_complete_pass
from rq.timeouts import JobTimeoutException
from sqlalchemy import func, select
from tenderos_backend.agents.llm.fake_client import FakeLLMClient
from tenderos_backend.agents.schemas.redteam import RedteamCritique
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.redteam import RedteamFinding, RedteamPass
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import (
    graph_service,
    readiness_service,
    redteam_checks,
    redteam_service,
)
from tenderos_backend.graph_projection.driver import get_session as get_neo4j_session

pytestmark = pytest.mark.asyncio

INCOMPLETE = readiness_service.RED_TEAM_REVIEW_INCOMPLETE
NOT_PARSED = readiness_service.DOCUMENT_NOT_PARSED


@pytest_asyncio.fixture(autouse=True)
async def _clean_neo4j():
    yield
    async with get_neo4j_session() as session:
        await session.run("MATCH (n) DETACH DELETE n")


async def _tender(name: str) -> uuid.UUID:
    async with get_session() as session:
        org = Organization(name=f"{name} Org")
        session.add(org)
        await session.flush()
        tender = Tender(
            organization_id=org.id, name=f"{name} Tender", submission_date=date(2026, 12, 1)
        )
        session.add(tender)
        await session.commit()
        return tender.id


async def _bump(tender_id: uuid.UUID) -> None:
    async with UnitOfWork() as uow:
        await uow.tenders.bump_state_version(tender_id)
        await uow.commit()


async def _record(tender_id: uuid.UUID, **row) -> None:
    async with UnitOfWork() as uow:
        await uow.redteam.record_pass(tender_id, **row)
        await uow.commit()


async def _passes(tender_id: uuid.UUID) -> list[RedteamPass]:
    async with get_session() as session:
        result = await session.execute(
            select(RedteamPass)
            .where(RedteamPass.tender_id == tender_id)
            .order_by(RedteamPass.finished_at, RedteamPass.id)
        )
        return list(result.scalars().all())


async def _finding_count(tender_id: uuid.UUID) -> int:
    async with get_session() as session:
        return await session.scalar(
            select(func.count())
            .select_from(RedteamFinding)
            .where(RedteamFinding.tender_id == tender_id)
        )


def _messages(result: readiness_service.ReadinessResult, category: str) -> list[str]:
    return [b["message"] for b in result.blockers if b["category"] == category]


def _client() -> FakeLLMClient:
    client = FakeLLMClient()
    client.script("redteam_critique", RedteamCritique, [RedteamCritique(findings=[])] * 5)
    return client


class CritiqueDown(Exception):
    pass


# ----------------------------------------------------------------------
# Blocker 8: the red-team review, from recorded passes.
# ----------------------------------------------------------------------


async def test_no_pass_recorded_blocks():
    tender_id = await _tender("NoPass")
    await add_parsed_document(tender_id)
    await graph_service.project_tender(tender_id)

    result = await readiness_service.evaluate_readiness(tender_id)

    assert _messages(result, INCOMPLETE) == [
        "No red-team pass has been recorded for this bid. Run the red team."
    ]


async def test_a_failed_pass_with_no_complete_pass_at_this_version_blocks():
    tender_id = await _tender("FailedPass")
    await add_parsed_document(tender_id)
    await graph_service.project_tender(tender_id)
    await _record(tender_id, outcome="failed", error_code="LLMTimeoutError")

    result = await readiness_service.evaluate_readiness(tender_id)

    assert _messages(result, INCOMPLETE) == [
        "The most recent red-team pass failed. Run the red team again."
    ]


async def test_a_complete_pass_that_skipped_the_graph_checks_blocks():
    tender_id = await _tender("SkippedPass")
    await add_parsed_document(tender_id)
    await graph_service.project_tender(tender_id)
    await _record(tender_id, outcome="completed", graph_checks="skipped")

    result = await readiness_service.evaluate_readiness(tender_id)

    assert _messages(result, INCOMPLETE) == [
        "The most recent red-team pass could not check the graph (it was stale). "
        "Run the red team again."
    ]


async def test_a_reprojection_with_no_new_pass_blocks_with_both_versions():
    # Item 96: the graph is fresh again, but no pass has looked at it.
    tender_id = await _tender("OlderPass")
    await add_parsed_document(tender_id)
    reviewed = await project_and_review(tender_id)
    await _bump(tender_id)
    await graph_service.project_tender(tender_id)

    result = await readiness_service.evaluate_readiness(tender_id)

    assert readiness_service.STALE_GRAPH_PROJECTION not in {b["category"] for b in result.blockers}
    assert _messages(result, INCOMPLETE) == [
        f"The red-team review checked graph version {reviewed}; the bid is now at version "
        f"{reviewed + 1}. Run the red team again."
    ]


async def test_a_complete_pass_at_the_current_version_does_not_block():
    tender_id = await _tender("CompletePass")
    await add_parsed_document(tender_id)
    await project_and_review(tender_id)

    result = await readiness_service.evaluate_readiness(tender_id)

    assert result.ready is True
    assert result.blockers == []


async def test_a_failed_attempt_after_a_complete_pass_at_this_version_does_not_block():
    # Q1: the failed attempt wrote no findings, so the complete pass's are current.
    tender_id = await _tender("FailedAfterComplete")
    await add_parsed_document(tender_id)
    await project_and_review(tender_id)
    await _record(tender_id, outcome="failed", error_code="LLMTimeoutError")

    result = await readiness_service.evaluate_readiness(tender_id)

    assert _messages(result, INCOMPLETE) == []
    assert result.ready is True


async def test_a_complete_pass_after_a_failed_one_clears_the_blocker():
    # Item 94: a run's failed pass, then a successful manual pass at this version.
    tender_id = await _tender("ManualClears")
    await add_parsed_document(tender_id)
    await graph_service.project_tender(tender_id)
    await _record(tender_id, outcome="failed", error_code="LLMTimeoutError")
    assert _messages(await readiness_service.evaluate_readiness(tender_id), INCOMPLETE)

    await record_complete_pass(tender_id)

    assert _messages(await readiness_service.evaluate_readiness(tender_id), INCOMPLETE) == []


async def test_the_version_message_hashes_the_same_across_generates():
    tender_id = await _tender("StableHash")
    await add_parsed_document(tender_id)
    await project_and_review(tender_id)
    await _bump(tender_id)
    await graph_service.project_tender(tender_id)

    first = await readiness_service.evaluate_readiness(tender_id)
    second = await readiness_service.evaluate_readiness(tender_id)

    assert _messages(first, INCOMPLETE)
    assert second.report_id == first.report_id


# ----------------------------------------------------------------------
# run_red_team records the pass.
# ----------------------------------------------------------------------


async def test_a_pass_on_a_fresh_graph_is_recorded_complete_at_that_version():
    tender_id = await _tender("RecordRan")
    await graph_service.project_tender(tender_id)
    async with UnitOfWork() as uow:
        version = (await uow.tenders.get(tender_id)).state_version

    await redteam_service.run_red_team(_client(), "fake-model", tender_id)

    [recorded] = await _passes(tender_id)
    assert (recorded.outcome, recorded.graph_checks, recorded.graph_state_version) == (
        "completed",
        "ran",
        version,
    )
    assert (recorded.error_code, recorded.workflow_run_id, recorded.operation_id) == (
        None,
        None,
        None,
    )


async def test_a_pass_on_a_stale_graph_is_recorded_with_the_graph_checks_skipped():
    tender_id = await _tender("RecordSkipped")

    await redteam_service.run_red_team(_client(), "fake-model", tender_id)

    [recorded] = await _passes(tender_id)
    assert (recorded.outcome, recorded.graph_checks, recorded.graph_state_version) == (
        "completed",
        "skipped",
        None,
    )


async def test_a_failing_pass_is_recorded_failed_and_writes_no_findings(monkeypatch):
    tender_id = await _tender("RecordFailed")
    await graph_service.project_tender(tender_id)
    before = await _finding_count(tender_id)

    async def _down(*args, **kwargs):
        raise CritiqueDown("the critique could not be reached")

    monkeypatch.setattr(redteam_service, "critique_proposal", _down)

    with pytest.raises(CritiqueDown):
        await redteam_service.run_red_team(_client(), "fake-model", tender_id)

    [recorded] = await _passes(tender_id)
    assert (recorded.outcome, recorded.graph_checks, recorded.error_code) == (
        "failed",
        None,
        "CritiqueDown",
    )
    assert recorded.graph_state_version is None
    assert await _finding_count(tender_id) == before


async def test_a_job_timeout_inside_the_pass_is_recorded_failed_and_re_raised(monkeypatch):
    # RQ's JobTimeoutException is an Exception: when its signal lands while
    # the pass's own code is running, the attempt failed and is recorded
    # so, and the exception still reaches the job's own timeout handling.
    tender_id = await _tender("RecordTimeout")
    await graph_service.project_tender(tender_id)

    async def _times_out(*args, **kwargs):
        raise JobTimeoutException("Task exceeded maximum timeout value (900 seconds)")

    monkeypatch.setattr(redteam_service, "critique_proposal", _times_out)

    with pytest.raises(JobTimeoutException):
        await redteam_service.run_red_team(_client(), "fake-model", tender_id)

    assert [(p.outcome, p.graph_checks, p.error_code) for p in await _passes(tender_id)] == [
        ("failed", None, "JobTimeoutException")
    ]


async def test_a_failure_inside_the_findings_transaction_leaves_no_completed_row(monkeypatch):
    # The completed row, the findings' upsert and their retirement commit
    # together: a failure at the retirement rolls back the new findings and
    # the row, and the pass is recorded failed instead.
    tender_id = await _tender("SameTransaction")
    await graph_service.project_tender(tender_id)
    await _record_missing_mandatory_requirement(tender_id)

    async def _retire_fails(self, *args, **kwargs):
        raise CritiqueDown("retirement failed")

    from tenderos_backend.db.repositories.redteam import RedteamRepository

    monkeypatch.setattr(RedteamRepository, "auto_retire_undetected_findings", _retire_fails)

    with pytest.raises(CritiqueDown):
        await redteam_service.run_red_team(_client(), "fake-model", tender_id)

    assert await _finding_count(tender_id) == 0
    assert [(p.outcome, p.error_code) for p in await _passes(tender_id)] == [
        ("failed", "CritiqueDown")
    ]


@pytest.mark.parametrize(
    "lands_before",
    [
        # During the deterministic checks: they read data newer than the
        # version a read after them would see as current.
        "check_archived_employee_selected",
        # Between the deterministic checks and the graph checks.
        "check_graph_contradictions",
    ],
)
async def test_an_edit_and_projection_landing_mid_pass_record_the_older_version(
    monkeypatch, lands_before
):
    # The version is read at the start of the pass: an edit (its version
    # bump) and a projection landing after that read make the pass record
    # the older version, so readiness asks for another pass rather than
    # trusting data or a graph the pass may not have read.
    tender_id = await _tender("MidPass")
    await add_parsed_document(tender_id)
    await graph_service.project_tender(tender_id)
    async with UnitOfWork() as uow:
        before = (await uow.tenders.get(tender_id)).state_version
    real = getattr(redteam_checks, lands_before)

    async def _edit_and_projection_land_first(*args):
        await _bump(tender_id)
        await graph_service.project_tender(tender_id)
        return await real(*args)

    monkeypatch.setattr(redteam_checks, lands_before, _edit_and_projection_land_first)

    await redteam_service.run_red_team(_client(), "fake-model", tender_id)

    [recorded] = await _passes(tender_id)
    assert (recorded.graph_checks, recorded.graph_state_version) == ("ran", before)
    result = await readiness_service.evaluate_readiness(tender_id)
    assert _messages(result, INCOMPLETE) == [
        f"The red-team review checked graph version {before}; the bid is now at version "
        f"{before + 1}. Run the red team again."
    ]


async def _record_missing_mandatory_requirement(tender_id: uuid.UUID) -> None:
    # A mandatory requirement with no compliance entry: run_red_team's first
    # check always raises a finding for it.
    async with UnitOfWork() as uow:
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
            chunk_text="req",
            normalized_text="req",
            content_hash=uuid.uuid4().hex,
        )
        await uow.requirements.create(
            tender_id=tender_id,
            display_code="REQ-001",
            requirement_text="Sample requirement text.",
            normalized_text="sample requirement text.",
            category="experience",
            mandatory=True,
            source_chunk_id=chunk.id,
            content_fingerprint=uuid.uuid4().hex,
        )
        await uow.commit()


# ----------------------------------------------------------------------
# Blocker 9: documents not parsed.
# ----------------------------------------------------------------------


async def _document(tender_id: uuid.UUID, filename: str, status: str) -> uuid.UUID:
    async with UnitOfWork() as uow:
        document = await uow.documents.create(
            side="client",
            doc_type="rfp",
            filename=filename,
            storage_key=f"test/{uuid.uuid4()}",
            sha256=uuid.uuid4().hex,
            mime_type="application/pdf",
            file_size_bytes=10,
            tender_id=tender_id,
        )
        if status != "uploaded":
            await uow.documents.update_parse_status(document.id, status)
        await uow.commit()
        return document.id


async def test_every_document_not_parsed_blocks_with_its_status_and_retry_clears_it():
    tender_id = await _tender("Documents")
    await _document(tender_id, "rfp.pdf", "parsed")
    failed = await _document(tender_id, "pricing.xlsx", "failed")
    await _document(tender_id, "annex.docx", "uploaded")
    await _document(tender_id, "amendment.pdf", "queued")
    await _document(tender_id, "notes.pdf", "parsing")
    await project_and_review(tender_id)

    result = await readiness_service.evaluate_readiness(tender_id)

    assert sorted(_messages(result, NOT_PARSED)) == [
        "Document 'amendment.pdf' has not finished processing (status: queued).",
        "Document 'annex.docx' has not finished processing (status: uploaded).",
        "Document 'notes.pdf' has not finished processing (status: parsing).",
        "Document 'pricing.xlsx' failed to parse. Retry it on the Upload page.",
    ]
    assert result.counts["documents_not_parsed"] == 4
    assert {b["target_id"] for b in result.blockers if b["category"] == NOT_PARSED} >= {str(failed)}

    async with UnitOfWork() as uow:
        await uow.documents.update_parse_status(failed, "parsed")
        await uow.commit()
    after = await readiness_service.evaluate_readiness(tender_id)
    assert "Document 'pricing.xlsx' failed to parse. Retry it on the Upload page." not in (
        _messages(after, NOT_PARSED)
    )
    assert after.counts["documents_not_parsed"] == 3


async def test_a_bid_with_no_documents_is_blocked():
    tender_id = await _tender("NoDocuments")
    await project_and_review(tender_id)

    result = await readiness_service.evaluate_readiness(tender_id)

    assert _messages(result, NOT_PARSED) == ["The bid has no documents."]
    assert result.counts["documents_not_parsed"] == 0
    assert result.ready is False


async def test_only_parsed_documents_is_not_a_document_blocker():
    tender_id = await _tender("AllParsed")
    await _document(tender_id, "rfp.pdf", "parsed")
    await project_and_review(tender_id)

    result = await readiness_service.evaluate_readiness(tender_id)

    assert _messages(result, NOT_PARSED) == []
    assert result.counts["documents_not_parsed"] == 0
