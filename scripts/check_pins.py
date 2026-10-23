"""Check that every image, action and tool version is pinned, and that the
places naming the same thing agree.

Phase 4 item 7 pinned each image as `tag@sha256:digest` and each GitHub
Action by commit SHA, and added Dependabot entries so the pins are updated
rather than frozen. Dependabot updates each file on its own, though, and
some copies it never reads, so a pin can drift from its sibling unnoticed:

- ci.yml's `services:` images and the VersityGW image in its run step
  (Dependabot's github-actions entry updates `uses:` only) must equal
  docker-compose.yml's image for the same service;
- apps/api/Dockerfile and apps/worker/Dockerfile must use the same base
  images (the uv stage and Python);
- setup-uv's `version:` in ci.yml must equal the uv image's tag, and
  setup-node's `node-version:` the Node image's version;
- both package.json files must name the same packageManager.

And every reference must be pinned at all: each compose `image:` and each
Dockerfile `FROM` as `name:tag@sha256:<64 hex>`, each workflow `uses:` as
`owner/repo@<40 hex>`.

Exit 0 when everything holds, 1 with one line per problem otherwise.
Run: uv run python scripts/check_pins.py   (CI's lint job runs it)
"""

import json
import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent

PINNED_IMAGE = re.compile(r"^[\w./-]+:[\w.-]+@sha256:[0-9a-f]{64}$")
PINNED_ACTION = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
FROM_LINE = re.compile(r"^FROM\s+(\S+)", re.MULTILINE)
DOCKERFILES = ("apps/api/Dockerfile", "apps/worker/Dockerfile", "apps/web/Dockerfile")
PACKAGE_JSONS = ("apps/web/package.json", "apps/landing/package.json")


def _image_name(ref: str) -> str:
    """`ghcr.io/astral-sh/uv:0.12.3@sha256:...` -> `ghcr.io/astral-sh/uv`."""
    return ref.split("@", 1)[0].rsplit(":", 1)[0]


def _image_tag(ref: str) -> str:
    return ref.split("@", 1)[0].rsplit(":", 1)[1]


def _from_images(root: Path, dockerfile: str) -> list[str]:
    return FROM_LINE.findall((root / dockerfile).read_text(encoding="utf-8"))


def check(root: Path) -> list[str]:
    problems: list[str] = []

    compose = yaml.safe_load((root / "docker-compose.yml").read_text(encoding="utf-8"))
    compose_images: dict[str, str] = {
        name: service["image"]
        for name, service in compose["services"].items()
        if "image" in service
    }
    for name, ref in compose_images.items():
        if not PINNED_IMAGE.match(ref):
            problems.append(
                f"docker-compose.yml: {name} image is not name:tag@sha256:digest: {ref}"
            )

    from_images: dict[str, list[str]] = {path: _from_images(root, path) for path in DOCKERFILES}
    for path, refs in from_images.items():
        for ref in refs:
            if not PINNED_IMAGE.match(ref):
                problems.append(f"{path}: FROM is not name:tag@sha256:digest: {ref}")
    if from_images["apps/api/Dockerfile"] != from_images["apps/worker/Dockerfile"]:
        problems.append(
            "apps/api/Dockerfile and apps/worker/Dockerfile use different base images: "
            f"{from_images['apps/api/Dockerfile']} vs {from_images['apps/worker/Dockerfile']}"
        )

    workflows = sorted((root / ".github" / "workflows").glob("*.yml"))
    setup_uv_versions: set[str] = set()
    node_versions: set[str] = set()
    for workflow_path in workflows:
        label = workflow_path.relative_to(root).as_posix()
        workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
        for job_name, job in workflow.get("jobs", {}).items():
            for service_name, service in (job.get("services") or {}).items():
                expected = compose_images.get(service_name)
                if expected is None:
                    problems.append(
                        f"{label}: job {job_name} service {service_name} has no compose counterpart"
                    )
                elif service.get("image") != expected:
                    problems.append(
                        f"{label}: job {job_name} service {service_name} image {service.get('image')} "
                        f"differs from docker-compose.yml's {expected}"
                    )
            for step in job.get("steps", []):
                uses = step.get("uses")
                if uses:
                    if not PINNED_ACTION.match(uses):
                        problems.append(
                            f"{label}: job {job_name} uses an action not pinned by commit SHA: {uses}"
                        )
                    action = uses.split("@", 1)[0]
                    inputs = step.get("with") or {}
                    if action == "astral-sh/setup-uv":
                        setup_uv_versions.add(str(inputs.get("version", "<unset>")))
                    if action == "actions/setup-node":
                        node_versions.add(str(inputs.get("node-version", "<unset>")))
                run = step.get("run") or ""
                # The object store has no services: entry (it needs a command);
                # its image is on a `docker run` line instead.
                if "s3" in compose_images and "versity/versitygw" in run:
                    if compose_images["s3"] not in run:
                        problems.append(
                            f"{label}: job {job_name} runs a VersityGW image other than "
                            f"docker-compose.yml's {compose_images['s3']}"
                        )

    uv_tags = {
        _image_tag(ref)
        for refs in from_images.values()
        for ref in refs
        if _image_name(ref) == "ghcr.io/astral-sh/uv"
    }
    if setup_uv_versions and setup_uv_versions != uv_tags:
        problems.append(
            f"setup-uv versions {sorted(setup_uv_versions)} differ from the uv image tags {sorted(uv_tags)}"
        )

    node_image_versions = {
        _image_tag(ref).split("-", 1)[0]
        for ref in from_images["apps/web/Dockerfile"]
        if _image_name(ref) == "node"
    }
    if node_versions and node_versions != node_image_versions:
        problems.append(
            f"setup-node versions {sorted(node_versions)} differ from the web image's Node "
            f"{sorted(node_image_versions)}"
        )

    package_managers = {
        path: json.loads((root / path).read_text(encoding="utf-8")).get("packageManager", "<unset>")
        for path in PACKAGE_JSONS
    }
    if len(set(package_managers.values())) != 1 or "<unset>" in package_managers.values():
        problems.append(f"packageManager differs or is unset: {package_managers}")

    return problems


def main() -> int:
    problems = check(REPO_ROOT)
    for problem in problems:
        print(problem)
    if problems:
        return 1
    print("pins: every image, action and tool version is pinned, and the copies agree")
    return 0


if __name__ == "__main__":
    sys.exit(main())
