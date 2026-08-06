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
}
