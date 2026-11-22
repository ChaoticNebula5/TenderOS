"""Observability PR 31: create or update the metrics_exporter's login role.

Run by the compose `migrate` service after alembic and Neo4j's constraints,
on every start (`python -m tenderos_backend.db.observability_role`). Not a
migration (decided 2026-11-22): a role is server-wide, and its password
must not live in a migration. Re-running is the point: each start grants
SELECT on everything in the `observability` schema again, so views a later
migration adds are covered with no new step.

The password comes from METRICS_EXPORTER_DB_PASSWORD in .env (never
logged). Empty or unset, the step is skipped and says so; the exporter then
refuses to start (tenderos_worker/metrics_exporter.py), so a missing
password shows there, not as a silent partial setup.

What the role may do, and nothing more:
- LOGIN, with no superuser, database, role, replication or row-security
  bypass rights, and at most 4 connections;
- USAGE on the `observability` schema and SELECT on its views. It has no
  grant on any table in `public`: the views run with their owner's rights
  (they are not security_invoker), so the role reads only the columns they
  expose;
- by default every transaction it opens is read-only and every statement
  stops after 5 s (role settings; the exporter sets both per session too).

The password is sent in the ALTER ROLE statement, quoted by the server's own
quote_literal. Postgres stores only its SCRAM hash, but a server logging DDL
(log_statement = ddl or all; off by default and on the stack) would write the
statement out; keep statement logging off where this runs.
"""

import asyncio
import logging
import os
import re

import asyncpg

from tenderos_backend.db.observability import (
    EXPORTER_LOGIN_VARIABLE,
    EXPORTER_ROLE,
    SCHEMA,
)

logger = logging.getLogger(__name__)

STATEMENT_TIMEOUT = "5s"
CONNECTION_LIMIT = 4
_ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


async def ensure_exporter_role(
    conn: asyncpg.Connection, password: str, role: str = EXPORTER_ROLE
) -> int:
    """Create the role if it is missing, set its password and limits, and
    grant it the schema. One transaction, so a failure leaves the role as it
    was. Returns how many views the role can now read. `role` is a
    parameter so a test can use a throwaway one."""
    if not _ROLE_NAME.match(role):
        raise ValueError(f"not a plain role name: {role!r}")
    if not password:
        raise ValueError("an empty password would leave the role unable to log in")
    quoted_password = await conn.fetchval("SELECT quote_literal($1::text)", password)
    async with conn.transaction():
        exists = await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", role)
        if not exists:
            await conn.execute(f'CREATE ROLE "{role}" LOGIN')
        await conn.execute(
            f'ALTER ROLE "{role}" WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT '
            f"NOREPLICATION NOBYPASSRLS CONNECTION LIMIT {CONNECTION_LIMIT} "
            f"PASSWORD {quoted_password}"
        )
        await conn.execute(f'ALTER ROLE "{role}" SET default_transaction_read_only = on')
        await conn.execute(f"ALTER ROLE \"{role}\" SET statement_timeout = '{STATEMENT_TIMEOUT}'")
        await conn.execute(f'GRANT USAGE ON SCHEMA {SCHEMA} TO "{role}"')
        await conn.execute(f'GRANT SELECT ON ALL TABLES IN SCHEMA {SCHEMA} TO "{role}"')
        views = await conn.fetchval(
            "SELECT count(*) FROM information_schema.role_table_grants "
            "WHERE grantee = $1 AND table_schema = $2 AND privilege_type = 'SELECT'",
            role,
            SCHEMA,
        )
    return int(views)


async def _run(dsn: str, password: str) -> int:
    conn = await asyncpg.connect(dsn=dsn)
    try:
        return await ensure_exporter_role(conn, password)
    finally:
        await conn.close()


def main() -> None:
    from tenderos_backend.core.config import settings
    from tenderos_backend.core.log_redaction import configure_logging

    configure_logging()
    password = os.environ.get(EXPORTER_LOGIN_VARIABLE, "")
    if not password:
        logger.info(
            "metrics exporter role not set up: %s is empty (the metrics_exporter will "
            "refuse to start)",
            EXPORTER_LOGIN_VARIABLE,
        )
        return
    views = asyncio.run(_run(settings.postgres_dsn.replace("+asyncpg", ""), password))
    logger.info(
        "metrics exporter role %s ready: USAGE on schema %s, SELECT on %d view(s)",
        EXPORTER_ROLE,
        SCHEMA,
        views,
    )


if __name__ == "__main__":
    main()
