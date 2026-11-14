from tenderos_backend.core.enums import Role
from tenderos_backend.domain.policies.rbac import ACTION_ROLES
from tenderos_backend.orchestration.gates import GATE_TYPE_ACTIONS


def test_create_bid_is_bid_manager_and_admin_only():
    assert ACTION_ROLES["create_bid"] == {Role.BID_MANAGER, Role.ADMIN}


def test_manage_users_roles_is_admin_only():
    assert ACTION_ROLES["manage_users_roles"] == {Role.ADMIN}


def test_final_readiness_approval_excludes_bid_manager():
    assert Role.BID_MANAGER not in ACTION_ROLES["final_readiness_approval"]


def test_resolve_redteam_finding_allows_every_role():
    assert ACTION_ROLES["resolve_redteam_finding"] == set(Role)


def test_finding_resolution_gate_is_decided_by_bid_manager_approver_and_admin_only():
    # Plan item 73, decision 2: narrower than resolving one finding.
    assert ACTION_ROLES["decide_finding_resolution_gate"] == {
        Role.BID_MANAGER,
        Role.APPROVER,
        Role.ADMIN,
    }
    assert GATE_TYPE_ACTIONS["finding_resolution"] == "decide_finding_resolution_gate"
