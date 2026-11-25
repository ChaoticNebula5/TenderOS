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
    # Observability PR 31: the metrics_exporter's role, and only if both
    # earlier steps succeeded (&&). PR 34: pg_stat_statements last.
    assert command[2] == (
        "/app/.venv/bin/alembic upgrade head && "
        "/app/.venv/bin/python -m tenderos_backend.graph_projection.apply_constraints && "
        "/app/.venv/bin/python -m tenderos_backend.db.observability_role && "
        "/app/.venv/bin/python -m tenderos_backend.db.query_stats"
    )


def test_postgres_preloads_pg_stat_statements_on_its_default_command():
    # PR 34 (plan item 118): the image's entrypoint runs `postgres` with
    # these arguments; no entrypoint override. track_utility off, so no
    # utility statement's text (migrate's ALTER ROLE ... PASSWORD) is kept.
    postgres = _compose()["services"]["postgres"]

    assert postgres["command"] == [
        "postgres",
        "-c",
        "shared_preload_libraries=pg_stat_statements",
        "-c",
        "pg_stat_statements.track_utility=off",
    ]
    assert "entrypoint" not in postgres


def test_both_neo4j_servers_get_a_minute_to_stop():
    # PR 34: Docker's default 10 s killed both before they finished stopping.
    services = _compose()["services"]

    assert services["neo4j"]["stop_grace_period"] == "60s"
    assert services["neo4j_test"]["stop_grace_period"] == "60s"


def test_migrate_gets_both_readers_passwords_and_stays_on_the_fake_provider():
    environment = _compose()["services"]["migrate"]["environment"]

    assert environment["METRICS_EXPORTER_DB_PASSWORD"] == "${METRICS_EXPORTER_DB_PASSWORD:-}"
    # Observability PR 32: Grafana's reader, made by the same setup step.
    assert environment["GRAFANA_DB_PASSWORD"] == "${GRAFANA_DB_PASSWORD:-}"
    assert environment["LLM_PROVIDER"] == "fake"


def test_grafana_holds_only_its_own_variables_and_stays_on_loopback():
    """Observability PR 32: no env_file, so none of .env's other
    credentials; its admin login, its own read-only Postgres password and
    the database's name. Anonymous access and sign-up off, nothing sent
    outside, provisioning read-only from the repo, published on loopback
    only (plan item 67 keeps it so at hosting)."""
    grafana = _compose()["services"]["grafana"]

    assert "env_file" not in grafana
    environment = grafana["environment"]
    assert {name for name in environment if not name.startswith("GF_")} == {
        "GRAFANA_DB_PASSWORD",
        "POSTGRES_DB",
    }
    assert environment["GF_AUTH_ANONYMOUS_ENABLED"] == "false"
    assert environment["GF_USERS_ALLOW_SIGN_UP"] == "false"
    assert environment["GF_ANALYTICS_REPORTING_ENABLED"] == "false"
    assert "./observability/grafana/provisioning:/etc/grafana/provisioning:ro" in grafana["volumes"]
    assert "./observability/dashboards:/etc/grafana/dashboards:ro" in grafana["volumes"]
    assert grafana["ports"] == ["127.0.0.1:3000:3000", "[::1]:3000:3000"]
    assert grafana["profiles"] == ["observability"]


def test_prometheus_loads_the_alert_rules_compose_mounts():
    prometheus = _compose()["services"]["prometheus"]
    config = yaml.safe_load(
        (REPO_ROOT / "observability/prometheus/prometheus.yml").read_text(encoding="utf-8")
    )

    assert "./observability/prometheus/rules:/etc/prometheus/rules:ro" in prometheus["volumes"]
    assert config["rule_files"] == ["/etc/prometheus/rules/*.rules.yml"]
    assert (REPO_ROOT / "observability/prometheus/rules/tenderos.rules.yml").is_file()


def test_the_metrics_exporter_holds_only_its_own_variables():
    """Least access: no env_file, so none of .env's credentials (the app's
    Postgres superuser, Neo4j, S3, the signing key); its own Postgres login,
    the database's name and Redis's URL only. Not published: Prometheus
    scrapes it on the compose network. Only in the observability profile."""
    exporter = _compose()["services"]["metrics_exporter"]

    assert "env_file" not in exporter
    assert set(exporter["environment"]) == {
        "METRICS_EXPORTER_DB_PASSWORD",
        "POSTGRES_DB",
        "REDIS_URL",
    }
    assert "ports" not in exporter
    assert exporter["profiles"] == ["observability"]
    assert exporter["command"][-1] == "tenderos_worker.metrics_exporter"


def test_prometheus_scrapes_the_api_and_the_exporter_on_the_compose_network():
    config = yaml.safe_load(
        (REPO_ROOT / "observability/prometheus/prometheus.yml").read_text(encoding="utf-8")
    )
    targets = {
        job["job_name"]: target
        for job in config["scrape_configs"]
        for group in job["static_configs"]
        for target in group["targets"]
    }

    assert targets["api"] == "api:8000"
    assert targets["metrics_exporter"] == "metrics_exporter:9105"


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
