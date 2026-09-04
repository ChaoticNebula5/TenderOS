import uuid
from dataclasses import dataclass, replace

from tenderos_backend.agents.commercial.explain_findings import explain_finding
from tenderos_backend.agents.commercial.parse_pricing_workbook import parse_pricing_workbook
from tenderos_backend.agents.llm.client import LLMClient
from tenderos_backend.core.hashing import sha256_hex
from tenderos_backend.db.models.commercial import PricingDocument, PricingFinding
from tenderos_backend.db.storage import storage
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services.document_service import create_synthetic_chunk
from tenderos_backend.domain.services.pricing_checks import (
    LogicalRow,
    PricingFindingDraft,
    check_cross_doc_mismatch,
    check_currency_mismatch,
    check_grand_total,
    check_missing_cells,
    evaluate_row_arithmetic,
    find_duplicate_costs,
    finding_fingerprint,
    group_rows,
)


class DocumentNotFoundError(Exception):
    pass


class PricingDocumentNotFoundError(Exception):
    pass


class PricingFindingNotFoundError(Exception):
    pass


def _fingerprint(*parts: str) -> str:
    return sha256_hex("|".join(parts).encode("utf-8"))


# ----------------------------------------------------------------------
# Workbook ingestion
# ----------------------------------------------------------------------


async def _persist_pricing_cells(
    document_id: uuid.UUID, tender_id: uuid.UUID, cells: list
) -> list[PricingDocument]:
    """The actual persistence step, taking already-parsed cells - split out
    from ingest_pricing_workbook so evaluate_commercial_for_tender can parse
    each workbook exactly once and reuse the same parsed cells for both
    persistence and in-memory checking, instead of fetching+parsing the
    same bytes from object storage twice per document.

    Idempotent on retry: B4's own upload dedup means the same document_id
    always has the same immutable bytes, so re-parsing always produces the
    same cell content and therefore the same content_fingerprint - the
    fingerprint lookup below reuses existing rows rather than re-creating
    them, the same "retry does not duplicate" shape every other block's
    ingestion step already follows.
    """
    cells_by_sheet: dict[str, list] = {}
    for cell in cells:
        cells_by_sheet.setdefault(cell.sheet_name, []).append(cell)

    persisted: list[PricingDocument] = []
    async with UnitOfWork() as uow:
        for sheet_name, sheet_cells in cells_by_sheet.items():
            pricing_document = await uow.commercial.get_pricing_document_by_sheet(
                document_id, sheet_name
            )
            if pricing_document is None:
                pricing_document = await uow.commercial.create_pricing_document(
                    tender_id=tender_id, document_id=document_id, sheet_name=sheet_name
                )

            for cell in sheet_cells:
                item_fingerprint = _fingerprint(
                    str(pricing_document.id), cell.cell_ref, cell.raw_value or "", cell.formula or ""
                )
                existing_item = await uow.commercial.get_pricing_line_item_by_fingerprint(
                    pricing_document.id, item_fingerprint
                )
                if existing_item is None:
                    await uow.commercial.create_pricing_line_item(
                        pricing_document_id=pricing_document.id,
                        cell_ref=cell.cell_ref,
                        content_fingerprint=item_fingerprint,
                        description=cell.description,
                        raw_value=cell.raw_value,
                        numeric_value=cell.numeric_value,
                        formula=cell.formula,
                        currency=cell.currency,
                    )
            persisted.append(pricing_document)
        await uow.commit()
    return persisted


async def ingest_pricing_workbook(
    document_id: uuid.UUID, tender_id: uuid.UUID
) -> list[PricingDocument]:
    """Standalone entrypoint: fetch the raw .xlsx bytes from object
    storage, parse every sheet, persist. G9's own manual re-validation
    route uses this directly; evaluate_commercial_for_tender below fetches
    and parses itself instead, so it can reuse the same parsed cells for
    checking without a second round-trip to storage.
    """
    async with UnitOfWork() as uow:
        document = await uow.documents.get(document_id)
    if document is None:
        raise DocumentNotFoundError()

    content = await storage.get(document.storage_key)
    cells = parse_pricing_workbook(content)
    return await _persist_pricing_cells(document_id, tender_id, cells)


# ----------------------------------------------------------------------
# Deterministic checks + LLM explanation + persistence
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class _DocumentRows:
    pricing_document_id: uuid.UUID
    rows: list[LogicalRow]
    grand_total: object


