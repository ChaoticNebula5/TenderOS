"""Plan item 59, Q5 (PR 21): a requirement's verification has three states.

`verified` alone made "never checked" and "checked and not supported" the
same false. Migration 035 adds `verification_checked_at` beside it:
- not checked yet: verified false, no time;
- verification failed: verified false, a time;
- verified: verified true, a time.
A check constraint refuses verified true with no time.

What these tests pin:
- a verdict records when it was checked, from the database clock, and a
  re-check with the same verdict still records its time, without a bump;
- a text edit resets the verdict (false, no time), and bumps when it was
  verified; an edit that keeps the text keeps it;
- the requirement review gate's summary keeps unverified_mandatory_count as
  the total and counts the two unverified states apart;
- the migration's backfill, run on rows as they were before it.
"""

import importlib.util
import uuid
from datetime import datetime
from pathlib import Path

import pytest
from _helpers import register_org_and_tender
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from tenderos_backend.agents.llm.fake_client import FakeLLMClient
from tenderos_backend.agents.requirements.verify import verify_and_persist
from tenderos_backend.agents.schemas.requirements import VerificationOutput
from tenderos_backend.db.models.audit import AuditEvent
from tenderos_backend.db.models.requirement import Requirement
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import gate_summary_service, requirement_service

pytestmark = pytest.mark.asyncio

_TEXT = "The bidder shall have ten years of experience."
_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "packages/tenderos_backend/src/tenderos_backend/db/migrations/versions"
    / "035_requirement_check_time.py"
)


async def _bid() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    """A bid with one source chunk: (org, actor, tender, chunk)."""
    email = f"verification-{uuid.uuid4().hex[:8]}@example.com"
    registered, tender = await register_org_and_tender(email, f"{email} org", f"{email} bid")
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
            chunk_text=_TEXT,
            normalized_text=_TEXT.lower(),
            content_hash=uuid.uuid4().hex,
        )
        await uow.commit()
        return registered.organization.id, registered.user.id, tender.id, chunk.id


async def _requirement(tender_id: uuid.UUID, chunk_id: uuid.UUID, mandatory: bool = True):
    async with UnitOfWork() as uow:
        requirement = await uow.requirements.create(
            tender_id=tender_id,
            display_code=f"EXP-{uuid.uuid4().hex[:4]}",
            requirement_text=_TEXT,
            normalized_text=_TEXT.lower(),
            category="experience",
            mandatory=mandatory,
            source_chunk_id=chunk_id,
            content_fingerprint=uuid.uuid4().hex,
        )
        await uow.commit()
        return requirement.id


def _client(*supported: bool) -> FakeLLMClient:
    """One scripted verdict per call, in order."""
    client = FakeLLMClient()
    client.script(
        "requirement_verification",
        VerificationOutput,
        [
            VerificationOutput(
                supported_by_source=s,
                mandatory_language_supported=s,
                verification_confidence=0.9,
                reasoning_summary="The source chunk states it." if s else "It does not.",
            )
            for s in supported
        ],
    )
    return client


async def _state(requirement_id: uuid.UUID) -> tuple[bool, datetime | None]:
    async with UnitOfWork() as uow:
        row = (
            await uow.session.execute(
                select(Requirement.verified, Requirement.verification_checked_at).where(
                    Requirement.id == requirement_id
                )
            )
        ).one()
        return row.verified, row.verification_checked_at


async def _version(tender_id: uuid.UUID) -> int:
    async with UnitOfWork() as uow:
        return (
            await uow.session.execute(select(Tender.state_version).where(Tender.id == tender_id))
        ).scalar_one()


async def test_a_verdict_is_timed_by_the_database_clock():
    """One session, as PR 20's decided_at test: the time is the writing
    transaction's own now()."""
    _org, _actor, tender_id, chunk = await _bid()
    rid = await _requirement(tender_id, chunk)
    async with UnitOfWork() as uow:
        written = await uow.requirements.set_verdict_for_text(rid, True, _TEXT)
        db_now = (await uow.session.execute(text("SELECT now()"))).scalar_one()
        checked_at = (
            await uow.session.execute(
                select(Requirement.verification_checked_at).where(Requirement.id == rid)
            )
        ).scalar_one()
        await uow.commit()
    assert written.outcome == "changed"
    assert checked_at == db_now


async def test_a_recheck_with_the_same_verdict_records_its_time_without_a_bump():
    _org, _actor, tender_id, chunk = await _bid()
    rid = await _requirement(tender_id, chunk)
    start = await _version(tender_id)
    client = _client(True, True)

    await verify_and_persist(client, "fake", rid, tender_id)
    verified, first = await _state(rid)
    assert verified is True and first is not None
    assert await _version(tender_id) == start + 1, "false to true is projected"

    await verify_and_persist(client, "fake", rid, tender_id)
    verified, second = await _state(rid)
    assert verified is True
    assert second is not None and second > first, "the re-check records its own time"
    assert await _version(tender_id) == start + 1, "the same verdict changes no graph"


async def test_a_failed_check_is_told_apart_from_one_not_run():
    _org, _actor, tender_id, chunk = await _bid()
    checked = await _requirement(tender_id, chunk)
    never = await _requirement(tender_id, chunk)
    start = await _version(tender_id)

    await verify_and_persist(_client(False), "fake", checked, tender_id)

    verified, checked_at = await _state(checked)
    assert verified is False and checked_at is not None, "failed: false, with a time"
    assert await _state(never) == (False, None), "not checked yet: false, no time"
    assert await _version(tender_id) == start, "false to false changes no graph"


