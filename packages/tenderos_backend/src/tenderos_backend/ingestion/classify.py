import re

# Filename-keyword heuristic, checked before the extension default - a
# ".pdf" named "Addendum-3.pdf" should classify as an amendment, not fall
# through to the generic PDF default. No locked doc_type enum exists (Part C
# leaves documents.doc_type as free TEXT), so these values are a reasonable
# starting vocabulary, not a spec-mandated list.
_KEYWORD_TYPES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"addendum|amendment", re.IGNORECASE), "amendment"),
    (
        re.compile(r"boq|bill.?of.?quantities|pricing|rate.?schedule", re.IGNORECASE),
        "pricing_workbook",
    ),
    (re.compile(r"clarification|q.?&.?a", re.IGNORECASE), "clarification"),
    (re.compile(r"annex|appendix", re.IGNORECASE), "annexure"),
    (re.compile(r"\brfp\b|request.?for.?proposal|tender.?notice", re.IGNORECASE), "rfp"),
]

_EXTENSION_DEFAULTS = {
    ".pdf": "rfp",
    ".docx": "annexure",
    ".xlsx": "pricing_workbook",
}


def classify_document(filename: str, mime_type: str | None) -> str:
    for pattern, doc_type in _KEYWORD_TYPES:
        if pattern.search(filename):
            return doc_type
    return _EXTENSION_DEFAULTS.get(_extension_of(filename), "unclassified")


def _extension_of(filename: str) -> str:
    dot_index = filename.rfind(".")
    return filename[dot_index:].lower() if dot_index != -1 else ""
