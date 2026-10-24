"""Phase 4 item 7: scripts/check_pins.py. The real repo must pass, and each
kind of drift the script exists to catch must fail it: planted in a copy
of the real files in a temporary directory, never in the repo itself."""

import importlib.util
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "check_pins", REPO_ROOT / "scripts" / "check_pins.py"
)
assert _spec is not None and _spec.loader is not None
check_pins = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_pins)

_FILES = (
    "docker-compose.yml",
    ".github/workflows/ci.yml",
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
    assert old in text, f"{old!r} not in {relative}; the test needs updating"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def test_the_repo_passes():
    assert check_pins.check(REPO_ROOT) == []


def test_the_copied_tree_passes(tree: Path):
    assert check_pins.check(tree) == []


def test_a_compose_image_without_a_digest_fails(tree: Path):
    _edit(
        tree,
        "docker-compose.yml",
        "image: redis:8.10.1-alpine@sha256:ef216d85ec44f4fdf7c0e6df2d1757a4734ed32fbb278cabd3181b5a27353b79",
        "image: redis:8-alpine",
    )
    problems = check_pins.check(tree)
    assert any("docker-compose.yml: redis image is not" in p for p in problems)


def test_a_ci_service_image_that_drifts_from_compose_fails(tree: Path):
    # What a Dependabot docker-compose PR alone would leave behind.
    _edit(
        tree,
        "docker-compose.yml",
        "neo4j:5.26.31-community@sha256:c",
        "neo4j:5.26.32-community@sha256:c",
    )
    problems = check_pins.check(tree)
    assert any(
        "service neo4j image" in p and "differs from docker-compose.yml" in p for p in problems
    )


def test_a_versitygw_run_line_that_drifts_from_compose_fails(tree: Path):
    _edit(
        tree, ".github/workflows/ci.yml", "versity/versitygw:v1.9.0@", "versity/versitygw:v1.9.1@"
    )
    assert any("VersityGW image other than" in p for p in check_pins.check(tree))


def test_an_action_pinned_by_tag_fails(tree: Path):
    _edit(
        tree,
        ".github/workflows/ci.yml",
        "actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9",
        "actions/cache@v6",
    )
    assert any("not pinned by commit SHA: actions/cache@v6" in p for p in check_pins.check(tree))


def test_api_and_worker_base_images_that_differ_fail(tree: Path):
    _edit(tree, "apps/worker/Dockerfile", "python:3.12.13-slim@", "python:3.12.14-slim@")
    assert any("use different base images" in p for p in check_pins.check(tree))


def test_a_setup_uv_version_that_drifts_from_the_uv_image_fails(tree: Path):
    _edit(tree, ".github/workflows/ci.yml", 'version: "0.13.5"', 'version: "0.13.6"')
    assert any("setup-uv versions" in p for p in check_pins.check(tree))


def test_a_setup_node_version_that_drifts_from_the_web_image_fails(tree: Path):
    _edit(tree, ".github/workflows/ci.yml", 'node-version: "22.23.4"', 'node-version: "22"')
    assert any("setup-node versions" in p for p in check_pins.check(tree))


def test_package_managers_that_differ_fail(tree: Path):
    _edit(tree, "apps/landing/package.json", '"npm@10.9.9"', '"npm@11.0.0"')
    assert any("packageManager differs" in p for p in check_pins.check(tree))
