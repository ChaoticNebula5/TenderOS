"""Plan item 20 (decision 0034): the outbox re-drive tool.

A re-drive resets a dead-lettered outbox row (status pending, attempt_count
0, available_at now()) and nothing else, and refuses, with one line saying
why, an event whose job would do nothing: the guards from items 18, 19 and
the Phase 4 consumer guards are reported, never fought.

Real Postgres; the one delivery test uses the real dispatcher's per-event
path against the test Redis. Events these tests re-drive are set back to
`dispatched` afterwards, so a later dispatcher test in the same run never
delivers them.
"""

import importlib.util
import io
import logging
import subprocess
import sys
import uuid
from datetime import timedelta
from pathlib import Path

import docx
import pytest
import pytest_asyncio
from _helpers import register_org_and_tender
from redis import Redis
from sqlalchemy import func, select, update
from tenderos_backend.db.models.async_operation import AsyncOperation
from tenderos_backend.db.models.outbox import OutboxEvent
from tenderos_backend.db.models.reanalysis_claim import ReanalysisClaim
from tenderos_backend.db.models.workflow import ApprovalGate, WorkflowRun
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.events.outbox_events import (
    AGGREGATE_APPROVAL_GATE,
    AGGREGATE_ASYNC_OPERATION,
    AGGREGATE_DOCUMENT,
    AGGREGATE_KNOWLEDGE_SELECTION,
    AGGREGATE_REQUIREMENT,
    AGGREGATE_TENDER,
    AGGREGATE_WORKFLOW_RUN,
    EVENT_DOCUMENT_UPLOADED,
    EVENT_GATE_DECISION_RECORDED,
    EVENT_KNOWLEDGE_DOCUMENT_UPLOADED,
    EVENT_KNOWLEDGE_SELECTION_CHANGED,
    EVENT_OPERATION_REQUESTED,
    EVENT_REQUIREMENT_VERIFICATION_REQUESTED,
    EVENT_TENDER_CREATED,
    EVENT_WORKFLOW_RUN_CREATED,
    gate_decision_recorded_payload,
    knowledge_document_uploaded_payload,
    knowledge_selection_changed_payload,
    operation_requested_payload,
    requirement_verification_requested_payload,
    tender_created_payload,
    workflow_run_created_payload,
)
from tenderos_backend.domain.services import document_service, outbox_redrive, run_reaper
from tenderos_backend.domain.services.requirement_service import (
    RequirementDraft,
    persist_extraction_results,
)
from tenderos_worker import outbox_dispatcher
from tenderos_worker.jobs.ingestion import PARSER_VERSION, parse_and_embed_document
from tenderos_worker.outbox_dispatcher import MAX_ATTEMPTS
from tenderos_worker.settings import settings

pytestmark = pytest.mark.asyncio

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "redrive_outbox", REPO_ROOT / "scripts" / "redrive_outbox.py"
)
assert _spec is not None and _spec.loader is not None
redrive_cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(redrive_cli)

CTX = outbox_redrive.RedriveContext(parser_version=PARSER_VERSION, max_attempts=MAX_ATTEMPTS)
_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@pytest_asyncio.fixture
async def made():
    """Collects the events a test makes, and afterwards sets any it left
    pending back to dispatched, so no later dispatcher test delivers them."""
    event_ids: list[uuid.UUID] = []
    yield event_ids
    if event_ids:
        async with UnitOfWork() as uow:
            await uow.session.execute(
                update(OutboxEvent)
                .where(OutboxEvent.id.in_(event_ids), OutboxEvent.status == "pending")
                .values(status="dispatched", dispatched_at=func.now())
            )
            await uow.commit()


async def _tender(label: str):
    email = f"{label}-{uuid.uuid4().hex[:8]}@example.com"
    registered, tender = await register_org_and_tender(email, f"{email} org", f"{email} tender")
    return registered, tender


