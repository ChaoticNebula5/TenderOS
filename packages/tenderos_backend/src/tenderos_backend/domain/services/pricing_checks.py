from dataclasses import dataclass

from tenderos_backend.agents.commercial.parse_pricing_workbook import ParsedPricingCell
from tenderos_backend.core.hashing import sha256_hex

# Build Guide's own denominator for B8B's stop-gate: "arithmetic
# correctness = 100%, seeded defect recall = 100%, for the supported
# deterministic test set." A row whose Line Total formula isn't the simple,
# recognizable =B{row}*C{row} shape is explicitly OUTSIDE that supported
# set - it must fall to needs_human_review, never be silently accepted or
# guessed at as wrong.
_SUPPORTED_MULTIPLY_TOLERANCE = 0.01


@dataclass(frozen=True)
class LogicalRow:
    """One reconstructed pricing-sheet row, grouped back from
    parse_pricing_workbook's per-cell ParsedPricingCell records by
    row_number - what every check function below actually reasons about,
    since "is this line's total right" is a question about a row, not a
    single cell in isolation.
    """

    row_number: int
    description: str
    unit_cost: ParsedPricingCell | None
    quantity: ParsedPricingCell | None
    line_total: ParsedPricingCell | None
    currency: ParsedPricingCell | None


@dataclass(frozen=True)
class PricingFindingDraft:
    """The deterministic engine's own output - no LLM involved yet. This is
    the "arithmetic engine" Build Guide requires be code, not a model: every
    field here is either read directly from parsed cells or computed by
    plain arithmetic, and explain_findings.py is never allowed to alter any
    of them, only to explain and classify severity for what's already here.
    """

    finding_type: str
    cell_refs: list[str]
    expected_value: float | None
    actual_value: float | None
    delta: float | None
    note: str
    default_severity: str
    # Populated only for cross_doc_mismatch - pricing_findings.item_a_ref/
    # item_b_ref (Part C, JSONB) exist to disambiguate WHICH pricing_
    # document each side of a finding came from, which cell_refs alone
    # can't do once a finding spans more than one document. Every other
    # finding type (including duplicate_cost, which only ever compares
    # rows within one document) stays within a single pricing_document_id,
    # so cell_refs alone is already unambiguous and these stay None.
    item_a_ref: dict | None = None
    item_b_ref: dict | None = None


def group_rows(cells: list[ParsedPricingCell]) -> tuple[list[LogicalRow], ParsedPricingCell | None]:
    """Regroups parse_pricing_workbook's flat per-cell list back into
    logical rows plus the sheet's own grand-total cell (role="grand_total",
    which has no sibling unit_cost/quantity/currency cells of its own).
    """
    rows_by_number: dict[int, dict[str, ParsedPricingCell]] = {}
    grand_total: ParsedPricingCell | None = None
    for cell in cells:
        if cell.role == "grand_total":
            grand_total = cell
            continue
        rows_by_number.setdefault(cell.row_number, {})[cell.role] = cell

    rows = [
        LogicalRow(
            row_number=row_number,
            description=next(iter(by_role.values())).description or "",
            unit_cost=by_role.get("unit_cost"),
            quantity=by_role.get("quantity"),
            line_total=by_role.get("line_total"),
            currency=by_role.get("currency"),
        )
        for row_number, by_role in sorted(rows_by_number.items())
    ]
    return rows, grand_total


def _fingerprint(*parts: str) -> str:
    return sha256_hex("|".join(parts).encode("utf-8"))


# ----------------------------------------------------------------------
# missing_cell - purely mechanical, no policy call: a priced line item
# with no unit cost is definitionally incomplete.
# ----------------------------------------------------------------------


def check_missing_cells(rows: list[LogicalRow]) -> list[PricingFindingDraft]:
    findings = []
    for row in rows:
        if row.unit_cost is not None and row.unit_cost.numeric_value is None:
            findings.append(
                PricingFindingDraft(
                    finding_type="missing_cell",
                    cell_refs=[row.unit_cost.cell_ref],
                    expected_value=None,
                    actual_value=None,
                    delta=None,
                    note=f"{row.description!r} has no unit cost recorded.",
                    default_severity="major",
                )
            )
    return findings


