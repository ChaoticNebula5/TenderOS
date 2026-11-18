"""One definition of "may this knowledge item back a decision or a claim now"
(Phase 4 plan items 89 and 91, decision 0111).

Every evidence reader judges a knowledge item through `evidence_validity`
(or its Cypher twin, `FACT_COUNTS_CYPHER`), at the time it runs, from the
item's current row:
- `archived` is withdrawn (label `invalid`);
- a status of `expired`, or a `valid_until` before today, is expired (label
  `expired`). The certification import writes `expired` only when the date
  had already passed at import and nothing updates it later, so the date is
  what says a certificate has lapsed;
- otherwise the label is `valid`, and the item is verified only if its
  status is `active` and, for an employee, its profile is verified. So
  `unverified` (set only by hand) is valid but not verified (user decision,
  Q1), and an employee's profile is read live too (Q2).
An item counts only when it is verified and its label is `valid`.

Nothing is stored or bumped when a date passes: `valid_until` does not change,
only today does, and every reader compares it with today when it runs (the
graph keeps `valid_until` on its fact nodes and compares it with `$today`).

"Today" is the UTC date (`today_utc`): a certificate counts through its
`valid_until` day and lapses at 00:00 UTC the day after (05:30 IST). Each
evaluation takes today once and passes it down, so one report or one pass is
consistent with itself.

The roster readers (selection, readiness's removed-employee blocker, the red
team's archived-employee check, the resource gate's count) ask a different
question, "removed from the roster", and keep
`KnowledgeRepository.archived_item_ids` (Q4).

Evidence with no knowledge item (a document excerpt) has nothing to judge
here; its readers keep the link's stored values.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime

VALID = "valid"
INVALID = "invalid"
EXPIRED = "expired"
UNKNOWN = "unknown"

# Why an item does not count now (None when it counts).
REASON_MISSING = "missing"
REASON_EXPIRED = "expired"
REASON_WITHDRAWN = "withdrawn"
REASON_UNVERIFIED = "unverified"


@dataclass(frozen=True)
class ItemValidity:
    validity_status: str  # one of the link labels: valid, invalid, expired, unknown
    verified: bool
    reason: str | None
    # Whether the item's status is `active` and its date has not passed: the
    # rule retrieval's valid_only filter applies (an unverified profile is
    # still offered to the model; sufficiency then rejects it).
    active_in_date: bool
    title: str | None = None
    # The item's valid_until, when it is the passed date that expired it.
    expired_on: date | None = None

    @property
    def counts(self) -> bool:
        return self.verified and self.validity_status == VALID


def today_utc() -> date:
    return datetime.now(UTC).date()


def evidence_validity(
    *,
    status: str | None,
    valid_until: date | None,
    today: date,
    is_employee: bool = False,
    profile_review_status: str | None = None,
    title: str | None = None,
) -> ItemValidity:
    """The validity of one knowledge item now. `status` None means the item
    does not exist (a deleted row)."""
    if status is None:
        return ItemValidity(UNKNOWN, False, REASON_MISSING, active_in_date=False, title=title)
    date_passed = valid_until is not None and valid_until < today
    # A passed date first, then archived, then an expired status: the order
    # compliance's fresh signals have always used.
    if date_passed:
        label, reason = EXPIRED, REASON_EXPIRED
    elif status == "archived":
        label, reason = INVALID, REASON_WITHDRAWN
    elif status == "expired":
        label, reason = EXPIRED, REASON_EXPIRED
    else:
        label, reason = VALID, None
    verified = status == "active" and (not is_employee or profile_review_status == "verified")
    if reason is None and not verified:
        reason = REASON_UNVERIFIED
    return ItemValidity(
        validity_status=label,
        verified=verified,
        reason=reason,
        active_in_date=status == "active" and not date_passed,
        title=title,
        expired_on=valid_until if date_passed else None,
    )


# The same rule in Cypher, for a fact node bound as `fact` and today passed
# as `$today`: true exactly when evidence_validity(...).counts is, over the
# fact node's projected status, valid_until and (on an Employee node)
# profile_review_status. tests/integration/test_evidence_validity_parity.py
# runs both over the same table of cases. coalesce: a missing property makes
# the comparison null, which must read as "does not count".
FACT_COUNTS_CYPHER = (
    "coalesce(fact.status = 'active' "
    "AND (fact.valid_until IS NULL OR fact.valid_until >= $today) "
    "AND (NOT fact:Employee OR fact.profile_review_status = 'verified'), false)"
)

# Evidence with no fact node (a document excerpt, or an item type the graph
# does not project) keeps the Evidence node's stored copy of the link.
STORED_EVIDENCE_COUNTS_CYPHER = (
    "coalesce(evidence.verified = true AND evidence.validity_status = 'valid', false)"
)
