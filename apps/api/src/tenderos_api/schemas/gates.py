from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class GateDecisionRequest(BaseModel):
    decision: str
    # R5's example payload includes target_updates, but B3's stub gates have
    # no real target content to apply updates to (targets=[] in every gate
    # opened this block) - accepted for contract completeness, not yet acted
    # on. Real per-target application arrives with the blocks that give gate
    # targets real content (B7+).
    target_updates: list[dict] = []
    notes: str | None = None


class GateResponse(BaseModel):
    gate_id: UUID
    gate_type: str
    workflow_run_id: UUID
    status: str
    targets: list[dict]
    summary: dict
    allowed_decisions: list[str]
    decision_payload: dict | None
    decided_by: UUID | None
    requested_at: datetime
    decided_at: datetime | None
