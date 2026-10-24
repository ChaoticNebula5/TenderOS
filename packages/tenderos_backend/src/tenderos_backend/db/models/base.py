import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    # Every migration declares string columns as Text, so a bare Mapped[str]
    # is TEXT here too, not SQLAlchemy's default VARCHAR. A column that
    # names its own type (String(n), JSONB, ...) is unaffected.
    type_annotation_map = {str: Text}


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
