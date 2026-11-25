"""Observability PRs 31 and 32: create or update the `observability`
schema's reader roles: the metrics_exporter's and Grafana's.

Run by the compose `migrate` service after alembic and Neo4j's constraints,
on every start (`python -m tenderos_backend.db.observability_role`). Not a
migration (decided 2026-11-22): a role is server-wide, and its password
must not live in a migration.

Each reader (db/observability.py's READERS) gets SELECT on its own views by
name, and everything else in the schema is revoked on every start (PR 32,
answer B), so neither reader can read the other's views, and a grant made
by hand does not survive the next start. A view a later migration adds is
read by nobody until a reader's list names it; a test fails when a view
belongs to no reader.

Each reader's password comes from its own variable in .env (never logged).
Empty or unset, that reader is skipped and the step says so; the exporter
then refuses to start (tenderos_worker/metrics_exporter.py), and Grafana's
Postgres datasource fails its health check (fake-stack.ps1 -Observability
reports it), so a missing password shows there, not as a silent partial
setup.

What each role may do, and nothing more:
- LOGIN, with no superuser, database, role, replication or row-security
  bypass rights, and at most 4 connections;
- USAGE on the `observability` schema and SELECT on its own views. It has
  no grant on any table in `public`: the views run with their owner's rights
  (they are not security_invoker), so the role reads only what they expose;
- by default every transaction it opens is read-only, and every statement
  stops after the reader's timeout (role settings; the exporter sets both
  per session too).

The password is sent in the ALTER ROLE statement, quoted by the server's own
quote_literal. Postgres stores only its SCRAM hash, but a server logging DDL
(log_statement = ddl or all; off by default and on the stack) would write the
statement out; keep statement logging off where this runs. The same holds
for pg_stat_statements (PR 34): it keeps a utility statement's text with its
constants, so the stack's server sets pg_stat_statements.track_utility=off
(docker-compose.yml); a host that loads the library must set it too.
"""

import asyncio
import logging
import os
import re

import asyncpg

from tenderos_backend.db.observability import READERS, SCHEMA, Reader

logger = logging.getLogger(__name__)

CONNECTION_LIMIT = 4
_ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_VIEW_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_TIMEOUT = re.compile(r"^[0-9]{1,4}(ms|s)$")


async def ensure_reader_role(
    conn: asyncpg.Connection, reader: Reader, password: str, role: str | None = None
) -> list[str]:
    """Create the reader's role if it is missing, set its password and
    limits, grant it the schema and its own views, and revoke everything
    else in the schema. One transaction, so a failure leaves the role as it
    was. Returns the views the role can now read, sorted. `role` overrides
    the reader's name so a test can use a throwaway one."""
    name = role or reader.role
    if not _ROLE_NAME.match(name):
        raise ValueError(f"not a plain role name: {name!r}")
    if not password:
        raise ValueError("an empty password would leave the role unable to log in")
    bad_views = [v for v in reader.views if not _VIEW_NAME.match(v)]
    if bad_views or not _TIMEOUT.match(reader.statement_timeout):
        raise ValueError(
            f"not a plain view name or timeout: {bad_views or reader.statement_timeout!r}"
        )
    quoted_password = await conn.fetchval("SELECT quote_literal($1::text)", password)
    async with conn.transaction():
        exists = await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", name)
        if not exists:
            await conn.execute(f'CREATE ROLE "{name}" LOGIN')
        await conn.execute(
            f'ALTER ROLE "{name}" WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT '
            f"NOREPLICATION NOBYPASSRLS CONNECTION LIMIT {CONNECTION_LIMIT} "
            f"PASSWORD {quoted_password}"
        )
        await conn.execute(f'ALTER ROLE "{name}" SET default_transaction_read_only = on')
        await conn.execute(
            f"ALTER ROLE \"{name}\" SET statement_timeout = '{reader.statement_timeout}'"
        )
        await conn.execute(f'GRANT USAGE ON SCHEMA {SCHEMA} TO "{name}"')
        # Everything in the schema first, then its own views back by name: a
        # grant made by hand, or one left from PR 31's "everything" grant,
        # does not survive a start.
        await conn.execute(f'REVOKE ALL ON ALL TABLES IN SCHEMA {SCHEMA} FROM "{name}"')
        for view in reader.views:
            await conn.execute(f'GRANT SELECT ON {SCHEMA}.{view} TO "{name}"')
        granted = await conn.fetch(
            "SELECT table_name FROM information_schema.role_table_grants "
            "WHERE grantee = $1 AND table_schema = $2 AND privilege_type = 'SELECT' "
            "ORDER BY table_name",
            name,
            SCHEMA,
        )
    return [row["table_name"] for row in granted]


async def _run(dsn: str, passwords: dict[str, str]) -> dict[str, list[str]]:
    conn = await asyncpg.connect(dsn=dsn)
    try:
        return {
            reader.role: await ensure_reader_role(conn, reader, passwords[reader.role])
            for reader in READERS
            if passwords.get(reader.role)
        }
    finally:
        await conn.close()


def main() -> None:
    from tenderos_backend.core.config import settings
    from tenderos_backend.core.log_redaction import configure_logging

    configure_logging()
    passwords = {reader.role: os.environ.get(reader.login_variable, "") for reader in READERS}
    for reader in READERS:
        if not passwords[reader.role]:
            logger.info(
                "observability reader %s not set up: %s is empty",
                reader.role,
                reader.login_variable,
            )
    if not any(passwords.values()):
        return
    granted = asyncio.run(_run(settings.postgres_dsn.replace("+asyncpg", ""), passwords))
    for role, views in granted.items():
        logger.info(
            "observability reader %s ready: USAGE on schema %s, SELECT on %d view(s): %s",
            role,
            SCHEMA,
            len(views),
            ", ".join(views),
        )


if __name__ == "__main__":
    main()
