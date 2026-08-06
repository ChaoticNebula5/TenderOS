from typing import TypedDict

# R4's canonical task_type enum, transcribed verbatim.
TASK_TYPES = (
    "requirement_extraction",
    "requirement_verification",
    "evidence_retrieval",
    "compliance_analysis",
    "talent_analysis",
    "commercial_validation",
    "proposal_drafting",
    "proposal_revision",
    "graph_projection",
    "red_team_review",
    "readiness_generation",
    "impact_reanalysis",
)

# B3's specialist_dispatch fans out to exactly these three (R2's compliance_subgraph/
# talent_subgraph/commercial_subgraph). No real available_artifacts signal exists yet
# (document counts, staffing requirements, pricing docs - all later blocks), so the
# stub always selects all three instead of implementing E2's actual selection
# reasoning ahead of having anything real to select on.
_STUB_TASK_PLAN: tuple[tuple[str, str], ...] = (
    ("compliance_analysis", "evidence_compliance"),
    ("talent_analysis", "talent"),
    ("commercial_validation", "commercial"),
)


class PlannedTask(TypedDict):
    task_key: str
    task_type: str
    assigned_agent: str
    reasoning_summary: str
    priority: int
    input_refs: dict


def build_plan(workflow_run_id: str) -> list[PlannedTask]:
    """E2's plan-emission contract, stubbed.

    Real task-selection reasoning (E2's actual supervisor logic, replanning per
    R2.3) arrives once B4-B8 give it real available_artifacts to reason about.
    task_key is deterministic per (task_type, workflow_run_id) - R4 requires task_key
    to be stable so replanning/retries can't create duplicate logical tasks; a stub
    with no varying input_refs derives it from the run alone rather than hashing
    normalized inputs the way a real task_key eventually will.
    """
    plan: list[PlannedTask] = []
    for task_type, assigned_agent in _STUB_TASK_PLAN:
        assert task_type in TASK_TYPES, f"unknown task_type: {task_type}"
        plan.append(
            {
                "task_key": f"{task_type}:{workflow_run_id}",
                "task_type": task_type,
                "assigned_agent": assigned_agent,
                "reasoning_summary": "B3 stub plan: always dispatch all three specialists.",
                "priority": 5,
                "input_refs": {},
            }
        )
    return plan
