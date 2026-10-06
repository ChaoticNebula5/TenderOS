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

# ingestion/embed.py's SentenceTransformer(_MODEL_NAME) otherwise downloads
# from Hugging Face on first real use - fine on a warm dev machine with
# network access, a real failure mode for a fresh/offline deployment's
# first ingestion job. Baked in at build time instead, in its own layer
# before COPY of app code so it stays cached across ordinary code changes.
# Model names match ingestion/embed.py's and agents/knowledge/rerank.py's
# own _MODEL_NAME exactly - keep these in sync if either ever changes, and
# with apps/api/Dockerfile's identical step. B17 Phase 0 added the reranker:
# before that it was baked into no image at all, so this worker also
# fetched it from Hugging Face on first use.
RUN uv run --frozen --package tenderos-worker python -c \
    "from sentence_transformers import CrossEncoder, SentenceTransformer; \
SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2'); \
CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')"

# Runtime only, set after the download above so it cannot block it - see
# apps/api/Dockerfile's identical ENV for the reasoning.
ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

COPY apps/worker apps/worker
COPY packages/tenderos_backend packages/tenderos_backend
COPY alembic.ini alembic.ini
# B5: agents/prompts/loader.py and core/prompts.py read this at a
# CWD-relative path (WORKDIR /app matches this COPY's destination). The API
# image needs its own copy too (apps/api/Dockerfile) - workflow_service.
# create_workflow_run reads bundle_version() from the API process, not just
# the worker.
COPY prompts prompts

CMD ["uv", "run", "--frozen", "--package", "tenderos-worker", "python", "-m", "tenderos_worker.worker"]
