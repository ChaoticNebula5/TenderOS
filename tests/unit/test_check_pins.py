"""Phase 4 item 7: scripts/check_pins.py. The real repo must pass, and each
kind of drift the script exists to catch must fail it: planted in a copy
of the real files in a temporary directory, never in the repo itself.

Each test reads the current value from the copied file and plants drift
relative to it, so a pin bump that keeps the copies in line (Dependabot's
PR plus the ci.yml fix) passes here too; only check_pins.py itself goes
red on a bump that leaves a copy behind."""

import importlib.util
import json
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "check_pins", REPO_ROOT / "scripts" / "check_pins.py"
)
assert _spec is not None and _spec.loader is not None
check_pins = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_pins)

_CI = ".github/workflows/ci.yml"
_FILES = (
    "docker-compose.yml",
    _CI,
    *check_pins.DOCKERFILES,
    *check_pins.PACKAGE_JSONS,
)


@pytest.fixture
def tree() -> Iterator[Path]:
    # Its own temporary directory rather than pytest's tmp_path: on the
    # Windows dev host pytest's shared base directory (pytest-of-<user> in
    # %TEMP%) was left unreadable by another process, and every tmp_path
    # test errored at setup there (engineering log, 2026-10-23).
    with tempfile.TemporaryDirectory(prefix="check-pins-") as directory:
        root = Path(directory)
        for relative in _FILES:
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO_ROOT / relative, target)
        yield root


def _edit(root: Path, relative: str, old: str, new: str) -> None:
    path = root / relative
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{old!r} not in {relative}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _compose_image(root: Path, service: str) -> str:
    compose = yaml.safe_load((root / "docker-compose.yml").read_text(encoding="utf-8"))
    return compose["services"][service]["image"]


def _ci_steps(root: Path, action: str) -> list[dict]:
    workflow = yaml.safe_load((root / _CI).read_text(encoding="utf-8"))
    return [
        step
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if step.get("uses", "").split("@", 1)[0] == action
    ]


def _another_tag(ref: str) -> str:
    """`redis:8.10.1-alpine@sha256:...` -> `redis:8.10.1-alpine-drift@sha256:...`:
    still pinned, so only the comparison between copies can catch it."""
    name_and_tag, digest = ref.split("@", 1)
    return f"{name_and_tag}-drift@{digest}"


def test_the_repo_passes():
    assert check_pins.check(REPO_ROOT) == []


def test_the_copied_tree_passes(tree: Path):
    assert check_pins.check(tree) == []


def test_a_pin_bump_that_keeps_the_copies_in_line_passes(tree: Path):
    # What a docker-compose PR plus its ci.yml fix leaves behind.
    ref = _compose_image(tree, "redis")
    _edit(tree, "docker-compose.yml", f"image: {ref}", f"image: {_another_tag(ref)}")
    _edit(tree, _CI, f"image: {ref}", f"image: {_another_tag(ref)}")
    assert check_pins.check(tree) == []


def test_a_compose_image_without_a_digest_fails(tree: Path):
    ref = _compose_image(tree, "redis")
    _edit(tree, "docker-compose.yml", f"image: {ref}", f"image: {ref.split('@', 1)[0]}")
    problems = check_pins.check(tree)
    assert any("docker-compose.yml: redis image is not" in p for p in problems)


def test_a_ci_service_image_that_drifts_from_compose_fails(tree: Path):
    # What a Dependabot docker-compose PR alone would leave behind.
    ref = _compose_image(tree, "neo4j")
    _edit(tree, "docker-compose.yml", f"image: {ref}", f"image: {_another_tag(ref)}")
    problems = check_pins.check(tree)
    assert any(
        "service neo4j image" in p and "differs from docker-compose.yml" in p for p in problems
    )


def test_a_versitygw_run_line_that_drifts_from_compose_fails(tree: Path):
    ref = _compose_image(tree, "s3")
    _edit(tree, _CI, ref, _another_tag(ref))
    assert any("VersityGW image other than" in p for p in check_pins.check(tree))


def test_an_action_pinned_by_tag_fails(tree: Path):
    uses = _ci_steps(tree, "actions/cache")[0]["uses"]
    _edit(tree, _CI, uses, "actions/cache@v6")
    assert any("not pinned by commit SHA: actions/cache@v6" in p for p in check_pins.check(tree))


def test_api_and_worker_base_images_that_differ_fail(tree: Path):
    python = next(
        ref
        for ref in check_pins._from_images(tree, "apps/worker/Dockerfile")
        if check_pins._image_name(ref) == "python"
    )
    _edit(tree, "apps/worker/Dockerfile", python, _another_tag(python))
    assert any("use different base images" in p for p in check_pins.check(tree))


def test_a_setup_uv_version_that_drifts_from_the_uv_image_fails(tree: Path):
    version = _ci_steps(tree, "astral-sh/setup-uv")[0]["with"]["version"]
    _edit(tree, _CI, f'version: "{version}"', f'version: "{version}-drift"')
    assert any("setup-uv versions" in p for p in check_pins.check(tree))


def test_a_setup_node_version_that_drifts_from_the_web_image_fails(tree: Path):
    version = _ci_steps(tree, "actions/setup-node")[0]["with"]["node-version"]
    _edit(tree, _CI, f'node-version: "{version}"', f'node-version: "{version}-drift"')
    assert any("setup-node versions" in p for p in check_pins.check(tree))


def test_package_managers_that_differ_fail(tree: Path):
    landing = "apps/landing/package.json"
    package_manager = json.loads((tree / landing).read_text(encoding="utf-8"))["packageManager"]
    _edit(tree, landing, f'"{package_manager}"', f'"{package_manager}-drift"')
    assert any("packageManager differs" in p for p in check_pins.check(tree))


_NO_TIMEOUT_JOB = """
  planted:
    runs-on: ubuntu-latest
    steps:
      - run: echo planted
"""


def test_a_new_job_without_a_timeout_fails(tree: Path):
    path = tree / _CI
    path.write_text(path.read_text(encoding="utf-8") + _NO_TIMEOUT_JOB, encoding="utf-8")
    assert check_pins.check(tree) == [f"{_CI}: job planted has no timeout-minutes"]


@pytest.mark.parametrize("job_name", ["lint", "e2e", "security"])
def test_an_existing_job_that_loses_its_timeout_fails(tree: Path, job_name: str):
    # First, middle and last, so a check that stops early can't pass.
    path = tree / _CI
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert job_name in workflow["jobs"]
    del workflow["jobs"][job_name]["timeout-minutes"]
    path.write_text(yaml.safe_dump(workflow, sort_keys=False), encoding="utf-8")
    assert f"{_CI}: job {job_name} has no timeout-minutes" in check_pins.check(tree)


def test_an_empty_timeout_fails(tree: Path):
    # `timeout-minutes:` with no value parses as null: present, and no limit.
    path = tree / _CI
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    workflow["jobs"]["lint"]["timeout-minutes"] = None
    path.write_text(yaml.safe_dump(workflow, sort_keys=False), encoding="utf-8")
    assert f"{_CI}: job lint has no timeout-minutes" in check_pins.check(tree)


@pytest.mark.parametrize("name", ["other.yml", "other.yaml"])
def test_a_job_without_a_timeout_in_another_workflow_fails(tree: Path, name: str):
    other = tree / ".github" / "workflows" / name
    other.write_text(f"name: Other\non: push\njobs:{_NO_TIMEOUT_JOB}", encoding="utf-8")
    assert check_pins.check(tree) == [
        f".github/workflows/{name}: job planted has no timeout-minutes"
    ]
