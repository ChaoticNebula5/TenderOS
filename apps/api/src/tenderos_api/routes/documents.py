from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from tenderos_backend.core.config import settings
from tenderos_backend.db.models.user import User
from tenderos_backend.domain.services import document_service, tender_service

from tenderos_api.dependencies import require_permission
from tenderos_api.schemas.documents import DocumentResponse

router = APIRouter(tags=["documents"])

# B15 owns hardening ("maximum document sizes", archive-bomb defenses); B4
# only needs a working limit so G2 step 1 ("validate type and maximum size")
# is real. Extension AND content-type must both be acceptable - checking one
# alone lets a mislabelled file through to a parser that cannot read it.
# Content-types that carry no information about the actual file - treated as
# "not asserted" rather than "wrong".
_UNASSERTED_CONTENT_TYPES = {None, "", "application/octet-stream", "binary/octet-stream"}

_ALLOWED_TYPES: dict[str, set[str]] = {
    ".pdf": {"application/pdf"},
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    },
    ".xlsx": {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    },
    # G6: "Employee-master imports accept CSV or XLSX with a documented
    # template." Added here rather than only for that one route, since
    # _validate_upload is shared - a CSV uploaded where CSV isn't a valid
    # import type still fails loudly at the parser, the same posture this
    # function already takes for any other type/content mismatch.
    ".csv": {"text/csv"},
}


def _validate_upload(filename: str, content_type: str | None, size_bytes: int) -> None:
    extension = filename[filename.rfind(".") :].lower() if "." in filename else ""
    allowed_content_types = _ALLOWED_TYPES.get(extension)
    if allowed_content_types is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported file type {extension!r}. Allowed: .pdf, .docx, .xlsx",
        )
    # Only a positively WRONG type is rejected. A missing type, or the
    # generic binary fallback, means "this client did not assert anything" -
    # curl without /etc/mime.types, plain httpx/requests calls, and some
    # browsers all send application/octet-stream for a perfectly valid
    # .docx, and 415-ing those would reject real uploads. The extension is
    # already checked above, and the header is client-controlled so it is
    # never a security boundary anyway - real content sniffing is B15's
    # file-type validation. A file that lies about its type still fails
    # loudly in the parser and lands as parse_status='failed'.
    if content_type in _UNASSERTED_CONTENT_TYPES:
        content_type = None
    if content_type and content_type not in allowed_content_types:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Content-Type {content_type!r} does not match {extension!r}",
        )

    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    if size_bytes > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds the {settings.max_upload_size_mb} MB limit",
        )
    if size_bytes == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty"
        )


@router.post(
    "/tenders/{tender_id}/documents",
    response_model=DocumentResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_document(
    tender_id: UUID,
    response: Response,
    file: UploadFile = File(...),
    current_user: User = Depends(require_permission("upload_client_docs")),
) -> DocumentResponse:
    """G2's upload flow. Returns 202 as soon as the object is stored and the
    outbox event committed - parsing/OCR/chunking/embedding happen later on
    the ingestion queue (R1: no parsing run executes inside a request).

    No Idempotency-Key: I1's required list does not include document upload,
    and the SHA-256 dedup below is the stronger guarantee anyway - it is
    keyed on the file's own bytes rather than a client-chosen header, so a
    retried upload converges even if the client forgets the key.
    """
    tender = await tender_service.get_tender(tender_id)
    if tender is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tender not found")

    content = await file.read()
    filename = file.filename or "upload"
    _validate_upload(filename, file.content_type, len(content))

    document, is_new = await document_service.create_document(
        tender_id=tender_id,
        organization_id=tender.organization_id,
        actor_user_id=current_user.id,
        filename=filename,
        content=content,
        mime_type=file.content_type,
    )
    # A duplicate is not newly accepted work - 200 says "this already
    # exists, here it is" rather than implying a second ingestion run was
    # queued (it was not).
    if not is_new:
        response.status_code = status.HTTP_200_OK
    return DocumentResponse.model_validate(document, from_attributes=True)


@router.get("/tenders/{tender_id}/documents", response_model=list[DocumentResponse])
async def list_documents(
    tender_id: UUID,
    current_user: User = Depends(require_permission("upload_client_docs")),
) -> list[DocumentResponse]:
    documents = await document_service.list_documents_for_tender(tender_id)
    return [DocumentResponse.model_validate(d, from_attributes=True) for d in documents]


@router.get("/documents/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: UUID,
    current_user: User = Depends(require_permission("upload_client_docs")),
) -> DocumentResponse:
    document = await document_service.get_document(document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return DocumentResponse.model_validate(document, from_attributes=True)


@router.post(
    "/documents/{document_id}/reprocess",
    response_model=DocumentResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def reprocess_document(
    document_id: UUID,
    current_user: User = Depends(require_permission("upload_client_docs")),
) -> DocumentResponse:
    document = await document_service.get_document(document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    tender = await tender_service.get_tender(document.tender_id) if document.tender_id else None
    if tender is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tender not found")

    reprocessed = await document_service.reprocess_document(
        document_id, tender.organization_id, current_user.id
    )
    if reprocessed is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return DocumentResponse.model_validate(reprocessed, from_attributes=True)
