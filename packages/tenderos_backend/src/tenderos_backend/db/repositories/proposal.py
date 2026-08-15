import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tenderos_backend.db.models.proposal import (
    ProposalClaim,
    ProposalClaimEmployee,
    ProposalClaimEvidence,
    ProposalClaimRequirement,
    ProposalSection,
)


class ProposalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # proposal_sections
    # ------------------------------------------------------------------

    async def create_proposal_section(
        self,
        tender_id: uuid.UUID,
        section_name: str,
        content: str = "",
        version: int = 1,
        status: str = "draft",
    ) -> ProposalSection:
        section = ProposalSection(
            tender_id=tender_id,
            section_name=section_name,
            content=content,
            version=version,
            status=status,
        )
        self._session.add(section)
        await self._session.flush()
        return section

    async def get_proposal_section(self, section_id: uuid.UUID) -> ProposalSection | None:
        return await self._session.get(ProposalSection, section_id)

    async def get_latest_proposal_section(
        self, tender_id: uuid.UUID, section_name: str
    ) -> ProposalSection | None:
        # "Latest" here means highest version for this (tender, section_name)
        # pair - the row draft_proposal_for_tender checks to decide whether a
        # re-draft reuses it in place (status still 'draft') or must create a
        # new version instead (status already 'review'/'approved' - see the
        # versioning policy in proposal_service.py's own module docstring).
        result = await self._session.execute(
            select(ProposalSection)
            .where(
                ProposalSection.tender_id == tender_id,
                ProposalSection.section_name == section_name,
            )
            .order_by(ProposalSection.version.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_proposal_sections_for_tender(
        self, tender_id: uuid.UUID
    ) -> list[ProposalSection]:
        result = await self._session.execute(
            select(ProposalSection).where(ProposalSection.tender_id == tender_id)
        )
        return list(result.scalars().all())

    async def update_proposal_section(
        self, section_id: uuid.UUID, **fields: object
    ) -> ProposalSection:
        section = await self._session.get(ProposalSection, section_id)
        if section is None:
            raise ValueError(f"ProposalSection {section_id} not found")
        for key, value in fields.items():
            setattr(section, key, value)
        await self._session.flush()
        return section

    # ------------------------------------------------------------------
    # proposal_claims
    # ------------------------------------------------------------------

    async def create_proposal_claim(
        self,
        section_id: uuid.UUID,
        claim_text: str,
        claim_type: str,
        is_factual: bool,
        grounding_status: str,
        content_fingerprint: str,
    ) -> ProposalClaim:
        claim = ProposalClaim(
            section_id=section_id,
            claim_text=claim_text,
            claim_type=claim_type,
            is_factual=is_factual,
            grounding_status=grounding_status,
            content_fingerprint=content_fingerprint,
        )
        self._session.add(claim)
        await self._session.flush()
        return claim

    async def get_proposal_claim(self, claim_id: uuid.UUID) -> ProposalClaim | None:
        return await self._session.get(ProposalClaim, claim_id)

    async def get_proposal_claim_by_fingerprint(
        self, section_id: uuid.UUID, content_fingerprint: str
    ) -> ProposalClaim | None:
        # proposal_claims' own UNIQUE(section_id, content_fingerprint) (Part
        # C) - the lookup that makes re-drafting a section idempotent: a
        # retry that reproduces the exact same claim text reuses this row
        # rather than raising IntegrityError or accumulating duplicates
        # (the stop-gate's own "retry does not duplicate claims" test).
        result = await self._session.execute(
            select(ProposalClaim).where(
                ProposalClaim.section_id == section_id,
                ProposalClaim.content_fingerprint == content_fingerprint,
            )
        )
        return result.scalar_one_or_none()

    async def list_proposal_claims_for_section(
        self, section_id: uuid.UUID
    ) -> list[ProposalClaim]:
        result = await self._session.execute(
            select(ProposalClaim).where(ProposalClaim.section_id == section_id)
        )
        return list(result.scalars().all())

    async def update_proposal_claim(self, claim_id: uuid.UUID, **fields: object) -> ProposalClaim:
        claim = await self._session.get(ProposalClaim, claim_id)
        if claim is None:
            raise ValueError(f"ProposalClaim {claim_id} not found")
        for key, value in fields.items():
            setattr(claim, key, value)
        await self._session.flush()
        return claim

    # ------------------------------------------------------------------
    # proposal_claim_evidence / _requirements / _employees (junctions)
    # ------------------------------------------------------------------

    async def link_claim_evidence(
        self, proposal_claim_id: uuid.UUID, evidence_link_id: uuid.UUID
    ) -> None:
        stmt = (
            pg_insert(ProposalClaimEvidence)
            .values(proposal_claim_id=proposal_claim_id, evidence_link_id=evidence_link_id)
            .on_conflict_do_nothing(constraint="uq_proposal_claim_evidence")
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def list_evidence_for_claim(
        self, proposal_claim_id: uuid.UUID
    ) -> list[ProposalClaimEvidence]:
        result = await self._session.execute(
            select(ProposalClaimEvidence).where(
                ProposalClaimEvidence.proposal_claim_id == proposal_claim_id
            )
        )
        return list(result.scalars().all())

    async def link_claim_requirement(
        self, proposal_claim_id: uuid.UUID, requirement_id: uuid.UUID
    ) -> None:
        stmt = (
            pg_insert(ProposalClaimRequirement)
            .values(proposal_claim_id=proposal_claim_id, requirement_id=requirement_id)
            .on_conflict_do_nothing(constraint="uq_proposal_claim_requirement")
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def list_requirements_for_claim(
        self, proposal_claim_id: uuid.UUID
    ) -> list[ProposalClaimRequirement]:
        result = await self._session.execute(
            select(ProposalClaimRequirement).where(
                ProposalClaimRequirement.proposal_claim_id == proposal_claim_id
            )
        )
        return list(result.scalars().all())

    async def link_claim_employee(
        self, proposal_claim_id: uuid.UUID, employee_knowledge_item_id: uuid.UUID
    ) -> None:
        stmt = (
            pg_insert(ProposalClaimEmployee)
            .values(
                proposal_claim_id=proposal_claim_id,
                employee_knowledge_item_id=employee_knowledge_item_id,
            )
            .on_conflict_do_nothing(constraint="uq_proposal_claim_employee")
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def list_employees_for_claim(
        self, proposal_claim_id: uuid.UUID
    ) -> list[ProposalClaimEmployee]:
        result = await self._session.execute(
            select(ProposalClaimEmployee).where(
                ProposalClaimEmployee.proposal_claim_id == proposal_claim_id
            )
        )
        return list(result.scalars().all())
