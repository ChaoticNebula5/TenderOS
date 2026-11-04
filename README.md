# TenderOS

Evidence-grounded, human-supervised bid operations platform for IT services and consulting firms.

`TenderOS-PRD.md` and `TenderOS-Build-Guide.md` are the authoritative product and technical specification. Read them before changing anything here. The project is being built strictly in the dependency order defined in the Build Guide's Part J (B0 → B17); this repository currently reflects **B13 — Functional UI**. Login through readiness runs end to end against the real stack: 17 screens, a full FastAPI surface, LangGraph orchestration, Postgres/Neo4j/Redis, and a Playwright scenario that drives the golden path through a real browser with no direct database edits or Swagger calls.

## Status

Functional through B13. Run it:

```
docker compose up          # full local stack on the fake LLM provider; the one-shot migrate service applies migrations first
uv run pytest               # backend tests
uv run alembic upgrade head # apply migrations by hand (from the host, with POSTGRES_DSN on localhost:5433)
npm --prefix apps/web run test:e2e # Playwright golden-path scenario, against a running stack
```

The stack's Postgres is published on host port 5433, not 5432, so a native
PostgreSQL on the host cannot answer in its place.

`docker compose up` never reaches the live LLM provider, whatever `.env`
says: `docker-compose.yml` sets `LLM_PROVIDER=fake` and blanks every Gemini
key for the api, the workers and the outbox dispatcher, and each logs its
provider when it starts. A live session goes only through
`scripts/real-stack.ps1`, which adds `docker-compose.live.yml` (it costs
real money); afterwards recreate the stack with `scripts/fake-stack.ps1`.

See the `Makefile` for the equivalent `lint` / `typecheck` / `format` / `test` targets.

## Layout

See Build Guide **A4** for the full repository layout and the rationale behind it.

## Testing

Backend tests run against a dedicated `tenderos_test` database, never the
dev/live `tenderos` one - `tests/integration/conftest.py`'s own `_clean_tables`
fixture deletes every row in every table after each test, and a session-scoped
guard refuses to run at all against a database not named `*_test`. One-time
setup, once the Postgres container is up:

```
docker exec tenderos-postgres-1 psql -U tenderos -d tenderos -c "CREATE DATABASE tenderos_test"
```

`make test` points `POSTGRES_DSN`, `REDIS_URL`, `NEO4J_URI`, and
`S3_ENDPOINT_URL` at `localhost` automatically (`.env`'s own values are the
Docker-internal hostnames the containers use to reach each other, not
reachable from the host); `uv run pytest` alone does not, and will refuse
to run against `.env`'s default Postgres DSN.

The Playwright scenario (`apps/web/e2e/full-flow.spec.ts`) is separate from
the backend suite - it drives a real browser against a running
`docker compose up` stack (`npm --prefix apps/web run test:e2e`), not a
mocked one, and is not part of `make test`. Locally, start that stack with
`scripts/fake-stack.ps1` (fake LLM provider). CI runs the same scenario in
its `e2e` job on every push and pull request, on a fake-provider stack whose
`.env` is generated from `.env.example` (`scripts/ci_e2e_env.py`); a failing
`e2e` fails the run (since its first five runs on main passed), and each
run's step 11 timeline is in the job summary
(`scripts/e2e_step11_timeline.py`).
