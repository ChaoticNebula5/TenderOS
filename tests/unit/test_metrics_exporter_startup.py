"""Observability PR 31 (B): the metrics_exporter refuses to start without its
own password, in one ERROR line naming variables, never values, and needs
nothing from .env to get that far.

Run in a fresh process with a bare environment and an empty working
directory, as the container gets it (no env_file): only the variables the
compose service sets.
"""

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from tenderos_worker.metrics_exporter import ExporterConfig, ExporterRefusedError

_BASE_ENV = {
    key: os.environ[key]
    for key in ("PATH", "SYSTEMROOT", "PYTHONPATH", "VIRTUAL_ENV")
    if key in os.environ
}


def _run(env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "tenderos_worker.metrics_exporter"],
        env={**_BASE_ENV, **env},
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_without_its_password_it_refuses_in_one_error_line(tmp_path: Path):
    result = _run(
        {"LOG_FORMAT": "json", "POSTGRES_DB": "tenderos", "REDIS_URL": "redis://127.0.0.1:1/0"},
        tmp_path,
    )

    assert result.returncode == 1, result.stderr
    lines = [line for line in result.stderr.splitlines() if line.strip()]
    assert len(lines) == 1, lines
    record = json.loads(lines[0])
    assert record["level"] == "ERROR"
    assert record["message"].startswith("startup refused: Refusing to start the metrics exporter")
    assert "METRICS_EXPORTER_DB_PASSWORD" in record["message"]


def test_the_refusal_names_every_missing_variable_and_no_value():
    secret = uuid.uuid4().hex
    with pytest.raises(ExporterRefusedError) as refused:
        ExporterConfig.from_environ({"METRICS_EXPORTER_DB_PASSWORD": secret})

    message = str(refused.value)
    assert "POSTGRES_DB" in message and "REDIS_URL" in message
    assert secret not in message


def test_the_defaults_point_at_the_compose_services():
    config = ExporterConfig.from_environ(
        {"METRICS_EXPORTER_DB_PASSWORD": "x", "POSTGRES_DB": "db", "REDIS_URL": "redis://r/0"}
    )

    assert (config.db_host, config.db_port, config.port) == ("postgres", 5432, 9105)
    assert config.db_role == "tenderos_metrics_exporter"
