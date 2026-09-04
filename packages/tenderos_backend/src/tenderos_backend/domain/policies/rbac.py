from tenderos_backend.core.enums import Role

# Transcribed verbatim from Build Guide Part H (RBAC Matrix). Only "create_bid"
# and "manage_users_roles" have real callers in B2 (tender creation, membership
# creation); the rest are here now because the table itself is fully locked and
# small - later blocks reference these same keys rather than re-transcribing.
ACTION_ROLES: dict[str, frozenset[Role]] = {
    "create_bid": frozenset({Role.BID_MANAGER, Role.ADMIN}),
    "upload_client_docs": frozenset(
        {
            Role.BID_MANAGER,
            Role.SOLUTION_ARCHITECT,
            Role.COMMERCIAL_REVIEWER,
            Role.PROPOSAL_WRITER,
            Role.ADMIN,
        }
    ),
    "review_requirements": frozenset(
        {Role.BID_MANAGER, Role.SOLUTION_ARCHITECT, Role.PROPOSAL_WRITER, Role.ADMIN}
    ),
    "approve_compliance": frozenset(
        {Role.BID_MANAGER, Role.SOLUTION_ARCHITECT, Role.APPROVER, Role.ADMIN}
    ),
    "select_resource": frozenset(
        {Role.BID_MANAGER, Role.TALENT_REVIEWER, Role.APPROVER, Role.ADMIN}
    ),
    "resolve_commercial_finding": frozenset(
        {Role.BID_MANAGER, Role.COMMERCIAL_REVIEWER, Role.APPROVER, Role.ADMIN}
    ),
    "edit_proposal": frozenset(
        {Role.BID_MANAGER, Role.SOLUTION_ARCHITECT, Role.PROPOSAL_WRITER, Role.ADMIN}
    ),
    "resolve_redteam_finding": frozenset(
        {
            Role.BID_MANAGER,
            Role.SOLUTION_ARCHITECT,
            Role.TALENT_REVIEWER,
            Role.COMMERCIAL_REVIEWER,
            Role.PROPOSAL_WRITER,
            Role.APPROVER,
            Role.ADMIN,
        }
    ),
    "final_readiness_approval": frozenset({Role.APPROVER, Role.ADMIN}),
    "manage_organization_knowledge": frozenset(
        {Role.BID_MANAGER, Role.SOLUTION_ARCHITECT, Role.TALENT_REVIEWER, Role.ADMIN}
    ),
    "manage_users_roles": frozenset({Role.ADMIN}),
    # Not in Part H's matrix - added in B3 alongside POST /workflow-runs/{id}/cancel
    # (G3 lists the route, I6 defines cancellation semantics, but no RBAC row
    # names who may call it). Mirrors "Create bid"'s bid_manager+admin row with
    # approver added, since approver already sits above bid_manager in every
    # other gate-adjacent row (compliance, resource, commercial, final readiness).
    "cancel_workflow_run": frozenset({Role.BID_MANAGER, Role.APPROVER, Role.ADMIN}),
    # B10 (G12): Part H's matrix has no "Graph" row at all. view_graph covers
    # the 4 read/compute routes (status, impact, contradictions, trace) -
    # full 7-role breadth, same as resolve_redteam_finding (the widest
    # existing action), justified by PRD 27.13's own invariant ("Traceability
    # must be accessible from normal workflows, not hidden in an admin-only
    # screen") and by impact/contradiction analysis being inherently
    # cross-cutting across compliance/talent/proposal, not owned by one
    # specialist role. trigger_graph_projection (the manual POST .../
    # graph/project trigger) is narrower - same set as edit_proposal, since
    # forcing a recompute is closer to a content-management action than
    # passive viewing.
    "view_graph": frozenset(
        {
            Role.BID_MANAGER,
            Role.SOLUTION_ARCHITECT,
            Role.TALENT_REVIEWER,
            Role.COMMERCIAL_REVIEWER,
            Role.PROPOSAL_WRITER,
            Role.APPROVER,
            Role.ADMIN,
        }
    ),
    "trigger_graph_projection": frozenset(
        {Role.BID_MANAGER, Role.SOLUTION_ARCHITECT, Role.PROPOSAL_WRITER, Role.ADMIN}
    ),
    # B12 (G11): Part H's matrix has "Final readiness approval" (the GATE
    # decision, APPROVER/ADMIN-only) but no row for reading or generating
    # the readiness REPORT itself - the same gap B10 found for graph reads,
    # resolved the same way. view_readiness mirrors view_graph's full
    # 7-role breadth: readiness blockers span compliance/talent/commercial/
    # red-team, so it's inherently cross-cutting like graph impact, and
    # PRD §10.13's own transparency requirement ("explain why the bid is
    # not ready") argues against restricting it to approvers only - a bid
    # manager needs to see the SAME blocker list before ever reaching the
    # final gate, not just the approver deciding it. generate_readiness
    # (the manual POST .../readiness/generate trigger, mirroring B10/B11's
    # own manual-trigger precedent) is narrower, same base set as trigger_
    # graph_projection - forcing a recompute, not passive viewing - PLUS
    # Role.APPROVER specifically (a real gap trigger_graph_projection
    # itself doesn't have to worry about): an approver sitting on a
    # final_readiness gate that decide_gate is refusing to let them waive
    # past (a stale or not-ready report) needs to be able to regenerate it
    # themselves rather than being blocked on someone else with a narrower
    # role doing it for them.
    "view_readiness": frozenset(
        {
            Role.BID_MANAGER,
            Role.SOLUTION_ARCHITECT,
            Role.TALENT_REVIEWER,
            Role.COMMERCIAL_REVIEWER,
            Role.PROPOSAL_WRITER,
            Role.APPROVER,
            Role.ADMIN,
        }
    ),
    "generate_readiness": frozenset(
        {Role.BID_MANAGER, Role.SOLUTION_ARCHITECT, Role.PROPOSAL_WRITER, Role.APPROVER, Role.ADMIN}
    ),
    # B13 (G3): GET /workflow-runs/{id}, .../tasks, .../events, and the new
    # GET /tenders/{id}/workflow-runs had all been gated on create_bid
    # (Bid manager/Admin only) since B3 - wrong, not merely inconsistent:
    # create_bid is an authoring action, and gating a READ of run status
    # behind it hides that status from Architect/Talent reviewer/
    # Commercial reviewer/Proposal writer/Approver - five roles actively
    # working the tender - which sits directly against B13's own "no
    # hidden auto-approval" functional requirement (a human can't watch
    # for a problem in a process they can't see). Retrofitted to full
    # 7-role breadth, matching view_graph/view_readiness's own precedent,
    # rather than adding this one action correctly while leaving the
    # three existing routes on the old one - the same asymmetry the
    # set_knowledge_selection/set_candidate_selection cross-tenant fix
    # rejected in the opposite direction (a fix applied to one call site
    # and assumed to cover its sibling). Cheap to fix now; expensive once
    # B14's SSE subscription lands on list_events and inherits whichever
    # RBAC shape this action still has then.
    #
    # This is the FOURTH instance of the same shape - view_graph (B10),
    # view_readiness (B12), view_workflow (B13), and view_gates (B13,
    # immediately below - found in the very same pass as this one, in a
    # module this fix hadn't even touched yet) - which makes it a
    # pattern worth naming, not unrelated coincidences: Part H's matrix
    # was written around eleven AUTHORING actions (create, upload,
    # approve, select, resolve, edit, manage) and has never once named a
    # read action. Every read-only surface built since B10 has had to
    # invent one from scratch, each time discovered only when a block
    # happened to look. No later block should assume a route it's
    # building has legitimate RBAC just because it reuses an
    # already-named action from Part H - checking whether that action's
    # own *shape* (authoring vs. reading) actually matches what the new
    # route does is what view_graph/view_readiness/view_workflow/
    # view_gates all eventually had to do the hard way.
    "view_workflow": frozenset(
        {
            Role.BID_MANAGER,
            Role.SOLUTION_ARCHITECT,
            Role.TALENT_REVIEWER,
            Role.COMMERCIAL_REVIEWER,
            Role.PROPOSAL_WRITER,
            Role.APPROVER,
            Role.ADMIN,
        }
    ),
    # B13 (gates.py): GET /workflow-runs/{id}/gates and GET /gates/{id}
    # were both gated on create_bid too - the fourth instance of the
    # exact shape documented above, found while typing GateResponse for
    # the shared gate-control component, not while looking for RBAC bugs.
    # Deciding a gate (POST .../decision) already uses a different,
    # correctly narrow dependency - require_gate_decision_permission,
    # which dispatches per gate_type via orchestration/gates.py's own
    # GATE_TYPE_ACTIONS - so only the two reads needed this action, not
    # the decision route too.
    "view_gates": frozenset(
        {
            Role.BID_MANAGER,
            Role.SOLUTION_ARCHITECT,
            Role.TALENT_REVIEWER,
            Role.COMMERCIAL_REVIEWER,
            Role.PROPOSAL_WRITER,
            Role.APPROVER,
            Role.ADMIN,
        }
    ),
    # B13 (§7 step 10, item e of the B13 plan): GET /proposal/claims/{id}/
    # trace was gated on edit_proposal (4 roles); GET /compliance/{id}/
    # trace was gated on approve_compliance (4 roles, a different 4 -
    # Approver instead of Proposal writer). Both are the fifth and sixth
    # instances of the same shape named at view_workflow's own comment
    # above (a read gated on a narrow authoring action) - found this time
    # not by inspection but because Screen 15 (Readiness) makes it
    # concrete: the Approver deciding final_gate has no way to open the
    # drawer explaining why a claim/compliance finding is unsupported, and
    # resolve_redteam_finding (already 7-role) can target either a claim
    # or a compliance entry (redteam_findings.target_type is free-form
    # TEXT, no CHECK constraint - confirmed against Part C), so any of the
    # seven roles resolving a finding may need to inspect what it targets,
    # not just Approver. Both widened to the full 7-role breadth,
    # matching resolve_redteam_finding's own precedent for "any role might
    # need this, don't restrict by specialism." Every other G7/G10 route
    # (list/patch/approve/generate) stays on its existing authoring
    # action, unchanged - only these two read paths move.
    "view_proposal_trace": frozenset(
        {
            Role.BID_MANAGER,
            Role.SOLUTION_ARCHITECT,
            Role.TALENT_REVIEWER,
            Role.COMMERCIAL_REVIEWER,
            Role.PROPOSAL_WRITER,
            Role.APPROVER,
            Role.ADMIN,
        }
    ),
    "view_compliance_trace": frozenset(
        {
            Role.BID_MANAGER,
            Role.SOLUTION_ARCHITECT,
            Role.TALENT_REVIEWER,
            Role.COMMERCIAL_REVIEWER,
            Role.PROPOSAL_WRITER,
            Role.APPROVER,
            Role.ADMIN,
        }
    ),
    # I5 (Build Guide :2755-2763): "require bid-manager approval to
    # continue." Not in Part H's matrix at all - I5 postdates it, same gap
    # shape as cancel_workflow_run above. Approver added alongside
    # bid_manager, not bid_manager+admin alone - matching every other
    # gate-adjacent row in this table (approve_compliance, select_resource,
    # resolve_commercial_finding, final_readiness_approval all include
    # Approver), the same reasoning cancel_workflow_run's own comment
    # already gives: Approver sits above Bid manager in every other
    # gate-adjacent row here, not narrower.
    "approve_cost_budget": frozenset({Role.BID_MANAGER, Role.APPROVER, Role.ADMIN}),
}