# ----------------------------------------------------------------------
# currency_mismatch - the sheet's own dominant currency (the mode across
# all rows) is the reference point; any row disagreeing with it is flagged.
# Purely mechanical (a plain equality check) - no tolerance or shape
# judgment involved, unlike the arithmetic check below.
# ----------------------------------------------------------------------


def check_currency_mismatch(rows: list[LogicalRow]) -> list[PricingFindingDraft]:
    currencies = [row.currency.raw_value for row in rows if row.currency and row.currency.raw_value]
    if not currencies:
        return []
    dominant = max(set(currencies), key=currencies.count)

    findings = []
    for row in rows:
        if row.currency is None or row.currency.raw_value is None:
            continue
        if row.currency.raw_value != dominant:
            findings.append(
                PricingFindingDraft(
                    finding_type="currency_mismatch",
                    cell_refs=[row.currency.cell_ref],
                    expected_value=None,
                    actual_value=None,
                    delta=None,
                    note=(
                        f"{row.description!r} is priced in {row.currency.raw_value!r}, "
                        f"but the sheet's other line items use {dominant!r}."
                    ),
                    default_severity="critical",
                )
            )
    return findings


# ----------------------------------------------------------------------
# TODO(human): implement evaluate_row_arithmetic and find_duplicate_costs.
#
# This is B8B's real judgment call - the arithmetic engine Build Guide
# insists must be code, not a model, and the exact reason B8B's stop-gate
# is a hard 100%/100% bar rather than a soft target like B5/B6's recall
# gates. Two related functions, both genuinely undecided until you write
# them:
#
# evaluate_row_arithmetic(row: LogicalRow) -> PricingFindingDraft | None
#   Steps (plain-English, not code):
#   1. If row.unit_cost is None, row.quantity is None, or either has
#      numeric_value is None - there's nothing to verify here (check_
#      missing_cells above already flags the missing-cell case on its own;
#      don't double-report it as a wrong_total too).
#   2. If row.line_total.formula is not None: decide what counts as
#      "supported". The fixture's normal rows use exactly
#      f"=B{row.row_number}*C{row.row_number}" - is that literal string
#      match the bar, or something looser (e.g. any formula that only
#      references this row's own unit_cost/quantity cells, regardless of
#      exact syntax)? A formula that doesn't meet your bar can't be
#      verified - return a PricingFindingDraft with finding_type=
#      "needs_human_review", cell_refs=[row.line_total.cell_ref],
#      expected_value=None (you don't know what it should be), actual_value
#      =None, delta=None, and a note explaining WHY it couldn't be
#      recomputed (cite the actual formula text). A formula that DOES meet
#      your bar needs no finding at all (return None) - it's the working
#      source of truth, just never cached by openpyxl.
#   3. If row.line_total.formula is None (a literal number sits where a
#      formula normally would): this is the "wrong total" case IF it
#      differs from unit_cost x quantity. Decide the tolerance - exact
#      equality, or an epsilon for float rounding (a suggested starting
#      point is already declared above as
#      _SUPPORTED_MULTIPLY_TOLERANCE, but confirm or change it, and decide
#      whether it's an absolute or relative tolerance). Within tolerance ->
#      None. Outside it -> a "wrong_total" PricingFindingDraft with
#      expected_value = unit_cost x quantity, actual_value = the literal
#      value, delta = actual - expected, cell_refs = the three involved
#      cells (unit_cost, quantity, line_total).
#
# find_duplicate_costs(rows: list[LogicalRow]) -> list[PricingFindingDraft]
#   Decide what makes two rows "duplicates" - this is the real design
#   question, not a lookup:
#     - Exact description match (case/whitespace-normalized) AND the same
#       unit_cost AND the same quantity? Or is same description + same
#       unit_cost alone enough, regardless of quantity?
#     - What if descriptions differ slightly ("Senior Engineer" vs "Sr.
#       Engineer") but everything else matches - still worth flagging, or
#       out of scope (same non-fuzzy-matching boundary this codebase
#       already accepts elsewhere, e.g. KNOWN_LIMITATIONS.md's employee-
#       name-matching entry)?
#   For each duplicate pair found, return ONE "duplicate_cost"
#   PricingFindingDraft citing both rows' line_total cell_refs (cell_refs=
#   [row_a.line_total.cell_ref, row_b.line_total.cell_ref]), expected_value/
#   actual_value/delta=None (there's no single "correct" number here, just
#   two rows that shouldn't both exist), and a note naming both
#   descriptions.
#
# What's already available to use:
#   - `LogicalRow` (defined above) - .row_number, .description, .unit_cost/
#     .quantity/.line_total/.currency (each a ParsedPricingCell | None,
#     with .cell_ref, .numeric_value, .formula, .raw_value).
#   - `PricingFindingDraft` (defined above) - the return shape both
#     functions must produce.
#   - `_SUPPORTED_MULTIPLY_TOLERANCE` - a starting tolerance value, yours to
#     keep or change.
#
# Why this is worth writing by hand: get the formula-shape bar too loose
# and a genuinely wrong total gets waved through as "supported, no finding"
# - a real stop-gate failure (arithmetic correctness < 100%). Get it too
# strict and every ordinary =B*C row becomes a needs_human_review finding,
# which technically satisfies the letter of the stop-gate (nothing is
# silently wrong) but defeats the entire point of automating commercial
# validation. There is no lookup table that resolves this tension - it's
# the actual judgment call PRD 10.10 delegates to code instead of the LLM
# precisely because it has to be gotten right, not approximated.
def evaluate_row_arithmetic(row: LogicalRow) -> PricingFindingDraft | None:
    if row.unit_cost is None or row.quantity is None or row.line_total is None:
        return None
    if row.unit_cost.numeric_value is None or row.quantity.numeric_value is None:
        return None
    
    if row.line_total.formula is not None:
        expected_formula = f"={row.unit_cost.cell_ref}*{row.quantity.cell_ref}"
        normalised_formula = "".join(expected_formula.split()).casefold()
        
        actual = row.line_total.formula
        normalised_actual = "".join(actual.split()).casefold()
        
        if normalised_actual == normalised_formula:
            return None
        
        return PricingFindingDraft(
            finding_type="needs_human_review",
            cell_refs=[row.line_total.cell_ref],
            expected_value=None,
            actual_value=None,
            delta=None,
            note=f"Unsupported line-total formula {actual!r}; it could not be recomputed safely.",
            default_severity="major",
        )
    if row.line_total.numeric_value is not None:
        expected_value = (
            row.unit_cost.numeric_value * row.quantity.numeric_value
        )
        actual_value = row.line_total.numeric_value
        delta = actual_value - expected_value

        if abs(delta) <= _SUPPORTED_MULTIPLY_TOLERANCE:
            return None

        return PricingFindingDraft(
            finding_type="wrong_total",
            cell_refs=[
                row.unit_cost.cell_ref,
                row.quantity.cell_ref,
                row.line_total.cell_ref,
            ],
            expected_value=expected_value,
            actual_value=actual_value,
            delta=delta,
            note=(
                f"{row.description!r} has line total {actual_value}, "
                f"but unit cost × quantity gives {expected_value}."
            ),
            default_severity="critical",
        )

    return None


