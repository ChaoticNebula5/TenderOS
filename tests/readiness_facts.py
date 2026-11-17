"""Saved facts readiness reads, set up the way a clean bid has them (plan
items 94, 96 and 99).

Readiness now blocks a bid with no parsed document, and a bid on which no
complete red-team pass has seen the current state version. A test that
means "this bid is otherwise clean" sets both up here rather than
repeating the rows in each file:

- add_parsed_document: a tender document whose parse finished;
- record_complete_pass: a completed pass, graph checks run, at the bid's
  current version (what run_red_team records after a pass on a fresh graph);
- project_and_review: project the graph, then record that pass, which is
  what "projected and reviewed at this version" means.
"""

import uuid

from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import graph_service


async def add_parsed_document(tender_id: uuid.UUID, filename: str = "rfp.pdf") -> uuid.UUID:
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
        await uow.documents.update_parse_status(document.id, "parsed")
        await uow.commit()
        return document.id


async def record_complete_pass(tender_id: uuid.UUID) -> int:
    """A completed pass whose graph checks ran at the bid's current version.
    Returns that version."""
    async with UnitOfWork() as uow:
        tender = await uow.tenders.get(tender_id)
        assert tender is not None
        version = tender.state_version
        await uow.redteam.record_pass(
            tender_id, outcome="completed", graph_checks="ran", graph_state_version=version
        )
        await uow.commit()
        return version


async def project_and_review(tender_id: uuid.UUID) -> int:
    await graph_service.project_tender(tender_id)
    return await record_complete_pass(tender_id)
