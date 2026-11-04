"""Phase 4 plan item 46: scripts/check_ci_stays_fake.py. The real repo must
pass, and each way CI or the base compose file could reach the live LLM
provider must fail it: planted in a copy of the real files in a temporary
directory (a git repository of its own, for the tracked-override check),
never in the repo itself."""

import importlib.util
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "check_ci_stays_fake", REPO_ROOT / "scripts" / "check_ci_stays_fake.py"
)
assert _spec is not None and _spec.loader is not None
check_ci_stays_fake = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_ci_stays_fake)

_CI = ".github/workflows/ci.yml"
_LIVE = "docker-compose.live.yml"


def _files_ci_uses() -> list[str]:
    text = (REPO_ROOT / _CI).read_text(encoding="utf-8")
    named = [
        *check_ci_stays_fake.SCRIPT_PATH.findall(text),
        *check_ci_stays_fake.COMPOSE_PATH.findall(text),
    ]
    return sorted({n.rstrip(".") for n in named if (REPO_ROOT / n.rstrip(".")).is_file()})


@pytest.fixture
def tree() -> Iterator[Path]:
    # Its own temporary directory rather than pytest's tmp_path, as in
    # test_check_pins.py (pytest's shared base directory on the Windows dev
    # host, engineering log 2026-10-23).
    with tempfile.TemporaryDirectory(prefix="check-ci-fake-") as directory:
        root = Path(directory)
        for relative in [_CI, "docker-compose.yml", _LIVE, *_files_ci_uses()]:
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO_ROOT / relative, target)
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=root, check=True)
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        yield root


def _edit(root: Path, relative: str, old: str, new: str) -> None:
    path = root / relative
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{old!r} not in {relative}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _problems(root: Path) -> list[str]:
    # ci.yml runs the check itself, so the tree holds a copy of it, which
    # names the live file by necessity; it skips that copy, as in CI.
    return check_ci_stays_fake.check(root, root / "scripts" / "check_ci_stays_fake.py")


def test_the_real_repo_passes():
    assert check_ci_stays_fake.check(REPO_ROOT) == []


def test_the_copied_tree_passes(tree):
    assert _problems(tree) == []


def test_a_job_whose_compose_file_names_the_live_overlay_fails(tree):
    _edit(tree, _CI, ":docker-compose.ci-e2e.yml", f":docker-compose.ci-e2e.yml:{_LIVE}")
    assert f"{_CI} names the live compose file" in _problems(tree)


def test_a_step_that_passes_the_live_overlay_with_f_fails(tree):
    _edit(
        tree,
        _CI,
        "docker compose up -d --no-build migrate",
        f"docker compose -f {_LIVE} up -d --no-build migrate",
    )
    assert f"{_CI} names the live compose file" in _problems(tree)


def test_a_script_a_workflow_runs_that_names_the_live_overlay_fails(tree):
    script = tree / "scripts" / "ci_e2e_env.py"
    script.write_text(
        script.read_text(encoding="utf-8") + f"\n# COMPOSE_FILE += {_LIVE}\n", encoding="utf-8"
    )
    assert (
        "scripts/ci_e2e_env.py, used by a workflow, names the live compose file" in _problems(tree)
    )


def test_a_compose_file_that_includes_the_live_overlay_fails_but_a_comment_does_not(tree):
    overlay = tree / "docker-compose.ci-e2e.yml"
    original = overlay.read_text(encoding="utf-8")
    overlay.write_text(original + f"\n# not this: {_LIVE}\n", encoding="utf-8")
    assert _problems(tree) == []
    overlay.write_text(original + f"\ninclude:\n  - {_LIVE}\n", encoding="utf-8")
    assert (
        "docker-compose.ci-e2e.yml, used by a workflow, names the live compose file"
        in _problems(tree)
    )


def test_an_e2e_compose_file_without_the_fake_overlay_fails(tree):
    _edit(tree, _CI, ":docker-compose.e2e-fake.yml", "")
    assert (
        f"{_CI} job e2e: COMPOSE_FILE leaves out docker-compose.e2e-fake.yml" in _problems(tree)
    )


def test_a_workflow_that_sets_a_live_provider_fails(tree):
    _edit(tree, _CI, "LLM_PROVIDER: fake", "LLM_PROVIDER: gemini")
    assert f"{_CI} job test: LLM_PROVIDER is 'gemini', not 'fake'" in _problems(tree)


@pytest.mark.parametrize(
    ("service", "old", "new"),
    [
        ("api", "    environment: *fake-provider\n    ports:", "    ports:"),
        ("operations_worker", "      <<: *fake-provider\n", ""),
        (
            "migrate",
            '"upgrade", "head"]\n    env_file: .env\n    environment: *fake-provider\n',
            '"upgrade", "head"]\n    env_file: .env\n',
        ),
    ],
)
def test_an_app_service_off_the_fake_provider_fails(tree, service, old, new):
    _edit(tree, "docker-compose.yml", old, new)
    problems = _problems(tree)
    assert f"docker-compose.yml: {service} does not set LLM_PROVIDER to 'fake'" in problems
    assert f"docker-compose.yml: {service} does not set GEMINI_API_KEY to ''" in problems


def test_a_live_key_in_the_base_file_fails(tree):
    _edit(tree, "docker-compose.yml", '  GEMINI_API_KEY: ""', "  GEMINI_API_KEY: ${GEMINI_API_KEY}")
    problems = _problems(tree)
    for service in check_ci_stays_fake.FAKE_SERVICES:
        assert f"docker-compose.yml: {service} does not set GEMINI_API_KEY to ''" in problems


def test_a_new_service_that_reads_env_fails_until_it_is_fake(tree):
    _edit(
        tree,
        "docker-compose.yml",
        "\n  web:\n",
        "\n  reporter:\n    build: .\n    env_file: .env\n\n  web:\n",
    )
    assert "docker-compose.yml: reporter does not set LLM_PROVIDER to 'fake'" in _problems(tree)
    _edit(
        tree,
        "docker-compose.yml",
        "    env_file: .env\n\n  web:\n",
        "    env_file: .env\n    environment: *fake-provider\n\n  web:\n",
    )
    assert _problems(tree) == []


def test_a_tracked_compose_override_fails(tree):
    (tree / "docker-compose.override.yml").write_text(
        "services:\n  api:\n    environment:\n      LLM_PROVIDER: gemini\n", encoding="utf-8"
    )
    assert _problems(tree) == []
    subprocess.run(["git", "add", "docker-compose.override.yml"], cwd=tree, check=True)
    assert (
        "docker-compose.override.yml is tracked: a bare docker compose up would load it"
        in _problems(tree)
    )