async def _draft_findings_for_tender(
    document_rows: list[_DocumentRows],
) -> list[PricingFindingDraft]:
    drafts: list[PricingFindingDraft] = []
    for doc in document_rows:
        single_doc_drafts: list[PricingFindingDraft] = []
        for row in doc.rows:
            arithmetic_finding = evaluate_row_arithmetic(row)
            if arithmetic_finding is not None:
                single_doc_drafts.append(arithmetic_finding)
        single_doc_drafts.extend(find_duplicate_costs(doc.rows))
        single_doc_drafts.extend(check_missing_cells(doc.rows))
        single_doc_drafts.extend(check_currency_mismatch(doc.rows))
        grand_total_finding = check_grand_total(doc.rows, doc.grand_total)
        if grand_total_finding is not None:
            single_doc_drafts.append(grand_total_finding)

        # B16 Group 6: tag every single-document draft with the document
        # it came from - the check functions above are pure (rows in,
        # drafts out) and have no document identity to set this
        # themselves, so it's stamped on here, at the one place that
        # still knows which pricing_document these rows belong to.
        drafts.extend(
            replace(draft, pricing_document_id=doc.pricing_document_id)
            for draft in single_doc_drafts
        )

    cross_doc_input = [(str(doc.pricing_document_id), doc.rows) for doc in document_rows]
    drafts.extend(check_cross_doc_mismatch(cross_doc_input))
    return drafts


async def evaluate_commercial_for_tender(
    client: LLMClient,
    model: str,
    tender_id: uuid.UUID,
    workflow_run_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
) -> list[PricingFinding]:
    """The full pipeline: ingest every .xlsx document uploaded to this
    tender, run every deterministic check across all of them together
    (cross_doc_mismatch needs every sheet at once, not one at a time), then
    explain and persist each finding. PRD 10.10's own scoping choice
    applied here: every .xlsx uploaded to the tender is treated as a
    commercial artefact - this build doesn't yet distinguish a pricing
    workbook from some other unrelated spreadsheet a bidder might upload,
    since the synthetic package (and PRD 10.10's own "when commercial
    artefacts exist" framing) doesn't require that distinction yet.
    """
    async with UnitOfWork() as uow:
        documents = await uow.documents.list_for_tender(tender_id)
    xlsx_documents = [d for d in documents if d.filename.lower().endswith(".xlsx")]

    document_rows: list[_DocumentRows] = []
    for document in xlsx_documents:
        content = await storage.get(document.storage_key)
        cells = parse_pricing_workbook(content)
        pricing_documents = await _persist_pricing_cells(document.id, tender_id, cells)

        cells_by_sheet: dict[str, list] = {}
        for cell in cells:
            cells_by_sheet.setdefault(cell.sheet_name, []).append(cell)

        for pricing_document in pricing_documents:
            sheet_cells = cells_by_sheet.get(pricing_document.sheet_name, [])
            rows, grand_total = group_rows(sheet_cells)
            document_rows.append(_DocumentRows(pricing_document.id, rows, grand_total))

    drafts = await _draft_findings_for_tender(document_rows)

    persisted: list[PricingFinding] = []
    for draft in drafts:
        explanation = await explain_finding(
            client, model, draft, tender_id, workflow_run_id=workflow_run_id, task_id=task_id
        )
        fingerprint = finding_fingerprint(str(tender_id), draft)
        async with UnitOfWork() as uow:
            existing = await uow.commercial.get_pricing_finding_by_fingerprint(tender_id, fingerprint)
            if existing is None:
                finding = await uow.commercial.create_pricing_finding(
                    tender_id=tender_id,
                    finding_type=draft.finding_type,
                    note=f"{draft.note} {explanation.explanation}",
                    content_fingerprint=fingerprint,
                    pricing_document_id=draft.pricing_document_id,
                    item_a_ref=draft.item_a_ref,
                    item_b_ref=draft.item_b_ref,
                    delta=draft.delta,
                    severity=explanation.severity,
                )
            else:
                finding = existing
            await uow.commit()
        persisted.append(finding)
    return persisted


# ----------------------------------------------------------------------
# Human review (G9)
# ----------------------------------------------------------------------


def pricing_finding_evidence_fingerprint(
    finding_id: uuid.UUID, suffix: str | None = None
) -> str:
    """Shared with proposal_service.py's own _facts_from_pricing, which
    needs to look up the same EvidenceLink(s) this fingerprint scheme
    creates - one formula, not two independently-maintained copies that
    could drift (this project's own recurring "duplicated path" lesson).
    suffix distinguishes cross_doc_mismatch's two links ("a"/"b") from
    every other finding type's single one (None).
    """
    return sha256_hex(
        "|".join(["pricing_finding_evidence", str(finding_id), suffix or ""]).encode("utf-8")
    )


