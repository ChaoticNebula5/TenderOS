FROM python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY apps/worker/pyproject.toml apps/worker/pyproject.toml
COPY packages/tenderos_backend/pyproject.toml packages/tenderos_backend/pyproject.toml

RUN uv sync --frozen --package tenderos-api

COPY apps/api apps/api
COPY packages/tenderos_backend packages/tenderos_backend
COPY alembic.ini alembic.ini
# B5: workflow_service.create_workflow_run reads prompts/bundle.yaml
# (core/prompts.py's bundle_version()) at row-creation time, before any
# agent runs - that call happens from this process (the API route), not
# just the worker, so this image needs the file too, not only
# apps/worker/Dockerfile.
COPY prompts prompts

EXPOSE 8000

CMD ["uv", "run", "--frozen", "--package", "tenderos-api", "uvicorn", "tenderos_api.main:app", "--app-dir", "apps/api/src", "--host", "0.0.0.0", "--port", "8000"]
