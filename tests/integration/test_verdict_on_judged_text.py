"""Plan item 59, fix 6 (PR 20): a verification verdict is written only on the
text it judged.

verify_and_persist reads a requirement's text, asks the model whether the
source supports it (seconds, outside any transaction), then writes the
verdict. A person's text edit that commits during the call used to get the
verdict on the old text, and a bump if it changed `verified`. Now the write
reads the row locked and compares requirement_text with the text judged
(a single conditional UPDATE until PR 21, which writes the check time with
every verdict and so needs the old value of `verified`): a refused write
means no write, no bump and one INFO line. A requirement
deleted meanwhile (merge and split delete them) keeps today's
RequirementNotFoundError.

Steps are ordered by awaiting: the model call is paused (pause_once on
verify.verify_requirement), the edit is made, the call is released.
"""

import asyncio
import logging
import uuid

import pytest
from _helpers import register_org_and_tender
from _lock_waits import pause_once, waiting
from sqlalchemy import select
from tenderos_backend.agents.llm.fake_client import FakeLLMClient
from tenderos_backend.agents.requirements import verify as verify_module
from tenderos_backend.agents.requirements.verify import verify_and_persist
from tenderos_backend.agents.schemas.requirements import VerificationOutput
from tenderos_backend.db.models.requirement import Requirement
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import requirement_service
from tenderos_backend.domain.services.requirement_service import RequirementNotFoundError

pytestmark = pytest.mark.asyncio

_VERIFY_LOGGER = "tenderos_backend.agents.requirements.verify"
_JUDGED = "The bidder shall have ten years of experience."
_EDITED = "The bidder shall have five years of experience."


async def _requirement() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    """An unverified mandatory requirement. Returns (org, actor, tender,
    requirement)."""
    email = f"verdict-{uuid.uuid4().hex[:8]}@example.com"
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
            chunk_text=_JUDGED,
            normalized_text=_JUDGED.lower(),
            content_hash=uuid.uuid4().hex,
        )
        requirement = await uow.requirements.create(
            tender_id=tender.id,
            display_code="EXP-001",
            requirement_text=_JUDGED,
            normalized_text=_JUDGED.lower(),
            category="experience",
            mandatory=True,
            source_chunk_id=chunk.id,
            content_fingerprint=uuid.uuid4().hex,
        )
        await uow.commit()
        return registered.organization.id, registered.user.id, tender.id, requirement.id


def _supported_client() -> FakeLLMClient:
    client = FakeLLMClient()
    client.script(
        "requirement_verification",
        VerificationOutput,
        [
            VerificationOutput(
                supported_by_source=True,
                mandatory_language_supported=True,
                verification_confidence=0.9,
                reasoning_summary="The source chunk states it.",
            )
        ],
    )
    return client


async def _stored(requirement_id: uuid.UUID) -> tuple[str, bool]:
    async with UnitOfWork() as uow:
        row = (
            await uow.session.execute(
                select(Requirement.requirement_text, Requirement.verified).where(
                    Requirement.id == requirement_id
                )
            )
        ).one()
        return row.requirement_text, row.verified


async def _version(tender_id: uuid.UUID) -> int:
    async with UnitOfWork() as uow:
        return (
            await uow.session.execute(select(Tender.state_version).where(Tender.id == tender_id))
        ).scalar_one()


def _info_lines(caplog, requirement_id: uuid.UUID) -> list[int]:
    return [
        r.levelno
        for r in caplog.records
        if r.name == _VERIFY_LOGGER and str(requirement_id) in r.message
    ]


async def test_a_text_edit_committed_during_the_model_call_refuses_the_verdict(monkeypatch, caplog):
    org, actor, tender_id, rid = await _requirement()
    reached, release = pause_once(monkeypatch, verify_module, "verify_requirement", after=False)
    caplog.set_level(logging.INFO, logger=_VERIFY_LOGGER)
    start = await _version(tender_id)

    verify = asyncio.create_task(verify_and_persist(_supported_client(), "fake", rid, tender_id))
    try:
        await asyncio.wait_for(reached.wait(), timeout=10)
        await requirement_service.patch_requirement(rid, org, actor, requirement_text=_EDITED)
    finally:
        release.set()
    await asyncio.wait_for(verify, timeout=10)

    assert await _stored(rid) == (_EDITED, False), "the verdict on the old text was written"
    assert await _version(tender_id) == start, "a refused verdict does not bump"
    assert _info_lines(caplog, rid) == [logging.INFO]


async def test_a_text_edit_still_open_when_the_verdict_arrives_refuses_it(monkeypatch, caplog):
    """The edit's transaction holds the row when the verdict is written: the
    write waits on it, then re-checks the text against the row the edit
    committed, and matches nothing."""
    _org, _actor, tender_id, rid = await _requirement()
    reached, release = pause_once(monkeypatch, verify_module, "verify_requirement", after=False)
    caplog.set_level(logging.INFO, logger=_VERIFY_LOGGER)
    start = await _version(tender_id)

    verify = asyncio.create_task(verify_and_persist(_supported_client(), "fake", rid, tender_id))
    try:
        await asyncio.wait_for(reached.wait(), timeout=10)
        async with UnitOfWork() as edit:
            await edit.requirements.update_fields(
                rid, requirement_text=_EDITED, normalized_text=_EDITED.lower()
            )
            release.set()
            try:
                await waiting(verify, "the verdict never waited for the open edit")
            finally:
                await edit.commit()
    finally:
        release.set()
    await asyncio.wait_for(verify, timeout=10)

    assert await _stored(rid) == (_EDITED, False)
    assert await _version(tender_id) == start
    assert _info_lines(caplog, rid) == [logging.INFO]


async def test_a_verdict_on_unchanged_text_is_written_and_bumps(monkeypatch, caplog):
    """The control, through the same pause: no edit, so the verdict is
    written and moves the version, with no INFO line."""
    _org, _actor, tender_id, rid = await _requirement()
    reached, release = pause_once(monkeypatch, verify_module, "verify_requirement", after=False)
    caplog.set_level(logging.INFO, logger=_VERIFY_LOGGER)
    start = await _version(tender_id)

    verify = asyncio.create_task(verify_and_persist(_supported_client(), "fake", rid, tender_id))
    await asyncio.wait_for(reached.wait(), timeout=10)
    release.set()
    await asyncio.wait_for(verify, timeout=10)

    assert await _stored(rid) == (_JUDGED, True)
    assert await _version(tender_id) == start + 1
    assert _info_lines(caplog, rid) == []


async def test_a_requirement_deleted_during_the_model_call_is_still_not_found(monkeypatch):
    """Merge and split delete requirements. A verdict for one deleted while
    it was judged answers as before: RequirementNotFoundError."""
    _org, _actor, tender_id, rid = await _requirement()
    reached, release = pause_once(monkeypatch, verify_module, "verify_requirement", after=False)

    verify = asyncio.create_task(verify_and_persist(_supported_client(), "fake", rid, tender_id))
    try:
        await asyncio.wait_for(reached.wait(), timeout=10)
        async with UnitOfWork() as uow:
            await uow.requirements.delete(rid)
            await uow.commit()
    finally:
        release.set()
    with pytest.raises(RequirementNotFoundError):
        await asyncio.wait_for(verify, timeout=10)
