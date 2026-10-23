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

# Phase 4 item 28: third-party dependencies only here; the workspace
# packages are installed after their code is copied (below). Before, this
# step installed them from a tree with no code yet, and `uv run` in the CMD
# rebuilt and reinstalled both on every container start (seen in the logs:
# "Uninstalled 2 packages ... Installed 2 packages").
RUN uv sync --frozen --no-install-workspace --package tenderos-worker

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
RUN /app/.venv/bin/python -c \
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

# The workspace packages, now that their code is here (see the first sync).
RUN uv sync --frozen --package tenderos-worker

# Phase 4 item 28: the venv's program runs directly, not under `uv run`.
# uv 0.12.3 did forward SIGTERM (observed), but the image is already
# synced, so `uv run` only added a per-start reinstall and a runtime
# dependency on uv's own signal handling. Under compose, `init: true` makes
# tini PID 1; where the image runs without an init, the program itself is
# PID 1 and gets no default signal actions. Either way each one handles
# SIGTERM itself: RQ's worker does a warm shutdown, and the outbox
# dispatcher (docker-compose.yml's command) finishes its tick and exits.
CMD ["/app/.venv/bin/python", "-m", "tenderos_worker.worker"]
