import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tenderos_backend.db.models.talent import (
    RoleMatch,
    RoleMatchEvidence,
    RoleRequirement,
    RoleRequirementSource,
)


class TalentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # role_requirements
    # ------------------------------------------------------------------

    async def create_role_requirement(
        self,
        tender_id: uuid.UUID,
        role_name: str,
        content_fingerprint: str,
        min_experience_years: float | None = None,
        required_skills: list | None = None,
        required_certs: list | None = None,
        source_chunk_id: uuid.UUID | None = None,
    ) -> RoleRequirement:
        role = RoleRequirement(
            tender_id=tender_id,
            role_name=role_name,
            min_experience_years=min_experience_years,
            required_skills=required_skills or [],
            required_certs=required_certs or [],
            source_chunk_id=source_chunk_id,
            content_fingerprint=content_fingerprint,
        )
        self._session.add(role)
        await self._session.flush()
        return role

    async def get_role_requirement(self, role_requirement_id: uuid.UUID) -> RoleRequirement | None:
        return await self._session.get(RoleRequirement, role_requirement_id)

    async def get_role_requirement_by_fingerprint(
        self, tender_id: uuid.UUID, content_fingerprint: str
    ) -> RoleRequirement | None:
        result = await self._session.execute(
            select(RoleRequirement).where(
                RoleRequirement.tender_id == tender_id,
                RoleRequirement.content_fingerprint == content_fingerprint,
            )
        )
        return result.scalar_one_or_none()

    async def list_role_requirements_for_tender(self, tender_id: uuid.UUID) -> list[RoleRequirement]:
        result = await self._session.execute(
            select(RoleRequirement).where(RoleRequirement.tender_id == tender_id)
        )
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # role_requirement_sources (junction: role -> originating requirement)
    # ------------------------------------------------------------------

    async def link_role_requirement_source(
        self, role_requirement_id: uuid.UUID, requirement_id: uuid.UUID
    ) -> None:
        # Same ON CONFLICT DO NOTHING idempotency shape as
        # ComplianceRepository.link_evidence - re-extracting roles for a
        # tender that's already had this exact role/requirement link made
        # must not raise or duplicate.
        stmt = (
            pg_insert(RoleRequirementSource)
            .values(role_requirement_id=role_requirement_id, requirement_id=requirement_id)
            .on_conflict_do_nothing(constraint="uq_role_requirement_source")
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def list_sources_for_role(
        self, role_requirement_id: uuid.UUID
    ) -> list[RoleRequirementSource]:
        # B10: the graph projector's SATISFIES edge (Role -> Requirement)
        # source - the only reader of this junction until now was the write
        # side (link_role_requirement_source).
        result = await self._session.execute(
            select(RoleRequirementSource).where(
                RoleRequirementSource.role_requirement_id == role_requirement_id
            )
        )
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # role_matches
    # ------------------------------------------------------------------

    async def create_role_match(
        self,
        role_requirement_id: uuid.UUID,
        employee_knowledge_item_id: uuid.UUID,
        score: float | None = None,
        strengths: list | None = None,
        gaps: list | None = None,
    ) -> RoleMatch:
        match = RoleMatch(
            role_requirement_id=role_requirement_id,
            employee_knowledge_item_id=employee_knowledge_item_id,
            score=score,
            strengths=strengths or [],
            gaps=gaps or [],
        )
        self._session.add(match)
        await self._session.flush()
        return match

    async def get_role_match(self, role_match_id: uuid.UUID) -> RoleMatch | None:
        return await self._session.get(RoleMatch, role_match_id)

    async def get_role_match_by_candidate(
        self, role_requirement_id: uuid.UUID, employee_knowledge_item_id: uuid.UUID
    ) -> RoleMatch | None:
        # role_matches' own UNIQUE(role_requirement_id, employee_knowledge_item_id)
        # (Part C) - this is the lookup that makes re-scoring a candidate
        # idempotent: update the existing row in place rather than raising
        # IntegrityError or accumulating duplicate scores over retries.
        result = await self._session.execute(
            select(RoleMatch).where(
                RoleMatch.role_requirement_id == role_requirement_id,
                RoleMatch.employee_knowledge_item_id == employee_knowledge_item_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_role_matches_for_role(self, role_requirement_id: uuid.UUID) -> list[RoleMatch]:
        result = await self._session.execute(
            select(RoleMatch).where(RoleMatch.role_requirement_id == role_requirement_id)
        )
        return list(result.scalars().all())

    async def update_role_match(self, role_match_id: uuid.UUID, **fields: object) -> RoleMatch:
        match = await self._session.get(RoleMatch, role_match_id)
        if match is None:
            raise ValueError(f"RoleMatch {role_match_id} not found")
        for key, value in fields.items():
            setattr(match, key, value)
        await self._session.flush()
        return match

    # ------------------------------------------------------------------
    # role_match_evidence (junction, reuses B7's evidence_links table)
    # ------------------------------------------------------------------

    async def link_role_match_evidence(
        self, role_match_id: uuid.UUID, evidence_link_id: uuid.UUID, criterion: str
    ) -> None:
        stmt = (
            pg_insert(RoleMatchEvidence)
            .values(
                role_match_id=role_match_id,
                evidence_link_id=evidence_link_id,
                criterion=criterion,
            )
            .on_conflict_do_nothing(constraint="uq_role_match_evidence")
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def list_evidence_for_role_match(self, role_match_id: uuid.UUID) -> list[RoleMatchEvidence]:
        result = await self._session.execute(
            select(RoleMatchEvidence).where(RoleMatchEvidence.role_match_id == role_match_id)
        )
        return list(result.scalars().all())
