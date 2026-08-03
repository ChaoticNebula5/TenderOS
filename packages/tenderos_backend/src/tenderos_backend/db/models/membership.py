import uuid

from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from tenderos_backend.core.enums import Role
from tenderos_backend.db.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin

_ROLE_VALUES = tuple(role.value for role in Role)


class OrganizationMembership(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "organization_memberships"
    __table_args__ = (
        CheckConstraint(f"role IN {_ROLE_VALUES}", name="ck_membership_role"),
        UniqueConstraint("organization_id", "user_id", name="uq_org_user"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(nullable=False)
