"""Check that CI can never run the live LLM provider, and that the base
compose file keeps every app service on the fake one.

Phase 4 plan item 46. docker-compose.yml sets LLM_PROVIDER=fake and blanks
every Gemini key for the services that call an LLM; docker-compose.live.yml
is the one file that puts the live provider back, for scripts/real-stack.ps1.
CI's e2e runs on the fake stack and must stay there: a live run in CI would
spend money on every push, and its results would not be reproducible. So
this fails when:

- a workflow under .github/workflows names docker-compose.live.yml;
- a file a workflow uses names it: each `scripts/...` path a workflow
  mentions is read in full, comments included, and each docker-compose file
  it mentions (in COMPOSE_FILE, after -f, or anywhere else) by its parsed
  content, where `include:` and `extends:` would name it;
- a job's COMPOSE_FILE leaves out docker-compose.e2e-fake.yml;
- a workflow sets LLM_PROVIDER (workflow, job or step env) to anything but
  fake;
- in docker-compose.yml, a service that reads .env (`env_file`), or one of
  the services named below, does not set LLM_PROVIDER to fake and every
  Gemini key to "" in `environment:` (a new service that reads .env gets
  the fake settings before CI passes);
- a compose override file is tracked: `docker compose up` loads it with no
  -f at all, so it could undo the fake default without being named.

Exit 0 when everything holds, 1 with one line per problem otherwise.
Run: uv run python scripts/check_ci_stays_fake.py   (CI's lint job runs it)
"""

import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ".github/workflows"
LIVE_FILE = re.compile(r"docker-compose\.live\.ya?ml", re.IGNORECASE)
FAKE_OVERLAY = "docker-compose.e2e-fake.yml"
SCRIPT_PATH = re.compile(r"scripts/[\w./-]+")
COMPOSE_PATH = re.compile(r"docker-compose[\w.-]*\.ya?ml")
OVERRIDE_FILES = (
    "docker-compose.override.yml",
    "docker-compose.override.yaml",
    "compose.override.yml",
    "compose.override.yaml",
)
# The services that must exist and stay fake: the ones that call an LLM
# (each through agents/llm/factory.py::build_llm_client), and migrate, which
# calls none but runs the same backend image, where a `docker compose run
# migrate python ...` could. Any other service that reads .env must too.
FAKE_SERVICES = (
    "api",
    "workflow_worker",
    "ingestion_worker",
    "maintenance_worker",
    "operations_worker",
    "outbox_dispatcher",
    "migrate",
)
FAKE_ENVIRONMENT = {
    "LLM_PROVIDER": "fake",
    "GEMINI_API_KEY": "",
    "COMPLIANCE_GEMINI_API_KEY": "",
    "TALENT_GEMINI_API_KEY": "",
    "COMMERCIAL_GEMINI_API_KEY": "",
}


def _env_blocks(workflow: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Every `env:` mapping in a workflow, with where it is."""
    blocks = [("workflow", workflow.get("env") or {})]
    for job_name, job in (workflow.get("jobs") or {}).items():
        blocks.append((f"job {job_name}", job.get("env") or {}))
        for i, step in enumerate(job.get("steps") or []):
            blocks.append((f"job {job_name} step {i + 1}", step.get("env") or {}))
    return blocks


def _check_workflows(root: Path, own_path: Path) -> list[str]:
    problems: list[str] = []
    used: set[str] = set()
    for path in sorted((root / WORKFLOWS).glob("*.y*ml")):
        name = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        if LIVE_FILE.search(text):
            problems.append(f"{name} names the live compose file")
        used.update(SCRIPT_PATH.findall(text))
        used.update(COMPOSE_PATH.findall(text))

        workflow = yaml.safe_load(text) or {}
        for where, env in _env_blocks(workflow):
            compose_file = env.get("COMPOSE_FILE")
            if compose_file is not None:
                files = str(compose_file).split(":")
                used.update(files)
                if FAKE_OVERLAY not in files:
                    problems.append(f"{name} {where}: COMPOSE_FILE leaves out {FAKE_OVERLAY}")
            provider = env.get("LLM_PROVIDER")
            if provider is not None and str(provider) != "fake":
                problems.append(f"{name} {where}: LLM_PROVIDER is {provider!r}, not 'fake'")

    # A path at the end of a sentence in a comment keeps its full stop.
    for rel in sorted({u.rstrip(".") for u in used}):
        path = root / rel
        # The live file itself, named in a workflow, is reported above.
        if LIVE_FILE.search(rel) or not path.is_file():
            continue
        if path.resolve() == own_path.resolve():
            continue
        text = path.read_text(encoding="utf-8")
        if COMPOSE_PATH.fullmatch(path.name):
            # Its parsed content only: a compose file pulls another in through
            # `include:` or `extends: file:`, never through a comment, and
            # docker-compose.yml's own header names the live file to explain it.
            text = yaml.safe_dump(yaml.safe_load(text))
        if LIVE_FILE.search(text):
            problems.append(f"{rel}, used by a workflow, names the live compose file")
    return problems


def _check_base_compose(root: Path) -> list[str]:
    problems: list[str] = []
    compose = yaml.safe_load((root / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose.get("services") or {}
    reading_env = [name for name, service in services.items() if "env_file" in service]
    for name in dict.fromkeys([*FAKE_SERVICES, *reading_env]):
        if name not in services:
            problems.append(f"docker-compose.yml: service {name} is missing")
            continue
        environment = services[name].get("environment") or {}
        if isinstance(environment, list):
            # Compose's other form: ["KEY=value", ...].
            environment = dict(item.partition("=")[::2] for item in environment)
        for key, want in FAKE_ENVIRONMENT.items():
            if environment.get(key) != want:
                problems.append(f"docker-compose.yml: {name} does not set {key} to {want!r}")
    return problems


def _check_no_tracked_override(root: Path) -> list[str]:
    tracked = subprocess.run(
        ["git", "ls-files", "--", *OVERRIDE_FILES],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return [f"{path} is tracked: a bare docker compose up would load it" for path in tracked]


def check(root: Path, own_path: Path = Path(__file__)) -> list[str]:
    return (
        _check_workflows(root, own_path)
        + _check_base_compose(root)
        + _check_no_tracked_override(root)
    )


def main() -> int:
    problems = check(REPO_ROOT)
    for problem in problems:
        print(problem)
    if problems:
        return 1
    print(
        "fake provider: no CI workflow reaches docker-compose.live.yml, its e2e keeps "
        f"{FAKE_OVERLAY}, and docker-compose.yml sets every service that reads .env to fake"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
