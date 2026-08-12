import io
from dataclasses import dataclass

from openpyxl import load_workbook

# PRD line 774 (data minimization): "store professional bid-relevant
# information, not unrelated sensitive HR data such as salary, bank details,
# home address, medical records, government identifiers, or private
# performance notes." This is the PRD's own denylist, not an open design
# call - matched as a case-insensitive substring against each column header
# so a template deviation (someone adds a "Salary" or "SSN" column) is
# rejected at parse time rather than silently imported.
_SENSITIVE_HEADER_MARKERS = (
    "salary",
    "bank",
    "address",
    "medical",
    "ssn",
    "social security",
    "passport",
    "national id",
    "government id",
    "performance note",
    "performance review",
)

# The documented template (Build Guide G6, synthetic_tender_a.py's
# _render_employee_master_xlsx): Employee Code, Name, Role, Status. Only
# Name is genuinely required - Employee Code is used for dedup when present
# but some organizations' rosters won't have one, and Role/Status are
# free-text/enum-ish fields a reviewer can fill in later during the human
# review step (E3-style: missing data enters review, it doesn't block import).
_REQUIRED_HEADERS = {"name"}
_KNOWN_HEADERS = {"employee code", "name", "role", "status"}


class EmployeeMasterValidationError(Exception):
    pass


@dataclass(frozen=True)
class EmployeeMasterRow:
    row_number: int
    employee_code: str | None
    name: str
    role: str | None
    status: str | None


def parse_employee_master(content: bytes) -> list[EmployeeMasterRow]:
    """Pure parsing, no DB access - mirrors ingestion/parse_xlsx.py's shape
    (bytes in, structured records out). Persisting these as knowledge_items/
    employees rows, including the decision of how a template's free-text
    Status value ("active"/"removed"/...) maps to knowledge_items.status
    ('active'/'expired'/'archived'/'unverified'), is the import service's
    job, not this parser's - kept separate so this function stays a pure,
    independently testable transform.
    """
    workbook = load_workbook(io.BytesIO(content), data_only=True)
    sheet = workbook.active

    header_cells = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True), ())
    headers = [str(cell).strip() if cell is not None else "" for cell in header_cells]
    normalized_headers = [header.lower() for header in headers]

    offending = [
        header
        for header, normalized in zip(headers, normalized_headers, strict=True)
        if any(marker in normalized for marker in _SENSITIVE_HEADER_MARKERS)
    ]
    if offending:
        raise EmployeeMasterValidationError(
            f"Employee-master template contains unsupported sensitive column(s): "
            f"{', '.join(offending)}"
        )

    missing = _REQUIRED_HEADERS - {h for h in normalized_headers if h in _KNOWN_HEADERS}
    if missing:
        raise EmployeeMasterValidationError(
            f"Employee-master template is missing required column(s): {', '.join(sorted(missing))}"
        )

    column_index = {header: idx for idx, header in enumerate(normalized_headers)}

    def _cell(row: tuple, header: str) -> str | None:
        idx = column_index.get(header)
        if idx is None or idx >= len(row) or row[idx] is None:
            return None
        value = str(row[idx]).strip()
        return value or None

    rows: list[EmployeeMasterRow] = []
    for row_number, row in enumerate(
        sheet.iter_rows(min_row=2, values_only=True), start=2
    ):
        name = _cell(row, "name")
        if name is None:
            continue  # blank row - not a data-quality error, just skipped
        rows.append(
            EmployeeMasterRow(
                row_number=row_number,
                employee_code=_cell(row, "employee code"),
                name=name,
                role=_cell(row, "role"),
                status=_cell(row, "status"),
            )
        )
    return rows
