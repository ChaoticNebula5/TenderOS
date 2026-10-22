"""The OCR branch of ingestion/parse_pdf.py, end to end: an image-only PDF
goes through pdfplumber (no text layer), page rendering (pypdfium2),
Pillow and the tesseract binary, and parse_pdf returns the page's text.

Before this file the branch had no test; it was smoke-tested by hand when
Pillow went to 13 (2026-10-22). The PDF is built in-process with Pillow, so
no fixture file is needed.

Tesseract is a system binary, not a Python package. Where it is missing
(the Windows dev host) the test skips. CI installs it and sets
TENDEROS_REQUIRE_OCR=1, which turns the skip into a failure, so the test
cannot quietly stop running there. Lives in tests/unit, not
tests/integration: it needs no database, and the integration conftest's
session guard requires a _test Postgres, which would stop it running
inside the worker container.
"""

import io
import os

import pdfplumber
import pytesseract
import pytest
from PIL import Image, ImageDraw, ImageFont
from tenderos_backend.ingestion import parse_pdf as parse_pdf_module
from tenderos_backend.ingestion.ocr import page_needs_ocr

_LINES = ["TENDER OCR CHECK", "SUBMISSION DEADLINE", "FIFTEEN MARCH"]


def _tesseract_available() -> bool:
    try:
        pytesseract.get_tesseract_version()
    except (pytesseract.TesseractNotFoundError, OSError):
        return False
    return True


@pytest.fixture(autouse=True)
def _require_tesseract() -> None:
    if _tesseract_available():
        return
    if os.environ.get("TENDEROS_REQUIRE_OCR") == "1":
        pytest.fail("TENDEROS_REQUIRE_OCR=1 but the tesseract binary was not found")
    pytest.skip("tesseract binary not installed (set TENDEROS_REQUIRE_OCR=1 to fail instead)")


def _image_only_pdf() -> bytes:
    """One A4 page at 150 DPI holding only a picture of the text: no text
    layer, which is what a scanner produces. Pillow's bundled scalable font
    keeps the glyphs identical on every platform."""
    image = Image.new("RGB", (1240, 1754), color="white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=64)
    for i, line in enumerate(_LINES):
        draw.text((120, 200 + i * 120), line, fill="black", font=font)
    buf = io.BytesIO()
    image.save(buf, format="PDF")
    return buf.getvalue()


def test_an_image_only_pdf_is_read_through_ocr():
    content = _image_only_pdf()

    # The page really is image-only, so the text below can only have come
    # from the OCR branch, not from pdfplumber's text layer.
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        page = pdf.pages[0]
        native = page.extract_text() or ""
        assert native.strip() == ""
        assert page_needs_ocr(native, len(page.images) > 0)

    blocks, page_count = parse_pdf_module.parse_pdf(content)

    assert page_count == 1
    assert len(blocks) == 1
    assert blocks[0].page_num == 1
    words = set(blocks[0].text.upper().split())
    for expected in ("TENDER", "CHECK", "SUBMISSION", "DEADLINE", "MARCH"):
        assert expected in words, f"{expected!r} not in the OCR text: {blocks[0].text!r}"
