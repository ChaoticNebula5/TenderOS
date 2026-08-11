"""Generates B4's synthetic fixtures into data/synthetic/.

B4 names six explicitly: native-text RFP PDF, scanned two-page RFP, DOCX
annexure with tables, XLSX pricing workbook with formulas, corrupted file,
duplicate upload. A seventh, dense_compliance_matrix.pdf, was added after
those six turned out to all be too short to ever fill chunk.py's packer
buffer - every packed piece measured under 65 tokens against a 256-token
cap, so the six-fixture bisection-rate measurement was 0% for the wrong
reason (nothing was big enough to stress it), not because MAX_CHUNK_CHARS
was well-calibrated. This fixture is long, dense (clause codes, currency,
cert/evidence references - no sentence punctuation to split on), and
measured at ~1.8-2.0 chars/token, matching the worst real density tender
documents actually contain.

They are generated rather than committed as binaries so the repository
stays diffable and nothing here is mistaken for real tender content (A5.1
keeps private documents out of the repo entirely).

Run: uv run python scripts/make_synthetic_fixtures.py
"""

import io
from pathlib import Path

import docx
from openpyxl import Workbook
from PIL import Image, ImageDraw, ImageFont
from synthetic_common import native_pdf as _native_pdf

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "synthetic"


def _scanned_pdf(pages: list[str]) -> bytes:
    """Image-only pages with no text layer - what a scanner produces, and
    the only fixture that should ever trigger the OCR fallback."""
    images = []
    for text in pages:
        image = Image.new("RGB", (1240, 1754), color="white")  # A4 at 150 DPI
        draw = ImageDraw.Draw(image)
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", 32)
        except OSError:
            font = ImageFont.load_default()
        for i, line in enumerate(text.split("\n")):
            draw.text((100, 120 + i * 50), line, fill="black", font=font)
        images.append(image)

    buf = io.BytesIO()
    images[0].save(buf, format="PDF", save_all=True, append_images=images[1:])
    return buf.getvalue()


def _dense_compliance_matrix_pdf(rows_per_page: int, page_count: int) -> bytes:
    """Rows shaped like real compliance-matrix/pricing-schedule lines: clause
    codes, cert/evidence references, currency amounts - no sentence
    punctuation, so _atomic_units cannot split on ". " and _pack_units packs
    many whole lines together per chunk. This is what makes it dense: short
    tokens, heavy on digits and hyphens, ~1.8-2.0 measured chars/token,
    versus ~4.3-6.1 for the prose fixtures.
    """
    pages: list[list[str]] = []
    row = 0
    for _ in range(page_count):
        lines = []
        for _ in range(rows_per_page):
            row += 1
            lines.append(
                f"C.{row:03d}-MANDATORY REF-ANNEX-{row % 12}-SEC-{row % 7}.{row % 5} "
                f"CERT-ISO{27000 + row % 99}-REV{row % 9} USD-{row * 1250}.00 "
                f"EVID-{row:05d}-A{row % 3}"
            )
        pages.append(lines)
    return _native_pdf(pages)


