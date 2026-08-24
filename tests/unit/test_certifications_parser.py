"""agents/knowledge/certifications_xlsx.py - pure parsing, no DB. The
native-Excel-date regression test is the actual reproduction of
KNOWN_LIMITATIONS.md's "native Excel date/datetime cells becoming strings
date.fromisoformat() can't parse" - built by writing a REAL typed date/
datetime value into an xlsx cell via openpyxl, not a string that merely
looks like one, so this fails against the pre-fix code for the real
reason (a datetime's str() carries a time component date.fromisoformat
rejects), not a stand-in."""

import io
from datetime import date, datetime

from openpyxl import Workbook
from tenderos_backend.agents.knowledge.certifications_xlsx import (
    CertificationValidationError,
    parse_certifications,
)

_HEADERS = ["Cert Key", "Employee", "Certification", "Issuer", "Issue Date", "Expiry Date"]


def _xlsx_bytes(rows: list[list]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(_HEADERS)
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_parse_certifications_handles_native_excel_date_cells():
    # Written as real datetime.datetime objects - openpyxl's own native
    # representation for a date-formatted cell, exactly what a user's real
    # "Issue Date"/"Expiry Date" columns produce when Excel itself owns the
    # cell formatting (as opposed to a cell someone typed a plain string
    # into). Pre-fix, _parse_date stringified this to "2024-01-15 00:00:00"
    # and date.fromisoformat rejected the time component.
    content = _xlsx_bytes(
        [
            [
                "CERT-01",
                "EMP-01",
                "PMP",
                "PMI",
                datetime(2024, 1, 15),
                datetime(2026, 1, 15),
            ]
        ]
    )

    rows = parse_certifications(content)

    assert len(rows) == 1
    assert rows[0].issue_date == date(2024, 1, 15)
    assert rows[0].expiry_date == date(2026, 1, 15)


def test_parse_certifications_handles_native_excel_date_only_cells():
    # openpyxl also accepts a plain datetime.date directly (not every
    # caller/library round-trips through datetime.datetime) - checked
    # separately since date is not a subclass relationship the fix can get
    # backwards without a distinct test: datetime IS a date, but date is
    # not a datetime, so an isinstance ordering bug would only surface on
    # one of these two cases, not both.
    content = _xlsx_bytes(
        [["CERT-02", "EMP-01", "AWS Certified", "AWS", date(2023, 6, 1), None]]
    )

    rows = parse_certifications(content)

    assert rows[0].issue_date == date(2023, 6, 1)
    assert rows[0].expiry_date is None


def test_parse_certifications_still_accepts_iso_string_dates():
    content = _xlsx_bytes(
        [["CERT-03", "EMP-01", "CISSP", "ISC2", "2022-03-10", "2025-03-10"]]
    )

    rows = parse_certifications(content)

    assert rows[0].issue_date == date(2022, 3, 10)
    assert rows[0].expiry_date == date(2025, 3, 10)


def test_parse_certifications_rejects_an_unparseable_date_string():
    content = _xlsx_bytes([["CERT-04", "EMP-01", "CISSP", "ISC2", "not a date", None]])

    try:
        parse_certifications(content)
        raise AssertionError("expected CertificationValidationError")
    except CertificationValidationError as exc:
        assert "Issue Date" in str(exc)


def test_parse_certifications_leaves_employee_ref_none_for_a_blank_cell():
    # Confirms the PARSER's own behavior for a blank Employee cell (already
    # correct) is None, not an empty string - the bug this pass fixes with
    # a TODO(human) is in the *import service*'s handling of that None, not
    # here.
    content = _xlsx_bytes([["CERT-05", None, "CISSP", "ISC2", None, None]])

    rows = parse_certifications(content)

    assert rows[0].employee_ref is None