async def _event(
    made: list[uuid.UUID],
    aggregate_type: str,
    event_type: str,
    payload: dict,
    *,
    status: str = "failed",
    attempt_count: int = MAX_ATTEMPTS,
) -> uuid.UUID:
    """An outbox event in `status`, as mark_failed leaves a dead letter by
    default (status failed, attempt_count at the limit)."""
    async with UnitOfWork() as uow:
        event = await uow.outbox.create(
            aggregate_type=aggregate_type,
            aggregate_id=uuid.uuid4(),
            event_type=event_type,
            payload=payload,
            idempotency_key=f"redrive-test:{uuid.uuid4()}",
        )
        event.status = status
        event.attempt_count = attempt_count
        if status == "dispatched":
            event.dispatched_at = func.now()
        await uow.commit()
        made.append(event.id)
        return event.id


async def _row(event_id: uuid.UUID) -> tuple:
    async with UnitOfWork() as uow:
        event = await uow.outbox.get(event_id)
        assert event is not None
        return (event.status, event.attempt_count, event.available_at, event.dispatched_at)


async def _queued_run(label: str) -> tuple[uuid.UUID, uuid.UUID]:
    _registered, tender = await _tender(label)
    async with UnitOfWork() as uow:
        run = await uow.workflow.create_run(tender.id, "v1", {})
        await uow.commit()
        return run.id, tender.id


async def _set_run(run_id: uuid.UUID, **values) -> None:
    async with UnitOfWork() as uow:
        await uow.session.execute(update(WorkflowRun).where(WorkflowRun.id == run_id).values(**values))
        await uow.commit()


async def _run_status(run_id: uuid.UUID) -> tuple[str, str | None]:
    async with UnitOfWork() as uow:
        run = await uow.workflow.get_run(run_id)
        assert run is not None
        return run.status, run.error_code


async def _gate(run_id: uuid.UUID, tender_id: uuid.UUID, key: str, status: str) -> uuid.UUID:
    gate_id = uuid.uuid4()
    async with UnitOfWork() as uow:
        await uow.gates.create(gate_id, tender_id, run_id, key, "requirement_review", {})
        if status != "pending":
            await uow.session.execute(
                update(ApprovalGate)
                .where(ApprovalGate.id == gate_id)
                .values(status=status, decision_payload={"decision": status}, decided_at=func.now())
            )
        await uow.commit()
    return gate_id


async def _document(tender, registered, *, ingest: bool) -> uuid.UUID:
    """A tender document; ingested (its parser run completed) or not. Its
    own DocumentUploaded is set dispatched, so only the tests' events act."""
    doc = docx.Document()
    doc.add_paragraph("Bidders must hold a valid tax clearance certificate.")
    buf = io.BytesIO()
    doc.save(buf)
    document, _ = await document_service.create_document(
        tender_id=tender.id,
        organization_id=registered.organization.id,
        actor_user_id=uuid.uuid4(),
        filename=f"annexure-{uuid.uuid4().hex[:6]}.docx",
        content=buf.getvalue(),
        mime_type=_DOCX,
    )
    async with UnitOfWork() as uow:
        await uow.session.execute(
            update(OutboxEvent)
            .where(
                OutboxEvent.event_type == EVENT_DOCUMENT_UPLOADED,
                OutboxEvent.payload["document_id"].astext == str(document.id),
            )
            .values(status="dispatched", dispatched_at=func.now())
        )
        await uow.commit()
    if ingest:
        await parse_and_embed_document(str(document.id))
    return document.id


def _one(results: list[outbox_redrive.RedriveResult]) -> outbox_redrive.RedriveResult:
    assert len(results) == 1
    return results[0]


# 1
async def test_a_dry_run_changes_nothing(made):
    registered, tender = await _tender("redrive-dry")
    document_id = await _document(tender, registered, ingest=False)
    event_id = await _event(
        made, AGGREGATE_DOCUMENT, EVENT_DOCUMENT_UPLOADED, {"document_id": str(document_id)}
    )
    before = await _row(event_id)

    result = _one(await outbox_redrive.redrive([event_id], CTX, apply=False))

    assert result.line.startswith("would re-drive: ")
    assert not result.redriven and not result.refused
    assert await _row(event_id) == before


