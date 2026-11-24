"""Plan item 80 (PR 33): EmailHolders gives the resume review listing the
same answer as emails_held_by_others, which it replaces there, for every
employee and every email: shared emails, case and space variants, an
employee with none, one whose email is only spaces, and an employee outside
the list (its own email is then held by whoever else has it)."""

import uuid

import pytest
from tenderos_backend.db.models.knowledge import Employee
from tenderos_backend.domain.services.knowledge_service import (
    EmailHolders,
    emails_held_by_others,
)


def _employee(email: str | None) -> Employee:
    return Employee(knowledge_item_id=uuid.uuid4(), name="E", email=email)


EMPLOYEES = [
    _employee("jordan@example.test"),
    _employee(" Jordan@Example.TEST"),  # the same email as the first
    _employee("priya@example.test"),
    _employee(None),
    _employee("   "),  # normalizes to the empty string, as the duplicate check reads it
    _employee("ravi@example.test"),
]
OUTSIDE = _employee("priya@example.test")
EMAILS = [
    "jordan@example.test",
    "priya@example.test",
    "ravi@example.test",
    "",
    "nobody@example.test",
    None,
]


@pytest.mark.parametrize("employee", [*EMPLOYEES, OUTSIDE], ids=lambda e: repr(e.email))
def test_held_by_others_matches_emails_held_by_others(employee: Employee):
    expected = emails_held_by_others(EMPLOYEES, employee.knowledge_item_id)
    held = EmailHolders(EMPLOYEES).held_by_others(employee.knowledge_item_id)
    for email in EMAILS:
        assert (email in held) == (email in expected), (employee.email, email)


def test_the_cases_that_differ_from_a_plain_set():
    holders = EmailHolders(EMPLOYEES)
    jordan, jordan_again, priya = EMPLOYEES[0], EMPLOYEES[1], EMPLOYEES[2]
    # Shared: each of the two holders sees it held by the other.
    assert "jordan@example.test" in holders.held_by_others(jordan.knowledge_item_id)
    assert "jordan@example.test" in holders.held_by_others(jordan_again.knowledge_item_id)
    # One holder: not held by another for that holder, held for everyone else.
    assert "priya@example.test" not in holders.held_by_others(priya.knowledge_item_id)
    assert "priya@example.test" in holders.held_by_others(jordan.knowledge_item_id)
    # Outside the list: nothing of its own is subtracted.
    assert "priya@example.test" in holders.held_by_others(OUTSIDE.knowledge_item_id)
