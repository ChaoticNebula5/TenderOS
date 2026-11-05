"""docker-compose.yml's migrate service applies Neo4j's constraints (phase 4 plan
item 49). The running behaviour is observed on the fake stack, but no test runs a
stack, so this keeps the wiring from drifting: the order is what makes it fail
closed, and every app service already waits on migrate.
"""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
_APP_SERVICES = (
    "api",
    "workflow_worker",
    "ingestion_worker",
    "maintenance_worker",
    "operations_worker",
    "outbox_dispatcher",
)


def _compose() -> dict:
    return yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))


def test_migrate_applies_constraints_after_alembic_and_only_if_it_succeeded():
    command = _compose()["services"]["migrate"]["command"]

    assert command[:2] == ["sh", "-c"]
    assert command[2] == (
        "/app/.venv/bin/alembic upgrade head && "
        "/app/.venv/bin/python -m tenderos_backend.graph_projection.apply_constraints"
    )


def test_migrate_waits_for_a_healthy_neo4j():
    depends_on = _compose()["services"]["migrate"]["depends_on"]

    assert depends_on["neo4j"] == {"condition": "service_healthy"}
    assert depends_on["postgres"] == {"condition": "service_healthy"}


def test_every_app_service_waits_for_migrate_to_succeed():
    services = _compose()["services"]

    for name in _APP_SERVICES:
        assert services[name]["depends_on"]["migrate"] == {
            "condition": "service_completed_successfully"
        }, name
