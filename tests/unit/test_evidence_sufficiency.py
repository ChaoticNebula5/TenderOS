"""enforce_evidence_sufficiency (domain/services/compliance_service.py) -
the deterministic PRD 8.4 enforcement, pure function, no DB. This is the one
place in B7's pipeline where the invariant becomes a fact about what gets
persisted rather than a hope about model behavior - see the plan/
KNOWN_LIMITATIONS.md."""

import uuid

import pytest
from tenderos_backend.agents.schemas.compliance import ComplianceProposal
from tenderos_backend.domain.services.compliance_service import enforce_evidence_sufficiency


class _FakeEvidence:
    def __init__(self, verified: bool, validity_status: str) -> None:
        self.id = uuid.uuid4()
        self.verified = verified
        self.validity_status = validity_status


def _proposal(status: str) -> ComplianceProposal:
    return ComplianceProposal(
        status=status, evidence_refs=["evidence_1"], confidence=0.9, reasoning_summary="x"
    )


@pytest.mark.parametrize("status", ["fully_compliant", "partially_compliant"])
def test_downgrades_to_evidence_unavailable_when_no_sufficient_evidence(status):
    result = enforce_evidence_sufficiency(
        _proposal(status), [_FakeEvidence(verified=False, validity_status="expired")]
    )
    assert result.status == "evidence_unavailable"
    assert result.downgraded is True


def test_downgrades_to_evidence_unavailable_when_no_evidence_at_all():
    result = enforce_evidence_sufficiency(_proposal("fully_compliant"), [])
    assert result.status == "evidence_unavailable"
    assert result.downgraded is True


def test_fully_compliant_stays_when_all_cited_evidence_sufficient():
    result = enforce_evidence_sufficiency(
        _proposal("fully_compliant"),
        [
            _FakeEvidence(verified=True, validity_status="valid"),
            _FakeEvidence(verified=True, validity_status="valid"),
        ],
    )
    assert result.status == "fully_compliant"
    assert result.downgraded is False


def test_fully_compliant_downgrades_to_partially_compliant_when_evidence_mixed():
    result = enforce_evidence_sufficiency(
        _proposal("fully_compliant"),
        [
            _FakeEvidence(verified=True, validity_status="valid"),
            _FakeEvidence(verified=False, validity_status="expired"),
        ],
    )
    assert result.status == "partially_compliant"
    assert result.downgraded is True


def test_partially_compliant_stays_with_at_least_one_sufficient_item():
    result = enforce_evidence_sufficiency(
        _proposal("partially_compliant"),
        [
            _FakeEvidence(verified=True, validity_status="valid"),
            _FakeEvidence(verified=False, validity_status="expired"),
        ],
    )
    assert result.status == "partially_compliant"
    assert result.downgraded is False


@pytest.mark.parametrize(
    "status", ["non_compliant", "clarification_required", "evidence_unavailable"]
)
def test_non_evidence_backed_statuses_pass_through_unchanged(status):
    result = enforce_evidence_sufficiency(_proposal(status), [])
    assert result.status == status
    assert result.downgraded is False
