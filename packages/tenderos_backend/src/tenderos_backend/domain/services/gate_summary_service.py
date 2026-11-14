"""The counts a gate's panel shows, computed one way for both of its uses.

Plan item 73, decision 3 (user, 2026-11-13). A gate's summary used to be
computed only inside its node, once, when the gate opened, and stored in the
gate's request_payload. The panel showed that snapshot beside tables that had
moved on ("Pending approval 2" next to two approved rows). Now:

- the gate nodes call these functions to write the snapshot, as before;
- the gates API calls the same functions, through live_summary, for a
  pending gate, so the panel can show the counts as they are now, with the
  snapshot under them as "When this gate opened".

One function per gate type, so a snapshot and a live reading of the same
data can never disagree because two copies of a count drifted.

The final readiness gate has no live summary: its counts are a readiness
report's, and the Readiness page shows the latest report itself.

Lives in domain, not orchestration: the API calls it, and the dependency
direction is orchestration -> domain services, never the reverse.
"""

import uuid

from tenderos_backend.core.config import settings
from tenderos_backend.db.models.workflow import ApprovalGate
from tenderos_backend.db.unit_of_work import UnitOfWork

# "High risk" means a status PRD 8.4 would never let stand unapproved
# without evidence: not a separate ML-scored risk tier, just the two statuses
# that most need a human's eyes before the tender proceeds (moved here from
# compliance_gate_node with its count, plan item 73).
_HIGH_RISK_COMPLIANCE = ("evidence_unavailable", "non_compliant")
# The finding gate counts open findings of these severities.
FINDING_GATE_SEVERITIES = ("critical", "major")


async def requirement_review_summary(uow: UnitOfWork, tender_id: uuid.UUID) -> dict:
    requirements = await uow.requirements.list_for_tender(tender_id)
    submission_items = await uow.requirements.list_submission_items_for_tender(tender_id)
    clarifications = await uow.requirements.list_clarification_items_for_tender(tender_id)
    mandatory = [r for r in requirements if r.mandatory]
    return {
        "requirement_count": len(requirements),
        "mandatory_count": len(mandatory),
        "unverified_mandatory_count": len([r for r in mandatory if not r.verified]),
        "submission_item_count": len(submission_items),
        "clarification_count": len(clarifications),
    }


async def compliance_review_summary(uow: UnitOfWork, tender_id: uuid.UUID) -> dict:
    entries = await uow.compliance.list_compliance_entries_for_tender(tender_id)
    pending = [e for e in entries if not e.human_approved]
    return {
        "compliance_entry_count": len(entries),
        "pending_count": len(pending),
        "high_risk_count": len([e for e in pending if e.status in _HIGH_RISK_COMPLIANCE]),
    }


async def resource_selection_summary(uow: UnitOfWork, tender_id: uuid.UUID) -> dict:
    """`candidate_count` and `unselected_count` count (role, candidate)
    matches, not people or roles: one role scored against four candidates is
    four. That is what made the panel read "Unselected roles 4" for one role
    (item 79's C3). `unselected_role_count` is what the panel now shows: the
    roles with no human-selected candidate. The two match counts stay in the
    payload, so every snapshot keeps the same keys."""
    roles = await uow.talent.list_role_requirements_for_tender(tender_id)
    matches_by_role = [await uow.talent.list_role_matches_for_role(role.id) for role in roles]
    all_matches = [m for matches in matches_by_role for m in matches]
    return {
        "role_count": len(roles),
        "candidate_count": len(all_matches),
        "unselected_count": len([m for m in all_matches if not m.human_selected]),
        "unselected_role_count": len(
            [matches for matches in matches_by_role if not any(m.human_selected for m in matches)]
        ),
    }


async def commercial_approval_summary(uow: UnitOfWork, tender_id: uuid.UUID) -> dict:
    findings = await uow.commercial.list_pricing_findings_for_tender(tender_id)
    pending = [f for f in findings if not f.resolved]
    return {
        "finding_count": len(findings),
        "pending_count": len(pending),
        "critical_count": len([f for f in pending if f.severity == "critical"]),
    }


async def finding_resolution_summary(uow: UnitOfWork, tender_id: uuid.UUID) -> dict:
    findings = await uow.redteam.list_open_for_tender(tender_id, severities=FINDING_GATE_SEVERITIES)
    return {
        "open_finding_count": len(findings),
        "critical_count": len([f for f in findings if f.severity == "critical"]),
    }


async def cost_budget_summary(uow: UnitOfWork, workflow_run_id: uuid.UUID) -> dict:
    # float, not Decimal: settings.max_tender_cost_usd is a float, and a
    # Decimal cannot be subtracted from one (cost_budget.py's own note).
    accumulated = float(await uow.agent_traces.sum_cost_for_run(workflow_run_id))
    return {
        "accumulated_cost_usd": accumulated,
        "budget_usd": settings.max_tender_cost_usd,
        "overage_usd": accumulated - settings.max_tender_cost_usd,
    }


async def live_summary(gate: ApprovalGate) -> dict | None:
    """The gate's counts as they are now, or None: for a decided gate (its
    snapshot is the record of what was decided on) and for the final
    readiness gate (see the module docstring)."""
    if gate.status != "pending":
        return None
    async with UnitOfWork() as uow:
        if gate.gate_type == "requirement_review":
            return await requirement_review_summary(uow, gate.tender_id)
        if gate.gate_type == "compliance_review":
            return await compliance_review_summary(uow, gate.tender_id)
        if gate.gate_type == "resource_selection":
            return await resource_selection_summary(uow, gate.tender_id)
        if gate.gate_type == "commercial_approval":
            return await commercial_approval_summary(uow, gate.tender_id)
        if gate.gate_type == "finding_resolution":
            return await finding_resolution_summary(uow, gate.tender_id)
        if gate.gate_type == "cost_budget":
            return await cost_budget_summary(uow, gate.workflow_run_id)
    return None