# 2
async def test_apply_resets_a_failed_event_and_the_dispatcher_delivers_it_once(made):
    registered, tender = await _tender("redrive-apply")
    document_id = await _document(tender, registered, ingest=False)
    event_id = await _event(
        made, AGGREGATE_DOCUMENT, EVENT_DOCUMENT_UPLOADED, {"document_id": str(document_id)}
    )

    result = _one(
        await outbox_redrive.redrive([event_id], CTX, apply=True, reason="parser fix deployed")
    )
    assert result.redriven and result.line.startswith("re-driven: ")
    status, attempts, _available, _dispatched = await _row(event_id)
    assert (status, attempts) == ("pending", 0)

    connection = Redis.from_url(settings.redis_url)
    queues = outbox_dispatcher._queues(connection)
    job_id = f"ingest_{document_id}_{PARSER_VERSION}_{event_id}"
    try:
        await outbox_dispatcher._dispatch_claimed_event(queues, event_id)
        await outbox_dispatcher._dispatch_claimed_event(queues, event_id)
        assert (await _row(event_id))[0] == "dispatched"
        assert queues["ingestion"].get_job_ids().count(job_id) == 1
    finally:
        job = queues["ingestion"].fetch_job(job_id)
        if job is not None:
            queues["ingestion"].remove(job)
            job.delete()
        connection.close()


# 3
async def test_unknown_pending_and_dispatched_events_are_refused(made):
    pending = await _event(
        made, AGGREGATE_TENDER, EVENT_TENDER_CREATED, {}, status="pending", attempt_count=3
    )
    dispatched = await _event(
        made, AGGREGATE_TENDER, EVENT_TENDER_CREATED, {}, status="dispatched", attempt_count=0
    )
    unknown = uuid.uuid4()
    before = {e: await _row(e) for e in (pending, dispatched)}

    results = await outbox_redrive.redrive(
        [unknown, pending, dispatched], CTX, apply=True, reason="test"
    )

    assert [r.refused for r in results] == [True, True, True]
    assert results[0].line == "refused: no such outbox event"
    assert results[1].line == (
        f"refused: pending, not dead-lettered (3 of {MAX_ATTEMPTS} attempts used): "
        "the dispatcher will deliver it"
    )
    assert results[2].line.startswith("refused: dispatched at ")
    assert results[2].line.endswith(": nothing to re-drive")
    assert {e: await _row(e) for e in (pending, dispatched)} == before


# 4
async def test_a_run_the_dead_letter_failed_is_refused_naming_dispatch_failed(made):
    run_id, tender_id = await _queued_run("redrive-dlq-run")
    event_id = await _event(
        made,
        AGGREGATE_WORKFLOW_RUN,
        EVENT_WORKFLOW_RUN_CREATED,
        workflow_run_created_payload(run_id, tender_id),
    )
    # Item 19's own dead-letter path fails the run.
    async with UnitOfWork() as uow:
        event = await uow.outbox.get(event_id)
        assert event is not None
        assert await run_reaper.fail_dead_lettered_run(uow, event) is not None
        await uow.commit()
    before = await _row(event_id)

    result = _one(await outbox_redrive.redrive([event_id], CTX, apply=True, reason="test"))

    assert result.refused
    assert result.line == (
        f"refused: run {run_id} is failed (DISPATCH_FAILED): start processing again for a new run"
    )
    assert await _row(event_id) == before
    assert await _run_status(run_id) == ("failed", "DISPATCH_FAILED")


# 5
async def test_a_still_queued_run_is_redriven(made):
    run_id, tender_id = await _queued_run("redrive-queued-run")
    event_id = await _event(
        made,
        AGGREGATE_WORKFLOW_RUN,
        EVENT_WORKFLOW_RUN_CREATED,
        workflow_run_created_payload(run_id, tender_id),
    )

    result = _one(await outbox_redrive.redrive([event_id], CTX, apply=True, reason="test"))

    assert result.redriven
    assert result.line == f"re-driven: run {run_id} is queued"
    assert (await _row(event_id))[:2] == ("pending", 0)
    assert await _run_status(run_id) == ("queued", None)