def check_grand_total(
    rows: list[LogicalRow], grand_total: ParsedPricingCell | None
) -> PricingFindingDraft | None:
    """The sheet-level sibling of evaluate_row_arithmetic - same formula-
    shape-recognition and tolerance policy (reuses _SUPPORTED_MULTIPLY_
    TOLERANCE, not a second, independently-chosen threshold), just applied
    to "sum of line totals" instead of "unit cost x quantity". Written
    directly rather than scaffolded as its own TODO(human): the real
    judgment call already got made once, in evaluate_row_arithmetic - the
    grand total is the same question at a different granularity, not a
    fresh design decision (same "already established and reviewed"
    reasoning agents/knowledge/relationships.py's resolve_demonstrates_
    verification used for its own sibling-of-an-already-decided-pattern
    case).

    The expected sum is built from each row's own DISPLAYED line total for
    literal rows (whatever number is actually written in the cell, even if
    evaluate_row_arithmetic separately flagged it as wrong - a real SUM
    formula sums what's shown, not a hypothetically corrected version, so a
    wrong row total and a wrong grand total stay two independent findings
    rather than one compounding into the other) and from the recomputed
    unit_cost x quantity for supported-formula rows (there is nothing
    "displayed" to sum there at all - openpyxl never caches a value for a
    formula it didn't itself evaluate). A row whose own total isn't
    resolvable at all (an unsupported formula) makes the grand total itself
    unverifiable too - this returns None rather than a false wrong_total
    built from an incomplete sum.
    """
    if grand_total is None:
        return None

    line_total_values: list[float] = []
    for row in rows:
        if row.line_total is None:
            return None
        if row.line_total.formula is not None:
            expected_formula = (
                f"={row.unit_cost.cell_ref}*{row.quantity.cell_ref}"
                if row.unit_cost and row.quantity
                else None
            )
            normalised_actual = "".join(row.line_total.formula.split()).casefold()
            normalised_expected = (
                "".join(expected_formula.split()).casefold() if expected_formula else None
            )
            if normalised_expected is None or normalised_actual != normalised_expected:
                return None  # an unresolved row makes the grand total unverifiable too
            if row.unit_cost is None or row.quantity is None:
                return None
            if row.unit_cost.numeric_value is None or row.quantity.numeric_value is None:
                return None
            line_total_values.append(row.unit_cost.numeric_value * row.quantity.numeric_value)
        elif row.line_total.numeric_value is not None:
            line_total_values.append(row.line_total.numeric_value)
        else:
            return None

    expected_sum = sum(line_total_values)

    if grand_total.formula is not None:
        expected_formula = f"=SUM(D2:D{rows[-1].row_number + 1})" if rows else None
        normalised_actual = "".join(grand_total.formula.split()).casefold()
        normalised_expected = (
            "".join(expected_formula.split()).casefold() if expected_formula else None
        )
        if normalised_expected is not None and normalised_actual == normalised_expected:
            return None
        return PricingFindingDraft(
            finding_type="needs_human_review",
            cell_refs=[grand_total.cell_ref],
            expected_value=None,
            actual_value=None,
            delta=None,
            note=(
                f"Unsupported grand-total formula {grand_total.formula!r}; "
                "it could not be recomputed safely."
            ),
            default_severity="major",
        )

    if grand_total.numeric_value is None:
        return None

    delta = grand_total.numeric_value - expected_sum
    if abs(delta) <= _SUPPORTED_MULTIPLY_TOLERANCE:
        return None

    return PricingFindingDraft(
        finding_type="wrong_total",
        cell_refs=[grand_total.cell_ref],
        expected_value=expected_sum,
        actual_value=grand_total.numeric_value,
        delta=delta,
        note=(
            f"Grand total is {grand_total.numeric_value}, "
            f"but the line items sum to {expected_sum}."
        ),
        default_severity="critical",
    )


