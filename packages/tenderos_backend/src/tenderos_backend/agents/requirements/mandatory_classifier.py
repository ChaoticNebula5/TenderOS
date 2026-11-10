import re
from dataclasses import dataclass

_MANDATORY_TERMS = (
    "shall",
    "must",
    "will",
    "is required to",
    "is responsible for",
    "mandatory",
    "is obligated to",
    "no later than",
    "not later than",
    "at a minimum",
)

_OPTIONAL_TERMS = (
    "should",
    "may",
    "is encouraged to",
    "preferred",
    "optional",
    "at the vendor's discretion",
    "where possible",
    "if applicable",
    "desirable",
    "nice to have",
)

# How far (characters) on EITHER side of a matched term to look for a
# negation marker. Both directions matter for different terms: "not
# required" negates forward of "not", but "shall not provide" negates a modal
# verb from behind it - "shall" is the match, "not" trails it. Deliberately a
# fixed, small character window rather than a sentence/clause boundary -
# "fixed window" is a predictable rule a test can pin down exactly; "find the
# enclosing clause" is its own ambiguous parsing problem, which would just
# relocate the fuzziness this classifier exists to avoid rather than removing
# it.
_NEGATION_WINDOW_CHARS = 20
_NEGATION_MARKERS = ("not ", "n't ", "no longer ", "never ")


@dataclass(frozen=True)
class MandatoryClassification:
    """The classifier's independent verdict on one piece of source text -
    combined with the LLM's own `mandatory` field by extract.py, not here.
    `ambiguous=True` means no confident verdict was reached (both signals
    fired, neither fired, or a matched term looked negated), which extract.py
    treats as "defer to the LLM's judgment" rather than overriding it.
    """

    mandatory: bool
    ambiguous: bool
    matched_terms: tuple[str, ...]


def _term_matches(text: str, term: str) -> list[re.Match[str]]:
    pattern = rf"(?<!\w){re.escape(term)}(?!\w)"
    return list(re.finditer(pattern, text))


def _is_negated(text: str, match: re.Match[str]) -> bool:
    # The window deliberately excludes the matched span itself, not just its
    # surroundings - "not later than" is one of our own mandatory terms and
    # contains the literal substring "not ", so a window that included the
    # match would make that term self-negate on every hit.
    before = text[max(0, match.start() - _NEGATION_WINDOW_CHARS) : match.start()]
    after = text[match.end() : min(len(text), match.end() + _NEGATION_WINDOW_CHARS)]
    return any(marker in before for marker in _NEGATION_MARKERS) or any(
        marker in after for marker in _NEGATION_MARKERS
    )


def classify_mandatory_language(text: str) -> MandatoryClassification:
    lower_text = " ".join(text.lower().split())

    mandatory_matches: list[str] = []
    optional_matches: list[str] = []
    any_negated = False

    for term in _MANDATORY_TERMS:
        matches = _term_matches(lower_text, term)
        if matches:
            mandatory_matches.append(term)
            if any(_is_negated(lower_text, m) for m in matches):
                any_negated = True

    for term in _OPTIONAL_TERMS:
        matches = _term_matches(lower_text, term)
        if matches:
            optional_matches.append(term)
            if any(_is_negated(lower_text, m) for m in matches):
                any_negated = True

    matched_terms = tuple(mandatory_matches + optional_matches)
    has_mandatory = bool(mandatory_matches)
    has_optional = bool(optional_matches)

    # A negated match near ANY matched term makes the whole classification
    # unreliable - the crude, predictable move is to punt to human review
    # rather than try to flip the verdict (a fixed-window negation check
    # can tell you a negation word is nearby, not reliably which matched
    # term it scopes over in a compound sentence).
    if any_negated:
        return MandatoryClassification(mandatory=False, ambiguous=True, matched_terms=matched_terms)

    if has_mandatory and not has_optional:
        return MandatoryClassification(mandatory=True, ambiguous=False, matched_terms=matched_terms)

    if has_optional and not has_mandatory:
        return MandatoryClassification(
            mandatory=False, ambiguous=False, matched_terms=matched_terms
        )

    # Both matched, or neither matched.
    return MandatoryClassification(mandatory=False, ambiguous=True, matched_terms=matched_terms)
