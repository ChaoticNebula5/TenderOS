"""Shared low-level fixture-writing helpers, used by both
make_synthetic_fixtures.py (B4) and synthetic_tender_a.py (B5) - extracted
rather than duplicated once a second script needed the same hand-built PDF
writer B4 already wrote (no PDF-writing library is in the locked dependency
set; A2 lists pdfplumber/pypdf for reading only).
"""

import io


def native_pdf(pages: list[list[str]]) -> bytes:
    """Minimal PDF with real text operators - one page per `pages` entry,
    one line per string in that entry."""
    font_num = 2
    page_nums: list[int] = []
    content_nums: list[int] = []
    page_objs: list[bytes] = []
    content_objs: list[bytes] = []
    next_num = 3

    for lines in pages:
        page_num, content_num = next_num, next_num + 1
        next_num += 2
        page_nums.append(page_num)
        content_nums.append(content_num)

        parts = [b"BT /F1 12 Tf 72 720 Td 16 TL"]
        for i, line in enumerate(lines):
            escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
            parts.append(f"({escaped}) Tj".encode() if i == 0 else f"T* ({escaped}) Tj".encode())
        parts.append(b"ET")
        stream = b"\n".join(parts)

        content_objs.append(
            f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream"
        )
        page_objs.append(
            f"<< /Type /Page /Parent 100 0 R /Resources << /Font << /F1 {font_num} 0 R >> >> "
            f"/MediaBox [0 0 612 792] /Contents {content_num} 0 R >>".encode()
        )

    kids = " ".join(f"{n} 0 R" for n in page_nums)
    objects: list[tuple[int, bytes]] = [
        (1, b"<< /Type /Catalog /Pages 100 0 R >>"),
        (font_num, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"),
        (100, f"<< /Type /Pages /Kids [{kids}] /Count {len(page_nums)} >>".encode()),
    ]
    objects.extend(zip(page_nums, page_objs, strict=True))
    objects.extend(zip(content_nums, content_objs, strict=True))
    objects.sort(key=lambda pair: pair[0])

    buf = io.BytesIO()
    buf.write(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for num, body in objects:
        offsets[num] = buf.tell()
        buf.write(f"{num} 0 obj\n".encode() + body + b"\nendobj\n")

    xref_offset = buf.tell()
    highest = max(offsets)
    buf.write(f"xref\n0 {highest + 1}\n".encode())
    buf.write(b"0000000000 65535 f \n")
    for num in range(1, highest + 1):
        buf.write(f"{offsets.get(num, 0):010d} 00000 n \n".encode())
    buf.write(
        f"trailer\n<< /Size {highest + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF".encode()
    )
    return buf.getvalue()
