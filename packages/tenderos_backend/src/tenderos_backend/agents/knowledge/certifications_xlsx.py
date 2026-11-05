import io
from dataclasses import dataclass
from datetime import date, datetime

from openpyxl import load_workbook

# Documented template (Build Guide G6, synthetic_tender_a.py's
# _render_certifications_xlsx): Cert Key, Employee, Certification, Issuer,
# Issue Date, Expiry Date. Certification name and the employee it belongs to
# are the only two fields a row can't be persisted without; issuer/dates are
# real-world-optional (an org might record a certification without ever
# having captured its issue date).
_REQUIRED_HEADERS = {"employee", "certification"}
_KNOWN_HEADERS = {"cert key", "employee", "certification", "issuer", "issue date", "expiry date"}


class CertificationValidationError(Exception):
    pass


@dataclass(frozen=True)
class CertificationRow:
    row_number: int
    cert_key: str | None
    employee_ref: str | None  # employee_code this certification belongs to
    name: str
    issuer: str | None
    issue_date: date | None
    expiry_date: date | None


def _parse_date_cell(raw: object, row_number: int, column: str) -> date | None:
    """A native Excel date/datetime cell arrives from openpyxl as an
    already-typed datetime.datetime (or, less often, datetime.date) object,
    not a string - openpyxl's own data_only=True evaluation returns the
    real typed value for a date-formatted cell, not its display text.
    Stringifying it first (str(a_datetime) -> "2024-01-15 00:00:00") and
    only then handing that to date.fromisoformat breaks, because
    date.fromisoformat rejects a time component - it wants exactly
    YYYY-MM-DD. Checked here, before any stringification, so a real date
    cell is read directly; a plain text cell (someone typed "2024-01-15"
    into a General-formatted column) still falls through to the ISO-string
    parse below exactly as before. datetime is checked before date since
    datetime.datetime is itself a date subclass - reversing the order would
    silently truncate every datetime's time component via the wrong branch.
    """
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    value = str(raw).strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CertificationValidationError(
            f"Row {row_number}: {column!r} is not a valid ISO date (YYYY-MM-DD): {value!r}"
        ) from exc


def parse_certifications(content: bytes) -> list[CertificationRow]:
    """Pure parsing, no DB access - same shape as employee_master.py.
    Resolving employee_ref (an employee_code) to an actual knowledge_item_id,
    and deciding whether a past expiry_date sets knowledge_items.status to
    'expired' at import time, are the import service's job, not this
    parser's - see domain/services/knowledge_service.py.
    """
    workbook = load_workbook(io.BytesIO(content), data_only=True)
    sheet = workbook.active

    header_cells = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True), ())
    headers = [str(cell).strip() if cell is not None else "" for cell in header_cells]
    normalized_headers = [header.lower() for header in headers]

    missing = _REQUIRED_HEADERS - {h for h in normalized_headers if h in _KNOWN_HEADERS}
    if missing:
        raise CertificationValidationError(
            f"Certifications template is missing required column(s): {', '.join(sorted(missing))}"
        )

    column_index = {header: idx for idx, header in enumerate(normalized_headers)}

    def _raw_cell(row: tuple, header: str) -> object:
        idx = column_index.get(header)
        if idx is None or idx >= len(row):
            return None
        return row[idx]

    def _cell(row: tuple, header: str) -> str | None:
        raw = _raw_cell(row, header)
        if raw is None:
            return None
        value = str(raw).strip()
        return value or None

    rows: list[CertificationRow] = []
    for row_number, row in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
        name = _cell(row, "certification")
        if name is None:
            continue  # blank row
        rows.append(
            CertificationRow(
                row_number=row_number,
                cert_key=_cell(row, "cert key"),
                employee_ref=_cell(row, "employee"),
                name=name,
                issuer=_cell(row, "issuer"),
                issue_date=_parse_date_cell(_raw_cell(row, "issue date"), row_number, "Issue Date"),
                expiry_date=_parse_date_cell(
                    _raw_cell(row, "expiry date"), row_number, "Expiry Date"
                ),
            )
        )
    return rows
