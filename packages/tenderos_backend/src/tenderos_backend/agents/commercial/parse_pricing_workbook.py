import io
from dataclasses import dataclass

from openpyxl import load_workbook

# The documented template (Build Guide B8B, synthetic_tender_a.py's
# _render_pricing_xlsx): Item, Unit Cost, Quantity, Line Total, Currency.
# Currency is the one optional column - older/simpler pricing workbooks may
# not carry it at all, in which case currency-mismatch checking simply has
# nothing to compare.
_REQUIRED_HEADERS = {"item", "unit cost", "quantity", "line total"}


class PricingWorkbookValidationError(Exception):
    pass


@dataclass(frozen=True)
class ParsedPricingCell:
    """One persisted pricing_line_items row's worth of data - deliberately
    per-CELL, not per-logical-row: E6's own example finding cites multiple
    individual cell_refs together (["F18", "F24"]), which only makes sense
    if each priced fact (a unit cost, a quantity, a line total, a currency)
    is its own addressable, individually-citable row. `row_number` is
    carried explicitly (not left for a caller to re-parse out of cell_ref)
    so pricing_checks.py can regroup these back into logical rows directly.
    """

    sheet_name: str
    cell_ref: str
    row_number: int
    role: str  # "unit_cost" / "quantity" / "line_total" / "currency" / "grand_total"
    description: str | None
    raw_value: str | None
    numeric_value: float | None
    formula: str | None
    currency: str | None


def _try_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(str(value))
    except ValueError:
        return None


def _is_formula(value: object) -> bool:
    return isinstance(value, str) and value.startswith("=")


def parse_pricing_workbook(content: bytes) -> list[ParsedPricingCell]:
    """Pure parsing, no DB access - mirrors agents/knowledge/employee_master.py's
    shape (bytes in, structured records out). Reads BOTH the formula view
    (data_only=False) and the cached-value view (data_only=True) of every
    cell: openpyxl never computes a formula it did not itself read a cached
    result for, so a workbook this project generated (or any workbook never
    re-opened in real Excel since its last edit) has NO cached value behind
    any formula cell at all - data_only=True legitimately returns None for
    those, which is why raw_value/numeric_value fall back to the formula
    string rather than a computed number whenever a formula is present.
    """
    values_wb = load_workbook(io.BytesIO(content), data_only=True)
    formulas_wb = load_workbook(io.BytesIO(content), data_only=False)

    cells: list[ParsedPricingCell] = []
    for sheet_name in values_wb.sheetnames:
        values_sheet = values_wb[sheet_name]
        formulas_sheet = formulas_wb[sheet_name]

        header_cols: dict[str, int] = {}
        for col_idx in range(1, values_sheet.max_column + 1):
            header = values_sheet.cell(row=1, column=col_idx).value
            if header:
                header_cols[str(header).strip().lower()] = col_idx

        missing = _REQUIRED_HEADERS - header_cols.keys()
        if missing:
            raise PricingWorkbookValidationError(
                f"sheet {sheet_name!r} is missing required column(s): {sorted(missing)}"
            )
        currency_col = header_cols.get("currency")

        item_col = header_cols["item"]
        cost_col = header_cols["unit cost"]
        qty_col = header_cols["quantity"]
        total_col = header_cols["line total"]

        for row_idx in range(2, values_sheet.max_row + 1):
            item_value = values_sheet.cell(row=row_idx, column=item_col).value
            if item_value is None:
                continue
            description = str(item_value).strip()
            is_grand_total = description.lower() == "total"

            def _cell_record(col_idx: int, role: str, cell_currency: str | None) -> ParsedPricingCell:
                col_letter = values_sheet.cell(row=row_idx, column=col_idx).column_letter
                cell_ref = f"{col_letter}{row_idx}"
                # data_only=True's own cached_value is never itself a
                # formula string (openpyxl either resolved a real cached
                # result there or returns None) - only the formulas_sheet
                # view (data_only=False) ever shows the raw "=..." text.
                cached_value = values_sheet.cell(row=row_idx, column=col_idx).value
                raw_cell = formulas_sheet.cell(row=row_idx, column=col_idx).value
                formula = raw_cell if _is_formula(raw_cell) else None
                numeric_value = _try_float(cached_value)
                raw_value = None if cached_value is None else str(cached_value)
                if raw_value is None and formula is not None:
                    raw_value = formula
                return ParsedPricingCell(
                    sheet_name=sheet_name,
                    cell_ref=cell_ref,
                    row_number=row_idx,
                    role=role,
                    description=description,
                    raw_value=raw_value,
                    numeric_value=numeric_value,
                    formula=formula,
                    currency=cell_currency,
                )

            row_currency = None
            if currency_col is not None:
                currency_value = values_sheet.cell(row=row_idx, column=currency_col).value
                row_currency = str(currency_value).strip() if currency_value else None

            if is_grand_total:
                cells.append(_cell_record(total_col, "grand_total", row_currency))
                continue

            cells.append(_cell_record(cost_col, "unit_cost", row_currency))
            cells.append(_cell_record(qty_col, "quantity", row_currency))
            cells.append(_cell_record(total_col, "line_total", row_currency))
            if currency_col is not None:
                cells.append(_cell_record(currency_col, "currency", row_currency))

    return cells
