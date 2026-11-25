"""PR 34 (plan item 118; user decision 2026-11-25, Q7 b): create the
pg_stat_statements extension in the app's database, so every query's time is
recorded, the workers' as well as the api's, before a statement_timeout is
chosen from real numbers.

Run by the compose `migrate` service last, after alembic, Neo4j's
constraints and the observability readers, on every start
(`python -m tenderos_backend.db.query_stats`). Not a migration: the
extension works only on a server that loads its library at start
(shared_preload_libraries, a server setting; docker-compose.yml's postgres
sets it), and creating it needs a role allowed to (it is not a trusted
extension). A host may give the migrating role neither, and a migration that
failed there would stop every deploy. So this step decides, and says which
in one line:
- the library is not preloaded (CI's own Postgres service, for one): skipped;
- the server tracks utility statements (pg_stat_statements.track_utility on,
  the library's default): not created, with a WARNING. The library keeps a
  utility statement's text with its constants, and this same migrate run
  sends ALTER ROLE ... PASSWORD (db/observability_role.py), so the view
  would show the readers' passwords. docker-compose.yml sets it off; a host
  must too;
- the role may not create the extension: skipped, for an administrator to do;
- otherwise: created if missing, and its version logged.
Any other error (the server unreachable, say) fails the step, as alembic
would have just before it.

What it records is read by a person with psql (the view
pg_stat_statements, in the public schema); no app code reads it. It keeps
each query's normalized text (constants replaced by $1, $2, ...), calls,
rows and timings, for every database on the server, with each row's dbid.
"""

import asyncio
import logging

import asyncpg

logger = logging.getLogger(__name__)

EXTENSION = "pg_stat_statements"


def preloads(setting: str) -> bool:
    """Whether a shared_preload_libraries value names the extension's
    library (a comma-separated list; entries may be quoted or spaced)."""
    return EXTENSION in {entry.strip().strip('"') for entry in setting.split(",")}


async def ensure_query_stats(conn: asyncpg.Connection) -> tuple[bool, str]:
    """Create the extension when the server preloads its library and keeps
    no utility statement's text. Returns whether it is ready, and the one
    line that says what happened."""
    setting = await conn.fetchval("SELECT current_setting('shared_preload_libraries')")
    if not preloads(setting or ""):
        return (
            False,
            f"{EXTENSION} skipped: the server does not preload it (shared_preload_libraries)",
        )
    utility = await conn.fetchval(
        "SELECT current_setting('pg_stat_statements.track_utility', true)"
    )
    if utility != "off":
        return False, (
            f"{EXTENSION} not created: pg_stat_statements.track_utility is {utility}, so a "
            "utility statement's text, a role's password among them, would be kept; set it off"
        )
    try:
        await conn.execute(f"CREATE EXTENSION IF NOT EXISTS {EXTENSION}")
    except asyncpg.InsufficientPrivilegeError:
        return False, f"{EXTENSION} skipped: this role may not create it; an administrator can"
    version = await conn.fetchval(
        "SELECT extversion FROM pg_extension WHERE extname = $1", EXTENSION
    )
    return True, f"{EXTENSION} ready (version {version})"


async def _run(dsn: str) -> tuple[bool, str]:
    conn = await asyncpg.connect(dsn=dsn)
    try:
        return await ensure_query_stats(conn)
    finally:
        await conn.close()


def main() -> None:
    from tenderos_backend.core.config import settings
    from tenderos_backend.core.log_redaction import configure_logging

    configure_logging()
    ready, line = asyncio.run(_run(settings.postgres_dsn.replace("+asyncpg", "")))
    # A skip is not a failure (migrate still exits 0), but one a host should
    # see: WARNING, except where the server does not load the library at all.
    if ready or "does not preload" in line:
        logger.info(line)
    else:
        logger.warning(line)


if __name__ == "__main__":
    main()
