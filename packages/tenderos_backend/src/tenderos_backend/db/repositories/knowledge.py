import uuid
from datetime import UTC, date, datetime

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tenderos_backend.db.models.knowledge import (
    Capability,
    Certification,
    CompanyDocument,
    Employee,
    KnowledgeImportBatch,
    KnowledgeImportItem,
    KnowledgeItem,
    KnowledgeItemChunk,
    KnowledgeRelationship,
    PastProject,
    TenderKnowledgeSelection,
)


class KnowledgeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Import batches / items
    # ------------------------------------------------------------------

    async def create_import_batch(
        self,
        organization_id: uuid.UUID,
        import_type: str,
        source_document_id: uuid.UUID | None = None,
        created_by: uuid.UUID | None = None,
    ) -> KnowledgeImportBatch:
        batch = KnowledgeImportBatch(
            organization_id=organization_id,
            import_type=import_type,
            source_document_id=source_document_id,
            created_by=created_by,
        )
        self._session.add(batch)
        await self._session.flush()
        return batch

    async def get_import_batch(self, batch_id: uuid.UUID) -> KnowledgeImportBatch | None:
        return await self._session.get(KnowledgeImportBatch, batch_id)

    async def mark_import_batch_completed(self, batch_id: uuid.UUID) -> bool:
        """Phase 4 consumer guard: stamp completed_at (database clock) the
        first time a batch's document import finishes. Returns False if it
        was already stamped. Only the capability-statement path uses it so
        far; nothing else writes this column."""
        result = await self._session.execute(
            update(KnowledgeImportBatch)
            .where(
                KnowledgeImportBatch.id == batch_id,
                KnowledgeImportBatch.completed_at.is_(None),
            )
            .values(completed_at=func.now())
            .returning(KnowledgeImportBatch.id)
            .execution_options(synchronize_session=False)
        )
        return result.scalar_one_or_none() is not None

    async def list_import_batches_for_org(
        self, organization_id: uuid.UUID
    ) -> list[KnowledgeImportBatch]:
        result = await self._session.execute(
            select(KnowledgeImportBatch)
            .where(KnowledgeImportBatch.organization_id == organization_id)
            .order_by(KnowledgeImportBatch.created_at.desc())
        )
        return list(result.scalars().all())

    async def update_import_batch(
        self, batch_id: uuid.UUID, **fields: object
    ) -> KnowledgeImportBatch:
        batch = await self._session.get(KnowledgeImportBatch, batch_id)
        if batch is None:
            raise ValueError(f"KnowledgeImportBatch {batch_id} not found")
        for key, value in fields.items():
            setattr(batch, key, value)
        await self._session.flush()
        return batch

    async def create_import_item(
        self, batch_id: uuid.UUID, source_ref: str, extracted_payload: dict | None = None
    ) -> KnowledgeImportItem:
        item = KnowledgeImportItem(
            batch_id=batch_id,
            source_ref=source_ref,
            extracted_payload=extracted_payload or {},
        )
        self._session.add(item)
        await self._session.flush()
        return item

    async def get_import_item(self, item_id: uuid.UUID) -> KnowledgeImportItem | None:
        return await self._session.get(KnowledgeImportItem, item_id)

    async def get_import_item_by_source_ref(
        self, batch_id: uuid.UUID, source_ref: str
    ) -> KnowledgeImportItem | None:
        """Lets a per-row import function check "was this row already
        processed" before calling create_import_item, so a retried worker
        job (the same rows, re-parsed from the same file) can resume past
        whatever uq_knowledge_import_item_source_ref already committed
        instead of crashing on the first duplicate (batch_id, source_ref)
        it re-attempts. See KNOWN_LIMITATIONS.md's "neither retryable nor
        accurately observable" entry.
        """
        result = await self._session.execute(
            select(KnowledgeImportItem).where(
                KnowledgeImportItem.batch_id == batch_id,
                KnowledgeImportItem.source_ref == source_ref,
            )
        )
        return result.scalar_one_or_none()

    async def list_import_items_for_batch(self, batch_id: uuid.UUID) -> list[KnowledgeImportItem]:
        result = await self._session.execute(
            select(KnowledgeImportItem).where(KnowledgeImportItem.batch_id == batch_id)
        )
        return list(result.scalars().all())

    async def update_import_item(self, item_id: uuid.UUID, **fields: object) -> KnowledgeImportItem:
        item = await self._session.get(KnowledgeImportItem, item_id)
        if item is None:
            raise ValueError(f"KnowledgeImportItem {item_id} not found")
        for key, value in fields.items():
            setattr(item, key, value)
        await self._session.flush()
        await self._session.refresh(item)
        return item

    # ------------------------------------------------------------------
    # knowledge_items (generic parent row)
    # ------------------------------------------------------------------

    async def get_knowledge_item(self, knowledge_item_id: uuid.UUID) -> KnowledgeItem | None:
        return await self._session.get(KnowledgeItem, knowledge_item_id)

    async def get_knowledge_item_by_fingerprint(
        self, organization_id: uuid.UUID, item_type: str, content_fingerprint: str
    ) -> KnowledgeItem | None:
        result = await self._session.execute(
            select(KnowledgeItem).where(
                KnowledgeItem.organization_id == organization_id,
                KnowledgeItem.item_type == item_type,
                KnowledgeItem.content_fingerprint == content_fingerprint,
            )
        )
        return result.scalar_one_or_none()

    async def get_knowledge_items_by_chunk_id(self, chunk_id: uuid.UUID) -> list[KnowledgeItem]:
        # knowledge_items.source_chunk_id is the "primary" chunk a knowledge
        # item was extracted from (Part C) - retrieval's search over
        # document_chunks needs this to map a matched chunk back to the
        # knowledge_item(s) it's evidence for. Plural, not scalar_one: B4's
        # chunker groups multiple paragraphs under one heading into a
        # single chunk when they fit the token budget, so one chunk can
        # legitimately be the source for several knowledge_items at once
        # (confirmed live - two of the four capability-statement projects
        # landed in the same chunk).
        #
        # A second, OR'd condition against knowledge_item_chunks handles the
        # reverse cardinality the scalar column can't: one knowledge_item
        # (an employee) evidenced by several chunks, not one chunk shared by
        # several items. See docs/eval/citation-correctness-fix-options.md -
        # certifications/past_projects/capabilities never need this side,
        # since each is genuinely sourced from exactly one chunk; only the
        # scalar column ever matches for them, and this OR is a no-op for
        # those rows.
        result = await self._session.execute(
            select(KnowledgeItem).where(
                or_(
                    KnowledgeItem.source_chunk_id == chunk_id,
                    KnowledgeItem.id.in_(
                        select(KnowledgeItemChunk.knowledge_item_id).where(
                            KnowledgeItemChunk.chunk_id == chunk_id
                        )
                    ),
                )
            )
        )
        return list(result.scalars().all())

    async def associate_knowledge_item_chunks(
        self, knowledge_item_id: uuid.UUID, chunk_ids: list[uuid.UUID]
    ) -> None:
        # Separate from create_employee rather than another constructor
        # parameter - create_employee already takes 12, and this needs
        # calling from two places in import_resume (new-employee creation
        # and the duplicate-résumé-merge branch), not only at creation time.
        # ON CONFLICT DO NOTHING (not session.add, which would raise
        # IntegrityError on the composite primary key) - a retried worker
        # job re-running import_resume on the same résumé, or the
        # duplicate-merge branch re-associating chunks it already holds
        # from a prior upload, must not crash on re-associating a pair that
        # already exists. Same "retried job hits a real duplicate, must not
        # crash" shape as get_import_item_by_source_ref above.
        if not chunk_ids:
            return
        stmt = (
            pg_insert(KnowledgeItemChunk)
            .values(
                [
                    {"knowledge_item_id": knowledge_item_id, "chunk_id": chunk_id}
                    for chunk_id in chunk_ids
                ]
            )
            .on_conflict_do_nothing()
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def list_knowledge_items_for_org(
        self, organization_id: uuid.UUID, item_type: str | None = None
    ) -> list[KnowledgeItem]:
        stmt = select(KnowledgeItem).where(KnowledgeItem.organization_id == organization_id)
        if item_type is not None:
            stmt = stmt.where(KnowledgeItem.item_type == item_type)
        result = await self._session.execute(stmt.order_by(KnowledgeItem.title))
        return list(result.scalars().all())

    async def update_knowledge_item(
        self, knowledge_item_id: uuid.UUID, **fields: object
    ) -> KnowledgeItem:
        item = await self._session.get(KnowledgeItem, knowledge_item_id)
        if item is None:
            raise ValueError(f"KnowledgeItem {knowledge_item_id} not found")
        for key, value in fields.items():
            setattr(item, key, value)
        await self._session.flush()
        await self._session.refresh(item)
        return item

    # ------------------------------------------------------------------
    # Employees (knowledge_items row + employees child row, created together
    # since an employee never exists as one without the other)
    # ------------------------------------------------------------------

    async def create_employee(
        self,
        organization_id: uuid.UUID,
        name: str,
        content_fingerprint: str,
        title: str,
        employee_code: str | None = None,
        email: str | None = None,
        current_title: str | None = None,
        professional_location: str | None = None,
        years_experience: float | None = None,
        resume_document_id: uuid.UUID | None = None,
        source_document_id: uuid.UUID | None = None,
        parsed_profile: dict | None = None,
    ) -> tuple[KnowledgeItem, Employee]:
        knowledge_item = KnowledgeItem(
            organization_id=organization_id,
            item_type="employee",
            title=title,
            content_fingerprint=content_fingerprint,
            source_document_id=source_document_id,
        )
        self._session.add(knowledge_item)
        await self._session.flush()

        employee = Employee(
            knowledge_item_id=knowledge_item.id,
            organization_id=organization_id,
            employee_code=employee_code,
            name=name,
            email=email,
            current_title=current_title,
            professional_location=professional_location,
            years_experience=years_experience,
            resume_document_id=resume_document_id,
            parsed_profile=parsed_profile or {},
        )
        self._session.add(employee)
        await self._session.flush()
        return knowledge_item, employee

    async def get_employee(self, knowledge_item_id: uuid.UUID) -> Employee | None:
        return await self._session.get(Employee, knowledge_item_id)

    async def get_employee_by_code(
        self, organization_id: uuid.UUID, employee_code: str
    ) -> Employee | None:
        result = await self._session.execute(
            select(Employee).where(
                Employee.organization_id == organization_id,
                Employee.employee_code == employee_code,
            )
        )
        return result.scalar_one_or_none()

    async def find_employee_duplicate_candidates(
        self, organization_id: uuid.UUID
    ) -> list[Employee]:
        """Every employee in the organization - agents/knowledge/dedup.py's
        resolve_employee_duplicate does the actual matching (employee_code,
        email, resume_document_id, normalize_employee_name) in Python. This
        used to pre-filter with SQL (employee_code=/email=/resume_document_id=
        exact matches, name matched via ILIKE), but ILIKE can't replicate
        normalize_employee_name's accent-stripping or parenthetical-suffix
        removal - "Dana Whitfield (dup)" never matched "Dana Whitfield" at
        the SQL layer, silently starving the Python matcher of the one
        candidate it needed to see (caught live against the seeded
        EMP-01/EMP-05 duplicate fixture). Adding unaccent() would mean a new
        Postgres extension for one call site; organization employee counts
        are small enough (tens-hundreds, not the scale that would make a
        full-table fetch expensive) that returning everyone and filtering in
        Python - where the normalization logic already lives and is already
        correct - is the simpler, honest fix. Revisit if org sizes ever make
        this fetch itself the bottleneck.
        """
        result = await self._session.execute(
            select(Employee).where(Employee.organization_id == organization_id)
        )
        return list(result.scalars().all())

    async def list_bid_eligible_employees(self, organization_id: uuid.UUID) -> list[Employee]:
        result = await self._session.execute(
            select(Employee).where(
                Employee.organization_id == organization_id,
                Employee.eligible_for_bid.is_(True),
                Employee.profile_review_status == "verified",
            )
        )
        return list(result.scalars().all())

    async def list_candidate_pool_employees(self, organization_id: uuid.UUID) -> list[Employee]:
        """B8A's talent candidate pool, deliberately looser than
        list_bid_eligible_employees (which requires profile_review_status
        == 'verified' exactly - built for a "browse the approved talent
        library" view). eligible_for_bid is still a hard filter (Build
        Guide test: "employee not eligible for bid use excluded" has no
        "unless reviewed" exception), but 'needs_review' and 'stale'
        profiles pass through here rather than being hidden - PRD 10.9
        asks the system to "surface explicit gaps and stale or unknown
        profile facts," not silently exclude them, and B8A's own test
        wording ("stale profile visibly warned OR excluded by policy")
        treats warning as an acceptable alternative to exclusion. Only
        'archived' (retired from the roster) and 'rejected' (explicitly
        disqualified) are hard-excluded here - talent_service.py's
        gap-enforcement step is what turns 'needs_review'/'stale' into a
        visible warning for anything that does pass through.
        """
        result = await self._session.execute(
            select(Employee).where(
                Employee.organization_id == organization_id,
                Employee.eligible_for_bid.is_(True),
                Employee.profile_review_status.notin_(["archived", "rejected"]),
            )
        )
        return list(result.scalars().all())

    async def update_employee(self, knowledge_item_id: uuid.UUID, **fields: object) -> Employee:
        employee = await self._session.get(Employee, knowledge_item_id)
        if employee is None:
            raise ValueError(f"Employee {knowledge_item_id} not found")
        for key, value in fields.items():
            setattr(employee, key, value)
        await self._session.flush()
        return employee

    # ------------------------------------------------------------------
    # Certifications / past projects / capabilities / company documents
    # (each a knowledge_items row + one child row, same shape as employees)
    # ------------------------------------------------------------------

    async def create_certification(
        self,
        organization_id: uuid.UUID,
        title: str,
        content_fingerprint: str,
        holder_employee_item_id: uuid.UUID | None = None,
        issuer: str | None = None,
        credential_id: str | None = None,
        valid_from: date | None = None,
        valid_until: date | None = None,
        source_document_id: uuid.UUID | None = None,
        source_chunk_id: uuid.UUID | None = None,
        status: str = "active",
    ) -> tuple[KnowledgeItem, Certification]:
        knowledge_item = KnowledgeItem(
            organization_id=organization_id,
            item_type="certification",
            title=title,
            content_fingerprint=content_fingerprint,
            valid_from=valid_from,
            valid_until=valid_until,
            source_document_id=source_document_id,
            source_chunk_id=source_chunk_id,
            status=status,
        )
        self._session.add(knowledge_item)
        await self._session.flush()

        certification = Certification(
            knowledge_item_id=knowledge_item.id,
            holder_employee_item_id=holder_employee_item_id,
            issuer=issuer,
            credential_id=credential_id,
        )
        self._session.add(certification)
        await self._session.flush()
        return knowledge_item, certification

    async def list_certifications_for_employee(
        self, holder_employee_item_id: uuid.UUID
    ) -> list[Certification]:
        result = await self._session.execute(
            select(Certification).where(
                Certification.holder_employee_item_id == holder_employee_item_id
            )
        )
        return list(result.scalars().all())

    # B10: the graph projector resolves a reachable knowledge_item_id to its
    # child row by item_type - get_employee already existed (B6), these
    # three are the same shape for the other 3 shared node labels. No
    # existing caller needed a lookup-by-own-id for these until now, since
    # every prior block only ever walked from an employee outward
    # (list_certifications_for_employee) rather than starting from an
    # arbitrary knowledge_item_id and needing to know what it is.
    async def get_certification(self, knowledge_item_id: uuid.UUID) -> Certification | None:
        return await self._session.get(Certification, knowledge_item_id)

    async def get_past_project(self, knowledge_item_id: uuid.UUID) -> PastProject | None:
        return await self._session.get(PastProject, knowledge_item_id)

    async def get_capability(self, knowledge_item_id: uuid.UUID) -> Capability | None:
        return await self._session.get(Capability, knowledge_item_id)

    async def create_past_project(
        self,
        organization_id: uuid.UUID,
        title: str,
        content_fingerprint: str,
        client_name: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        description: str | None = None,
        source_document_id: uuid.UUID | None = None,
        source_chunk_id: uuid.UUID | None = None,
    ) -> tuple[KnowledgeItem, PastProject]:
        knowledge_item = KnowledgeItem(
            organization_id=organization_id,
            item_type="past_project",
            title=title,
            content_fingerprint=content_fingerprint,
            source_document_id=source_document_id,
            source_chunk_id=source_chunk_id,
        )
        self._session.add(knowledge_item)
        await self._session.flush()

        project = PastProject(
            knowledge_item_id=knowledge_item.id,
            client_name=client_name,
            start_date=start_date,
            end_date=end_date,
            description=description,
        )
        self._session.add(project)
        await self._session.flush()
        return knowledge_item, project

    async def create_capability(
        self,
        organization_id: uuid.UUID,
        title: str,
        content_fingerprint: str,
        category: str | None = None,
        description: str | None = None,
        source_document_id: uuid.UUID | None = None,
        source_chunk_id: uuid.UUID | None = None,
    ) -> tuple[KnowledgeItem, Capability]:
        knowledge_item = KnowledgeItem(
            organization_id=organization_id,
            item_type="capability",
            title=title,
            content_fingerprint=content_fingerprint,
            source_document_id=source_document_id,
            source_chunk_id=source_chunk_id,
        )
        self._session.add(knowledge_item)
        await self._session.flush()

        capability = Capability(
            knowledge_item_id=knowledge_item.id, category=category, description=description
        )
        self._session.add(capability)
        await self._session.flush()
        return knowledge_item, capability

    async def create_company_document(
        self,
        organization_id: uuid.UUID,
        title: str,
        content_fingerprint: str,
        document_id: uuid.UUID,
        doc_subtype: str,
    ) -> tuple[KnowledgeItem, CompanyDocument]:
        knowledge_item = KnowledgeItem(
            organization_id=organization_id,
            item_type="company_document",
            title=title,
            content_fingerprint=content_fingerprint,
            source_document_id=document_id,
        )
        self._session.add(knowledge_item)
        await self._session.flush()

        company_document = CompanyDocument(
            knowledge_item_id=knowledge_item.id, document_id=document_id, doc_subtype=doc_subtype
        )
        self._session.add(company_document)
        await self._session.flush()
        return knowledge_item, company_document

    # ------------------------------------------------------------------
    # knowledge_relationships (WORKED_ON / DEMONSTRATES)
    # ------------------------------------------------------------------

    async def create_relationship(
        self,
        organization_id: uuid.UUID,
        source_knowledge_item_id: uuid.UUID,
        relationship_type: str,
        target_knowledge_item_id: uuid.UUID,
        content_fingerprint: str,
        source_chunk_id: uuid.UUID | None = None,
        confidence: float | None = None,
        verified: bool = False,
    ) -> KnowledgeRelationship:
        relationship = KnowledgeRelationship(
            organization_id=organization_id,
            source_knowledge_item_id=source_knowledge_item_id,
            relationship_type=relationship_type,
            target_knowledge_item_id=target_knowledge_item_id,
            source_chunk_id=source_chunk_id,
            confidence=confidence,
            verified=verified,
            content_fingerprint=content_fingerprint,
        )
        self._session.add(relationship)
        await self._session.flush()
        return relationship

    async def get_relationship_by_fingerprint(
        self, organization_id: uuid.UUID, content_fingerprint: str
    ) -> KnowledgeRelationship | None:
        result = await self._session.execute(
            select(KnowledgeRelationship).where(
                KnowledgeRelationship.organization_id == organization_id,
                KnowledgeRelationship.content_fingerprint == content_fingerprint,
            )
        )
        return result.scalar_one_or_none()

    async def list_relationships_for_org(
        self, organization_id: uuid.UUID, verified_only: bool = False
    ) -> list[KnowledgeRelationship]:
        stmt = select(KnowledgeRelationship).where(
            KnowledgeRelationship.organization_id == organization_id
        )
        if verified_only:
            stmt = stmt.where(KnowledgeRelationship.verified.is_(True))
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # tender_knowledge_selections
    # ------------------------------------------------------------------

    async def get_selection(
        self, tender_id: uuid.UUID, knowledge_item_id: uuid.UUID
    ) -> TenderKnowledgeSelection | None:
        result = await self._session.execute(
            select(TenderKnowledgeSelection).where(
                TenderKnowledgeSelection.tender_id == tender_id,
                TenderKnowledgeSelection.knowledge_item_id == knowledge_item_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_selections_for_tender(
        self, tender_id: uuid.UUID, selected_only: bool = False
    ) -> list[TenderKnowledgeSelection]:
        stmt = select(TenderKnowledgeSelection).where(
            TenderKnowledgeSelection.tender_id == tender_id
        )
        if selected_only:
            stmt = stmt.where(TenderKnowledgeSelection.selected.is_(True))
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def upsert_selection(
        self,
        tender_id: uuid.UUID,
        knowledge_item_id: uuid.UUID,
        selected: bool,
        actor_user_id: uuid.UUID,
        change_reason: str | None = None,
    ) -> TenderKnowledgeSelection:
        existing = await self.get_selection(tender_id, knowledge_item_id)
        now = datetime.now(UTC)
        if existing is None:
            selection = TenderKnowledgeSelection(
                tender_id=tender_id,
                knowledge_item_id=knowledge_item_id,
                selected=selected,
                selected_at=now if selected else None,
                selected_by=actor_user_id if selected else None,
                deselected_at=None if selected else now,
                deselected_by=None if selected else actor_user_id,
                change_reason=change_reason,
            )
            self._session.add(selection)
            await self._session.flush()
            return selection

        existing.selected = selected
        if selected:
            existing.selected_at = now
            existing.selected_by = actor_user_id
        else:
            existing.deselected_at = now
            existing.deselected_by = actor_user_id
        existing.change_reason = change_reason
        await self._session.flush()
        return existing
