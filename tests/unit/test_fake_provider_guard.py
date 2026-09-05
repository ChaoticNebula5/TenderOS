"""B17: core/config.py::_reject_fake_provider_in_production - the hard
guard against a Phase 4 public demo silently serving fabricated LLM
output. Called automatically at import time against the real settings
object (see config.py's own call site, right after `settings = Settings()`)
- tested here directly, with arbitrary inputs, rather than only indirectly
by way of a real env var + a fresh import.
"""

import pytest
from tenderos_backend.core.config import _reject_fake_provider_in_production


def test_fake_and_production_together_raises():
    with pytest.raises(RuntimeError, match="LLM_PROVIDER=fake is not permitted"):
        _reject_fake_provider_in_production("fake", "production")


@pytest.mark.parametrize(
    ("llm_provider", "app_env"),
    [
        ("gemini", "production"),
        ("gemini", "local"),
        ("gemini", "e2e"),
        ("fake", "local"),
        ("fake", "e2e"),
    ],
)
def test_every_other_combination_is_allowed(llm_provider: str, app_env: str):
    _reject_fake_provider_in_production(llm_provider, app_env)
