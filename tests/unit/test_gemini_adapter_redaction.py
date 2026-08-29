"""B15: gemini_adapter.py's _redacted_validation_summary - the root-cause
fix for the identified leak (MalformedOutputError embedding a pydantic
ValidationError's raw input_value). Pure function, no network/Redis
dependency - constructs a real ValidationError via a real pydantic model,
not a hand-built fake."""

from pydantic import BaseModel
from tenderos_backend.agents.llm.gemini_adapter import _redacted_validation_summary

_CONFIDENTIAL_TEXT = "CONFIDENTIAL-ZETA-9"


class _Target(BaseModel):
    category: str


def _real_validation_error():
    try:
        _Target.model_validate({"category": 12345})
    except Exception as exc:  # pydantic.ValidationError
        return exc
    raise AssertionError("expected model_validate to raise")


def test_summary_omits_the_raw_input_value():
    exc = _real_validation_error()
    # Sanity check the fixture itself actually carries the raw value the
    # old, unfixed code would have leaked - otherwise this test would pass
    # for the wrong reason (nothing to redact in the first place).
    assert "12345" in str(exc)

    summary = _redacted_validation_summary(exc)

    assert "12345" not in summary
    assert "category" in summary
    assert "string_type" in summary or "type" in summary.lower()


def test_summary_omits_confidential_source_text():
    try:
        _Target.model_validate({"category": {"nested": _CONFIDENTIAL_TEXT}})
    except Exception as exc:
        assert _CONFIDENTIAL_TEXT in str(exc)  # fixture sanity check
        summary = _redacted_validation_summary(exc)
        assert _CONFIDENTIAL_TEXT not in summary
        return
    raise AssertionError("expected model_validate to raise")
