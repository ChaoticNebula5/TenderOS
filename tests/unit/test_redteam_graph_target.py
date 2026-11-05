"""check_graph_contradictions' per-row label (phase 4 plan item 35). Each shape
a contradiction row can have, with the graph faked: which rows a real graph
produces is tests/integration/test_redteam_target_type.py's job, and the id
actually being in the named table is checked there too.
"""

import uuid

import pytest
from tenderos_backend.domain.services import graph_service, redteam_checks

pytestmark = pytest.mark.asyncio

_TENDER = uuid.uuid4()
_ID = uuid.uuid4()
_NODE = {"id": str(_ID)}
# A second id on the row's other nodes: the target_id must come from the node
# the label names, not from whichever one was found first.
_OTHER = {"id": str(uuid.uuid4())}


async def _drafts(monkeypatch, row):
    async def _rows(_tender_id):
        return [{"severity": "critical", **row}]

    monkeypatch.setattr(graph_service, "find_contradictions_for_redteam", _rows)
    return await redteam_checks.check_graph_contradictions(_TENDER)


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (
            {"category": "expired_certification", "cert": _OTHER, "compliance": _NODE},
            "compliance_entry",
        ),
        ({"category": "unsupported_grounded_claim", "claim": _NODE}, "proposal_claim"),
        (
            {
                "category": "deselected_fact_in_use",
                "fact": _OTHER,
                "via": "SUPPORTS",
                "target": _NODE,
            },
            "compliance_entry",
        ),
        (
            {
                "category": "deselected_fact_in_use",
                "fact": _OTHER,
                "via": "CITED_IN",
                "target": _NODE,
            },
            "proposal_claim",
        ),
        (
            {
                "category": "removed_employee_committed",
                "employee": _OTHER,
                "via": "FILLS",
                "target": _NODE,
            },
            "role_requirement",
        ),
        (
            {
                "category": "removed_employee_committed",
                "employee": _OTHER,
                "via": "NAMED_IN",
                "target": _NODE,
            },
            "proposal_claim",
        ),
    ],
)
async def test_each_row_shape_gets_the_label_of_the_node_it_stores(monkeypatch, row, expected):
    [draft] = await _drafts(monkeypatch, row)

    assert draft.target_type == expected
    assert draft.target_id == _ID
    assert draft.finding_category == row["category"]
    # Part of the fingerprint: a change here would be a second identity break.
    assert draft.violated_condition == f"category={row['category']}; node_id={_ID}"


@pytest.mark.parametrize(
    "row",
    [
        {"category": "deselected_fact_in_use", "via": "SOME_NEW_EDGE", "target": _NODE},
        {"category": "removed_employee_committed", "target": _NODE},
        # A fact alone used to be accepted as the target, under a label that
        # named something else.
        {"category": "deselected_fact_in_use", "fact": _NODE},
        {"category": "deselected_fact_in_use", "via": "SUPPORTS", "target": None},
    ],
)
async def test_a_row_it_cannot_place_raises_instead_of_inventing_a_label(monkeypatch, row):
    with pytest.raises(ValueError, match="cannot place graph contradiction row"):
        await _drafts(monkeypatch, row)
