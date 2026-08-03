from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession

from tenderos_backend.db.repositories.audit import AuditRepository
from tenderos_backend.db.repositories.idempotency import IdempotencyRepository
from tenderos_backend.db.repositories.memberships import MembershipRepository
from tenderos_backend.db.repositories.organizations import OrganizationRepository
from tenderos_backend.db.repositories.outbox import OutboxRepository
from tenderos_backend.db.repositories.tenders import TenderRepository
from tenderos_backend.db.repositories.users import UserRepository
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

    async def __aenter__(self) -> "UnitOfWork":
        self.session = await self._session_cm.__aenter__()
        self.organizations = OrganizationRepository(self.session)
        self.users = UserRepository(self.session)
        self.memberships = MembershipRepository(self.session)
        self.tenders = TenderRepository(self.session)
        self.outbox = OutboxRepository(self.session)
        self.idempotency = IdempotencyRepository(self.session)
        self.audit = AuditRepository(self.session)
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
