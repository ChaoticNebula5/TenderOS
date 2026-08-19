from langgraph.graph import END

from tenderos_backend.orchestration.state import TenderState



# Both a "reject" and a "request_changes" decision must stop the run from
# advancing - they differ in what the fine-grained status/coarse read says
# happened (rejected -> failed; changes_requested -> waiting_for_human), not
# in whether the graph proceeds. Every route_after_*_gate function below
# checks against this pair rather than "rejected" alone.
_GATE_HALT_STATUSES = frozenset({"rejected", "changes_requested"})


def route_after_requirement_gate(state: TenderState) -> str:
    if state["status"] in _GATE_HALT_STATUSES:
        return END
    return "specialist_dispatch"


def route_after_compliance_gate(state: TenderState) -> str:
    # compliance_gate sits after specialist_join, before resource_selection_
    # gate (see compliance_gate_node's own docstring for why it's positioned
    # there and not nested inside compliance_analysis's Send()'d branch).
    if state["status"] in _GATE_HALT_STATUSES:
        return END
    return "resource_selection_gate"


def route_after_resource_selection_gate(state: TenderState) -> str:
    # Same sequential-slot reasoning as compliance_gate above.
    if state["status"] in _GATE_HALT_STATUSES:
        return END
    return "commercial_approval_gate"


def route_after_commercial_approval_gate(state: TenderState) -> str:
    # Same sequential-slot reasoning as the two gates above - the last of
    # the three post-join gates. Routes into proposal_drafting (B9) rather
    # than straight to final_gate now - the "pass-through seam" the B8 plan
    # flagged in advance.
    if state["status"] in _GATE_HALT_STATUSES:
        return END
    return "proposal_drafting"


def route_after_specialist_join(state: TenderState) -> str:
    # B11: specialist_join fires both on the first pass (after all 3
    # specialists Send()) AND on a revision pass where targeted_replan
    # dispatched compliance_analysis - the ONLY corrective task_type that
    # goes through specialist_dispatch at all (a proposal_revision-only
    # revision routes straight to proposal_drafting and never reaches this
    # node - see route_after_targeted_replan). revision_count > 0 is
    # therefore an unambiguous signal that this is a revision pass: skip
    # the three post-join gates entirely (they already ran their course on
    # the first pass; re-opening them here isn't in B11's build list and
    # would re-litigate decisions a human already approved) and go straight
    # to proposal_drafting, same as those three gates' own eventual exit.
    if state.get("revision_count", 0) > 0:
        return "proposal_drafting"
    return "compliance_gate"


def route_after_red_team(state: TenderState) -> str:
    # The review_router from R2's diagram - routing logic, not a node (see
    # the plan's Q1 answer). Three independent checks, in order:
    #   1. Nothing open at all -> done, no gate needed.
    #   2. Budget exhausted (B2.4's own stop condition) -> force to a human.
    #   3. A revision was just attempted and produced the exact same open
    #      critical-finding set as before it ran (B2.4's "no measurable
    #      progress" stop condition, distinct from the budget check) ->
    #      also force to a human, regardless of remaining budget. Findings
    #      are upserted by content_fingerprint with a stable id per
    #      fingerprint, so comparing open_critical_finding_ids sets IS
    #      comparing fingerprint sets - no second field needed.
    # Auto-correctability (whether anything open can even be automatically
    # fixed) is NOT checked here - that's targeted_replan_node's own job,
    # since it already needs map_finding_to_corrective_task to act.
    open_critical = state.get("open_critical_finding_ids") or []
    if not open_critical:
        return "final_gate"
    if state["revision_count"] >= state["max_revisions"]:
        return "finding_gate"
    if state["revision_count"] > 0 and set(open_critical) == set(
        state.get("pre_revision_finding_ids") or []
    ):
        return "finding_gate"
    return "targeted_replan"


def route_after_targeted_replan(state: TenderState) -> str:
    # targeted_replan_node sets revision_dispatch_target explicitly rather
    # than this function inferring it from dispatch_task_ids' emptiness,
    # which is ambiguous between "nothing was auto-correctable" and "only a
    # proposal redraft is needed" (a state that legitimately has an empty
    # dispatch_task_ids too, since proposal_drafting isn't Send()-dispatched).
    target = state.get("revision_dispatch_target", "none")
    if target == "specialist_dispatch":
        return "specialist_dispatch"
    if target == "proposal_drafting":
        return "proposal_drafting"
    return "finding_gate"


def route_after_finding_gate(state: TenderState) -> str:
    # Zero edges back into the loop under any decision value (Q5) - reject
    # or request_changes both end the run, same as every other gate;
    # approve proceeds to final_gate. This one-branch-forward shape is what
    # makes the revision loop provably bounded independent of
    # route_after_red_team's own checks.
    if state["status"] in _GATE_HALT_STATUSES:
        return END
    return "final_gate"