# 6
async def test_a_gate_decision_whose_run_finished_or_moved_on_is_refused(made):
    finished_run, tender_a = await _queued_run("redrive-gate-done")
    gate_a = await _gate(finished_run, tender_a, "requirement_review:plan-1", "approved")
    await _set_run(finished_run, status="completed", completed_at=func.now())
    finished_event = await _event(
        made,
        AGGREGATE_APPROVAL_GATE,
        EVENT_GATE_DECISION_RECORDED,
        gate_decision_recorded_payload(gate_a, finished_run),
    )

    moved_run, tender_b = await _queued_run("redrive-gate-later")
    await _set_run(moved_run, status="running")
    first_gate = await _gate(moved_run, tender_b, "requirement_review:plan-1", "approved")
    later_gate = await _gate(moved_run, tender_b, "compliance_review:plan-1", "pending")
    await _set_run(moved_run, status="waiting_for_human")
    moved_event = await _event(
        made,
        AGGREGATE_APPROVAL_GATE,
        EVENT_GATE_DECISION_RECORDED,
        gate_decision_recorded_payload(first_gate, moved_run),
    )

    finished, moved = await outbox_redrive.redrive(
        [finished_event, moved_event], CTX, apply=True, reason="test"
    )

    assert finished.refused and moved.refused
    assert finished.line == (
        f"refused: run {finished_run} is completed: nothing for this event to resume"
    )
    assert moved.line == f"refused: run {moved_run} is waiting at a later gate {later_gate}"
    assert (await _row(finished_event))[0] == "failed"
    assert (await _row(moved_event))[0] == "failed"


# 7
async def test_an_operation_request_is_refused_once_failed_and_redriven_while_queued(made):
    registered, tender = await _tender("redrive-op")
    async with UnitOfWork() as uow:
        failed_op = await uow.operations.create_queued(
            registered.organization.id, tender.id, "redteam_run", None
        )
        queued_op = await uow.operations.create_queued(
            registered.organization.id, tender.id, "commercial_validation", None
        )
        assert failed_op is not None and queued_op is not None
        failed_id, queued_id = failed_op.id, queued_op.id
        await uow.session.execute(
            update(AsyncOperation)
            .where(AsyncOperation.id == failed_id)
            .values(status="failed", error_code="DISPATCH_FAILED", finished_at=func.now())
        )
        await uow.commit()
    failed_event = await _event(
        made,
        AGGREGATE_ASYNC_OPERATION,
        EVENT_OPERATION_REQUESTED,
        operation_requested_payload(failed_id, "redteam_run"),
    )
    queued_event = await _event(
        made,
        AGGREGATE_ASYNC_OPERATION,
        EVENT_OPERATION_REQUESTED,
        operation_requested_payload(queued_id, "commercial_validation"),
    )

    failed, queued = await outbox_redrive.redrive(
        [failed_event, queued_event], CTX, apply=True, reason="test"
    )

    assert failed.refused
    assert failed.line == f"refused: operation {failed_id} is failed (DISPATCH_FAILED): request it again"
    assert (await _row(failed_event))[0] == "failed"
    assert queued.redriven
    assert queued.line == f"re-driven: operation {queued_id} is queued"


# 8
async def test_a_reanalysis_with_a_live_lease_or_completed_is_refused_lapsed_or_unclaimed_runs(made):
    _registered, tender = await _tender("redrive-reanalysis")
    item = uuid.uuid4()
    claims = {"live": 11, "completed": 12, "lapsed": 13, "none": 14}
    async with UnitOfWork() as uow:
        for state_version in (11, 12, 13):
            await uow.reanalysis_claims.claim(tender.id, item, state_version, timedelta(minutes=30))
        await uow.session.execute(
            update(ReanalysisClaim)
            .where(ReanalysisClaim.tender_id == tender.id, ReanalysisClaim.state_version == 12)
            .values(status="completed", completed_at=func.now())
        )
        await uow.session.execute(
            update(ReanalysisClaim)
            .where(ReanalysisClaim.tender_id == tender.id, ReanalysisClaim.state_version == 13)
            .values(locked_until=func.now() - timedelta(minutes=1))
        )
        await uow.commit()
    events = {
        name: await _event(
            made,
            AGGREGATE_KNOWLEDGE_SELECTION,
            EVENT_KNOWLEDGE_SELECTION_CHANGED,
            knowledge_selection_changed_payload(tender.id, item, True, state_version),
        )
        for name, state_version in claims.items()
    }

    results = dict(
        zip(
            events,
            await outbox_redrive.redrive(list(events.values()), CTX, apply=True, reason="test"),
            strict=True,
        )
    )

    assert results["live"].refused
    assert results["live"].line.startswith("refused: a delivery is running now; its lease runs until ")
    assert results["live"].line.endswith(" UTC: re-drive after that if it has not completed")
    assert results["completed"].refused
    assert results["completed"].line.startswith(
        "refused: the reanalysis for this change already completed at "
    )
    assert results["lapsed"].redriven
    assert results["lapsed"].line.startswith("re-driven: the last delivery's lease lapsed at ")
    assert results["none"].redriven
    assert results["none"].line == "re-driven: no reanalysis has started for this change"
    assert (await _row(events["live"]))[0] == "failed"
    assert (await _row(events["completed"]))[0] == "failed"