async def _materialize_one_pricing_evidence_link(
    uow: UnitOfWork, finding: PricingFinding, pricing_document_id: uuid.UUID, suffix: str | None
) -> None:
    fingerprint = pricing_finding_evidence_fingerprint(finding.id, suffix)
    existing = await uow.compliance.get_evidence_link_by_fingerprint(
        finding.tender_id, fingerprint
    )
    if existing is not None:
        return
    pricing_document = await uow.commercial.get_pricing_document(pricing_document_id)
    if pricing_document is None:
        return
    chunk = await create_synthetic_chunk(uow, pricing_document.document_id, finding.note)
    await uow.compliance.create_evidence_link(
        tender_id=finding.tender_id,
        source_kind="document_excerpt",
        content_fingerprint=fingerprint,
        source_chunk_id=chunk.id,
        excerpt=finding.note,
        verified=True,
        validity_status="valid",
    )


async def _ground_pricing_finding(uow: UnitOfWork, finding: PricingFinding) -> None:
    """B16 Group 6: resolved pricing findings become real, citable
    evidence - Option C from the citation-correctness follow-up
    (KNOWN_LIMITATIONS.md's [B9] entry). A resolved finding is a
    statement about a specific, real, already-uploaded pricing document,
    so representing it as a document_excerpt EvidenceLink (a synthetic
    chunk quoting the finding's own note, the same pattern
    import_certification_row already established for certifications) is
    accurate, not a workaround - and needs zero change to evidence_links'
    own schema or any of its existing readers (compliance coverage,
    red-team contradiction detection, the trace UI all already handle an
    ordinary document_excerpt link).

    cross_doc_mismatch is the one finding type genuinely about two
    documents at once - checked directly against pricing_checks.py, not
    assumed: item_a_ref/item_b_ref are already symmetric, equally-weighted
    references, and the finding's own note quotes both sides ("X is
    priced at V1 in document A, but V2 in document B") with neither
    privileged as the one being challenged. So it gets two evidence
    links, one per document, not one arbitrarily chosen side. Every
    other finding type has exactly one pricing_document_id (migration
    015) and gets one link.

    Idempotent by construction: resolve_pricing_finding_in_uow has no
    guard against being called again on an already-resolved finding, so
    this checks get_evidence_link_by_fingerprint first rather than
    crashing or duplicating evidence on a repeat resolve action.
    """
    if finding.finding_type == "cross_doc_mismatch":
        for suffix, ref in (("a", finding.item_a_ref), ("b", finding.item_b_ref)):
            if ref is None:
                continue
            await _materialize_one_pricing_evidence_link(
                uow, finding, uuid.UUID(ref["pricing_document_id"]), suffix
            )
        return
    if finding.pricing_document_id is not None:
        await _materialize_one_pricing_evidence_link(
            uow, finding, finding.pricing_document_id, None
        )
    # else: neither item_a_ref/item_b_ref nor pricing_document_id is set -
    # shouldn't happen for any of the 8 known finding types (migration
    # 015, PricingFindingDraft's own field docstrings), but a resolve
    # action must not crash over a data gap it can't help - the finding
    # just stays ungrounded, same as before this fix existed.


async def resolve_pricing_finding_in_uow(
    uow: UnitOfWork, pricing_finding_id: uuid.UUID, actor_user_id: uuid.UUID
) -> PricingFinding:
    """The transaction-open, no-commit core of resolve_pricing_finding -
    see compliance_service.approve_compliance_entry_in_uow's own docstring
    for why this exists."""
    finding = await uow.commercial.get_pricing_finding(pricing_finding_id)
    if finding is None:
        raise PricingFindingNotFoundError()
    tender = await uow.tenders.get(finding.tender_id)
    if tender is None:
        raise DocumentNotFoundError()

    before = {"resolved": finding.resolved}
    updated = await uow.commercial.update_pricing_finding(pricing_finding_id, resolved=True)
    await _ground_pricing_finding(uow, updated)
    await uow.audit.create(
        organization_id=tender.organization_id,
        actor_type="user",
        actor_id=str(actor_user_id),
        action="pricing_finding_resolve",
        tender_id=tender.id,
        target_type="pricing_finding",
        target_id=pricing_finding_id,
        before=before,
        after={"resolved": True},
    )
    return updated


async def resolve_pricing_finding(pricing_finding_id: uuid.UUID, actor_user_id: uuid.UUID) -> PricingFinding:
    async with UnitOfWork() as uow:
        updated = await resolve_pricing_finding_in_uow(uow, pricing_finding_id, actor_user_id)
        await uow.commit()
        return updated


async def list_pricing_documents_for_tender(tender_id: uuid.UUID) -> list[PricingDocument]:
    async with UnitOfWork() as uow:
        return await uow.commercial.list_pricing_documents_for_tender(tender_id)


async def list_pricing_findings_for_tender(tender_id: uuid.UUID) -> list[PricingFinding]:
    async with UnitOfWork() as uow:
        return await uow.commercial.list_pricing_findings_for_tender(tender_id)


async def get_pricing_document(pricing_document_id: uuid.UUID) -> PricingDocument | None:
    async with UnitOfWork() as uow:
        return await uow.commercial.get_pricing_document(pricing_document_id)
