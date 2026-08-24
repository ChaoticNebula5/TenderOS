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

test:
	POSTGRES_DSN=postgresql+asyncpg://tenderos:changeme@localhost:5432/tenderos_test uv run pytest

migrate:
	uv run alembic upgrade head