# 9
async def test_an_ingested_document_a_completed_import_and_a_recorded_verification_are_refused(
    made,
):
    registered, tender = await _tender("redrive-guards")
    document_id = await _document(tender, registered, ingest=True)
    ingested_event = await _event(
        made, AGGREGATE_DOCUMENT, EVENT_DOCUMENT_UPLOADED, {"document_id": str(document_id)}
    )

    async with UnitOfWork() as uow:
        batch = await uow.knowledge.create_import_batch(registered.organization.id, "manual")
        batch.completed_at = func.now()
        await uow.commit()
        batch_id = batch.id
    import_event = await _event(
        made,
        AGGREGATE_DOCUMENT,
        EVENT_KNOWLEDGE_DOCUMENT_UPLOADED,
        knowledge_document_uploaded_payload(document_id, batch_id),
    )

    async with UnitOfWork() as uow:
        chunks = await uow.documents.list_chunks_for_document(document_id)
    [requirement] = await persist_extraction_results(
        tender.id,
        [
            RequirementDraft(
                requirement_text="[REQ-001] The bidder must hold a valid tax clearance certificate.",
                category="eligibility",
                mandatory=True,
                source_chunk_id=chunks[0].id,
                document_id=document_id,
                confidence=0.9,
            )
        ],
    )
    verify_event = await _event(
        made,
        AGGREGATE_REQUIREMENT,
        EVENT_REQUIREMENT_VERIFICATION_REQUESTED,
        requirement_verification_requested_payload(requirement.id),
    )
    async with UnitOfWork() as uow:
        await uow.agent_traces.create(
            agent_name="requirement_verification",
            status="completed",
            tender_id=tender.id,
            input_refs={"requirement_id": str(requirement.id), "event_id": str(verify_event)},
        )
        await uow.commit()

    ingested, imported, verified = await outbox_redrive.redrive(
        [ingested_event, import_event, verify_event], CTX, apply=True, reason="test"
    )

    assert ingested.line == (
        f"refused: document {document_id} is already ingested with parser {PARSER_VERSION}"
    )
    assert imported.line.startswith(f"refused: import batch {batch_id} already completed at ")
    assert verified.line == (
        f"refused: requirement {requirement.id} is already verified for this event"
    )
    for event_id in (ingested_event, import_event, verify_event):
        assert (await _row(event_id))[0] == "failed"


# 10
async def test_a_payload_missing_its_id_is_refused_not_raised(made):
    event_id = await _event(made, AGGREGATE_WORKFLOW_RUN, EVENT_WORKFLOW_RUN_CREATED, {})

    result = _one(await outbox_redrive.redrive([event_id], CTX, apply=True, reason="test"))

    assert result.refused
    assert result.line == (
        "refused: payload has no readable workflow_run_id: it would dead-letter again"
    )
    assert (await _row(event_id))[0] == "failed"


