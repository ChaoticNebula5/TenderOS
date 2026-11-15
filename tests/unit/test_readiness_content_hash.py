"""readiness_service.report_content_hash (plan item 93): what makes two
reports at one version the same report. Canonical JSON over ready, the
blockers sorted on a fixed key and the counts; no volatile field."""

import uuid

from tenderos_backend.domain.services.readiness_service import report_content_hash


def _blocker(category: str, target_id: str | None, message: str) -> dict:
    return {
        "category": category,
        "target_type": "tender",
        "target_id": target_id,
        "message": message,
    }


_BLOCKERS = [
    _blocker("stale_graph_projection", str(uuid.UUID(int=1)), "Graph projection is stale."),
    _blocker("critical_redteam_finding", str(uuid.UUID(int=2)), "Unresolved critical: x."),
    _blocker("critical_redteam_finding", str(uuid.UUID(int=3)), "Unresolved critical: y."),
    _blocker("red_team_review_incomplete", None, "The most recent red-team pass failed."),
]
_COUNTS = {"requirements_total": 3, "resource_gaps": 0, "open_clarifications": 1}


def test_the_same_content_hashes_the_same():
    assert report_content_hash(False, _BLOCKERS, _COUNTS) == report_content_hash(
        False, [dict(b) for b in _BLOCKERS], dict(_COUNTS)
    )


def test_blocker_order_and_key_order_are_not_content():
    reordered_blockers = list(reversed(_BLOCKERS))
    reordered_keys = dict(reversed(list(_COUNTS.items())))
    assert report_content_hash(False, _BLOCKERS, _COUNTS) == report_content_hash(
        False, reordered_blockers, reordered_keys
    )


def test_each_field_is_content():
    base = report_content_hash(False, _BLOCKERS, _COUNTS)
    assert report_content_hash(True, _BLOCKERS, _COUNTS) != base, "ready"
    assert report_content_hash(False, _BLOCKERS[:-1], _COUNTS) != base, "a blocker"
    assert report_content_hash(False, _BLOCKERS, {**_COUNTS, "resource_gaps": 1}) != base, "counts"
    changed_message = [*_BLOCKERS[:-1], {**_BLOCKERS[-1], "message": "Something else."}]
    assert report_content_hash(False, changed_message, _COUNTS) != base, "a message"
