"""Verify Alembic migrations apply cleanly from an empty database: upgrade to
head, downgrade to base, upgrade to head again."""

import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

REPO_ROOT = Path(__file__).resolve().parent.parent


def _config() -> Config:
    return Config(str(REPO_ROOT / "alembic.ini"))


def main() -> None:
    cfg = _config()

    print("Upgrading to head...")
    command.upgrade(cfg, "head")

    print("Downgrading to base...")
    command.downgrade(cfg, "base")

    print("Re-upgrading to head...")
    command.upgrade(cfg, "head")

    print("Migrations verified: upgrade -> downgrade -> upgrade all succeeded.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Migration verification failed: {exc}", file=sys.stderr)
        sys.exit(1)
