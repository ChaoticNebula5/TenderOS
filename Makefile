.PHONY: up lint typecheck format test migrate

up:
	docker compose up

lint:
	uv run ruff check .
	npm --prefix apps/web run lint

format:
	uv run ruff format .
	npm --prefix apps/web run format

typecheck:
	uv run mypy

# .env points Redis/Neo4j/S3 at their Docker-internal hostnames
# (redis/neo4j/s3) for the api/worker containers - uv run pytest runs
# on the host, where those hostnames don't resolve. POSTGRES_DSN was the
# only one overridden here; any test touching Neo4j/Redis/S3 directly
# (tests/integration/test_graph_projection.py and others) would hang on
# an unresolvable host instead of running. Same loopback swap as
# POSTGRES_DSN's, one per service, ports unchanged (docker-compose.yml
# publishes each on its container-internal port number).
#
# RATE_LIMIT_ENABLED=false: both this project's real HTTP test harnesses
# (TestClient, httpx.ASGITransport) report one constant request.client.host
# for every request in the whole session - a global per-IP limit would
# eventually trip across unrelated tests sharing that identity, not a real
# signal about the code under test (see tenderos_api/config.py's own
# comment on this field, and tests/route/test_rate_limit_middleware.py for
# the dedicated tests that re-enable it deliberately).
#
# REDIS_URL uses database 1, not 0 (B17 Phase 0): the running stack's
# api/worker/outbox_dispatcher use database 0 for their RQ queues, so tests
# on /0 shared a namespace with any live worker (KNOWN_LIMITATIONS.md,
# "make test points pytest at the live stack's Neo4j and Redis DB 0").
# Neo4j has no such split - Community edition has one user database - so
# still stop the api/worker containers before running this target.
#
# The variables themselves live in scripts/test-backend.env (B17 Phase 0),
# the single definition shared with scripts/test-backend.ps1 - make is not
# installed on every machine this project runs on, and the env used to be
# retyped by hand there. `set -a` exports every KEY=VALUE the file sets.
test:
	set -a && . scripts/test-backend.env && set +a && uv run pytest

migrate:
	uv run alembic upgrade head
