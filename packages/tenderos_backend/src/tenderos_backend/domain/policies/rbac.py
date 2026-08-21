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
    # own manual-trigger precedent) is narrower, same set as trigger_graph_
    # projection - forcing a recompute, not passive viewing.
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
        {Role.BID_MANAGER, Role.SOLUTION_ARCHITECT, Role.PROPOSAL_WRITER, Role.ADMIN}
    ),
}
