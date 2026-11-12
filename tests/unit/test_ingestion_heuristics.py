"""OCR-trigger and document-classification heuristics - pure functions, no
DB, no model load."""

from tenderos_backend.ingestion.classify import classify_document
from tenderos_backend.ingestion.ocr import LOW_TEXT_CHAR_THRESHOLD, page_needs_ocr


def test_page_with_plenty_of_native_text_skips_ocr():
    text = "x" * (LOW_TEXT_CHAR_THRESHOLD + 1)
    assert page_needs_ocr(text, has_embedded_image=False) is False
    # Even with an image present - a text page that also contains a logo or
    # diagram must not pay for OCR it does not need.
    assert page_needs_ocr(text, has_embedded_image=True) is False


def test_low_text_page_with_image_triggers_ocr():
    assert page_needs_ocr("", has_embedded_image=True) is True
    assert page_needs_ocr("Figure 1", has_embedded_image=True) is True


def test_blank_page_without_image_does_not_trigger_ocr():
    """B4's smoke test wants OCR only where a page has little text. A blank
    separator page is also low-text, but OCR cannot recover text that was
    never there, so running it would be pure waste."""
    assert page_needs_ocr("", has_embedded_image=False) is False
    assert page_needs_ocr("   \n\t  ", has_embedded_image=False) is False


def test_whitespace_only_extraction_counts_as_low_text():
    """A scanned page whose text layer holds only whitespace artifacts still
    has zero real characters - measuring the unstripped length would let it
    clear the threshold and silently skip OCR, losing the whole page."""
    whitespace = " \n\t" * 500
    assert len(whitespace) > LOW_TEXT_CHAR_THRESHOLD
    assert page_needs_ocr(whitespace, has_embedded_image=True) is True


def test_threshold_boundary_is_inclusive():
    at_threshold = "x" * LOW_TEXT_CHAR_THRESHOLD
    just_under = "x" * (LOW_TEXT_CHAR_THRESHOLD - 1)
    assert page_needs_ocr(at_threshold, has_embedded_image=True) is False
    assert page_needs_ocr(just_under, has_embedded_image=True) is True


def test_classify_prefers_filename_keywords_over_extension():
    """A PDF named "Addendum-3.pdf" is an amendment, not a generic RFP - the
    keyword pass must win over the extension default."""
    assert classify_document("Addendum-3.pdf", "application/pdf") == "amendment"
    assert classify_document("Corrigendum.pdf", "application/pdf") == "rfp"
    assert classify_document("BOQ-final.pdf", "application/pdf") == "pricing_workbook"
    assert classify_document("Annexure-C.pdf", "application/pdf") == "annexure"


def test_classify_falls_back_to_extension():
    assert classify_document("untitled.pdf", "application/pdf") == "rfp"
    assert classify_document("untitled.docx", None) == "annexure"
    assert classify_document("untitled.xlsx", None) == "pricing_workbook"


def test_classify_unknown_extension():
    assert classify_document("notes.txt", "text/plain") == "unclassified"
    assert classify_document("noextension", None) == "unclassified"


def test_classify_is_case_insensitive():
    assert classify_document("AMENDMENT-1.PDF", "application/pdf") == "amendment"
    assert classify_document("pricing.XLSX", None) == "pricing_workbook"
