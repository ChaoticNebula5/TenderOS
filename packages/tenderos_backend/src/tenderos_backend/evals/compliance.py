import uuid
from dataclasses import dataclass, field

from sqlalchemy import select

from tenderos_backend.db.models.requirement import Requirement
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.evals.datasets import GroundTruthRequirement

# tender_a_requirements.jsonl's expected_compliance vocabulary
# ("compliant"/"partially_compliant"/"unsupported"/"needs_review") does not
# lexically match compliance_entries.status's locked enum (Part C) -
# flagged during B7 planning, not silently resolved. This mapping is the
# resolution: "compliant" -> fully_compliant (exact concept match despite
# the different word), "unsupported" -> evidence_unavailable (the ground
# truth's seeded-unsupported rows have no evidence at all, which is
# evidence_unavailable's definition - not non_compliant, which would mean
# evidence exists PROVING non-compliance, a case this synthetic package
# never plants). "needs_review" is deliberately NOT in this mapping - see
# evaluate_compliance's docstring.
_EXPECTED_COMPLIANCE_TO_STATUS = {
    "compliant": "fully_compliant",
    "partially_compliant": "partially_compliant",
    "unsupported": "evidence_unavailable",
}


@dataclass(frozen=True)
class ComplianceMismatch:
    requirement_key: str
    expected_status: str
    actual_status: str


@dataclass(frozen=True)
class ComplianceEvalReport:
    macro_f1: float
    confusion_matrix: dict[tuple[str, str], int]
    unsupported_compliance_rate: float
    total_scored: int
    total_excluded_needs_review: int
    total_unscoreable: int
    mismatches: list[ComplianceMismatch] = field(default_factory=list)


def _macro_f1(pairs: list[tuple[str, str]]) -> float:
    """Unweighted per-class F1, then averaged - a class instance-count
    imbalance (e.g. this package has far fewer "unsupported" than
    "needs_review"-excluded rows) shouldn't let one class's frequency
    dominate a macro-average the way a weighted/micro-average would. Plain
    Python rather than a new numpy/sklearn dependency - the label set here
    is five values at most, nothing this needs a library for.
    """
    if not pairs:
        return 1.0
    labels = {label for pair in pairs for label in pair}
    f1_scores = []
    for label in labels:
        tp = sum(1 for e, a in pairs if e == label and a == label)
        fp = sum(1 for e, a in pairs if a == label and e != label)
        fn = sum(1 for e, a in pairs if e == label and a != label)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        f1_scores.append(f1)
    return sum(f1_scores) / len(f1_scores)


async def evaluate_compliance(
    ground_truth: list[GroundTruthRequirement], tender_id: uuid.UUID
) -> ComplianceEvalReport:
    """Not a blocking gate (B7's stop-gate is invariant-based, not numeric -
    see KNOWN_LIMITATIONS.md/the B7 plan) - this is the separate,
    reported-only Evaluation section (Build Guide: compliance macro-F1,
    confusion matrix, unsupported-compliance rate).

    "needs_review" ground-truth rows (~55 of 74 in this package) are
    excluded from scoring entirely, not mapped to any status - they were
    never given a firm expected outcome by the ground-truth generator (see
    KNOWN_LIMITATIONS.md-adjacent B7 planning notes), so scoring them
    against any single status would be inventing ground truth that doesn't
    exist. Same "unscoreable, not a miss" precedent B6's retrieval eval
    established for a different reason (a [REQ-nnn] token with no
    persisted Requirement row) - tracked separately so the exclusion stays
    visible in the report rather than silently shrinking the denominator.

    unsupported_compliance_rate: of the ground-truth rows expected to be
    evidence_unavailable (seeded with no valid evidence), what fraction
    the pipeline nonetheless scored as fully_compliant or
    partially_compliant - the empirical counterpart to the stop-gate's
    structural "zero schema-valid compliant decisions without required
    evidence" check. A nonzero rate here on this package would mean
    enforce_evidence_sufficiency has a real gap the stop-gate's own seeded
    test didn't happen to exercise.
    """
    pairs: list[tuple[str, str]] = []
    mismatches: list[ComplianceMismatch] = []
    excluded_needs_review = 0
    unscoreable = 0
    unsupported_total = 0
    unsupported_wrongly_compliant = 0

    async with UnitOfWork() as uow:
        for gt in ground_truth:
            expected_status = _EXPECTED_COMPLIANCE_TO_STATUS.get(gt.expected_compliance)
            if expected_status is None:
                excluded_needs_review += 1
                continue

            token = f"[{gt.requirement_key}]"
            result = await uow.session.execute(
                select(Requirement)
                .where(
                    Requirement.requirement_text.contains(token),
                    Requirement.tender_id == tender_id,
                )
                .limit(1)
            )
            requirement = result.scalar_one_or_none()
            if requirement is None:
                unscoreable += 1
                continue

            entry = await uow.compliance.get_compliance_entry_by_requirement(requirement.id)
            if entry is None:
                unscoreable += 1
                continue

            actual_status = entry.status
            pairs.append((expected_status, actual_status))
            if actual_status != expected_status:
                mismatches.append(
                    ComplianceMismatch(gt.requirement_key, expected_status, actual_status)
                )

            if expected_status == "evidence_unavailable":
                unsupported_total += 1
                if actual_status in ("fully_compliant", "partially_compliant"):
                    unsupported_wrongly_compliant += 1

    confusion_matrix: dict[tuple[str, str], int] = {}
    for expected, actual in pairs:
        confusion_matrix[(expected, actual)] = confusion_matrix.get((expected, actual), 0) + 1

    return ComplianceEvalReport(
        macro_f1=_macro_f1(pairs),
        confusion_matrix=confusion_matrix,
        unsupported_compliance_rate=(
            unsupported_wrongly_compliant / unsupported_total if unsupported_total else 0.0
        ),
        total_scored=len(pairs),
        total_excluded_needs_review=excluded_needs_review,
        total_unscoreable=unscoreable,
        mismatches=mismatches,
    )
