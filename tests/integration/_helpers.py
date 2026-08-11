"""Shared integration-test setup, not a test module itself (leading
underscore keeps pytest from collecting it).

ingest_one_document exists specifically because B5's create_workflow_run now
refuses to start a run until every one of the tender's documents has
parse_status='parsed' (DocumentsNotReadyError) - every test that creates a
workflow run needs a real ingested document first, not just a bare tender
row, which B3's tests never needed.
"""

import io
import uuid

import docx
from tenderos_backend.db.models.document import Document
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.domain.services import auth_service, document_service, tender_service
from tenderos_backend.domain.services.auth_service import RegisterResult
from tenderos_worker.jobs.ingestion import parse_and_embed_document


async def register_org_and_tender(
    email: str, organization_name: str, tender_name: str
) -> tuple[RegisterResult, Tender]:
    registered = await auth_service.register(
        email=email, password="hunter2", organization_name=organization_name
    )
    tender = await tender_service.create_tender(
        organization_id=registered.organization.id,
        name=tender_name,
        created_by=registered.user.id,
    )
    return registered, tender


async def ingest_one_document(tender_id: uuid.UUID, organization_id: uuid.UUID) -> Document:
    doc = docx.Document()
    doc.add_paragraph("Placeholder content for workflow-run test fixtures.")
    buf = io.BytesIO()
    doc.save(buf)

    document, _ = await document_service.create_document(
        tender_id=tender_id,
        organization_id=organization_id,
        actor_user_id=uuid.uuid4(),
        filename="placeholder.docx",
        content=buf.getvalue(),
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    await parse_and_embed_document(str(document.id))
    return document


async def make_ready_run_inputs(
    email: str, organization_name: str, tender_name: str
) -> tuple[RegisterResult, Tender]:
    """register_org_and_tender + ingest_one_document in one call - what every
    create_workflow_run caller in these tests needs."""
    registered, tender = await register_org_and_tender(email, organization_name, tender_name)
    await ingest_one_document(tender.id, registered.organization.id)
    return registered, tender
