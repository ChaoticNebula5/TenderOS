"""Write the compose .env for CI's fake-provider E2E (Phase 4 plan item 30).

The stack reads `.env` twice: compose interpolates it into the data
services (`${POSTGRES_PASSWORD}` and the rest), and every app service loads
it with `env_file: .env`. A runner has no `.env`, and CI holds no secrets,
so this builds one from `.env.example`: every credential is replaced with a
value generated for this run, the DSN's password is made to match
POSTGRES_PASSWORD, and the provider is the fake one with every Gemini key
blank. Nothing it writes opens anything outside the runner's own
containers.

Guards:
- it refuses to overwrite an existing file, so running it by mistake on a
  dev machine cannot replace a real `.env`;
- every `changeme` left in `.env.example` must be either generated here or
  a model name on an explicit list (APP_ENV stays `local`, where model
  names are not checked, decision 0042); a new credential added to the
  template with `changeme` fails here until it is handled;
- values are never printed. Under GitHub Actions each generated value is
  registered with `::add-mask::`, so a console log line that ever contains
  one is masked;
- masking does not reach artifact files, so `--scan DIR` checks every file
  under DIR (the saved container logs) for each generated value and exits
  1, naming the variables only, if one appears. The job uploads the logs
  only after that check passes.

Run: uv run python scripts/ci_e2e_env.py [--template .env.example] [--output .env]
     uv run python scripts/ci_e2e_env.py --scan e2e-logs [--output .env]
"""

import argparse
import os
import secrets
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

REPO_ROOT = Path(__file__).resolve().parent.parent

PLACEHOLDER = "changeme"

# Credentials: each gets a fresh value. Hex and URL-safe alphabets only, so
# no value needs quoting in a .env file or escaping in a URL.
_GENERATORS = {
    "APP_SECRET_KEY": lambda: secrets.token_urlsafe(48),
    "POSTGRES_PASSWORD": lambda: secrets.token_hex(24),
    "NEO4J_PASSWORD": lambda: secrets.token_hex(24),
    # VersityGW takes the root pair as given; 20 and 40 characters match the
    # usual S3 key shapes.
    "S3_ACCESS_KEY": lambda: secrets.token_hex(10),
    "S3_SECRET_KEY": lambda: secrets.token_hex(20),
    # 64 characters, over the 32-byte minimum every process enforces.
    "JWT_SIGNING_KEY": lambda: secrets.token_urlsafe(48),
    "GRAFANA_ADMIN_PASSWORD": lambda: secrets.token_hex(16),
}

# Fixed values: the fake provider, and no Gemini key at all. The fake
# overlay (docker-compose.e2e-fake.yml) sets these too; setting them here
# as well means the file is safe even if that overlay were left out.
_FIXED = {
    "LLM_PROVIDER": "fake",
    "GEMINI_API_KEY": "",
    "COMPLIANCE_GEMINI_API_KEY": "",
    "TALENT_GEMINI_API_KEY": "",
    "COMMERCIAL_GEMINI_API_KEY": "",
}

# Placeholders that may stay: model names, which only production checks.
_ALLOWED_PLACEHOLDERS = {"PLANNER_MODEL", "REASONING_MODEL", "WRITER_MODEL", "CRITIC_MODEL"}


def _parse(template: str) -> list[tuple[str, str] | str]:
    """Lines of the template: (name, value) for assignments, the raw line
    for comments and blanks, so the output keeps the template's order."""
    entries: list[tuple[str, str] | str] = []
    for line in template.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            entries.append(line)
            continue
        name, value = stripped.split("=", 1)
        entries.append((name.strip(), value.strip()))
    return entries


def _dsn_with_password(dsn: str, user: str, password: str) -> str:
    parts = urlsplit(dsn)
    if parts.username != user:
        raise ValueError(f"POSTGRES_DSN's user is not POSTGRES_USER ({user})")
    host = parts.hostname or ""
    netloc = f"{user}:{password}@{host}" + (f":{parts.port}" if parts.port else "")
    return urlunsplit(parts._replace(netloc=netloc))


def build_env(template: str) -> tuple[str, dict[str, str]]:
    """Returns the .env text and the generated values by name."""
    entries = _parse(template)
    values = {e[0]: e[1] for e in entries if isinstance(e, tuple)}

    generated = {name: make() for name, make in _GENERATORS.items() if name in values}
    missing = sorted(set(_GENERATORS) - set(values))
    if missing:
        raise ValueError(f"the template has no {', '.join(missing)}")

    resolved = {**values, **generated, **_FIXED}
    resolved["POSTGRES_DSN"] = _dsn_with_password(
        values["POSTGRES_DSN"], values["POSTGRES_USER"], generated["POSTGRES_PASSWORD"]
    )
    generated["POSTGRES_DSN"] = resolved["POSTGRES_DSN"]

    left = sorted(
        name
        for name, value in resolved.items()
        if PLACEHOLDER in value and name not in _ALLOWED_PLACEHOLDERS
    )
    if left:
        raise ValueError(f"placeholders this script does not handle: {', '.join(left)}")

    lines: list[str] = []
    for entry in entries:
        if isinstance(entry, tuple):
            lines.append(f"{entry[0]}={resolved[entry[0]]}")
        else:
            lines.append(entry)
    for name, value in _FIXED.items():
        if name not in values:
            lines.append(f"{name}={value}")
    return "\n".join(lines) + "\n", generated


def scan(env_text: str, directory: Path) -> list[str]:
    """Names of the generated variables whose value appears in any file
    under directory. Values shorter than 8 characters are not generated
    here, so none is skipped for being too common to search for."""
    values = {
        e[0]: e[1]
        for e in _parse(env_text)
        if isinstance(e, tuple) and (e[0] in _GENERATORS or e[0] == "POSTGRES_DSN")
    }
    found: set[str] = set()
    for path in sorted(p for p in directory.rglob("*") if p.is_file()):
        content = path.read_bytes().decode("utf-8", errors="replace")
        found.update(name for name, value in values.items() if value and value in content)
    return sorted(found)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--template", type=Path, default=REPO_ROOT / ".env.example")
    parser.add_argument("--output", type=Path, default=REPO_ROOT / ".env")
    parser.add_argument("--scan", type=Path, help="check DIR for the generated values instead")
    args = parser.parse_args(argv)

    if args.scan is not None:
        found = scan(args.output.read_text(encoding="utf-8"), args.scan)
        if found:
            print(f"generated values found in {args.scan}: {', '.join(found)}", file=sys.stderr)
            return 1
        print(f"no generated value in {args.scan}")
        return 0

    if args.output.exists():
        print(f"refusing to overwrite {args.output}: it exists", file=sys.stderr)
        return 1
    text, generated = build_env(args.template.read_text(encoding="utf-8"))
    if os.environ.get("GITHUB_ACTIONS") == "true":
        for value in generated.values():
            print(f"::add-mask::{value}")
    args.output.write_text(text, encoding="utf-8", newline="\n")
    args.output.chmod(0o600)
    print(f"wrote {args.output.name}: generated {', '.join(sorted(generated))}; LLM_PROVIDER=fake")
    return 0


if __name__ == "__main__":
    sys.exit(main())
