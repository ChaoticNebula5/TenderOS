import uuid
from dataclasses import dataclass, field

from tenderos_backend.db.unit_of_work import UnitOfWork

# B11's own 7 required categories, transcribed verbatim from the Build
# Guide's seeded-defects list. Unlike B5/B7's evals (which read ground
# truth from data/ground_truth/tender_a_*.jsonl, planted by
# synthetic_tender_a.py), 4 of these 7 are pipeline-behavior states a
# document generator can't plant (see the B11 plan's Q4 answer) - the
# caller constructs a matching tender directly via repository calls and
# passes the categories it seeded, rather than this module owning a fixed
# ground-truth file.
REQUIRED_CATEGORIES = (
    "missed_mandatory_requirement",
    "unsupported_claim",
    "expired_certification",
    "removed_employee_committed",
    "pricing_mismatch",
    "missing_mandatory_submission_item",
    "role_qualification_gap",
)

# Which of the 7 count toward the stop-gate's "critical" recall=1.00 bar,
# vs. the looser "overall" >= 0.90 bar. Mirrors redteam_service.py's own
# _AUTO_REVISION_SEVERITIES split by intent (a compliance/trust risk the
# proposal itself could be wrong about) rather than by the severity the
# checks happen to assign at detection time - missing_mandatory_
# submission_item and role_qualification_gap are real defects but "major"
# process gaps, not the proposal asserting something false.
_CRITICAL_CATEGORIES = frozenset(
    {
        "missed_mandatory_requirement",
        "unsupported_claim",
        "expired_certification",
        "removed_employee_committed",
        "pricing_mismatch",
    }
)


@dataclass(frozen=True)
class SeededDefect:
    category: str
    target_id: uuid.UUID


@dataclass(frozen=True)
class RedteamEvalReport:
    critical_recall: float
    overall_recall: float
    detected_categories: set[str] = field(default_factory=set)
    missed_categories: set[str] = field(default_factory=set)
    false_positive_count: int = 0
    false_positive_categories: set[str] = field(default_factory=set)


async def evaluate_redteam(
    tender_id: uuid.UUID, seeded: list[SeededDefect]
) -> RedteamEvalReport:
    """Recall against a tender that's already been seeded with known
    defects (via direct repository construction, per the plan's Q4
    answer) and already run through red_team_node/redteam_service.run_red_team
    at least once. False positives here mean "a category outside
    REQUIRED_CATEGORIES got detected" - the stop-gate's own "document false
    positives" bullet, not a precision computation (this package doesn't
    seed a clean-of-defects control tender to measure precision against).
    """
    async with UnitOfWork() as uow:
        findings = await uow.redteam.list_for_tender(tender_id)

    detected_target_categories = {(f.target_id, f.finding_category) for f in findings}
    detected_categories: set[str] = set()
    for defect in seeded:
        if (defect.target_id, defect.category) in detected_target_categories:
            detected_categories.add(defect.category)

    seeded_categories = {d.category for d in seeded}
    missed_categories = seeded_categories - detected_categories

    critical_seeded = seeded_categories & _CRITICAL_CATEGORIES
    critical_detected = detected_categories & _CRITICAL_CATEGORIES
    critical_recall = (
        len(critical_detected) / len(critical_seeded) if critical_seeded else 1.0
    )
    overall_recall = (
        len(detected_categories) / len(seeded_categories) if seeded_categories else 1.0
    )

    false_positive_categories = {
        f.finding_category for f in findings if f.finding_category not in REQUIRED_CATEGORIES
    }
    false_positive_count = sum(
        1 for f in findings if f.finding_category not in REQUIRED_CATEGORIES
    )

    return RedteamEvalReport(
        critical_recall=critical_recall,
        overall_recall=overall_recall,
        detected_categories=detected_categories,
        missed_categories=missed_categories,
        false_positive_count=false_positive_count,
        false_positive_categories=false_positive_categories,
    )
