"""agents/knowledge/employee_master.py - pure parsing, no DB. Added
alongside B13's knowledge-import fixes; no test existed for either format
before this (KNOWN_LIMITATIONS.md: "employee-master CSV spec'd but rejected
by upload validation with no parser")."""

from tenderos_backend.agents.knowledge.employee_master import (
    EmployeeMasterValidationError,
    parse_employee_master_csv,
)


def _csv_bytes(text: str) -> bytes:
    return text.encode("utf-8")


def test_parse_employee_master_csv_parses_a_valid_csv():
    content = _csv_bytes(
        "Employee Code,Name,Role,Status\r\n"
        "EMP-01,Dana Whitfield,Senior Engineer,active\r\n"
    )

    rows = parse_employee_master_csv(content)

    assert len(rows) == 1
    assert rows[0].employee_code == "EMP-01"
    assert rows[0].name == "Dana Whitfield"
    assert rows[0].role == "Senior Engineer"
    assert rows[0].status == "active"
    assert rows[0].row_number == 2


def test_parse_employee_master_csv_strips_a_leading_bom():
    # Excel's own "Save As CSV (Comma delimited)" writes a UTF-8 BOM before
    # the first header cell - a plain utf-8 decode leaves it stuck to
    # "Employee Code", silently failing the required-Name-column check
    # instead of the BOM-corrupted header ever being visible in the error.
    content = b"\xef\xbb\xbf" + _csv_bytes("Name\r\nDana Whitfield\r\n")

    rows = parse_employee_master_csv(content)

    assert len(rows) == 1
    assert rows[0].name == "Dana Whitfield"


def test_parse_employee_master_csv_requires_name_column():
    content = _csv_bytes("Employee Code,Role,Status\r\nEMP-01,Engineer,active\r\n")

    try:
        parse_employee_master_csv(content)
        raise AssertionError("expected EmployeeMasterValidationError")
    except EmployeeMasterValidationError as exc:
        assert "name" in str(exc).lower()


def test_parse_employee_master_csv_rejects_sensitive_columns():
    content = _csv_bytes("Name,Salary\r\nDana Whitfield,100000\r\n")

    try:
        parse_employee_master_csv(content)
        raise AssertionError("expected EmployeeMasterValidationError")
    except EmployeeMasterValidationError as exc:
        assert "salary" in str(exc).lower()


def test_parse_employee_master_csv_skips_blank_rows():
    content = _csv_bytes("Name,Role\r\nDana Whitfield,Engineer\r\n,\r\nSam Okafor,Architect\r\n")

    rows = parse_employee_master_csv(content)

    assert [r.name for r in rows] == ["Dana Whitfield", "Sam Okafor"]
