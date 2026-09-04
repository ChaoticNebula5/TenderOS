"""agents/llm/gemini_adapter.py::_estimate_cost_usd - a pure function, no
SDK/network mocking needed. Proves agent_traces.estimated_cost_usd (2,455
rows NULL, 0 ever populated - KNOWN_LIMITATIONS.md's dead-instrument entry)
is real arithmetic now, not still silently None everywhere.
"""

from tenderos_backend.agents.llm.gemini_adapter import _estimate_cost_usd


def test_a_known_model_computes_a_real_dollar_figure_from_published_rates():
    # gemini-3.1-flash-lite: $0.25/1M input, $1.50/1M output (this
    # project's own EXTRACTOR_MODEL) - 1000 input + 500 output tokens.
    cost = _estimate_cost_usd("gemini-3.1-flash-lite", 1000, 500)
    assert cost == (1000 / 1_000_000) * 0.25 + (500 / 1_000_000) * 1.50
    assert cost > 0.0


def test_an_unmapped_model_id_stays_none_not_a_guessed_price():
    cost = _estimate_cost_usd("some-future-model-not-in-the-table", 1000, 500)
    assert cost is None


def test_missing_token_counts_stay_none_not_a_false_zero():
    # A cache hit (gemini_adapter.py's own early return) has no fresh
    # usage_metadata at all - None must propagate, not silently become 0.0,
    # which would misrepresent a cache hit as a real, free call.
    assert _estimate_cost_usd("gemini-3.1-flash-lite", None, 500) is None
    assert _estimate_cost_usd("gemini-3.1-flash-lite", 1000, None) is None
    assert _estimate_cost_usd("gemini-3.1-flash-lite", None, None) is None


def test_every_configured_project_model_role_has_a_real_rate():
    # EXTRACTOR_MODEL/VERIFIER_MODEL (.env.example) are the two roles with
    # a real, non-"changeme" model id in this project today - both must be
    # priced, or metric 12 stays silently incomplete for the calls that
    # actually run.
    assert _estimate_cost_usd("gemini-3.1-flash-lite", 1, 1) is not None
    assert _estimate_cost_usd("gemini-3.5-flash-lite", 1, 1) is not None
