"""Observability PR 32: the three provisioned dashboards, checked as files.

Every panel reads one of the two provisioned datasources; every SQL query
reads only Grafana's views in the observability schema (never a table, and
never the exporter's views, which its login cannot read); every request
ratio leaves out scrapes and healthchecks, and every latency panel also the
two stream routes (PR 32 change 3: their duration is how long a stream
stayed open).
"""

import json
import re
from pathlib import Path

import pytest
import yaml
from tenderos_backend.db.observability import GRAFANA_ROLE, GRAFANA_VIEWS

REPO_ROOT = Path(__file__).resolve().parents[2]
DASHBOARDS = sorted((REPO_ROOT / "observability/dashboards").glob("*.json"))
PROVISIONING = REPO_ROOT / "observability/grafana/provisioning"
INTERNAL = 'route!~"/metrics|/health/.+"'
INTERNAL_OR_STREAM = 'route!~"/metrics|/health/.+|.*/events/stream"'


def _datasources() -> dict[str, dict]:
    config = yaml.safe_load(
        (PROVISIONING / "datasources/datasources.yml").read_text(encoding="utf-8")
    )
    return {ds["uid"]: ds for ds in config["datasources"]}


def _targets() -> list[tuple[str, str, dict]]:
    found = []
    for path in DASHBOARDS:
        for panel in json.loads(path.read_text(encoding="utf-8"))["panels"]:
            for target in panel["targets"]:
                found.append((path.name, panel["title"], target))
    return found


def test_three_dashboards_with_distinct_uids_read_only():
    boards = [json.loads(p.read_text(encoding="utf-8")) for p in DASHBOARDS]
    assert sorted(p.name for p in DASHBOARDS) == ["health.json", "llm.json", "work.json"]
    assert len({b["uid"] for b in boards}) == 3
    assert all(b["editable"] is False for b in boards)


def test_the_postgres_datasource_is_grafanas_reader_with_the_password_from_the_environment():
    postgres = _datasources()["postgres-observability"]
    assert postgres["user"] == GRAFANA_ROLE
    assert postgres["secureJsonData"]["password"] == "$GRAFANA_DB_PASSWORD"
    assert postgres["jsonData"]["database"] == "$POSTGRES_DB"
    assert all(ds["editable"] is False for ds in _datasources().values())


def test_the_dashboards_are_loaded_from_the_mounted_folder_and_cannot_be_changed_in_the_ui():
    config = yaml.safe_load(
        (PROVISIONING / "dashboards/dashboards.yml").read_text(encoding="utf-8")
    )
    (provider,) = config["providers"]
    assert provider["options"]["path"] == "/etc/grafana/dashboards"
    assert provider["allowUiUpdates"] is False and provider["disableDeletion"] is True


def test_every_panel_and_target_reads_a_provisioned_datasource():
    uids = set(_datasources())
    for path in DASHBOARDS:
        for panel in json.loads(path.read_text(encoding="utf-8"))["panels"]:
            assert panel["datasource"]["uid"] in uids, (path.name, panel["title"])
            for target in panel["targets"]:
                assert target["datasource"]["uid"] in uids, (path.name, panel["title"])


@pytest.mark.parametrize("dashboard, title, target", [t for t in _targets() if "rawSql" in t[2]])
def test_every_sql_query_reads_only_grafanas_views(dashboard: str, title: str, target: dict):
    # Every FROM or JOIN names a relation, except extract(epoch FROM ...),
    # which names a value; a subquery's FROM ( names none.
    relations = re.findall(
        r"(?<!epoch )\b(?:FROM|JOIN)\s+([\w.]+)", target["rawSql"], flags=re.IGNORECASE
    )
    assert relations, (dashboard, title)
    for relation in relations:
        schema, _, view = relation.partition(".")
        assert schema == "observability", (dashboard, title, relation)
        assert view in GRAFANA_VIEWS, (dashboard, title, relation)


@pytest.mark.parametrize("dashboard, title, target", [t for t in _targets() if "expr" in t[2]])
def test_request_panels_leave_out_scrapes_healthchecks_and_streams(
    dashboard: str, title: str, target: dict
):
    expr = target["expr"]
    for selector in re.findall(r"tenderos_http_requests_total(\{[^}]*\})?", expr):
        assert INTERNAL in selector, (dashboard, title)
    for selector in re.findall(r"tenderos_http_request_duration_seconds\w*(\{[^}]*\})?", expr):
        assert INTERNAL_OR_STREAM in selector, (dashboard, title)
