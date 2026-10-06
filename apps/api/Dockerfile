FROM python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY apps/worker/pyproject.toml apps/worker/pyproject.toml
COPY packages/tenderos_backend/pyproject.toml packages/tenderos_backend/pyproject.toml

RUN uv sync --frozen --package tenderos-api

# B17 Phase 0: this process embeds and reranks inside request handling
# (commercial_service._ground_pricing_finding, agents/knowledge/retrieval.py's
# query embedding, document_service), so a fresh container used to fetch
# both models from Hugging Face on its first such request - observed at
# about 63 s inside one resolve request (KNOWN_LIMITATIONS.md). Baked in at
# build time, in their own layer before the app-code COPY so ordinary code
# changes keep it cached. Model names match ingestion/embed.py's and
# agents/knowledge/rerank.py's own _MODEL_NAME exactly - keep these in
# sync, and with apps/worker/Dockerfile's identical step.
RUN uv run --frozen --package tenderos-api python -c \
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

EXPOSE 8000

CMD ["uv", "run", "--frozen", "--package", "tenderos-api", "uvicorn", "tenderos_api.main:app", "--app-dir", "apps/api/src", "--host", "0.0.0.0", "--port", "8000"]
