"""domain/services/proposal_service.py::enforce_claim_grounding - B9's
direct analog of test_evidence_sufficiency.py (B7) and
test_talent_gap_enforcement.py (B8A). Pins the claim invariant: a
non-factual claim is always not_required, a factual claim with no resolved
evidence is always unsupported (never silently downgraded to
not_required), and a compliance-type claim whose linked requirement's
approved status isn't affirmative is forced to needs_review regardless of
how much evidence it cites (E7's "compliance position mismatch flagged"
test).

enforce_claim_grounding deliberately takes no section name at all - this is
what makes the "no narrative exemption for Company Overview" requirement a
structural guarantee rather than a policy the function could get
selectively wrong: there is no parameter it could even branch on. A
section-specific proof (a claim drafted into Company Overview by name
still lands unsupported) lives in test_proposal_service.py instead, since
that's the layer that actually knows which section a claim came from.
"""

import uuid

from tenderos_backend.agents.schemas.proposal import ProposalClaimDraft
from tenderos_backend.domain.services.proposal_service import enforce_claim_grounding


def _claim(**overrides) -> ProposalClaimDraft:
    fields = {
        "claim_text": "The company has delivered three telecom network migrations.",
        "claim_type": "experience",
        "is_factual": True,
    }
    fields.update(overrides)
    return ProposalClaimDraft(**fields)


def test_non_factual_claim_is_not_required_even_with_no_evidence():
    claim = _claim(is_factual=False)

    result = enforce_claim_grounding(claim, [], [], {})

    assert result.status == "not_required"


def test_factual_claim_with_no_evidence_is_unsupported_not_dropped():
    claim = _claim(is_factual=True)

    result = enforce_claim_grounding(claim, [], [], {})

    assert result.status == "unsupported"


def test_factual_claim_with_evidence_is_grounded():
    claim = _claim(is_factual=True, claim_type="experience")

    result = enforce_claim_grounding(claim, [], [uuid.uuid4()], {})

    assert result.status == "grounded"


def test_compliance_claim_matching_affirmative_status_is_grounded():
    requirement_id = uuid.uuid4()
    claim = _claim(is_factual=True, claim_type="compliance")

    result = enforce_claim_grounding(
        claim, [requirement_id], [uuid.uuid4()], {requirement_id: "fully_compliant"}
    )

    assert result.status == "grounded"


def test_compliance_claim_disagreeing_with_approved_status_is_needs_review_despite_evidence():
    # The "compliance position mismatch flagged" test (E7): a claim that
    # cites real evidence must still not be "grounded" if what it actually
    # asserts contradicts the tender's own recorded compliance decision.
    requirement_id = uuid.uuid4()
    claim = _claim(is_factual=True, claim_type="compliance")

    result = enforce_claim_grounding(
        claim, [requirement_id], [uuid.uuid4()], {requirement_id: "non_compliant"}
    )

    assert result.status == "needs_review"


def test_compliance_claim_for_requirement_with_no_approved_decision_is_needs_review():
    # A requirement missing from compliance_status_by_requirement has no
    # approved compliance decision at all - treated the same as a
    # non-affirmative status, not as "no opinion" / implicitly fine.
    requirement_id = uuid.uuid4()
    claim = _claim(is_factual=True, claim_type="compliance")

    result = enforce_claim_grounding(claim, [requirement_id], [uuid.uuid4()], {})

    assert result.status == "needs_review"


def test_non_factual_claim_in_compliance_type_still_not_required():
    # is_factual is the only thing that may route a claim to not_required -
    # claim_type must never do that job either, symmetric to the
    # section-blindness guarantee above.
    requirement_id = uuid.uuid4()
    claim = _claim(is_factual=False, claim_type="compliance")

    result = enforce_claim_grounding(claim, [requirement_id], [], {requirement_id: "non_compliant"})

    assert result.status == "not_required"
