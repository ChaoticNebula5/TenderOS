import io
import zipfile
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

# B15: real content sniffing, not just extension/Content-Type (both
# client-controlled, neither a security boundary on its own - see the
# comment on _validate_extension_and_content_type below). .pdf and the two
# zip-based OOXML types each have a real, checkable signature.
_PDF_MAGIC = b"%PDF-"
_ZIP_MAGIC = b"PK\x03\x04"
# The specific internal member that only a real docx/xlsx contains -
# distinguishes an actual Word/Excel file from an arbitrary zip that merely
# has the right magic bytes (any zip starts with PK\x03\x04).
_REQUIRED_ZIP_MEMBER: dict[str, str] = {
    ".docx": "word/document.xml",
    ".xlsx": "xl/workbook.xml",
}

# B15: archive-bomb defenses. .docx/.xlsx are zip archives (python-docx/
# openpyxl parse them via zipfile) - nothing before this checked decompressed
# size, entry count, or compression ratio, so a small, highly-compressed
# upload could exhaust memory/disk during parsing. All three checks read
# zipfile.infolist() (central-directory metadata) only - this never
# decompresses entry data itself, so the check can't become the DoS it's
# guarding against. Residual gap, on record rather than silently assumed
# closed: this trusts the zip's own recorded central-directory sizes; a
# crafted archive with an internally inconsistent local-file-header/
# central-directory size (a known zip-bomb evasion shape) is not separately
# cross-checked here - see KNOWN_LIMITATIONS.md.
#
# Ratio and entry-count ceilings are measured against this project's own
# real fixture family (data/synthetic/tender_a/*.docx, *.xlsx - 11 files,
# measured directly during B15 planning): observed max ratio 23.82x, max
# entry count 17. Both ceilings below carry real headroom above those
# observed values (~8.4x on ratio, ~59x on entry count).
_MAX_ZIP_ENTRIES = 1000
_MAX_ZIP_RATIO = 200

# The absolute decompressed-size ceiling below is reasoned, not measured
# against a large real document - the largest legitimate file available
# anywhere in this repo decompresses to ~830KB, far too small to extrapolate
# "headroom above an observed max" from the way the two ceilings above are
# derived. Instead, derived from the constraint already enforced upstream:
# max_upload_size_mb (50MB, core/config.py) bounds the COMPRESSED input
# before this function ever runs. A legitimate OOXML file's ratio, per the
# measurement above, tops out around 24x; doubling that for headroom on a
# larger real document (more repeated styles/tables compresses at least as
# well as these small fixtures, not worse) gives roughly 50MB x 50 = 2.5GB
# as a plausible ceiling for what a legitimate 50MB upload could decompress
# to. This ceiling sits comfortably above that estimate while staying well
# below the 50MB x 200 (ratio cap) = 10GB worst case the ratio and
# compressed-size caps would otherwise allow in combination. Flagged in
# KNOWN_LIMITATIONS.md as reasoned-not-measured, not presented as tuned.
_MAX_ZIP_DECOMPRESSED_BYTES = 4 * 1024 * 1024 * 1024  # 4 GiB

_READ_CHUNK_BYTES = 1024 * 1024  # 1 MiB


def _validate_extension_and_content_type(filename: str, content_type: str | None) -> str:
    """Cheap, no-I/O checks - filename and header only. Returns the matched
    extension so the caller doesn't re-derive it. Neither check is a real
    security boundary on its own (both are attacker-controlled), which is
    exactly why _sniff_and_check_archive below exists - this function only
    rejects the two obviously-wrong-labeled cases cheaply, before spending a
    real read on the upload.
    """
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
    # .docx, and 415-ing those would reject real uploads.
    if content_type in _UNASSERTED_CONTENT_TYPES:
        content_type = None
    if content_type and content_type not in allowed_content_types:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Content-Type {content_type!r} does not match {extension!r}",
        )
    return extension


async def _read_bounded(file: UploadFile, max_bytes: int) -> bytes:
    """Reads file in max_bytes+1 chunk increments, rejecting the instant the
    limit is crossed - never materializes an unbounded upload in memory.
    Previously this was `content = await file.read()` with no argument,
    reading the entire body before size was ever checked (KNOWN_LIMITATIONS
    major finding #20). Worst-case over-read here is bounded by one chunk
    (_READ_CHUNK_BYTES = 1MiB), not the file's full size.
    """
    buffer = bytearray()
    while True:
        chunk = await file.read(_READ_CHUNK_BYTES)
        if not chunk:
            break
        buffer.extend(chunk)
        if len(buffer) > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File exceeds the {settings.max_upload_size_mb} MB limit",
            )
    return bytes(buffer)


def _sniff_and_check_archive(extension: str, content: bytes) -> None:
    """Real content sniffing (B15) - the extension/Content-Type check above
    trusts client-controlled labels; this checks the actual bytes. For the
    two zip-based types this also runs the archive-bomb checks (entry count,
    decompressed size, compression ratio) against the same already-open
    zipfile, at no extra I/O cost.
    """
    if extension == ".pdf":
        if not content.startswith(_PDF_MAGIC):
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="File content does not match a PDF",
            )
        return

    if extension in _REQUIRED_ZIP_MEMBER:
        if not content.startswith(_ZIP_MAGIC):
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=f"File content does not match {extension} (not a zip archive)",
            )
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                if _REQUIRED_ZIP_MEMBER[extension] not in archive.namelist():
                    raise HTTPException(
                        status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                        detail=f"Archive does not contain the expected {extension} structure",
                    )
                infos = archive.infolist()
                if len(infos) > _MAX_ZIP_ENTRIES:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="Archive contains too many entries",
                    )
                total_decompressed = sum(i.file_size for i in infos)
                if total_decompressed > _MAX_ZIP_DECOMPRESSED_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="Archive would decompress to an excessive size",
                    )
                total_compressed = sum(i.compress_size for i in infos) or 1
                if total_decompressed / total_compressed > _MAX_ZIP_RATIO:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="Archive compression ratio exceeds the allowed limit",
                    )
        except zipfile.BadZipFile as exc:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=f"File is not a valid {extension} archive",
            ) from exc
        return

    # .csv: plain text has no reliable magic bytes to assert positively -
    # reject only the unambiguous mismatch (a file labeled .csv that is
    # actually a PDF or a zip/OOXML archive) rather than attempting full
    # content validation, which the deterministic CSV parser already does
    # at ingestion time (fails loudly there, per that parser's own tests).
    if content.startswith(_PDF_MAGIC) or content.startswith(_ZIP_MAGIC):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="File content does not match .csv",
        )


async def _read_and_validate_upload(
    file: UploadFile, filename: str, content_type: str | None
) -> bytes:
    """Replaces the old read-then-validate call-site pattern
    (`content = await file.read(); _validate_upload(filename, content_type,
    len(content))`) at all 6 upload routes (documents.py, knowledge.py) with
    one function that reads bounded and sniffs real content, returning the
    validated bytes so no caller re-reads.
    """
    extension = _validate_extension_and_content_type(filename, content_type)
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    content = await _read_bounded(file, max_bytes)
    if len(content) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty"
        )
    _sniff_and_check_archive(extension, content)
    return content


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

    filename = file.filename or "upload"
    content = await _read_and_validate_upload(file, filename, file.content_type)

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
