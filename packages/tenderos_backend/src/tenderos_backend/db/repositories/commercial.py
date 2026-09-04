import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tenderos_backend.db.models.commercial import PricingDocument, PricingFinding, PricingLineItem


class CommercialRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # pricing_documents
    # ------------------------------------------------------------------

    async def create_pricing_document(
        self,
        tender_id: uuid.UUID,
        document_id: uuid.UUID,
        sheet_name: str,
        currency: str | None = None,
    ) -> PricingDocument:
        pricing_document = PricingDocument(
            tender_id=tender_id, document_id=document_id, sheet_name=sheet_name, currency=currency
        )
        self._session.add(pricing_document)
        await self._session.flush()
        return pricing_document

    async def get_pricing_document(self, pricing_document_id: uuid.UUID) -> PricingDocument | None:
        return await self._session.get(PricingDocument, pricing_document_id)

    async def get_pricing_document_by_sheet(
        self, document_id: uuid.UUID, sheet_name: str
    ) -> PricingDocument | None:
        # uq_pricing_document_sheet (Part C) - the lookup that makes
        # re-ingesting the same uploaded workbook idempotent: reuse the
        # existing pricing_documents row for a sheet rather than creating a
        # second one.
        result = await self._session.execute(
            select(PricingDocument).where(
                PricingDocument.document_id == document_id,
                PricingDocument.sheet_name == sheet_name,
            )
        )
        return result.scalar_one_or_none()

    async def list_pricing_documents_for_tender(self, tender_id: uuid.UUID) -> list[PricingDocument]:
        result = await self._session.execute(
            select(PricingDocument).where(PricingDocument.tender_id == tender_id)
        )
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # pricing_line_items
    # ------------------------------------------------------------------

    async def create_pricing_line_item(
        self,
        pricing_document_id: uuid.UUID,
        cell_ref: str,
        content_fingerprint: str,
        description: str | None = None,
        raw_value: str | None = None,
        numeric_value: float | None = None,
        formula: str | None = None,
        currency: str | None = None,
    ) -> PricingLineItem:
        item = PricingLineItem(
            pricing_document_id=pricing_document_id,
            cell_ref=cell_ref,
            description=description,
            raw_value=raw_value,
            numeric_value=numeric_value,
            formula=formula,
            currency=currency,
            content_fingerprint=content_fingerprint,
        )
        self._session.add(item)
        await self._session.flush()
        return item

    async def get_pricing_line_item_by_fingerprint(
        self, pricing_document_id: uuid.UUID, content_fingerprint: str
    ) -> PricingLineItem | None:
        result = await self._session.execute(
            select(PricingLineItem).where(
                PricingLineItem.pricing_document_id == pricing_document_id,
                PricingLineItem.content_fingerprint == content_fingerprint,
            )
        )
        return result.scalar_one_or_none()

    async def list_line_items_for_pricing_document(
        self, pricing_document_id: uuid.UUID
    ) -> list[PricingLineItem]:
        result = await self._session.execute(
            select(PricingLineItem).where(PricingLineItem.pricing_document_id == pricing_document_id)
        )
        return list(result.scalars().all())

    async def list_line_items_for_tender(self, tender_id: uuid.UUID) -> list[PricingLineItem]:
        # Cross-doc checks (cross_doc_mismatch) need every line item across
        # every pricing_document for the tender at once, not one document at
        # a time - joined through PricingDocument since PricingLineItem
        # carries no tender_id column of its own (Part C), same shape as
        # ComplianceRepository.list_compliance_entries_for_tender's join
        # through Requirement.
        result = await self._session.execute(
            select(PricingLineItem)
            .join(PricingDocument, PricingDocument.id == PricingLineItem.pricing_document_id)
            .where(PricingDocument.tender_id == tender_id)
        )
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # pricing_findings
    # ------------------------------------------------------------------

    async def create_pricing_finding(
        self,
        tender_id: uuid.UUID,
        finding_type: str,
        note: str,
        content_fingerprint: str,
        pricing_document_id: uuid.UUID | None = None,
        item_a_ref: dict | None = None,
        item_b_ref: dict | None = None,
        delta: float | None = None,
        severity: str = "major",
    ) -> PricingFinding:
        finding = PricingFinding(
            tender_id=tender_id,
            finding_type=finding_type,
            pricing_document_id=pricing_document_id,
            item_a_ref=item_a_ref,
            item_b_ref=item_b_ref,
            delta=delta,
            severity=severity,
            note=note,
            content_fingerprint=content_fingerprint,
        )
        self._session.add(finding)
        await self._session.flush()
        return finding

    async def get_pricing_finding(self, pricing_finding_id: uuid.UUID) -> PricingFinding | None:
        return await self._session.get(PricingFinding, pricing_finding_id)

    async def get_pricing_finding_by_fingerprint(
        self, tender_id: uuid.UUID, content_fingerprint: str
    ) -> PricingFinding | None:
        result = await self._session.execute(
            select(PricingFinding).where(
                PricingFinding.tender_id == tender_id,
                PricingFinding.content_fingerprint == content_fingerprint,
            )
        )
        return result.scalar_one_or_none()

    async def list_pricing_findings_for_tender(self, tender_id: uuid.UUID) -> list[PricingFinding]:
        result = await self._session.execute(
            select(PricingFinding).where(PricingFinding.tender_id == tender_id)
        )
        return list(result.scalars().all())

    async def update_pricing_finding(
        self, pricing_finding_id: uuid.UUID, **fields: object
    ) -> PricingFinding:
        finding = await self._session.get(PricingFinding, pricing_finding_id)
        if finding is None:
            raise ValueError(f"PricingFinding {pricing_finding_id} not found")
        for key, value in fields.items():
            setattr(finding, key, value)
        await self._session.flush()
        return finding
