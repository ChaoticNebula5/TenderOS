"""orchestration/routing.py's B11 additions - route_after_red_team (the
review_router routing function), route_after_targeted_replan,
route_after_specialist_join, route_after_finding_gate. All four are plain
synchronous state-readers, same as every pre-existing route_after_*
function - no DB, no LLM, so these run as pure unit tests against
hand-built TenderState dicts. This is also where the plan's Q5 progress
check (fix #1 from the plan review) gets its direct proof: a revision that
produces an identical open-finding set routes to finding_gate even with
budget remaining.
"""

from langgraph.graph import END
from tenderos_backend.orchestration.routing import (
    route_after_finding_gate,
    route_after_red_team,
    route_after_specialist_join,
    route_after_targeted_replan,
)


def _state(**overrides) -> dict:
    base = {
        "status": "red_teaming",
        "revision_count": 0,
        "max_revisions": 2,
        "open_critical_finding_ids": [],
        "pre_revision_finding_ids": [],
    }
    base.update(overrides)
    return base


class TestRouteAfterRedTeam:
    def test_no_open_findings_goes_to_readiness_generation(self):
        # B12: was final_gate directly until readiness_generation filled
        # in the "readiness" branch R2's diagram names.
        assert route_after_red_team(_state(open_critical_finding_ids=[])) == "readiness_generation"

    def test_open_findings_under_budget_goes_to_targeted_replan(self):
        state = _state(open_critical_finding_ids=["f1"], revision_count=0)
        assert route_after_red_team(state) == "targeted_replan"

    def test_budget_exhausted_goes_to_finding_gate_even_with_open_findings(self):
        state = _state(open_critical_finding_ids=["f1"], revision_count=2, max_revisions=2)
        assert route_after_red_team(state) == "finding_gate"

    def test_no_progress_after_a_revision_goes_to_finding_gate_with_budget_remaining(self):
        # The exact scenario the plan review's point 1 required: revision_count
        # is 1 of a possible 2 (budget NOT exhausted), but the open set is
        # byte-identical to what it was going into that revision - must stop
        # anyway, not spend the second revision on a finding that already
        # proved it wouldn't change.
        state = _state(
            open_critical_finding_ids=["f1"],
            pre_revision_finding_ids=["f1"],
            revision_count=1,
            max_revisions=2,
        )
        assert route_after_red_team(state) == "finding_gate"

    def test_progress_made_after_a_revision_goes_to_targeted_replan_again(self):
        # Same shape as the no-progress case but the open set genuinely
        # changed (f1 resolved, f2 is new) - budget remains, progress was
        # real, so another revision is worth attempting.
        state = _state(
            open_critical_finding_ids=["f2"],
            pre_revision_finding_ids=["f1"],
            revision_count=1,
            max_revisions=2,
        )
        assert route_after_red_team(state) == "targeted_replan"

    def test_first_pass_with_no_pre_revision_snapshot_is_not_treated_as_no_progress(self):
        # revision_count == 0 means no revision has been attempted yet -
        # pre_revision_finding_ids is absent/empty, and that must never be
        # misread as "matches the current open set" (which would also be
        # empty-vs-nonempty and could coincidentally compare equal to a
        # single-element set in a naive implementation).
        state = _state(open_critical_finding_ids=["f1"], revision_count=0)
        state.pop("pre_revision_finding_ids")
        assert route_after_red_team(state) == "targeted_replan"


class TestRouteAfterTargetedReplan:
    def test_specialist_dispatch_target(self):
        # cost_budget_gate now sits in front of BOTH live targets (I5,
        # Build Guide :2755-2763 - B2.4's own "cost budget exceeded" stop
        # condition at :731 covers the whole revision loop, not just the
        # specialist-heavy half of it). The gate node itself reads
        # revision_dispatch_target back out of state to pick the real
        # downstream target on approval - see test_cost_budget_routing.py.
        assert (
            route_after_targeted_replan(_state(revision_dispatch_target="specialist_dispatch"))
            == "cost_budget_gate"
        )

    def test_proposal_drafting_target(self):
        assert (
            route_after_targeted_replan(_state(revision_dispatch_target="proposal_drafting"))
            == "cost_budget_gate"
        )

    def test_none_target_goes_to_finding_gate(self):
        assert (
            route_after_targeted_replan(_state(revision_dispatch_target="none")) == "finding_gate"
        )

    def test_missing_target_defaults_to_finding_gate(self):
        state = _state()
        assert route_after_targeted_replan(state) == "finding_gate"


class TestRouteAfterSpecialistJoin:
    def test_first_pass_goes_to_compliance_gate(self):
        assert route_after_specialist_join(_state(revision_count=0)) == "compliance_gate"

    def test_revision_pass_skips_the_three_gates(self):
        assert route_after_specialist_join(_state(revision_count=1)) == "proposal_drafting"


class TestRouteAfterFindingGate:
    def test_approve_proceeds_to_readiness_generation(self):
        # B12: was final_gate directly until readiness_generation filled
        # in the "readiness" branch R2's diagram names.
        assert route_after_finding_gate(_state(status="ready")) == "readiness_generation"

    def test_reject_ends_the_run(self):
        assert route_after_finding_gate(_state(status="rejected")) == END

    def test_no_decision_value_reenters_the_loop(self):
        # The bound-by-construction claim: every legal status this node can
        # produce routes to either readiness_generation or END, never back
        # to targeted_replan/specialist_dispatch/red_team.
        for status in ("ready", "rejected", "awaiting_final_approval", "changes_requested"):
            result = route_after_finding_gate(_state(status=status))
            assert result in ("readiness_generation", END)
