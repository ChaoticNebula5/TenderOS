"""domain/services/pricing_checks.py against the REAL synthetic pricing
workbooks (data/synthetic/tender_a/pricing_schedule_{1,2}.xlsx) - this IS
B8B's own stop-gate assertion, not just illustrative coverage: on its
deterministic test set, every sum must be right and every seeded defect
must be found. Six seeded defect categories must each be found
exactly, and every OTHER (non-defective) row must produce zero findings -
false positives on correct data would themselves be a stop-gate failure,
not just noise."""

from pathlib import Path

from tenderos_backend.agents.commercial.parse_pricing_workbook import (
    ParsedPricingCell,
    parse_pricing_workbook,
)
from tenderos_backend.domain.services.pricing_checks import (
    LogicalRow,
    check_cross_doc_mismatch,
    check_currency_mismatch,
    check_grand_total,
    check_missing_cells,
    evaluate_row_arithmetic,
    find_duplicate_costs,
    group_rows,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "synthetic" / "tender_a"


def _load(filename: str):
    content = (FIXTURES_DIR / filename).read_bytes()
    return group_rows(parse_pricing_workbook(content))


def _all_findings_for_document(rows, grand_total) -> list:
    findings = []
    for row in rows:
        f = evaluate_row_arithmetic(row)
        if f is not None:
            findings.append(f)
    findings.extend(find_duplicate_costs(rows))
    findings.extend(check_missing_cells(rows))
    findings.extend(check_currency_mismatch(rows))
    gt = check_grand_total(rows, grand_total)
    if gt is not None:
        findings.append(gt)
    return findings


def test_sheet1_finds_exactly_its_four_seeded_defects():
    rows, grand_total = _load("pricing_schedule_1.xlsx")
    findings = _all_findings_for_document(rows, grand_total)
    by_type = [f.finding_type for f in findings]

    assert by_type.count("wrong_total") == 2, f"expected 2 wrong_total findings, got {by_type}"
    assert by_type.count("duplicate_cost") == 1, f"expected 1 duplicate_cost finding, got {by_type}"
    assert by_type.count("currency_mismatch") == 1, (
        f"expected 1 currency_mismatch finding, got {by_type}"
    )
    assert len(findings) == 4, f"unexpected extra/missing findings on sheet1: {findings}"

    wrong_total_cells = {tuple(f.cell_refs) for f in findings if f.finding_type == "wrong_total"}
    assert ("B2", "C2", "D2") in wrong_total_cells, (
        "row 2's own wrong total (unit_cost x qty) missed"
    )
    assert ("D7",) in wrong_total_cells, "the grand total's own wrong total missed"


def test_sheet2_finds_exactly_its_three_within_sheet_seeded_defects():
    rows, grand_total = _load("pricing_schedule_2.xlsx")
    findings = _all_findings_for_document(rows, grand_total)
    by_type = [f.finding_type for f in findings]

    assert by_type.count("wrong_total") == 1, f"expected 1 wrong_total finding, got {by_type}"
    assert by_type.count("needs_human_review") == 1, (
        f"expected 1 needs_human_review finding, got {by_type}"
    )
    assert by_type.count("missing_cell") == 1, f"expected 1 missing_cell finding, got {by_type}"
    assert len(findings) == 3, f"unexpected extra/missing findings on sheet2: {findings}"

    wrong_total = next(f for f in findings if f.finding_type == "wrong_total")
    assert wrong_total.cell_refs == ["B3", "C3", "D3"]
    needs_review = next(f for f in findings if f.finding_type == "needs_human_review")
    assert needs_review.cell_refs == ["D6"]
    missing = next(f for f in findings if f.finding_type == "missing_cell")
    assert missing.cell_refs == ["B4"]


def test_cross_doc_mismatch_is_found_across_both_sheets():
    rows1, _ = _load("pricing_schedule_1.xlsx")
    rows2, _ = _load("pricing_schedule_2.xlsx")

    findings = check_cross_doc_mismatch(
        [("pricing_schedule_1.xlsx", rows1), ("pricing_schedule_2.xlsx", rows2)]
    )

    assert len(findings) == 1, f"expected exactly 1 cross_doc_mismatch finding, got {findings}"
    assert findings[0].finding_type == "cross_doc_mismatch"
    assert set(findings[0].cell_refs) == {"B4", "B5"}


def test_all_six_seeded_defect_categories_are_recalled():
    """B8B's stop-gate, checked directly: every seeded defect is found, in
    each of the six categories (a wrong total, a missing cell, mismatched
    currencies, a duplicated cost, a discrepancy between sheets, an
    unsupported formula)."""
    rows1, grand_total1 = _load("pricing_schedule_1.xlsx")
    rows2, grand_total2 = _load("pricing_schedule_2.xlsx")

    all_findings = _all_findings_for_document(rows1, grand_total1)
    all_findings += _all_findings_for_document(rows2, grand_total2)
    all_findings += check_cross_doc_mismatch(
        [("pricing_schedule_1.xlsx", rows1), ("pricing_schedule_2.xlsx", rows2)]
    )

    found_types = {f.finding_type for f in all_findings}
    required = {
        "wrong_total",
        "missing_cell",
        "currency_mismatch",
        "duplicate_cost",
        "cross_doc_mismatch",
        "needs_human_review",
    }
    missing = required - found_types
    assert not missing, f"seeded defect recall < 100%: missing categories {missing}"


def test_grand_total_sum_formula_matching_the_real_row_range_is_not_flagged():
    """Regression test for an off-by-one in check_grand_total's expected-SUM
    range: it built f"=SUM(D2:D{rows[-1].row_number + 1})" - one row past the
    real last line-item row - so a genuinely correct =SUM(D2:D6)-style
    formula could never match and was always flagged needs_human_review.

    Neither real fixture exercises the SUM-match branch this bug lived in:
    sheet1's grand total is a literal wrong number, not a formula at all, and
    sheet2's own row 4 (missing unit cost) makes every row unresolvable
    before check_grand_total ever reaches the formula comparison - which is
    exactly why the existing suite passed with the bug still in place. This
    builds three rows in sheet2's own real shape (same cell refs, same
    =B{r}*C{r} formula convention, same row numbers 2-4) with every cell
    resolvable, paired with a grand-total formula spanning exactly those
    rows - =SUM(D2:D4) - to isolate and actually reach that branch.
    """
    sheet = "pricing_schedule_regression"

    def _row(row_number: int, unit_cost: float, quantity: float) -> LogicalRow:
        return LogicalRow(
            row_number=row_number,
            description=f"Line {row_number}",
            unit_cost=ParsedPricingCell(
                sheet_name=sheet,
                cell_ref=f"B{row_number}",
                row_number=row_number,
                role="unit_cost",
                description=None,
                raw_value=str(unit_cost),
                numeric_value=unit_cost,
                formula=None,
                currency="USD",
            ),
            quantity=ParsedPricingCell(
                sheet_name=sheet,
                cell_ref=f"C{row_number}",
                row_number=row_number,
                role="quantity",
                description=None,
                raw_value=str(quantity),
                numeric_value=quantity,
                formula=None,
                currency="USD",
            ),
            line_total=ParsedPricingCell(
                sheet_name=sheet,
                cell_ref=f"D{row_number}",
                row_number=row_number,
                role="line_total",
                description=None,
                raw_value=f"=B{row_number}*C{row_number}",
                numeric_value=None,
                formula=f"=B{row_number}*C{row_number}",
                currency="USD",
            ),
            currency=None,
        )

    rows = [_row(2, 100.0, 10.0), _row(3, 250.0, 4.0), _row(4, 50.0, 20.0)]
    grand_total = ParsedPricingCell(
        sheet_name=sheet,
        cell_ref="D5",
        row_number=5,
        role="grand_total",
        description=None,
        raw_value="=SUM(D2:D4)",
        numeric_value=None,
        formula="=SUM(D2:D4)",
        currency="USD",
    )

    finding = check_grand_total(rows, grand_total)
    assert finding is None, (
        f"a correct =SUM(D2:D4) formula over rows 2-4 was flagged as unsupported: {finding}"
    )


def test_correct_rows_produce_zero_findings_no_false_positives():
    """Arithmetic correctness = 100% cuts both ways: every row that was
    NOT seeded as a defect must produce no finding at all. Row 3 and row 4
    on sheet1 (Project Manager, Site Survey) are both ordinary, correct
    =B*C rows with no seeded issue."""
    rows, _ = _load("pricing_schedule_1.xlsx")
    correct_rows = [r for r in rows if r.description in ("Project Manager", "Site Survey")]
    assert len(correct_rows) == 2

    for row in correct_rows:
        finding = evaluate_row_arithmetic(row)
        assert finding is None, f"false positive on a correct row: {row.description!r} -> {finding}"
