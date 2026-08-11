"""Mandatory-language classifier - pure function, no DB, no LLM call. This
classifier is deliberately crude: it's meant to be a second, independent
signal the LLM's own mandatory/optional judgment gets checked against
(E3: "low confidence or disagreement enters human review"), not a replacement
for contextual understanding. `ambiguous=True` is the correct, honest output
whenever the classifier can't reach a confident verdict on its own - it is
not a failure mode to eliminate.
"""

from tenderos_backend.agents.requirements.mandatory_classifier import (
    classify_mandatory_language,
)


def test_confident_mandatory_language():
    result = classify_mandatory_language("The vendor shall provide onsite support.")
    assert result.mandatory is True
    assert result.ambiguous is False
    assert "shall" in result.matched_terms


def test_confident_optional_language():
    result = classify_mandatory_language("Additional customization is preferred by the committee.")
    assert result.ambiguous is False
    assert result.mandatory is False
    assert "preferred" in result.matched_terms


def test_no_signal_is_ambiguous_not_a_guess():
    result = classify_mandatory_language("The building has three floors.")
    assert result.ambiguous is True
    assert result.matched_terms == ()


def test_both_signals_present_is_ambiguous_not_a_coin_flip():
    result = classify_mandatory_language(
        "Bidders must submit three references and may optionally include a fourth."
    )
    assert result.ambiguous is True


def test_negation_after_modal_verb_is_ambiguous_not_mandatory():
    """"shall not be required to..." contains "shall", a mandatory term - but
    the sentence is the opposite of a mandatory requirement. Catching this
    requires checking for negation trailing the match, not just preceding
    it."""
    result = classify_mandatory_language(
        "The vendor shall not be required to provide onsite support."
    )
    assert result.ambiguous is True


def test_deadline_phrase_does_not_self_negate():
    """"not later than" is itself a mandatory term and contains the literal
    substring "not " - regression test for the negation window accidentally
    including the matched span itself and always flagging this term as
    negated."""
    result = classify_mandatory_language("Submissions are due not later than 5:00 PM.")
    assert result.mandatory is True
    assert result.ambiguous is False


def test_word_boundary_prevents_substring_collision():
    """"must" should not match inside "mustard", "will" should not match
    inside "goodwill"."""
    result = classify_mandatory_language("The catering menu includes mustard and goodwill gifts.")
    assert result.matched_terms == ()
    assert result.ambiguous is True


def test_case_insensitive_matching():
    result = classify_mandatory_language("The vendor SHALL comply with all applicable laws.")
    assert result.mandatory is True
    assert result.ambiguous is False
