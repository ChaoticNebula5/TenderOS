import uuid

from tenderos_backend.core.hashing import sha256_hex
from tenderos_backend.db.models.document import Document
from tenderos_backend.db.storage import storage
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.events.outbox_events import (
    AGGREGATE_DOCUMENT,
    EVENT_DOCUMENT_UPLOADED,
    document_uploaded_idempotency_key,
    document_uploaded_payload,
)
from tenderos_backend.ingestion.classify import classify_document


async def create_document(
    tender_id: uuid.UUID,
    organization_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    filename: str,
    content: bytes,
    mime_type: str | None,
) -> tuple[Document, bool]:
    """G2's upload flow (hash -> row + storage key -> upload object -> outbox
    event, 202) plus the duplicate-upload check the smoke test names
    explicitly. Keying the storage object by content hash rather than a
    generated document id (tenders/{tender_id}/{sha256}/{filename}) sidesteps
    the chicken-and-egg problem B3 solved with client-side UUIDs - two
    uploads of the same bytes land on the same key, so there's nothing to
    clean up if the dedup check below finds an existing row.

    Returns (document, is_new) - is_new=False means this exact file was
    already uploaded for this tender; no second storage write, no second
    outbox event, no second ingestion run.
    """
    sha256 = sha256_hex(content)

    async with UnitOfWork() as uow:
        existing = await uow.documents.get_by_tender_and_hash(tender_id, sha256)
        if existing is not None:
            return existing, False

        storage_key = f"tenders/{tender_id}/{sha256}/{filename}"
        await storage.put(storage_key, content, content_type=mime_type)

        doc_type = classify_document(filename, mime_type)
        document = await uow.documents.create(
            tender_id=tender_id,
            side="client",
            doc_type=doc_type,
            filename=filename,
            storage_key=storage_key,
            sha256=sha256,
            mime_type=mime_type,
            file_size_bytes=len(content),
        )
        await uow.outbox.create(
            aggregate_type=AGGREGATE_DOCUMENT,
            aggregate_id=document.id,
            event_type=EVENT_DOCUMENT_UPLOADED,
            payload=document_uploaded_payload(document.id, tender_id),
            idempotency_key=document_uploaded_idempotency_key(document.id),
        )
        await uow.audit.create(
            organization_id=organization_id,
            actor_type="user",
            actor_id=str(actor_user_id),
            action="upload_client_docs",
            tender_id=tender_id,
            target_type="document",
            target_id=document.id,
        )
        await uow.commit()
        return document, True


async def get_document(document_id: uuid.UUID) -> Document | None:
    async with UnitOfWork() as uow:
        return await uow.documents.get(document_id)


async def list_documents_for_tender(tender_id: uuid.UUID) -> list[Document]:
    async with UnitOfWork() as uow:
        return await uow.documents.list_for_tender(tender_id)


async def reprocess_document(
    document_id: uuid.UUID, organization_id: uuid.UUID, actor_user_id: uuid.UUID
) -> Document | None:
    """G2's POST /documents/{id}/reprocess - re-enqueues ingestion for a
    document that already exists in storage. Reuses DocumentUploaded rather
    than a separate event type, since the ingestion job is what needs to run
    again regardless of why; the idempotency_key gets a fresh uuid4 suffix
    each call since outbox_events.idempotency_key is unique and, unlike
    upload, a reprocess request isn't in I1's required-Idempotency-Key list -
    each call is a deliberate retry, not something that needs replay-safety
    at the HTTP layer. Retry-safety for the actual chunk writes lives in the
    ingestion job itself, not here.
    """
    async with UnitOfWork() as uow:
        document = await uow.documents.get(document_id)
        if document is None or document.tender_id is None:
            return None

        await uow.documents.update_parse_status(document_id, "queued")
        await uow.outbox.create(
            aggregate_type=AGGREGATE_DOCUMENT,
            aggregate_id=document.id,
            event_type=EVENT_DOCUMENT_UPLOADED,
            payload=document_uploaded_payload(document.id, document.tender_id),
            idempotency_key=f"{document_uploaded_idempotency_key(document.id)}:reprocess:{uuid.uuid4()}",
        )
        await uow.audit.create(
            organization_id=organization_id,
            actor_type="user",
            actor_id=str(actor_user_id),
            action="upload_client_docs",
            tender_id=document.tender_id,
            target_type="document",
            target_id=document.id,
        )
        await uow.commit()
        return await uow.documents.get(document_id)
