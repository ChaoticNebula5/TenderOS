import io

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from tenderos_backend.ingestion.chunk import ExtractedBlock


def parse_xlsx(content: bytes) -> list[ExtractedBlock]:
    """One ExtractedBlock per non-empty sheet. Cells are read with
    data_only=True (computed results) first, falling back to the formula
    string itself when no cached value exists (e.g. a workbook never opened
    in Excel has nothing cached) - a chunk reading "Total: 50000" is far
    more useful for search/retrieval than "=SUM(B2:B10)". Deeper formula-
    aware pricing validation belongs to the commercial module, not general
    ingestion chunking.
    """
    workbook_values = load_workbook(io.BytesIO(content), data_only=True)
    workbook_formulas = load_workbook(io.BytesIO(content), data_only=False)

    blocks: list[ExtractedBlock] = []
    for sheet_name in workbook_values.sheetnames:
        values_sheet = workbook_values[sheet_name]
        formulas_sheet = workbook_formulas[sheet_name]

        rows_text = []
        for row_idx in range(1, values_sheet.max_row + 1):
            cells_text = []
            for col_idx in range(1, values_sheet.max_column + 1):
                value = values_sheet.cell(row=row_idx, column=col_idx).value
                if value is None:
                    formula = formulas_sheet.cell(row=row_idx, column=col_idx).value
                    value = formula if formula is not None else ""
                cells_text.append(str(value))
            row_text = " | ".join(cells_text).strip(" |")
            if row_text:
                rows_text.append(row_text)

        if not rows_text:
            continue

        cell_range = f"A1:{get_column_letter(values_sheet.max_column)}{values_sheet.max_row}"
        blocks.append(
            ExtractedBlock(text="\n".join(rows_text), sheet_name=sheet_name, cell_range=cell_range)
        )

    return blocks
