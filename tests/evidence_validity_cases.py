"""The table of cases for core/evidence_validity.py (plan items 89 and 91),
shared by tests/unit/test_evidence_validity.py (the Python function) and
tests/integration/test_evidence_validity_parity.py (its Cypher twin, over the
same rows on real Neo4j nodes), so the two definitions are held to the same
answers.

Each case: an id, the item as stored (status, valid_until, whether it is an
employee, the employee's profile_review_status), and the expected result
(validity_status label, verified, reason, counts). TODAY is fixed, so the
date cases are relative to it."""

from dataclasses import dataclass
from datetime import date, timedelta

TODAY = date(2026, 11, 18)
YESTERDAY = TODAY - timedelta(days=1)
TOMORROW = TODAY + timedelta(days=1)


@dataclass(frozen=True)
class Case:
    id: str
    status: str | None
    valid_until: date | None
    is_employee: bool
    profile_review_status: str | None
    label: str
    verified: bool
    reason: str | None
    counts: bool


CASES = [
    Case("active, no date", "active", None, False, None, "valid", True, None, True),
    Case("active, in date", "active", TOMORROW, False, None, "valid", True, None, True),
    Case("active, its last day", "active", TODAY, False, None, "valid", True, None, True),
    Case(
        "active, date passed", "active", YESTERDAY, False, None, "expired", True, "expired", False
    ),
    Case(
        "expired status, no date", "expired", None, False, None, "expired", False, "expired", False
    ),
    Case(
        "expired status, in date",
        "expired",
        TOMORROW,
        False,
        None,
        "expired",
        False,
        "expired",
        False,
    ),
    Case("archived", "archived", None, False, None, "invalid", False, "withdrawn", False),
    Case(
        "archived, date passed",
        "archived",
        YESTERDAY,
        False,
        None,
        "expired",
        False,
        "expired",
        False,
    ),
    Case("unverified", "unverified", None, False, None, "valid", False, "unverified", False),
    Case("employee, profile verified", "active", None, True, "verified", "valid", True, None, True),
    Case(
        "employee, needs review",
        "active",
        None,
        True,
        "needs_review",
        "valid",
        False,
        "unverified",
        False,
    ),
    Case("employee, stale", "active", None, True, "stale", "valid", False, "unverified", False),
    Case(
        "employee, no profile row", "active", None, True, None, "valid", False, "unverified", False
    ),
    Case(
        "employee archived, verified",
        "archived",
        None,
        True,
        "verified",
        "invalid",
        False,
        "withdrawn",
        False,
    ),
    Case(
        "employee unverified status, verified profile",
        "unverified",
        None,
        True,
        "verified",
        "valid",
        False,
        "unverified",
        False,
    ),
]
