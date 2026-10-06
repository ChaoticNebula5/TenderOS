"""B17: agents/llm/fake_fallbacks.py's own per-agent fallback logic - real
production logic (installed at process startup when LLM_PROVIDER=fake), not
test scaffolding, so it gets its own direct test rather than only being
exercised indirectly by a live E2E run.
"""

import uuid

from tenderos_backend.agents.compliance.propose import EvidenceCandidateRef, _format_candidates
from tenderos_backend.agents.llm.fake_fallbacks import (
    _compliance_proposal_fallback,
    _proposal_drafting_fallback,
)
from tenderos_backend.agents.proposal.draft_section import GroundedFactRef, _format_facts


def _candidate(label: str, source_kind: str, validity_status: str) -> EvidenceCandidateRef:
    return EvidenceCandidateRef(
        label=label,
        knowledge_item_id=uuid.uuid4(),
        source_kind=source_kind,
        excerpt=f"excerpt for {label}",
        validity_status=validity_status,
    )


def test_company_overview_gets_a_non_factual_claim():
    # Real diagnosis, not a guess: a live fake-provider E2E run showed
    # Company Overview and Technical Approach both getting the identical
    # is_factual claim, both landing "unsupported" (enforce_claim_grounding,
    # proposal_service.py), and the E2E spec's own step-10 assertion hanging
    # because it (correctly, for real content) assumes the first unsupported
    # claim comes from a data-gated section - which Company Overview,
    # section_is_data_gated's own one deliberate exclusion, structurally
    # never is. is_factual=False is what keeps this section out of
    # "unsupported" territory entirely (enforce_claim_grounding's own first
    # branch: not claim.is_factual -> status="not_required").
    draft = _proposal_drafting_fallback("SECTION: Company Overview\nSome guidance here")

    assert len(draft.claims) >= 1
    assert all(not c.is_factual for c in draft.claims)


def test_every_other_section_still_gets_a_real_factual_claim():
    # The E2E spec's own step 10 precondition ("expected at least one
    # unsupported factual claim") still needs to be satisfiable - this
    # fallback isn't supposed to make every section safe, only the one
    # that structurally can never be checked.
    draft = _proposal_drafting_fallback("SECTION: Technical Approach\nSome guidance here")

    assert len(draft.claims) >= 1
    assert any(c.is_factual for c in draft.claims)


# B17 Phase 0 - the prompt-aware fallbacks. Every prompt below is rendered by
# the production formatter (propose._format_candidates, draft_section.
# _format_facts), never hand-written, so a change to either prompt format
# fails here instead of silently turning a fake E2E run red.


def test_compliance_fallback_cites_the_first_valid_employee_its_prompt_offered():
    prompt = "REQUIREMENT: x\n" + _format_candidates(
        [
            _candidate("evidence_1", "certification", "valid"),
            _candidate("evidence_2", "employee", "expired"),
            _candidate("evidence_3", "employee", "valid"),
            _candidate("evidence_4", "employee", "valid"),
        ]
    )

    proposal = _compliance_proposal_fallback(prompt)

    assert proposal.status == "fully_compliant"
    assert proposal.evidence_refs == ["evidence_3"], "exactly one, valid, employee, offered"


def test_compliance_fallback_cites_nothing_when_no_valid_employee_was_offered():
    prompt = _format_candidates(
        [
            _candidate("evidence_1", "certification", "valid"),
            _candidate("evidence_2", "employee", "expired"),
        ]
    )
    assert _compliance_proposal_fallback(prompt).evidence_refs == []
    assert _compliance_proposal_fallback(_format_candidates([])).evidence_refs == []


def test_drafting_fallback_adds_a_compliance_claim_citing_offered_facts_only():
    facts = _format_facts(
        [
            GroundedFactRef(label="requirement_1", kind="requirement", excerpt="req text"),
            GroundedFactRef(label="employee_1", kind="employee", excerpt="emp"),
            GroundedFactRef(label="evidence_2", kind="evidence", excerpt="ev"),
        ]
    )
    draft = _proposal_drafting_fallback(f"SECTION: Technical Approach\n{facts}")

    # The original uncited factual claim is still first - step 10's
    # "at least one unsupported factual claim" precondition depends on it.
    assert draft.claims[0].is_factual and draft.claims[0].evidence_refs == []
    cited = draft.claims[1]
    assert cited.claim_type == "compliance"
    assert cited.requirement_refs == ["requirement_1"]
    assert cited.evidence_refs == ["evidence_2"]


def test_drafting_fallback_adds_no_cited_claim_without_both_fact_kinds():
    only_requirement = _format_facts(
        [GroundedFactRef(label="requirement_1", kind="requirement", excerpt="req")]
    )
    draft = _proposal_drafting_fallback(f"SECTION: Technical Approach\n{only_requirement}")
    assert len(draft.claims) == 1
