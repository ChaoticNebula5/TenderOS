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
    organization_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    filename: str,
    content: bytes,
    mime_type: str | None,
    tender_id: uuid.UUID | None = None,
    side: str = "client",
    doc_type: str | None = None,
) -> tuple[Document, bool]:
    """G2's upload flow (hash -> row + storage key -> upload object -> outbox
    event, 202) plus the duplicate-upload check the smoke test names
    explicitly. Keying the storage object by content hash rather than a
    generated document id sidesteps the chicken-and-egg problem B3 solved
    with client-side UUIDs - two uploads of the same bytes land on the same
    key, so there's nothing to clean up if the dedup check below finds an
    existing row.

    tender_id is now optional (B6): organization-scoped uploads (resumes,
    certification documents, employee-master, capability statement) have no
    tender at all, matching documents' own tender_id-XOR-organization_id
    scope constraint. Exactly one of tender_id being set vs. absent decides
    which of get_by_tender_and_hash/get_by_org_and_hash runs and which
    storage-key prefix is used - callers don't pass organization_id=None
    for a tender upload, organization_id is always required either way
    (B6's org-scoped uploads still are "for" an org; tender uploads carry it
    for the audit event exactly as before).

    Returns (document, is_new) - is_new=False means this exact file was
    already uploaded for this tender/organization; no second storage write,
    no second outbox event, no second ingestion run.
    """
    sha256 = sha256_hex(content)

    async with UnitOfWork() as uow:
        existing = (
            await uow.documents.get_by_tender_and_hash(tender_id, sha256)
            if tender_id is not None
            else await uow.documents.get_by_org_and_hash(organization_id, sha256)
        )
        if existing is not None:
            return existing, False

        scope_prefix = f"tenders/{tender_id}" if tender_id is not None else f"organizations/{organization_id}"
        storage_key = f"{scope_prefix}/{sha256}/{filename}"
        await storage.put(storage_key, content, content_type=mime_type)

        resolved_doc_type = doc_type or classify_document(filename, mime_type)
        document = await uow.documents.create(
            tender_id=tender_id,
            organization_id=None if tender_id is not None else organization_id,
            side=side,
            doc_type=resolved_doc_type,
            filename=filename,
            storage_key=storage_key,
            sha256=sha256,
            mime_type=mime_type,
            file_size_bytes=len(content),
        )
        # B13: only for a real tender document - see KNOWN_LIMITATIONS.md.
        # DocumentUploaded's one consumer (outbox_dispatcher's own
        # _document_uploaded_dispatch) always routes to parse_and_embed_
        # document, the generic tender-RAG ingestion pipeline; it has no
        # meaning for an organization-scoped knowledge upload (employee-
        # master, certifications, resumes, capability statements), which
        # already fires its own KnowledgeDocumentUploaded event via
        # knowledge_service.queue_knowledge_document_import. reprocess_
        # document (below) already refuses to act when tender_id is None
        # for the identical reason - this call site is what never adopted
        # that same guard.
        if tender_id is not None:
            await uow.outbox.create(
                aggregate_type=AGGREGATE_DOCUMENT,
                aggregate_id=document.id,
                event_type=EVENT_DOCUMENT_UPLOADED,
                payload=document_uploaded_payload(
                    document.id,
                    tender_id=tender_id,
                    organization_id=None,
                ),
                idempotency_key=document_uploaded_idempotency_key(document.id),
            )
        await uow.audit.create(
            organization_id=organization_id,
            actor_type="user",
            actor_id=str(actor_user_id),
            action="upload_client_docs" if tender_id is not None else "upload_knowledge_docs",
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
