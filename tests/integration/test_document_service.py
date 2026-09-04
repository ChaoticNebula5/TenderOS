"""document_service.create_document - the concurrent-upload race B16's own
load test found live (not by inspection): two genuinely concurrent uploads
of byte-identical content both pass the get_by_tender_and_hash dedup check
before either commits, both attempt the insert, and the loser used to
propagate an unhandled IntegrityError as a 500 instead of reading back the
winner's row. Same select-then-insert shape, same fix pattern, as
readiness_service.py's own evaluate_readiness race.
"""

import asyncio
import uuid

import pytest
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import get_session
from tenderos_backend.domain.services import document_service

pytestmark = pytest.mark.asyncio


async def _make_tender() -> tuple[uuid.UUID, uuid.UUID]:
    async with get_session() as session:
        org = Organization(name="Document Service Concurrency Test Org")
        session.add(org)
        await session.flush()
        tender = Tender(organization_id=org.id, name="Document Service Concurrency Test Tender")
        session.add(tender)
        await session.flush()
        await session.commit()
        return org.id, tender.id


async def test_concurrent_identical_uploads_converge_to_one_document_not_a_500():
    org_id, tender_id = await _make_tender()
    content = b"identical content, uploaded twice at the same instant"
    actor_id = uuid.uuid4()

    # asyncio.gather, not two sequential awaits - the whole point is that
    # both calls' own get_by_tender_and_hash check must run before either
    # has committed its own insert, the exact window the fix is for.
    results = await asyncio.gather(
        document_service.create_document(
            organization_id=org_id,
            actor_user_id=actor_id,
            filename="a.pdf",
            content=content,
            mime_type="application/pdf",
            tender_id=tender_id,
        ),
        document_service.create_document(
            organization_id=org_id,
            actor_user_id=actor_id,
            filename="b.pdf",
            content=content,
            mime_type="application/pdf",
            tender_id=tender_id,
        ),
        return_exceptions=True,
    )

    exceptions = [r for r in results if isinstance(r, BaseException)]
    assert exceptions == [], f"neither call should raise - got {exceptions}"

    documents = [doc for doc, _is_new in results]
    is_new_flags = [is_new for _doc, is_new in results]
    assert documents[0].id == documents[1].id, "both calls must resolve to the same document row"
    assert sorted(is_new_flags) == [False, True], (
        "exactly one call created the row (is_new=True); the other read it back (is_new=False)"
    )
