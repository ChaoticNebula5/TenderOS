from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession

from tenderos_backend.db.repositories.agent_traces import AgentTraceRepository
from tenderos_backend.db.repositories.audit import AuditRepository
from tenderos_backend.db.repositories.commercial import CommercialRepository
from tenderos_backend.db.repositories.compliance import ComplianceRepository
from tenderos_backend.db.repositories.documents import DocumentRepository
from tenderos_backend.db.repositories.gates import GateRepository
from tenderos_backend.db.repositories.graph_projection import GraphProjectionRepository
from tenderos_backend.db.repositories.idempotency import IdempotencyRepository
from tenderos_backend.db.repositories.knowledge import KnowledgeRepository
from tenderos_backend.db.repositories.memberships import MembershipRepository
from tenderos_backend.db.repositories.organizations import OrganizationRepository
from tenderos_backend.db.repositories.outbox import OutboxRepository
from tenderos_backend.db.repositories.proposal import ProposalRepository
from tenderos_backend.db.repositories.requirements import RequirementRepository
from tenderos_backend.db.repositories.talent import TalentRepository
from tenderos_backend.db.repositories.tenders import TenderRepository
from tenderos_backend.db.repositories.users import UserRepository
from tenderos_backend.db.repositories.workflow import WorkflowRepository
from tenderos_backend.db.session import get_session


class UnitOfWork:
    def __init__(self) -> None:
        self._session_cm = get_session()
        self.session: AsyncSession
        self.organizations: OrganizationRepository
        self.users: UserRepository
        self.memberships: MembershipRepository
        self.tenders: TenderRepository
        self.outbox: OutboxRepository
        self.idempotency: IdempotencyRepository
        self.audit: AuditRepository
        self.workflow: WorkflowRepository
        self.gates: GateRepository
        self.documents: DocumentRepository
        self.graph_projection: GraphProjectionRepository
        self.requirements: RequirementRepository
        self.agent_traces: AgentTraceRepository
        self.knowledge: KnowledgeRepository
        self.compliance: ComplianceRepository
        self.talent: TalentRepository
        self.commercial: CommercialRepository
        self.proposal: ProposalRepository

    async def __aenter__(self) -> "UnitOfWork":
        self.session = await self._session_cm.__aenter__()
        self.organizations = OrganizationRepository(self.session)
        self.users = UserRepository(self.session)
        self.memberships = MembershipRepository(self.session)
        self.tenders = TenderRepository(self.session)
        self.outbox = OutboxRepository(self.session)
        self.idempotency = IdempotencyRepository(self.session)
        self.audit = AuditRepository(self.session)
        self.workflow = WorkflowRepository(self.session)
        self.gates = GateRepository(self.session)
        self.documents = DocumentRepository(self.session)
        self.graph_projection = GraphProjectionRepository(self.session)
        self.requirements = RequirementRepository(self.session)
        self.agent_traces = AgentTraceRepository(self.session)
        self.knowledge = KnowledgeRepository(self.session)
        self.compliance = ComplianceRepository(self.session)
        self.talent = TalentRepository(self.session)
        self.commercial = CommercialRepository(self.session)
        self.proposal = ProposalRepository(self.session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            await self.session.rollback()
        await self._session_cm.__aexit__(exc_type, exc, tb)

    async def commit(self) -> None:
        await self.session.commit()

    async def rollback(self) -> None:
        await self.session.rollback()
