FROM python:3.12-slim

# pytesseract is a thin wrapper around the tesseract CLI binary - it does not
# bundle it. OCR only ever runs inside ingestion jobs (this container), never
# the API, so only the worker image needs it.
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY apps/worker/pyproject.toml apps/worker/pyproject.toml
COPY packages/tenderos_backend/pyproject.toml packages/tenderos_backend/pyproject.toml

RUN uv sync --frozen --package tenderos-worker

COPY apps/worker apps/worker
COPY packages/tenderos_backend packages/tenderos_backend
COPY alembic.ini alembic.ini

CMD ["uv", "run", "--frozen", "--package", "tenderos-worker", "python", "-m", "tenderos_worker.worker"]