def find_duplicate_costs(rows: list[LogicalRow]) -> list[PricingFindingDraft]:
    findings: list[PricingFindingDraft] = []
    
    for index, row_a in enumerate(rows):
        for row_b in rows[index + 1:]:
            row_a_unit_cost = row_a.unit_cost
            row_a_quantity = row_a.quantity
            row_a_line_total = row_a.line_total

            row_b_unit_cost = row_b.unit_cost
            row_b_quantity = row_b.quantity
            row_b_line_total = row_b.line_total

            if (
                row_a_unit_cost is None
                or row_a_quantity is None
                or row_a_line_total is None
                or row_b_unit_cost is None
                or row_b_quantity is None
                or row_b_line_total is None
            ):
                continue
            
            if (
                row_a_unit_cost.numeric_value is None
                or row_a_quantity.numeric_value is None
                or row_b_unit_cost.numeric_value is None
                or row_b_quantity.numeric_value is None
            ):
                continue

            normalized_description_a = row_a.description.strip().casefold()
            normalized_description_b = row_b.description.strip().casefold()

            if (
                not normalized_description_a
                or not normalized_description_b
                or normalized_description_a != normalized_description_b
            ):
                continue
            
            if (
                row_a_unit_cost.numeric_value != row_b_unit_cost.numeric_value
                or row_a_quantity.numeric_value != row_b_quantity.numeric_value
            ):
                continue
            
            findings.append(
                PricingFindingDraft(
                    finding_type="duplicate_cost",
                    cell_refs=[
                        row_a_line_total.cell_ref,
                        row_b_line_total.cell_ref,
                    ],
                    expected_value=None,
                    actual_value=None,
                    delta=None,
                    note=(
                        f"{row_a.description!r} and {row_b.description!r} "
                        "have matching unit cost and quantity."
                    ),
                    default_severity="major",
                )
            )
    return findings
            


