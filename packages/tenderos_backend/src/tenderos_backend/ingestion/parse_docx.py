import io
import re

import docx
from docx.table import Table
from docx.text.paragraph import Paragraph

from tenderos_backend.ingestion.chunk import ExtractedBlock

_HEADING_STYLE = re.compile(r"^Heading (\d+)$")


def parse_docx(content: bytes) -> list[ExtractedBlock]:
    """One ExtractedBlock per paragraph or table, in true document order
    (python-docx's own .paragraphs/.tables collections don't interleave -
    iter_inner_content() does, which matters for annexures where a table
    sits between two sections of prose). heading_path tracks the stack of
    "Heading N" styled paragraphs seen so far, truncated back to the current
    level on each new heading - DOCX exposes real structural headings via
    paragraph.style.name, unlike PDF.
    """
    document = docx.Document(io.BytesIO(content))
    blocks: list[ExtractedBlock] = []
    heading_stack: list[tuple[int, str]] = []

    for item in document.iter_inner_content():
        if isinstance(item, Paragraph):
            text = item.text.strip()
            if not text:
                continue

            level = _heading_level(item.style.name if item.style else "")
            if level is not None:
                heading_stack = [h for h in heading_stack if h[0] < level]
                heading_stack.append((level, text))
                continue

            blocks.append(ExtractedBlock(text=text, heading_path=[h[1] for h in heading_stack]))

        elif isinstance(item, Table):
            table_text = _table_to_text(item)
            if table_text:
                blocks.append(
                    ExtractedBlock(text=table_text, heading_path=[h[1] for h in heading_stack])
                )

    return blocks


def _heading_level(style_name: str) -> int | None:
    match = _HEADING_STYLE.match(style_name)
    return int(match.group(1)) if match else None


def _table_to_text(table: Table) -> str:
    rows = [" | ".join(cell.text.strip() for cell in row.cells) for row in table.rows]
    return "\n".join(row for row in rows if row.strip())