async def test_a_text_edit_resets_the_verdict_and_bumps_when_it_was_verified():
    org, actor, tender_id, chunk = await _bid()
    rid = await _requirement(tender_id, chunk)
    await verify_and_persist(_client(True), "fake", rid, tender_id)
    start = await _version(tender_id)

    await requirement_service.patch_requirement(
        rid, org, actor, requirement_text="The bidder shall have twelve years of experience."
    )

    assert await _state(rid) == (False, None), "the verdict was on the old text"
    assert await _version(tender_id) == start + 1, "verified is on the graph's node"
    async with UnitOfWork() as uow:
        audit = (
            await uow.session.execute(
                select(AuditEvent.before, AuditEvent.after).where(
                    AuditEvent.target_id == rid, AuditEvent.action == "requirement_edit"
                )
            )
        ).one()
    assert audit.before["verified"] is True and audit.after["verified"] is False


async def test_an_edit_that_keeps_the_text_keeps_the_verdict():
    """A category change, and the same text sent again, are not text edits."""
    org, actor, tender_id, chunk = await _bid()
    rid = await _requirement(tender_id, chunk)
    await verify_and_persist(_client(True), "fake", rid, tender_id)
    before = await _state(rid)

    await requirement_service.patch_requirement(rid, org, actor, category="staffing")
    await requirement_service.patch_requirement(rid, org, actor, requirement_text=_TEXT)

    assert await _state(rid) == before


async def test_the_database_refuses_verified_with_no_check_time():
    _org, _actor, tender_id, chunk = await _bid()
    rid = await _requirement(tender_id, chunk)
    async with UnitOfWork() as uow:
        with pytest.raises(IntegrityError, match="ck_requirement_verified_has_check_time"):
            await uow.session.execute(
                text("UPDATE requirements SET verified = true WHERE id = :id"), {"id": rid}
            )


async def test_the_gate_summary_counts_the_two_unverified_states_apart():
    """unverified_mandatory_count stays the total of the two. An optional
    requirement is counted in neither."""
    _org, _actor, tender_id, chunk = await _bid()
    verified = await _requirement(tender_id, chunk)
    failed = await _requirement(tender_id, chunk)
    await _requirement(tender_id, chunk)  # mandatory, never checked
    await _requirement(tender_id, chunk, mandatory=False)  # optional, never checked
    await verify_and_persist(_client(True), "fake", verified, tender_id)
    await verify_and_persist(_client(False), "fake", failed, tender_id)

    async with UnitOfWork() as uow:
        summary = await gate_summary_service.requirement_review_summary(uow, tender_id)

    assert summary["mandatory_count"] == 3
    assert summary["unverified_mandatory_count"] == 2
    assert summary["mandatory_not_verified_yet_count"] == 1
    assert summary["mandatory_verification_failed_count"] == 1


def _migration():
    spec = importlib.util.spec_from_file_location("migration_035", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_the_backfill_reads_the_verification_traces():
    """The migration's own SQL, on rows as they were before it: the
    constraint dropped and every time null, in one transaction rolled back.

    - verified, with a completed trace: the latest trace's time;
    - verified, with none: its updated_at;
    - not verified, with a completed trace: failed, at that trace's time;
    - not verified, with only a failed call: not checked (the call wrote
      nothing);
    - not verified, with no trace: not checked.
    """
    migration = _migration()
    _org, _actor, tender_id, chunk = await _bid()
    rows = {name: await _requirement(tender_id, chunk) for name in "ABCDE"}
    async with UnitOfWork() as uow:
        for name, status in (("A", "completed"), ("A", "completed"), ("C", "completed")):
            await uow.agent_traces.create(
                agent_name="requirement_verification",
                status=status,
                tender_id=tender_id,
                input_refs={"requirement_id": str(rows[name])},
            )
        await uow.agent_traces.create(
            agent_name="requirement_verification",
            status="failed",
            tender_id=tender_id,
            input_refs={"requirement_id": str(rows["D"])},
        )
        await uow.commit()

    async with UnitOfWork() as uow:
        session = uow.session
        await session.execute(
            text("ALTER TABLE requirements DROP CONSTRAINT ck_requirement_verified_has_check_time")
        )
        ids = list(rows.values())
        await session.execute(
            text(
                "UPDATE requirements SET verification_checked_at = NULL, "
                "verified = (id = ANY(:verified)) WHERE id = ANY(:ids)"
            ),
            {"verified": [rows["A"], rows["B"]], "ids": ids},
        )
        await session.execute(migration.BACKFILL_VERIFIED)
        await session.execute(migration.BACKFILL_FAILED)
        got = {
            r.id: (r.verified, r.verification_checked_at, r.updated_at)
            for r in (
                await session.execute(
                    select(
                        Requirement.id,
                        Requirement.verified,
                        Requirement.verification_checked_at,
                        Requirement.updated_at,
                    ).where(Requirement.id.in_(ids))
                )
            ).all()
        }
        trace_times = {
            row.rid: row.latest
            for row in (
                await session.execute(
                    text(
                        "SELECT input_refs->>'requirement_id' AS rid, max(created_at) AS latest "
                        "FROM agent_traces WHERE status = 'completed' AND tender_id = :t GROUP BY 1"
                    ),
                    {"t": tender_id},
                )
            ).all()
        }
        await session.rollback()

    a, b, c, d, e = (got[rows[n]] for n in "ABCDE")
    assert a[:2] == (True, trace_times[str(rows["A"])])
    assert b[:2] == (True, b[2]), "no trace: its updated_at"
    assert c[:2] == (False, trace_times[str(rows["C"])]), "failed, at its check"
    assert d[:2] == (False, None), "a failed call wrote no verdict"
    assert e[:2] == (False, None)
