from uuid import UUID

from pydantic import BaseModel, Field


class RoleRequirementResponse(BaseModel):
    id: UUID
    tender_id: UUID
    role_name: str
    min_experience_years: float | None
    required_skills: list = Field(default_factory=list)
    required_certs: list = Field(default_factory=list)

    model_config = {"from_attributes": True}


class RoleMatchResponse(BaseModel):
    id: UUID
    role_requirement_id: UUID
    employee_knowledge_item_id: UUID
    score: float | None
    strengths: list = Field(default_factory=list)
    gaps: list = Field(default_factory=list)
    human_selected: bool

    model_config = {"from_attributes": True}


class SelectCandidateRequest(BaseModel):
    """G8's POST /roles/{role_id}/select-candidate. `selected` defaults
    True (the common case); the same route also handles the Build Guide's
    "deselecting employee does not delete employee" test with
    selected=False, since G8 locks only one route for this action.
    """

    employee_knowledge_item_id: UUID
    selected: bool = True
