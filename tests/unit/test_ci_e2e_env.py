"""Phase 4 plan item 30: the .env CI's E2E job builds from .env.example.

Run against the real .env.example, so a credential added to the template
with a placeholder fails here before it fails in CI. Temp files come from
tempfile.TemporaryDirectory, not pytest's tmp_path, which this Windows
host cannot use (engineering log, batch 2a host traps).
"""

import importlib.util
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "ci_e2e_env", REPO_ROOT / "scripts" / "ci_e2e_env.py"
)
assert _spec and _spec.loader
ci_e2e_env = importlib.util.module_from_spec(_spec)
sys.modules["ci_e2e_env"] = ci_e2e_env
_spec.loader.exec_module(ci_e2e_env)

TEMPLATE = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")


def _values(env_text: str) -> dict[str, str]:
    return {
        line.split("=", 1)[0]: line.split("=", 1)[1]
        for line in env_text.splitlines()
        if line and not line.startswith("#") and "=" in line
    }


@pytest.fixture
def workdir():
    with tempfile.TemporaryDirectory() as name:
        yield Path(name)


def test_every_credential_is_generated_and_the_provider_is_fake():
    text, generated = ci_e2e_env.build_env(TEMPLATE)
    values = _values(text)

    assert values["LLM_PROVIDER"] == "fake"
    for key in (
        "GEMINI_API_KEY",
        "COMPLIANCE_GEMINI_API_KEY",
        "TALENT_GEMINI_API_KEY",
        "COMMERCIAL_GEMINI_API_KEY",
    ):
        assert values[key] == ""
    for name in ci_e2e_env._GENERATORS:
        assert values[name] == generated[name]
        assert ci_e2e_env.PLACEHOLDER not in values[name]
    # The 32-byte minimum every process enforces (decision 0042).
    assert len(values["JWT_SIGNING_KEY"].encode("utf-8")) >= 32
    # Only model names may keep the placeholder (APP_ENV stays local).
    assert values["APP_ENV"] == "local"
    left = {k for k, v in values.items() if ci_e2e_env.PLACEHOLDER in v}
    assert left <= ci_e2e_env._ALLOWED_PLACEHOLDERS


def test_the_dsn_carries_the_generated_postgres_password():
    text, _ = ci_e2e_env.build_env(TEMPLATE)
    values = _values(text)
    dsn = urlsplit(values["POSTGRES_DSN"])

    assert dsn.username == values["POSTGRES_USER"]
    assert dsn.password == values["POSTGRES_PASSWORD"]
    assert dsn.hostname == "postgres" and dsn.port == 5432
    assert dsn.path == f"/{values['POSTGRES_DB']}"


def test_two_runs_generate_different_values():
    _, first = ci_e2e_env.build_env(TEMPLATE)
    _, second = ci_e2e_env.build_env(TEMPLATE)
    assert all(first[name] != second[name] for name in ci_e2e_env._GENERATORS)


def test_a_new_placeholder_credential_is_refused():
    with pytest.raises(ValueError, match="NEW_SERVICE_TOKEN"):
        ci_e2e_env.build_env(TEMPLATE + "NEW_SERVICE_TOKEN=changeme\n")


def test_an_existing_env_is_never_overwritten(workdir, capsys):
    target = workdir / ".env"
    target.write_text("KEEP=me\n", encoding="utf-8")

    code = ci_e2e_env.main(["--output", str(target)])

    assert code == 1
    assert target.read_text(encoding="utf-8") == "KEEP=me\n"
    assert "refusing to overwrite" in capsys.readouterr().err


def test_main_writes_the_file_and_prints_no_value(workdir, capsys, monkeypatch):
    """Outside GitHub Actions nothing is masked, so nothing may be printed.
    GITHUB_ACTIONS is removed explicitly: on a runner it is "true", main()
    then prints its ::add-mask:: lines (the next test), and this test failed
    in CI while passing locally (engineering log, item 30)."""
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    target = workdir / ".env"

    assert ci_e2e_env.main(["--output", str(target)]) == 0

    out = capsys.readouterr().out
    values = _values(target.read_text(encoding="utf-8"))
    for name in ci_e2e_env._GENERATORS:
        assert values[name] not in out
    assert "::add-mask::" not in out
    assert "LLM_PROVIDER=fake" in out


def test_under_github_actions_values_appear_only_on_mask_lines(workdir, capsys, monkeypatch):
    """On a runner each generated value is registered with ::add-mask::
    before anything else could print it. Each value must appear on a line
    starting with ::add-mask::, and on no other line."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    target = workdir / ".env"

    assert ci_e2e_env.main(["--output", str(target)]) == 0

    lines = capsys.readouterr().out.splitlines()
    values = _values(target.read_text(encoding="utf-8"))
    secrets_ = [values[name] for name in (*ci_e2e_env._GENERATORS, "POSTGRES_DSN")]
    mask_lines = [line for line in lines if line.startswith("::add-mask::")]
    other_lines = [line for line in lines if not line.startswith("::add-mask::")]
    for value in secrets_:
        assert any(value in line for line in mask_lines), "a generated value was not masked"
        assert not any(value in line for line in other_lines), "a generated value was printed"


def test_scan_names_a_generated_value_found_in_the_logs(workdir, capsys):
    target = workdir / ".env"
    assert ci_e2e_env.main(["--output", str(target)]) == 0
    values = _values(target.read_text(encoding="utf-8"))
    logs = workdir / "e2e-logs"
    logs.mkdir()
    (logs / "compose.log").write_text("api-1 | ordinary line\n", encoding="utf-8")

    assert ci_e2e_env.main(["--output", str(target), "--scan", str(logs)]) == 0

    (logs / "compose.log").write_text(f"s3-1 | key {values['S3_SECRET_KEY']}\n", encoding="utf-8")
    capsys.readouterr()
    assert ci_e2e_env.main(["--output", str(target), "--scan", str(logs)]) == 1
    err = capsys.readouterr().err
    assert "S3_SECRET_KEY" in err
    assert values["S3_SECRET_KEY"] not in err
