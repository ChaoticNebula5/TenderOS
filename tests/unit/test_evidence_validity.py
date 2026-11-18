"""Plan items 89 and 91: core/evidence_validity.py's one definition of "is
this evidence valid now", over the shared table of cases
(tests/evidence_validity_cases.py), plus the cases only Python has (a
missing item, the expiry date it reports, retrieval's active-and-in-date
rule, today in UTC)."""

from datetime import UTC, datetime

import pytest
from evidence_validity_cases import CASES, TODAY, YESTERDAY, Case
from tenderos_backend.core import evidence_validity as ev


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_each_case(case: Case):
    got = ev.evidence_validity(
        status=case.status,
        valid_until=case.valid_until,
        today=TODAY,
        is_employee=case.is_employee,
        profile_review_status=case.profile_review_status,
    )
    assert (got.validity_status, got.verified, got.reason, got.counts) == (
        case.label,
        case.verified,
        case.reason,
        case.counts,
    )


def test_a_missing_item_is_unknown_and_does_not_count():
    got = ev.evidence_validity(status=None, valid_until=None, today=TODAY)
    assert (got.validity_status, got.verified, got.reason, got.counts) == (
        "unknown",
        False,
        "missing",
        False,
    )


def test_a_passed_date_is_reported_and_a_status_alone_reports_none():
    lapsed = ev.evidence_validity(status="active", valid_until=YESTERDAY, today=TODAY)
    by_status = ev.evidence_validity(status="expired", valid_until=None, today=TODAY)
    assert lapsed.expired_on == YESTERDAY
    assert by_status.expired_on is None


@pytest.mark.parametrize(
    ("status", "valid_until", "active_in_date"),
    [
        ("active", None, True),
        ("active", TODAY, True),
        ("active", YESTERDAY, False),
        ("unverified", None, False),
        ("expired", None, False),
        ("archived", None, False),
    ],
)
def test_retrievals_rule_is_active_and_in_date(status, valid_until, active_in_date):
    got = ev.evidence_validity(status=status, valid_until=valid_until, today=TODAY)
    assert got.active_in_date is active_in_date


def test_an_unverified_employee_profile_is_still_active_and_in_date():
    """Retrieval offers it; sufficiency then rejects it as unverified."""
    got = ev.evidence_validity(
        status="active",
        valid_until=None,
        today=TODAY,
        is_employee=True,
        profile_review_status="needs_review",
    )
    assert got.active_in_date and not got.counts


def test_today_is_the_utc_date(monkeypatch):
    class _Clock:
        @staticmethod
        def now(tz=None):
            assert tz is UTC
            return datetime(2026, 11, 18, 23, 30, tzinfo=UTC)

    monkeypatch.setattr(ev, "datetime", _Clock)
    assert ev.today_utc() == TODAY
