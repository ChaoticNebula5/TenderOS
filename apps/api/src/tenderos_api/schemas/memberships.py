from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr
from tenderos_backend.core.enums import Role


class AddMembershipRequest(BaseModel):
    email: EmailStr
    role: Role


class MembershipResponse(BaseModel):
    id: UUID
    organization_id: UUID
    user_id: UUID
    role: str
    created_at: datetime

    model_config = {"from_attributes": True}