# 11
async def test_the_conditional_reset_loses_to_a_reset_made_meanwhile(made, monkeypatch):
    event_id = await _event(made, AGGREGATE_TENDER, EVENT_TENDER_CREATED, {})
    original_assess = outbox_redrive._assess

    async def assess_then_another_operator_resets(uow, event, ctx):
        verdict = await original_assess(uow, event, ctx)
        async with UnitOfWork() as other:
            assert await other.outbox.reset_failed(event_id)
            await other.commit()
        return verdict

    monkeypatch.setattr(outbox_redrive, "_assess", assess_then_another_operator_resets)

    result = _one(await outbox_redrive.redrive([event_id], CTX, apply=True, reason="test"))

    assert not result.redriven and result.refused
    assert result.line == "refused: its status changed to pending meanwhile"
    assert (await _row(event_id))[:2] == ("pending", 0)


# 12
async def test_list_shows_each_dead_letter_with_its_line(made, monkeypatch, capsys):
    monkeypatch.setattr(outbox_redrive, "LIST_LIMIT", 1_000_000)
    run_id, tender_id = await _queued_run("redrive-list")
    runnable = await _event(
        made,
        AGGREGATE_WORKFLOW_RUN,
        EVENT_WORKFLOW_RUN_CREATED,
        workflow_run_created_payload(run_id, tender_id),
    )
    unreadable = await _event(made, AGGREGATE_WORKFLOW_RUN, EVENT_WORKFLOW_RUN_CREATED, {})
    delivered = await _event(
        made, AGGREGATE_TENDER, EVENT_TENDER_CREATED, {}, status="dispatched", attempt_count=0
    )

    assert await redrive_cli.run(["--list"]) == redrive_cli.EXIT_OK

    out = capsys.readouterr().out.splitlines()
    assert f"{runnable} WorkflowRunCreated failed: would re-drive: run {run_id} is queued" in out
    assert (
        f"{unreadable} WorkflowRunCreated failed: refused: payload has no readable "
        "workflow_run_id: it would dead-letter again"
    ) in out
    assert not any(line.startswith(str(delivered)) for line in out)
    assert (await _row(runnable))[0] == "failed"


# 13
async def test_the_cli_exit_statuses_and_no_write_without_apply(made, capsys):
    run_id, tender_id = await _queued_run("redrive-cli")
    runnable = await _event(
        made,
        AGGREGATE_WORKFLOW_RUN,
        EVENT_WORKFLOW_RUN_CREATED,
        workflow_run_created_payload(run_id, tender_id),
    )
    before = await _row(runnable)

    assert await redrive_cli.run(["--event-id", str(runnable)]) == redrive_cli.EXIT_OK
    assert await _row(runnable) == before
    assert await redrive_cli.run(
        ["--event-id", str(runnable), "--event-id", str(uuid.uuid4())]
    ) == redrive_cli.EXIT_REFUSED
    assert await _row(runnable) == before
    for usage_error in (
        [],
        ["--list", "--event-id", str(runnable)],
        ["--list", "--apply", "--reason", "x"],
        ["--event-id", "not-a-uuid"],
        ["--event-id", str(runnable), "--reason", "x"],
        ["--no-such-flag"],
    ):
        assert await redrive_cli.run(usage_error) == redrive_cli.EXIT_ERROR, usage_error
    assert await _row(runnable) == before

    assert await redrive_cli.run(
        ["--event-id", str(runnable), "--apply", "--reason", "fixed"]
    ) == redrive_cli.EXIT_OK
    assert (await _row(runnable))[:2] == ("pending", 0)
    capsys.readouterr()


# 14
async def test_apply_needs_a_reason_and_each_reset_logs_id_type_and_reason(made, caplog, capsys):
    event_id = await _event(made, AGGREGATE_TENDER, EVENT_TENDER_CREATED, {})
    before = await _row(event_id)

    for no_reason in (["--apply"], ["--apply", "--reason", "   "]):
        assert await redrive_cli.run(["--event-id", str(event_id), *no_reason]) == (
            redrive_cli.EXIT_ERROR
        )
    assert "--apply needs --reason" in capsys.readouterr().err
    assert await _row(event_id) == before
    with pytest.raises(ValueError, match="needs a reason"):
        await outbox_redrive.redrive([event_id], CTX, apply=True, reason=None)
    assert await _row(event_id) == before

    with caplog.at_level(logging.INFO, logger=outbox_redrive.__name__):
        assert await redrive_cli.run(
            ["--event-id", str(event_id), "--apply", "--reason", "route fix 1f2e deployed"]
        ) == redrive_cli.EXIT_OK

    resets = [r.getMessage() for r in caplog.records if "re-driven: reset to pending" in r.getMessage()]
    assert resets == [
        f"outbox event {event_id} (TenderCreated) re-driven: reset to pending; "
        "reason: route fix 1f2e deployed"
    ]