# ----------------------------------------------------------------------
# cross_doc_mismatch - across every pricing_document in the tender, not
# just one sheet. Same normalized-description matching find_duplicate_costs
# uses within one sheet, applied across sheets instead.
# ----------------------------------------------------------------------


def check_cross_doc_mismatch(
    documents: list[tuple[str, list[LogicalRow]]],
) -> list[PricingFindingDraft]:
    """`documents` is [(pricing_document_id_str, rows), ...] for every
    pricing document in the tender. Flags a description appearing in more
    than one document with a different unit_cost - deliberately checked
    here, not inside find_duplicate_costs, since a name match across two
    DIFFERENT documents is evidence of a cross-document pricing
    inconsistency (Build Guide: "cross-sheet discrepancy"), not the same
    "these are probably the same line item, accidentally repeated" question
    a within-document duplicate raises.
    """
    seen: dict[str, tuple[str, LogicalRow]] = {}
    findings = []
    for doc_id, rows in documents:
        for row in rows:
            if row.unit_cost is None or row.unit_cost.numeric_value is None:
                continue
            key = row.description.strip().casefold()
            if key in seen:
                other_doc_id, other_row = seen[key]
                if (
                    other_doc_id != doc_id
                    and other_row.unit_cost.numeric_value != row.unit_cost.numeric_value
                ):
                    findings.append(
                        PricingFindingDraft(
                            finding_type="cross_doc_mismatch",
                            cell_refs=[other_row.unit_cost.cell_ref, row.unit_cost.cell_ref],
                            expected_value=other_row.unit_cost.numeric_value,
                            actual_value=row.unit_cost.numeric_value,
                            delta=row.unit_cost.numeric_value - other_row.unit_cost.numeric_value,
                            note=(
                                f"{row.description!r} is priced at "
                                f"{row.unit_cost.numeric_value} in pricing document {doc_id}, "
                                f"but {other_row.unit_cost.numeric_value} in "
                                f"pricing document {other_doc_id}."
                            ),
                            default_severity="critical",
                            item_a_ref={
                                "pricing_document_id": other_doc_id,
                                "cell_ref": other_row.unit_cost.cell_ref,
                            },
                            item_b_ref={
                                "pricing_document_id": doc_id,
                                "cell_ref": row.unit_cost.cell_ref,
                            },
                        )
                    )
                    continue
            seen[key] = (doc_id, row)
    return findings


def finding_fingerprint(tender_id: str, finding: PricingFindingDraft) -> str:
    return _fingerprint(tender_id, finding.finding_type, ",".join(sorted(finding.cell_refs)))
