"""domain/services/graph_service.py::evaluate_evidence_coverage - D7's
decision function. Pins the rule table directly against constructed rows,
the same shape as test_claim_grounding.py pins enforce_claim_grounding:
verified+valid evidence with no backing fact counts as valid (the
document-excerpt case); an active, still-selected backing fact is required
when one exists; a deselected or non-active fact disqualifies its evidence
even when the evidence link itself still looks fine; unverified/invalid
evidence never counts regardless of the fact.

B16: the valid_until half of "an active fact" (KNOWN_LIMITATIONS.md's
"a check doesn't actually verify what it claims to" standing pattern,
shape B, instance 4 - the function fetched valid_until fresh every call
and simply never read it). Fixed here, not just described - see the two
new tests below.
"""

from datetime import date

from tenderos_backend.domain.services.graph_service import evaluate_evidence_coverage


def _row(**overrides) -> dict:
    fields = {
        "evidence_id": "ev-1",
        "verified": True,
        "validity_status": "valid",
        "knowledge_item_status": "active",
        "valid_until": None,
        "selected": True,
    }
    fields.update(overrides)
    return fields


def test_no_evidence_requires_reevaluation():
    result = evaluate_evidence_coverage([])

    assert result.valid_path_count == 0
    assert result.recommended_action == "compliance_re_evaluation_required"


def test_document_excerpt_with_no_backing_fact_counts_as_valid():
    row = _row(knowledge_item_status=None, selected=None)

    result = evaluate_evidence_coverage([row])

    assert result.valid_path_count == 1
    assert result.valid_evidence_ids == ["ev-1"]
    assert result.recommended_action == "review_optional"


def test_active_selected_fact_counts_as_valid():
    row = _row(knowledge_item_status="active", selected=True)

    result = evaluate_evidence_coverage([row])

    assert result.valid_path_count == 1
    assert result.recommended_action == "review_optional"


def test_deselected_fact_disqualifies_evidence_even_if_verified():
    row = _row(knowledge_item_status="active", selected=False)

    result = evaluate_evidence_coverage([row])

    assert result.valid_path_count == 0
    assert result.invalid_evidence_ids == ["ev-1"]
    assert result.recommended_action == "compliance_re_evaluation_required"


def test_inactive_knowledge_item_disqualifies_evidence():
    row = _row(knowledge_item_status="expired", selected=True)

    result = evaluate_evidence_coverage([row])

    assert result.valid_path_count == 0
    assert result.recommended_action == "compliance_re_evaluation_required"


def test_unverified_evidence_never_counts_regardless_of_fact():
    row = _row(verified=False, knowledge_item_status="active", selected=True)

    result = evaluate_evidence_coverage([row])

    assert result.valid_path_count == 0


def test_invalid_validity_status_never_counts():
    row = _row(validity_status="invalid")

    result = evaluate_evidence_coverage([row])

    assert result.valid_path_count == 0


def test_one_valid_alternate_path_is_enough_when_another_fails():
    good = _row(evidence_id="ev-good", knowledge_item_status="active", selected=True)
    bad = _row(evidence_id="ev-bad", verified=False)

    result = evaluate_evidence_coverage([good, bad])

    assert result.valid_path_count == 1
    assert result.valid_evidence_ids == ["ev-good"]
    assert result.invalid_evidence_ids == ["ev-bad"]
    assert result.recommended_action == "review_optional"


def test_active_fact_with_expired_valid_until_disqualifies_evidence():
    # The exact real-world case this bug missed: knowledge_item_status is
    # still "active" (nothing separately flipped it to archived/expired),
    # but the certification's own valid_until has passed - the fact is
    # stale even though its status column doesn't say so yet.
    row = _row(knowledge_item_status="active", selected=True, valid_until=date(2020, 1, 1))

    result = evaluate_evidence_coverage([row], today=date(2026, 1, 1))

    assert result.valid_path_count == 0
    assert result.invalid_evidence_ids == ["ev-1"]
    assert result.recommended_action == "compliance_re_evaluation_required"


def test_active_fact_with_future_valid_until_still_counts_as_valid():
    row = _row(knowledge_item_status="active", selected=True, valid_until=date(2030, 1, 1))

    result = evaluate_evidence_coverage([row], today=date(2026, 1, 1))

    assert result.valid_path_count == 1
    assert result.recommended_action == "review_optional"
