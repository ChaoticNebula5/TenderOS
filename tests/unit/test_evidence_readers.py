"""Plan items 89 and 91 (addition 4): no evidence reader still asks
`archived_item_ids`, and every evidence reader goes through the one
definition of "valid now" (core/evidence_validity.py).

Read from the source (an AST walk of the backend package), so a new caller
fails here until it is placed on one side or the other:
- ROSTER_READERS: the only functions allowed to call archived_item_ids. They
  ask "removed from the roster", not "valid evidence" (user decision Q4);
- EVIDENCE_READERS: each must reach the definition through
  KnowledgeRepository.item_validity, evidence_validity or the Cypher twin
  FACT_COUNTS_CYPHER, and none of them may call archived_item_ids.

That the evidence readers ignore a knowledge-item link's stored
verified/validity_status is shown by behaviour, reader by reader:
tests/integration/test_evidence_validity_readers.py (stored invalid but in
date counts; stored valid but lapsed does not),
tests/integration/test_claim_check_live_facts.py and
tests/unit/test_evidence_coverage.py (the graph readers)."""

import ast
from collections import Counter
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "packages/tenderos_backend/src/tenderos_backend"

ROSTER_READERS = Counter(
    {
        ("domain/services/readiness_service.py", "_evaluate_readiness_once"): 1,
        ("domain/services/redteam_checks.py", "check_archived_employee_selected"): 2,
        ("domain/services/gate_summary_service.py", "resource_selection_summary"): 1,
        ("domain/services/proposal_service.py", "_facts_from_team"): 1,
        # _set_human_selected_in_uow was here until plan item 59 (PR 20): the
        # select's archived check now reads the item FOR SHARE and compares
        # its status, as verify_employee_profile always has, so the check
        # holds until the select commits. It still asks "removed from the
        # roster", not "valid evidence".
        ("domain/services/talent_service.py", "archived_employee_ids"): 1,
        # Plan item 111 (PR 24): a resume item's candidates say which are
        # removed from the roster (no merge into them), a roster question.
        ("domain/services/knowledge_service.py", "list_resume_candidates"): 1,
        # Plan item 112: the certificate holder choice leaves out employees
        # removed from the roster, a roster question.
        ("domain/services/knowledge_service.py", "list_certificate_holders"): 1,
    }
)

DEFINITION_NAMES = {"item_validity", "evidence_validity", "FACT_COUNTS_CYPHER"}

EVIDENCE_READERS = {
    ("agents/knowledge/retrieval.py", "search_company_knowledge"),
    ("domain/services/compliance_service.py", "_compute_candidate_signals"),
    ("domain/services/compliance_service.py", "get_compliance_trace"),
    ("domain/services/readiness_service.py", "_evaluate_readiness_once"),
    ("domain/services/proposal_service.py", "_prepare_evidence_facts"),
    ("domain/services/proposal_service.py", "_resolve_evidence_trace_entry"),
    ("domain/services/talent_service.py", "_evidence_validity_status"),
    ("domain/services/talent_service.py", "_held_certificates"),
    ("domain/services/graph_service.py", "evaluate_evidence_coverage"),
    ("graph_projection/queries.py", "find_unsupported_grounded_claim_contradictions"),
}


def _uses() -> tuple[Counter, set]:
    """(archived_item_ids calls per (file, function), the (file, function)
    pairs that name the definition)."""
    archived: Counter = Counter()
    definition: set = set()
    for path in sorted(PACKAGE.rglob("*.py")):
        rel = path.relative_to(PACKAGE).as_posix()
        _walk(ast.parse(path.read_text(encoding="utf-8")), rel, None, archived, definition)
    return archived, definition


def _walk(node, rel: str, function: str | None, archived: Counter, definition: set) -> None:
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _walk(child, rel, child.name, archived, definition)
            continue
        if isinstance(child, ast.Attribute) and child.attr == "archived_item_ids":
            if function != "archived_item_ids":  # its own definition
                archived[(rel, function)] += 1
        name = (
            child.attr
            if isinstance(child, ast.Attribute)
            else child.id
            if isinstance(child, ast.Name)
            else None
        )
        if name in DEFINITION_NAMES and function is not None:
            definition.add((rel, function))
        _walk(child, rel, function, archived, definition)


def test_only_the_roster_readers_call_archived_item_ids():
    archived, _ = _uses()
    assert archived == ROSTER_READERS


def test_every_evidence_reader_goes_through_the_one_definition():
    _, definition = _uses()
    assert EVIDENCE_READERS - definition == set()


def test_no_evidence_reader_is_a_roster_reader_except_readiness():
    """Readiness's one function holds both step 2 (evidence) and 6b (roster);
    its single archived_item_ids call is 6b's, pinned by the count above."""
    overlap = {key for key in EVIDENCE_READERS if key in ROSTER_READERS}
    assert overlap == {("domain/services/readiness_service.py", "_evaluate_readiness_once")}
