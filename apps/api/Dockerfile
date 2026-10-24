# Phase 4 item 7: base images pinned as tag@sha256:digest, at the versions
# that ran before pinning (Python 3.12.13; uv 0.12.3, which `pip install uv`
# had picked up). uv comes from its own image as a named stage, not
# `pip install uv`, so Dependabot's docker entry, which reads FROM lines,
# updates it too. apps/api/Dockerfile and apps/worker/Dockerfile use the
# same two images; scripts/check_pins.py fails CI if they differ.
FROM ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff AS uv

FROM python:3.14.7-slim@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d

COPY --from=uv /uv /uvx /bin/

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
RUN uv sync --frozen --no-install-workspace --package tenderos-api

# B17 Phase 0: this process embeds and reranks inside request handling
# (commercial_service._ground_pricing_finding, agents/knowledge/retrieval.py's
# query embedding, document_service), so a fresh container used to fetch
# both models from Hugging Face on its first such request - observed at
# about 63 s inside one resolve request (KNOWN_LIMITATIONS.md). Baked in at
# build time, in their own layer before the app-code COPY so ordinary code
# changes keep it cached. Model names match ingestion/embed.py's and
# agents/knowledge/rerank.py's own _MODEL_NAME exactly - keep these in
# sync, and with apps/worker/Dockerfile's identical step.
RUN /app/.venv/bin/python -c \
    "from sentence_transformers import CrossEncoder, SentenceTransformer; \
SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2'); \
CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')"

# Runtime only, set after the download above so it cannot block it: with
# both models baked in, nothing should ever reach huggingface.co at run
# time. Offline mode turns a missing model into an immediate, loud error
# instead of a silent network fetch inside a request.
ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

COPY apps/api apps/api
COPY packages/tenderos_backend packages/tenderos_backend
COPY alembic.ini alembic.ini
# B5: workflow_service.create_workflow_run reads prompts/bundle.yaml
# (core/prompts.py's bundle_version()) at row-creation time, before any
# agent runs - that call happens from this process (the API route), not
# just the worker, so this image needs the file too, not only
# apps/worker/Dockerfile.
COPY prompts prompts

# The workspace packages, now that their code is here (see the first sync).
RUN uv sync --frozen --package tenderos-api

# Phase 4 item 28: the venv's program runs directly, not under `uv run`.
# uv 0.12.3 did forward SIGTERM (observed), but the image is already
# synced, so `uv run` only added a per-start reinstall and a runtime
# dependency on uv's own signal handling. Under compose, `init: true` makes
# tini PID 1; where the image runs without an init, the program itself is
# PID 1 and gets no default signal actions. Either way each one handles
# SIGTERM itself: uvicorn shuts down gracefully, waiting at most 10 s
# for open connections (an SSE stream stays open until the client leaves).
EXPOSE 8000
CMD ["/app/.venv/bin/uvicorn", "tenderos_api.main:app", "--app-dir", "apps/api/src", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "10"]
