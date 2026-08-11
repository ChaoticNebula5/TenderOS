from functools import lru_cache
from pathlib import Path

import yaml

# Relative to CWD, not this file's location - every entry point in this repo
# (uv run pytest, uv run alembic, the Docker CMDs) already runs with CWD at
# the repo root (apps/worker/Dockerfile's WORKDIR /app + COPY prompts prompts
# mirrors this), the same convention alembic.ini and data/synthetic/ already
# rely on rather than a new PROMPTS_DIR env var.
_BUNDLE_PATH = Path("prompts/bundle.yaml")


@lru_cache(maxsize=1)
def load_bundle() -> dict:
    with _BUNDLE_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def bundle_version() -> str:
    """The real value for workflow_runs.prompt_bundle_version (Part C) -
    lives in core/, not agents/prompts/, specifically so domain services
    (workflow_service.create_workflow_run, which needs this at row-creation
    time, before any agent has run) can read it without importing from
    agents/ - A4.1's locked dependency direction is "agents call domain
    services," never the reverse, and a domain service importing
    agents/prompts/loader.py would invert that. agents/prompts/loader.py
    imports load_bundle() from here too, for the same reason load_prompt()
    shouldn't duplicate the YAML-reading logic.
    """
    return str(load_bundle()["bundle_version"])
