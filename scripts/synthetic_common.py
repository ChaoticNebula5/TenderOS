"""Shared low-level fixture-writing helpers, used by both
make_synthetic_fixtures.py (B4) and synthetic_tender_a.py (B5) - extracted
rather than duplicated once a second script needed the same hand-built PDF
writer B4 already wrote (no PDF-writing library is in the locked dependency
set; A2 lists pdfplumber/pypdf for reading only).
"""

import hashlib
import io
import re
import zipfile
from pathlib import Path

# A fixed timestamp for everything a .docx or .xlsx would otherwise stamp
# with the time it was written. 1980-01-01 is the earliest date a zip entry
# can hold.
_FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_FIXED_W3CDTF = b"2026-01-01T00:00:00Z"
_CORE_DATES = re.compile(rb"(<dcterms:(?:created|modified)\b[^>]*>)[^<]*(</dcterms:)")


def deterministic_ooxml(content: bytes) -> bytes:
    """The same .docx/.xlsx with every write-time value fixed, so the same
    generator code gives the same bytes on every run and every platform.

    What varied between two runs (measured 2026-10-22): every zip entry's
    timestamp (python-docx and openpyxl both use ZipFile.writestr with the
    current time), and openpyxl's dcterms:created/modified in
    docProps/core.xml (it stamps `modified` at save time whatever the
    workbook says). Entries are rewritten in their original order with a
    fixed time, fixed attributes, and stored uncompressed: deflate output
    depends on the zlib build, and the platform-dependent create_system
    byte is pinned too, so a Windows run and CI's Linux run agree."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(content)) as src, zipfile.ZipFile(out, "w") as dst:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename == "docProps/core.xml":
                data = _CORE_DATES.sub(rb"\g<1>" + _FIXED_W3CDTF + rb"\g<2>", data)
            fixed = zipfile.ZipInfo(info.filename, date_time=_FIXED_ZIP_TIME)
            fixed.compress_type = zipfile.ZIP_STORED
            fixed.create_system = 3
            fixed.external_attr = 0o644 << 16
            dst.writestr(fixed, data)
    return out.getvalue()


def write_manifest(manifest_path: Path, files: list[Path], repo_root: Path) -> None:
    """A sha256sum-format manifest ("<hex>  <path>", paths relative to the
    repo root, sorted), so `sha256sum -c <manifest>` run from the repo root
    checks every listed file. LF line endings on every platform."""
    lines = []
    for path in sorted(files, key=lambda p: p.relative_to(repo_root).as_posix()):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.relative_to(repo_root).as_posix()}\n")
    manifest_path.write_bytes("".join(lines).encode("ascii"))


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
