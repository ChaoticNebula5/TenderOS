from functools import cache
from pathlib import Path

from tenderos_backend.core.prompts import bundle_version, load_bundle

__all__ = ["PromptNotFoundError", "bundle_version", "load_prompt"]


class PromptNotFoundError(Exception):
    pass


@cache
def load_prompt(agent_name: str) -> str:
    """Returns the raw template text for one agent role (e.g.
    'requirement_extraction') - callers (agents/requirements/extract.py,
    verify.py) do their own str.format()-style substitution of input refs
    into the template; this function only resolves bundle.yaml's
    agent-name -> file mapping and reads the file. bundle_version is
    re-exported from core/prompts.py so existing callers importing it from
    this module don't break.
    """
    bundle = load_bundle()
    agents = bundle.get("agents", {})
    if agent_name not in agents:
        raise PromptNotFoundError(f"No prompt registered for agent {agent_name!r}")

    template_path = (
        Path("prompts/versions") / bundle["bundle_version"] / agents[agent_name]["template"]
    )
    if not template_path.exists():
        raise PromptNotFoundError(f"Prompt template missing: {template_path}")
    return template_path.read_text(encoding="utf-8")
