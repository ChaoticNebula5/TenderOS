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

EXPOSE 8000

CMD ["uv", "run", "--frozen", "--package", "tenderos-api", "uvicorn", "tenderos_api.main:app", "--app-dir", "apps/api/src", "--host", "0.0.0.0", "--port", "8000"]
