import uuid
from collections.abc import Iterable
from datetime import UTC, date, datetime

from sqlalchemy import func, or_, select, union, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tenderos_backend.core.evidence_validity import ItemValidity, evidence_validity
from tenderos_backend.db.models.compliance import EvidenceLink
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
from tenderos_backend.db.models.proposal import (
    ProposalClaim,
    ProposalClaimEmployee,
    ProposalSection,
)
from tenderos_backend.db.models.talent import RoleMatch, RoleRequirement
from tenderos_backend.db.repositories.talent import role_in_scope

# The knowledge item types the graph has a node for (D1): the projector's four
# typed branches (graph_projection/projector.py). The other types
# (company_document, client_reference, partner) are never projected, so a
# change to one makes no graph stale (plan item 95).
# tests/integration/test_projection_reach.py keeps the two in step.
PROJECTED_KNOWLEDGE_ITEM_TYPES = frozenset(
    {"employee", "certification", "past_project", "capability"}
)

# An employee removed from the roster: its knowledge item's status (Phase 4
# plan item 79, C1). The employee-master import writes it for a "removed" row
# (knowledge_service._EMPLOYEE_STATUS_TO_KNOWLEDGE_ITEM_STATUS) and the talent
# library's status edit can set it. This is the one place "archived" is read
# for employees: the candidate pool, the bid-eligible list and verification
# all use it, and since plan item 86 so do selection, the resource gate's
# counts, the red team, readiness and the proposal's facts (through
# archived_item_ids below). employees.profile_review_status also allows
# 'archived' in its CHECK, but nothing writes that value. Since plan items 89
# and 91 the evidence readers judge an item through item_validity instead
# (core/evidence_validity.py), which reads this status with the item's date
# and verification; archived_item_ids stays for the roster readers. Since
# plan item 95 any status change (archiving included) bumps every bid whose
# graph includes the item (list_tender_ids_projecting below).
KNOWLEDGE_ITEM_ARCHIVED = "archived"


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
        self,
        batch_id: uuid.UUID,
        source_ref: str,
        extracted_payload: dict | None = None,
        source_document_id: uuid.UUID | None = None,
    ) -> KnowledgeImportItem:
        item = KnowledgeImportItem(
            batch_id=batch_id,
            source_ref=source_ref,
            extracted_payload=extracted_payload or {},
            source_document_id=source_document_id,
        )
        self._session.add(item)
        await self._session.flush()
        return item

    async def get_import_item(self, item_id: uuid.UUID) -> KnowledgeImportItem | None:
        return await self._session.get(KnowledgeImportItem, item_id)

    async def get_import_item_for_update(self, item_id: uuid.UUID) -> KnowledgeImportItem | None:
        """The import item, its row locked until this transaction ends (plan
        item 80). Approving or rejecting reads its item this way, so a second
        decision on the same item waits and then sees the first one's status.
        populate_existing: a row already in the session is refreshed from the
        locked read, as GateRepository.get_for_update."""
        result = await self._session.execute(
            select(KnowledgeImportItem)
            .where(KnowledgeImportItem.id == item_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

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

    async def get_knowledge_item_for_update(
        self, knowledge_item_id: uuid.UUID
    ) -> KnowledgeItem | None:
        """The item, its row locked until this transaction ends, for a caller
        that compares the item's values with what it is about to write (plan
        item 59, fix 7: the bump decision's "before"). A second such caller
        waits, then reads what the first committed.

        FOR NO KEY UPDATE, not FOR UPDATE: it is the lock the UPDATE that
        follows takes, so nothing is upgraded, and it lets an insert of a row
        that references the item through (the foreign key check takes KEY
        SHARE). Fourteen foreign keys reference knowledge_items: evidence
        links, selections, role matches, relationships, claims. Nothing
        that takes this lock deletes the item or changes its id.
        populate_existing: a row already in the session is refreshed from the
        locked read."""
        result = await self._session.execute(
            select(KnowledgeItem)
            .where(KnowledgeItem.id == knowledge_item_id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def get_knowledge_item_for_share(
        self, knowledge_item_id: uuid.UUID
    ) -> KnowledgeItem | None:
        """The item, read FOR SHARE until this transaction ends: for a check
        of its status that a decision rests on, by a transaction that does
        not write the item (plan item 59, fix 8: verify's and the select's
        archived checks). An archive that has written the item but not
        committed is waited for, and one arriving after the check waits for
        this transaction.

        FOR SHARE is the weakest mode that conflicts with the archive's
        UPDATE (FOR NO KEY UPDATE); FOR KEY SHARE would not. A caller must
        not then write the item: two such transactions would each hold the
        share the other's write needs, a deadlock."""
        result = await self._session.execute(
            select(KnowledgeItem)
            .where(KnowledgeItem.id == knowledge_item_id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def archived_item_ids(self, knowledge_item_ids: Iterable[uuid.UUID]) -> set[uuid.UUID]:
        """The ids among these whose knowledge item is archived now: for an
        employee, removed from the roster.

        Plan item 86: archiving changes only knowledge_items.status, so a
        reader that must not trust a removed employee asks here, at the time
        it runs. One query for the whole set; an empty set queries nothing.

        Since plan items 89 and 91 only the roster readers ask here
        (selection, readiness's removed-employee blocker, the red team's
        archived-employee check, the resource gate's count, the team the
        drafter is offered, the team page's badge). Evidence readers use
        item_validity below, which also sees an expired date and
        verification (tests/unit/test_evidence_readers.py keeps it so).
        """
        ids = set(knowledge_item_ids)
        if not ids:
            return set()
        result = await self._session.execute(
            select(KnowledgeItem.id).where(
                KnowledgeItem.id.in_(ids), KnowledgeItem.status == KNOWLEDGE_ITEM_ARCHIVED
            )
        )
        return set(result.scalars().all())

    async def item_validity(
        self, knowledge_item_ids: Iterable[uuid.UUID], today: date
    ) -> dict[uuid.UUID, ItemValidity]:
        """Each of these knowledge items judged now (plan items 89 and 91):
        its status, its valid_until against `today` and, for an employee,
        its profile review, through core/evidence_validity.py. One query for
        the whole set; an id with no row comes back as missing (unknown).
        An empty set queries nothing."""
        ids = set(knowledge_item_ids)
        if not ids:
            return {}
        result = await self._session.execute(
            select(
                KnowledgeItem.id,
                KnowledgeItem.status,
                KnowledgeItem.valid_until,
                KnowledgeItem.item_type,
                KnowledgeItem.title,
                Employee.profile_review_status,
            )
            .outerjoin(Employee, Employee.knowledge_item_id == KnowledgeItem.id)
            .where(KnowledgeItem.id.in_(ids))
        )
        found = {
            row.id: evidence_validity(
                status=row.status,
                valid_until=row.valid_until,
                today=today,
                is_employee=row.item_type == "employee",
                profile_review_status=row.profile_review_status,
                title=row.title,
            )
            for row in result
        }
        missing = evidence_validity(status=None, valid_until=None, today=today)
        return {kid: found.get(kid, missing) for kid in ids}

    async def list_tender_ids_projecting(self, knowledge_item_id: uuid.UUID) -> list[uuid.UUID]:
        """Every bid whose graph includes this knowledge item's node, each once,
        sorted (plan item 95). A change to a projected field of the item makes
        each of these graphs stale, so each is bumped, in this order, so two
        edits bumping overlapping bids take the row locks in the same order.

        The same reach the projector walks (graph_projection/projector.py,
        "Reachable shared knowledge_item_id set"), five paths:
        1. a selection row for the item, selected or not;
        2. an evidence link to the item;
        3. a human-selected role match for the employee, on a role still in
           scope (role_in_scope: plan item 97, Q2);
        4. a proposal claim naming the employee;
        5. for an employee, holding a certification the bid reaches by 1 to 4
           (the projector pulls a reached certificate's holder in for HOLDS).
        An item of a type the projector has no node for reaches no bid.
        tests/integration/test_projection_reach.py ties this to the projector.
        """
        item = await self._session.get(KnowledgeItem, knowledge_item_id)
        if item is None or item.item_type not in PROJECTED_KNOWLEDGE_ITEM_TYPES:
            return []
        tender_ids = await self._tender_ids_reaching({knowledge_item_id})
        if item.item_type == "employee":
            held = await self._session.execute(
                select(Certification.knowledge_item_id).where(
                    Certification.holder_employee_item_id == knowledge_item_id
                )
            )
            tender_ids |= await self._tender_ids_reaching(set(held.scalars().all()))
        return sorted(tender_ids)

    async def _tender_ids_reaching(self, knowledge_item_ids: set[uuid.UUID]) -> set[uuid.UUID]:
        """Paths 1 to 4 of list_tender_ids_projecting, for a set of items."""
        if not knowledge_item_ids:
            return set()
        ids = knowledge_item_ids
        statement = union(
            select(TenderKnowledgeSelection.tender_id).where(
                TenderKnowledgeSelection.knowledge_item_id.in_(ids)
            ),
            select(EvidenceLink.tender_id).where(EvidenceLink.knowledge_item_id.in_(ids)),
            select(RoleRequirement.tender_id)
            .join(RoleMatch, RoleMatch.role_requirement_id == RoleRequirement.id)
            .where(
                RoleMatch.employee_knowledge_item_id.in_(ids),
                RoleMatch.human_selected.is_(True),
                role_in_scope(),
            ),
            select(ProposalSection.tender_id)
            .join(ProposalClaim, ProposalClaim.section_id == ProposalSection.id)
            .join(
                ProposalClaimEmployee, ProposalClaimEmployee.proposal_claim_id == ProposalClaim.id
            )
            .where(ProposalClaimEmployee.employee_knowledge_item_id.in_(ids)),
        )
        result = await self._session.execute(statement)
        return set(result.scalars().all())

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
    ) -> int:
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
        # Returns how many pairs were new (plan item 80: a resume merge's
        # audit row counts the chunks it added); a pair already held is not
        # counted.
        if not chunk_ids:
            return 0
        stmt = (
            pg_insert(KnowledgeItemChunk)
            .values(
                [
                    {"knowledge_item_id": knowledge_item_id, "chunk_id": chunk_id}
                    for chunk_id in chunk_ids
                ]
            )
            .on_conflict_do_nothing()
            .returning(KnowledgeItemChunk.chunk_id)
        )
        inserted = (await self._session.execute(stmt)).scalars().all()
        await self._session.flush()
        return len(inserted)

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

    async def get_employee_for_update(self, knowledge_item_id: uuid.UUID) -> Employee | None:
        """The employee, its row locked until this transaction ends (plan item
        80). A resume merge, by approval or by import_resume's automatic
        branch, reads the employee this way before comparing and writing, so
        two merges, or a merge and a verify or profile edit, do not compare
        against values the other is about to change. populate_existing: the
        duplicate candidates query has usually loaded this row already.

        Since plan item 59 (PR 20) every employee write that compares first
        reads this way: the profile edit, verify, and the employee-master
        import's DUPLICATE branch too. Lock order: the employee, then its
        knowledge item where a path takes both, then the bids' tenders.

        FOR NO KEY UPDATE since PR 20 (FOR UPDATE in PR 18): the lock the
        UPDATE that follows takes, as every other row lock of item 59. No
        foreign key references employees and nothing deletes one, so for
        inserts it changes nothing; it keeps one mode on the row. Two holders
        still wait for each other."""
        result = await self._session.execute(
            select(Employee)
            .where(Employee.knowledge_item_id == knowledge_item_id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

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
            select(Employee)
            .join(KnowledgeItem, KnowledgeItem.id == Employee.knowledge_item_id)
            .where(
                Employee.organization_id == organization_id,
                Employee.eligible_for_bid.is_(True),
                Employee.profile_review_status == "verified",
                KnowledgeItem.status != KNOWLEDGE_ITEM_ARCHIVED,
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
        asks the system to show gaps, and profile facts that are stale or
        unknown, explicitly, not silently exclude them, and B8A's own test
        wording (a stale profile is either visibly warned about or excluded
        by policy)
        treats warning as an acceptable alternative to exclusion. Only an
        archived employee (removed from the roster, PRD 10.9's "not
        archived") and a 'rejected' profile (explicitly disqualified) are
        hard-excluded here - talent_service.py's gap-enforcement step is
        what turns 'needs_review'/'stale' into a visible warning for
        anything that does pass through.

        Archived is read from the knowledge item's status, the column the
        import and the talent library write (KNOWLEDGE_ITEM_ARCHIVED above).
        It used to be read from profile_review_status, which nothing sets
        to 'archived', so a removed employee who had been verified stayed in
        the pool (plan item 79, C1).
        """
        result = await self._session.execute(
            select(Employee)
            .join(KnowledgeItem, KnowledgeItem.id == Employee.knowledge_item_id)
            .where(
                Employee.organization_id == organization_id,
                Employee.eligible_for_bid.is_(True),
                Employee.profile_review_status != "rejected",
                KnowledgeItem.status != KNOWLEDGE_ITEM_ARCHIVED,
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
