# TenderOS

Evidence-grounded, human-supervised bid operations platform for IT services and consulting firms.

`TenderOS-PRD.md` and `TenderOS-Build-Guide.md` are the authoritative product and technical specification. Read them before changing anything here. The project is being built strictly in the dependency order defined in the Build Guide's Part J (B0 → B17); this repository currently reflects **B0 — Documentation Baseline and Scaffold** only. No database, orchestration, or agent behavior exists yet.

## Status

Scaffold only. Nothing here is runnable as a product yet.

## Layout

See Build Guide **A4** for the full repository layout and the rationale behind it.

## Once later blocks land

```
docker compose up          # full local stack
uv run pytest               # backend tests
uv run alembic upgrade head # apply migrations
```

See the `Makefile` for the equivalent `lint` / `typecheck` / `format` / `test` targets.

Backend tests run against a dedicated `tenderos_test` database, never the
dev/live `tenderos` one - `tests/integration/conftest.py`'s own `_clean_tables`
fixture deletes every row in every table after each test, and a session-scoped
guard refuses to run at all against a database not named `*_test`. One-time
setup, once the Postgres container is up:

```
docker exec tenderos-postgres-1 psql -U tenderos -d tenderos -c "CREATE DATABASE tenderos_test"
```

`make test` points `POSTGRES_DSN` at it automatically; `uv run pytest` alone
does not, and will refuse to run against `.env`'s default DSN.
