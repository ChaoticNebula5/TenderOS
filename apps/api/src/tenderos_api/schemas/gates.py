from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class TargetUpdate(BaseModel):
    # B12: real per-target application, closing the gap this field carried
    # since B3 (see KNOWN_LIMITATIONS.md's now-resolved target_updates
    # entry). target_type intentionally NOT constrained to gate_target_
    # service._KNOWN_TARGET_TYPES here - apply_target_update's own
    # permissive-on-unknown-type posture (forward-compatibility) would be
    # defeated if Pydantic rejected an unrecognized type before it ever
    # reached that function. decision is narrower (Literal["approve"])
    # because that's the only value this block's contract defines at all,
    # not a forward-compatibility case - not a security boundary either
    # way, since gate_target_service re-validates both regardless of what
    # Pydantic already let through.
    target_type: str
    target_id: UUID
    decision: Literal["approve"]


class GateDecisionRequest(BaseModel):
    decision: str
    target_updates: list[TargetUpdate] = []
    notes: str | None = None
    # B12: only meaningful for gate_type="final_readiness" with
    # decision="approve" - see routes/gates.py's decide_gate. "refuses
    # approval until resolved or explicitly waived by authorized actor"
    # (Build Guide stop-gate, verbatim) - this is the explicit act. Ignored
    # for every other gate type/decision combination, not just permitted
    # but meaningless there (no readiness concept applies).
    waive: bool = False


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