# 15
async def test_a_payload_value_never_appears_in_output_or_logs(made, monkeypatch, caplog, capsys):
    monkeypatch.setattr(outbox_redrive, "LIST_LIMIT", 1_000_000)
    secret = f"Tender name {uuid.uuid4().hex} for the payload check"
    no_consumer = await _event(
        made,
        AGGREGATE_TENDER,
        EVENT_TENDER_CREATED,
        tender_created_payload(uuid.uuid4(), uuid.uuid4(), secret),
    )
    unreadable = await _event(
        made, AGGREGATE_WORKFLOW_RUN, EVENT_WORKFLOW_RUN_CREATED, {"name": secret}
    )

    # The project's own loggers at every level; the CLI keeps SQLAlchemy's
    # engine logger at WARNING, so it never logs a statement's parameters.
    with caplog.at_level(logging.DEBUG, logger="tenderos_backend"):
        assert await redrive_cli.run(["--list"]) == redrive_cli.EXIT_OK
        for event_id in (no_consumer, unreadable):
            await redrive_cli.run(["--event-id", str(event_id)])
            await redrive_cli.run(["--event-id", str(event_id), "--apply", "--reason", "check"])

    captured = capsys.readouterr()
    shown = captured.out + captured.err + caplog.text
    assert str(no_consumer) in shown and str(unreadable) in shown
    assert secret not in shown


async def test_the_script_run_as_a_command_prints_no_payload_and_no_sql(made):
    """The real entry point (main: the project's logging set up, asyncio.run)
    in its own process, with the test environment: the event's INFO line
    reaches stderr, SQLAlchemy logs no statement, and no payload value
    reaches either stream."""
    secret = f"Tender name {uuid.uuid4().hex} for the command check"
    event_id = await _event(
        made,
        AGGREGATE_TENDER,
        EVENT_TENDER_CREATED,
        tender_created_payload(uuid.uuid4(), uuid.uuid4(), secret),
    )

    done = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "redrive_outbox.py"), "--event-id", str(event_id)],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=REPO_ROOT,
    )

    assert done.returncode == 0, done.stderr
    assert f"{event_id} TenderCreated failed: would re-drive: no consumer acts on this event type" in (
        done.stdout
    )
    assert (
        f"INFO tenderos_backend.domain.services.outbox_redrive: outbox event {event_id} "
        "(TenderCreated) would re-drive: no consumer acts on this event type"
    ) in done.stderr
    assert "sqlalchemy.engine" not in done.stderr
    assert secret not in done.stdout + done.stderr


async def test_every_dispatcher_route_has_its_own_check():
    """The checks mirror the dispatcher's routes. A pair outside the routes
    goes to the dispatcher's placeholder job, which acts on nothing, so the
    tool re-drives it as having no consumer; a route added without a check
    would be re-driven the same way without its guard being read, so the two
    sets must stay equal (security review of 3ccdbe5)."""
    assert set(outbox_redrive._CHECKS) == set(outbox_dispatcher._ROUTES)


async def test_redrive_writes_only_the_outbox_row(made):
    """The reset touches nothing but the event: a re-driven run keeps its own
    row exactly as it was."""
    run_id, tender_id = await _queued_run("redrive-only-outbox")
    event_id = await _event(
        made,
        AGGREGATE_WORKFLOW_RUN,
        EVENT_WORKFLOW_RUN_CREATED,
        workflow_run_created_payload(run_id, tender_id),
    )
    async with UnitOfWork() as uow:
        before = (await uow.session.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))).scalar_one()
        before_row = (before.status, before.current_job_id, before.error_code, before.retry_count)

    await outbox_redrive.redrive([event_id], CTX, apply=True, reason="test")

    async with UnitOfWork() as uow:
        after = (await uow.session.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))).scalar_one()
        assert (after.status, after.current_job_id, after.error_code, after.retry_count) == before_row
