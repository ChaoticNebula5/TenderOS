"""redteam_service.finding_fingerprint - E8's own rule: "Finding identity
is based on the violated condition, not free-text wording." Same
plain-function unit-test shape as the fingerprint stability already proven
for pricing_checks.finding_fingerprint (B8B) - no DB needed.
"""

import uuid

from tenderos_backend.domain.services.redteam_service import (
    map_finding_to_corrective_task,
    finding_fingerprint,
)

_TENDER = uuid.uuid4()
_TARGET = uuid.uuid4()


def test_fingerprint_is_stable_for_identical_inputs():
    a = finding_fingerprint(_TENDER, "requirement", _TARGET, "missed_mandatory_requirement", "x")
    b = finding_fingerprint(_TENDER, "requirement", _TARGET, "missed_mandatory_requirement", "x")
    assert a == b


def test_fingerprint_ignores_finding_text_and_rebuttal():
    # finding_fingerprint's signature has no finding_text/rebuttal parameter
    # at all - a differently-worded re-detection of the same underlying
    # problem is guaranteed to produce the same fingerprint since there's
    # no wording input to vary in the first place.
    a = finding_fingerprint(_TENDER, "requirement", _TARGET, "missed_mandatory_requirement", "x")
    b = finding_fingerprint(_TENDER, "requirement", _TARGET, "missed_mandatory_requirement", "x")
    assert a == b


def test_fingerprint_differs_on_violated_condition():
    a = finding_fingerprint(_TENDER, "requirement", _TARGET, "missed_mandatory_requirement", "x")
    b = finding_fingerprint(_TENDER, "requirement", _TARGET, "missed_mandatory_requirement", "y")
    assert a != b


def test_fingerprint_differs_on_category():
    a = finding_fingerprint(_TENDER, "requirement", _TARGET, "missed_mandatory_requirement", "x")
    b = finding_fingerprint(_TENDER, "requirement", _TARGET, "role_qualification_gap", "x")
    assert a != b


def test_fingerprint_differs_across_tenders():
    a = finding_fingerprint(_TENDER, "requirement", _TARGET, "missed_mandatory_requirement", "x")
    b = finding_fingerprint(uuid.uuid4(), "requirement", _TARGET, "missed_mandatory_requirement", "x")
    assert a != b


class TestMapFindingToCorrectiveTask:
    def test_missed_mandatory_requirement_maps_to_compliance_analysis(self):
        assert map_finding_to_corrective_task("missed_mandatory_requirement") == "compliance_analysis"

    def test_expired_certification_maps_to_compliance_analysis(self):
        assert map_finding_to_corrective_task("expired_certification") == "compliance_analysis"

    def test_unsupported_claim_maps_to_proposal_revision(self):
        assert map_finding_to_corrective_task("unsupported_claim") == "proposal_revision"

    def test_removed_employee_committed_maps_to_proposal_revision(self):
        assert map_finding_to_corrective_task("removed_employee_committed") == "proposal_revision"

    def test_non_auto_correctable_categories_map_to_none(self):
        for category in (
            "pricing_mismatch",
            "missing_mandatory_submission_item",
            "role_qualification_gap",
            "deselected_fact_in_use",
            "unsupported_grounded_claim",
            "overstated_claim",
        ):
            assert map_finding_to_corrective_task(category) is None
