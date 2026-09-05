"""B17: agents/llm/fake_fallbacks.py's own per-agent fallback logic - real
production logic (installed at process startup when LLM_PROVIDER=fake), not
test scaffolding, so it gets its own direct test rather than only being
exercised indirectly by a live E2E run.
"""

from tenderos_backend.agents.llm.fake_fallbacks import _proposal_drafting_fallback


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
