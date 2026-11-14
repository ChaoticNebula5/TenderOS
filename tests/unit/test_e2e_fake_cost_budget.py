"""Plan item 73: the fake stack's cost budget defaults to the code's.

docker-compose.e2e-fake.yml sets MAX_TENDER_COST_USD from
TENDEROS_E2E_MAX_TENDER_COST_USD, with a default that must equal
Settings.max_tender_cost_usd's own default (user condition, 2026-11-13): a
change to one without the other would silently give CI's E2E a different
budget from every other environment. The variable exists only so a local
walk can open the cost-budget gate (the fake provider records no cost).
"""

import re
from pathlib import Path

import yaml
from tenderos_backend.core.config import Settings

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OVERLAY = REPO_ROOT / "docker-compose.e2e-fake.yml"
VALUE = re.compile(r"^\$\{TENDEROS_E2E_MAX_TENDER_COST_USD:-([0-9.]+)\}$")


def _overlay() -> dict:
    return yaml.safe_load(OVERLAY.read_text(encoding="utf-8"))


def test_the_compose_default_equals_the_settings_default():
    value = _overlay()["x-fake-provider"]["MAX_TENDER_COST_USD"]

    match = VALUE.fullmatch(value)
    assert match, f"unexpected MAX_TENDER_COST_USD form in {OVERLAY.name}: {value!r}"
    assert float(match.group(1)) == Settings.model_fields["max_tender_cost_usd"].default


def test_every_fake_service_gets_the_budget():
    overlay = _overlay()

    for name, service in overlay["services"].items():
        assert "MAX_TENDER_COST_USD" in service["environment"], name
