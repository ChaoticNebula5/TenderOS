import io

import pdfplumber

from tenderos_backend.ingestion.chunk import ExtractedBlock
from tenderos_backend.ingestion.ocr import ocr_page_image, page_needs_ocr

# Passed to page.to_image(resolution=...) only for pages that need OCR - 200
# DPI is a standard OCR-accuracy default (Tesseract's own docs recommend
# 200-300); higher costs more time for a background job with no benefit
# once text is already legible at this resolution.
_OCR_RENDER_RESOLUTION = 200


def parse_pdf(content: bytes) -> tuple[list[ExtractedBlock], int]:
    """One ExtractedBlock per page - the natural unit pdfplumber's own API
    already works in. How many pages' worth of text belong in one chunk is
    chunk.py's decision, not this function's. No heading_path is attempted
    here: PDFs have no structural heading metadata the way DOCX styles do,
    and reconstructing one from font-size heuristics is a real layout-
    analysis problem out of scope for B4 - heading_path stays empty for
    PDF-sourced chunks.
    """
    blocks: list[ExtractedBlock] = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        page_count = len(pdf.pages)
        for page in pdf.pages:
            text = page.extract_text() or ""
            has_image = len(page.images) > 0

            if page_needs_ocr(text, has_image):
                page_image = page.to_image(resolution=_OCR_RENDER_RESOLUTION).original
                text = ocr_page_image(page_image)

            blocks.append(ExtractedBlock(text=text, page_num=page.page_number))

    return blocks, page_count
