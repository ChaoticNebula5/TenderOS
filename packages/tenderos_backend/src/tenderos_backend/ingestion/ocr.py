import pytesseract
from PIL import Image

# Below this many natively-extracted characters, a page is a OCR candidate -
# but only if it also carries an embedded image (see page_needs_ocr). A
# typical typeset RFP page runs roughly 1,500-3,000 characters of body text;
# even a sparse page (a short section, a title page) usually clears a few
# hundred once headers/footers/section numbers are included. 200 characters
# (~30-40 words) is low enough that genuine text-bearing pages almost always
# clear it, but low enough to catch true near-empty extraction from a
# scanned page. This is a starting default meant to be tuned against real
# fixtures once an eval loop exists (same spirit as B5's failure-action list:
# "improve parsing/chunking first"), not a value derived from the spec.
LOW_TEXT_CHAR_THRESHOLD = 200


def ocr_page_image(image: Image.Image) -> str:
    """Runs Tesseract over a rendered page image and returns the extracted
    text. Called only for pages page_needs_ocr() flags - never unconditionally,
    since OCR is far more expensive than native text extraction.
    """
    return pytesseract.image_to_string(image)


# Low native text alone doesn't distinguish a scanned page from a genuinely
# blank one (an intentional separator page) - only the presence of an
# embedded image does. OCR can't recover text that was never there, so a
# blank page must return False here even though it's under threshold too.
def page_needs_ocr(extracted_text: str, has_embedded_image: bool) -> bool:
    stripped_extracted_text = extracted_text.strip()
    extracted_text_length = len(stripped_extracted_text)

    if extracted_text_length >= LOW_TEXT_CHAR_THRESHOLD:
        return False

    if has_embedded_image:
        return True
    return False