def _docx_annexure() -> bytes:
    document = docx.Document()
    document.add_heading("Annexure C: Compliance Matrix", level=1)
    document.add_paragraph(
        "Bidders shall complete every row of the matrix below. Incomplete "
        "submissions will be rejected without further evaluation."
    )
    document.add_heading("C.1 Mandatory Requirements", level=2)
    table = document.add_table(rows=4, cols=3)
    table.style = "Table Grid"
    rows = [
        ("Ref", "Requirement", "Evidence Required"),
        ("C.1.1", "Valid tax clearance certificate", "Certificate copy"),
        ("C.1.2", "Professional indemnity of 1,000,000", "Insurance schedule"),
        ("C.1.3", "Three comparable projects in five years", "Reference letters"),
    ]
    for row_idx, row in enumerate(rows):
        for col_idx, value in enumerate(row):
            table.cell(row_idx, col_idx).text = value

    document.add_heading("C.2 Desirable Requirements", level=2)
    document.add_paragraph("ISO 27001 certification is desirable but not mandatory.")

    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def _xlsx_pricing() -> bytes:
    workbook = Workbook()
    pricing = workbook.active
    pricing.title = "Pricing"
    pricing["A1"], pricing["B1"], pricing["C1"] = "Item", "Unit Cost", "Quantity"
    pricing["D1"] = "Line Total"
    line_items = [
        ("Senior Engineer", 1200, 12),
        ("Project Manager", 1500, 6),
        ("Site Survey", 8000, 1),
    ]
    for i, (item, cost, qty) in enumerate(line_items, start=2):
        pricing[f"A{i}"], pricing[f"B{i}"], pricing[f"C{i}"] = item, cost, qty
        pricing[f"D{i}"] = f"=B{i}*C{i}"
    pricing["A5"] = "Total"
    pricing["D5"] = "=SUM(D2:D4)"

    schedule = workbook.create_sheet("Schedule")
    schedule["A1"], schedule["B1"] = "Milestone", "Week"
    for i, (name, week) in enumerate([("Mobilisation", 2), ("Delivery", 10)], start=2):
        schedule[f"A{i}"], schedule[f"B{i}"] = name, week

    buf = io.BytesIO()
    workbook.save(buf)
    return buf.getvalue()


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    fixtures: dict[str, bytes] = {
        "native_text_rfp.pdf": _native_pdf(
            [
                [
                    "REQUEST FOR PROPOSAL - REF RFP-2026-014",
                    "",
                    "1. Scope of Work",
                    "The supplier shall deliver network infrastructure upgrades",
                    "across four regional sites within twelve months of award.",
                    "",
                    "2. Mandatory Requirements",
                    "2.1 The bidder must hold a valid tax clearance certificate.",
                    "2.2 The bidder must demonstrate three comparable projects.",
                ],
                [
                    "3. Submission Requirements",
                    "3.1 Proposals must be submitted by 31 March 2026 at 17:00.",
                    "3.2 Late submissions shall not be considered under any",
                    "circumstances whatsoever.",
                    "",
                    "4. Evaluation Criteria",
                    "Technical merit shall carry seventy percent of the score",
                    "and price shall carry thirty percent.",
                ],
            ]
        ),
        "scanned_rfp.pdf": _scanned_pdf(
            [
                "SCANNED TENDER NOTICE\n\nReference SCAN-2026-001\n\n"
                "The contractor shall provide all plant and\nmaterials necessary "
                "for completion of works.",
                "Page Two of Scanned Notice\n\nAll queries must be submitted in\n"
                "writing no later than fourteen days\nbefore the closing date.",
            ]
        ),
        "annexure_with_tables.docx": _docx_annexure(),
        "pricing_workbook.xlsx": _xlsx_pricing(),
        "dense_compliance_matrix.pdf": _dense_compliance_matrix_pdf(rows_per_page=35, page_count=6),
        # Valid extension, invalid content - exercises the failure path
        # without being rejected by upload validation first.
        "corrupted.docx": b"PK\x03\x04 this file claims to be a docx but is truncated garbage",
    }

    for name, content in fixtures.items():
        path = OUTPUT_DIR / name
        path.write_bytes(content)
        print(f"wrote {path.relative_to(OUTPUT_DIR.parent.parent)} ({len(content)} bytes)")

    # The "duplicate upload" fixture is deliberately a byte-identical copy of
    # an existing file under a different name - duplicate detection is keyed
    # on content hash, so a different name with identical bytes is exactly
    # the case that must be caught.
    duplicate = OUTPUT_DIR / "duplicate_of_annexure.docx"
    duplicate.write_bytes(fixtures["annexure_with_tables.docx"])
    print(f"wrote {duplicate.relative_to(OUTPUT_DIR.parent.parent)} (byte-identical copy)")


if __name__ == "__main__":
    main()
