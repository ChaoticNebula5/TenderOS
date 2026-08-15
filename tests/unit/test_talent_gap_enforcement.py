"""domain/services/talent_service.py::enforce_gap_visibility - B8A's direct
analog of test_evidence_sufficiency.py. Pins the policy: an expired/
unverified required certification always appears as a gap regardless of
what the LLM wrote, a stale/needs-review profile always gets a "profile
currency" gap, and unknown availability both strips any LLM-claimed
availability strength AND adds an "availability: unknown" gap - the three
invariants B8A's own Tests list names explicitly."""

from tenderos_backend.agents.schemas.talent import CandidateScoring, TalentGap, TalentStrength
from tenderos_backend.domain.services.talent_service import (
    CandidateTalentSignals,
    CertFinding,
    enforce_gap_visibility,
)


def _signals(
    cert_findings=None, profile_stale_or_unreviewed=False, availability_unknown=False
) -> CandidateTalentSignals:
    return CandidateTalentSignals(
        cert_findings=cert_findings or [],
        profile_stale_or_unreviewed=profile_stale_or_unreviewed,
        availability_unknown=availability_unknown,
    )


def test_expired_required_cert_omitted_by_llm_is_forced_into_gaps():
    scoring = CandidateScoring(score=0.8, strengths=[], gaps=[])
    signals = _signals(cert_findings=[CertFinding("PMP", True, "expired")])

    result = enforce_gap_visibility(scoring, signals)

    assert {"criterion": "PMP", "status": "expired"} in result.gaps
    assert result.enforced_notes, "must record that the layer added something the LLM didn't"


def test_unmatched_required_cert_is_forced_into_gaps_as_unverified():
    scoring = CandidateScoring(score=0.8, strengths=[], gaps=[])
    signals = _signals(cert_findings=[CertFinding("ISO 27001", False, "unverified")])

    result = enforce_gap_visibility(scoring, signals)

    assert {"criterion": "ISO 27001", "status": "unverified"} in result.gaps


def test_cert_gap_already_reported_by_llm_is_not_duplicated():
    scoring = CandidateScoring(
        score=0.8, strengths=[], gaps=[TalentGap(criterion="PMP", status="expired")]
    )
    signals = _signals(cert_findings=[CertFinding("PMP", True, "expired")])

    result = enforce_gap_visibility(scoring, signals)

    assert result.gaps == [{"criterion": "PMP", "status": "expired"}]
    assert result.enforced_notes == []


def test_valid_cert_never_generates_a_gap():
    scoring = CandidateScoring(score=0.9, strengths=[], gaps=[])
    signals = _signals(cert_findings=[CertFinding("PMP", True, "valid")])

    result = enforce_gap_visibility(scoring, signals)

    assert result.gaps == []


def test_stale_profile_with_no_llm_gap_gets_one_added():
    scoring = CandidateScoring(score=0.8, strengths=[], gaps=[])
    signals = _signals(profile_stale_or_unreviewed=True)

    result = enforce_gap_visibility(scoring, signals)

    assert any(g["criterion"] == "profile currency" for g in result.gaps)


def test_unknown_availability_strips_llm_claimed_availability_strength():
    scoring = CandidateScoring(
        score=0.9,
        strengths=[TalentStrength(criterion="Available immediately", evidence_ref="evidence_1")],
        gaps=[],
    )
    signals = _signals(availability_unknown=True)

    result = enforce_gap_visibility(scoring, signals)

    assert result.strengths == [], "an availability claim must not survive when availability is unknown"
    assert any(g["criterion"] == "availability" and g["status"] == "unknown" for g in result.gaps)


def test_unknown_availability_strips_a_strength_phrased_without_the_word_available():
    scoring = CandidateScoring(
        score=0.9,
        strengths=[TalentStrength(criterion="Ready to start immediately", evidence_ref="evidence_1")],
        gaps=[],
    )
    signals = _signals(availability_unknown=True)

    result = enforce_gap_visibility(scoring, signals)

    assert result.strengths == []


def test_unrelated_strengths_survive_availability_enforcement():
    scoring = CandidateScoring(
        score=0.9,
        strengths=[TalentStrength(criterion="ITOM experience", evidence_ref="evidence_2")],
        gaps=[],
    )
    signals = _signals(availability_unknown=True)

    result = enforce_gap_visibility(scoring, signals)

    assert result.strengths == [{"criterion": "ITOM experience", "evidence_ref": "evidence_2"}]


def test_known_availability_does_not_touch_strengths_or_add_a_gap():
    scoring = CandidateScoring(
        score=0.9,
        strengths=[TalentStrength(criterion="Available immediately", evidence_ref="evidence_1")],
        gaps=[],
    )
    signals = _signals(availability_unknown=False)

    result = enforce_gap_visibility(scoring, signals)

    assert result.strengths == [{"criterion": "Available immediately", "evidence_ref": "evidence_1"}]
    assert not any(g["criterion"] == "availability" for g in result.gaps)
