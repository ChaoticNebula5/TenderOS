"""Observability PR 32 (answer B): the observability schema's readers, each
granted its own views by name.

Every view belongs to exactly one reader, so a view a migration adds without
naming its reader fails here rather than being readable by nobody (or, under
PR 31's old "everything in the schema" grant, by everyone). Each reader's
password variable is named in .env.example and passed to migrate.
"""

from pathlib import Path

import yaml
from tenderos_backend.db.observability import (
    EXPORTER,
    GRAFANA,
    READERS,
    SCHEMA,
    VIEWS,
    create_views_statements,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_every_view_belongs_to_exactly_one_reader():
    names = [name for name, _ in VIEWS]
    assert len(names) == len(set(names))
    owners = {name: [r.role for r in READERS if name in r.views] for name in names}
    assert all(len(roles) == 1 for roles in owners.values()), owners
    for reader in READERS:
        assert set(reader.views) <= set(names), reader.role


def test_the_two_readers_and_their_limits():
    assert [r.role for r in READERS] == ["tenderos_metrics_exporter", "tenderos_grafana_reader"]
    assert EXPORTER.statement_timeout == "5s"
    assert GRAFANA.statement_timeout == "10s"
    assert len(EXPORTER.views) == 4 and len(GRAFANA.views) == 8


def test_each_readers_password_is_named_in_env_example_and_passed_to_migrate():
    example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    migrate = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))[
        "services"
    ]["migrate"]["environment"]
    for reader in READERS:
        # The name only, with an empty value: a reader is skipped without one.
        assert f"{reader.login_variable}=" in example, reader.login_variable
        assert migrate[reader.login_variable] == f"${{{reader.login_variable}:-}}"


def test_the_models_build_makes_the_schema_and_every_view():
    statements = create_views_statements()
    assert statements[0] == f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"
    assert [s.split(" AS ", 1)[0] for s in statements[1:]] == [
        f"CREATE OR REPLACE VIEW {SCHEMA}.{name}" for name, _ in VIEWS
    ]
