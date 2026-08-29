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

# .env points Redis/Neo4j/MinIO at their Docker-internal hostnames
# (redis/neo4j/minio) for the api/worker containers - uv run pytest runs
# on the host, where those hostnames don't resolve. POSTGRES_DSN was the
# only one overridden here; any test touching Neo4j/Redis/S3 directly
# (tests/integration/test_graph_projection.py and others) would hang on
# an unresolvable host instead of running. Same localhost swap as
# POSTGRES_DSN's, one per service, ports unchanged (docker-compose.yml
# publishes each on its container-internal port number).
#
# RATE_LIMIT_ENABLED=false: both this project's real HTTP test harnesses
# (TestClient, httpx.ASGITransport) report one constant request.client.host
# for every request in the whole session - a global per-IP limit would
# eventually trip across unrelated tests sharing that identity, not a real
# signal about the code under test (see tenderos_api/config.py's own
# comment on this field, and tests/route/test_rate_limit.py for the
# dedicated tests that re-enable it deliberately).
test:
	POSTGRES_DSN=postgresql+asyncpg://tenderos:changeme@localhost:5432/tenderos_test \
	REDIS_URL=redis://localhost:6379/0 \
	NEO4J_URI=bolt://localhost:7687 \
	S3_ENDPOINT_URL=http://localhost:9000 \
	RATE_LIMIT_ENABLED=false \
	uv run pytest

migrate:
	uv run alembic upgrade head
