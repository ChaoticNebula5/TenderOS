"""B5's stop-gate: run real extraction against the synthetic tender_a
package and report mandatory recall / citation validity against Part L2
ground truth.

Runs everything in-process (no live API/worker containers required) - upload
-> ingest -> extract are called directly via the same domain/agent functions
the real system uses, just without going through HTTP or RQ. Needs Postgres,
Redis, and MinIO reachable (docker compose up postgres redis minio) and a
real GEMINI_API_KEY in your local .env - this is the one part of B5 that
deliberately is NOT run against the fake LLM client, because the number it
produces has to mean something.

Run: uv run python scripts/run_eval.py
"""

import asyncio
import sys
import uuid
from pathlib import Path

from redis.asyncio import Redis as AsyncRedis
from tenderos_backend.agents.llm.gemini_adapter import GeminiClient
from tenderos_backend.agents.requirements.extract import extract_requirements
from tenderos_backend.core.config import settings
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import auth_service, document_service, tender_service
from tenderos_backend.domain.services.requirement_service import persist_extraction_results
from tenderos_backend.evals.datasets import load_requirements_ground_truth
from tenderos_backend.evals.extraction import evaluate_extraction
from tenderos_backend.evals.report import format_extraction_report
from tenderos_worker.jobs.ingestion import parse_and_embed_document

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "data" / "synthetic" / "tender_a"
GROUND_TRUTH_PATH = REPO_ROOT / "data" / "ground_truth" / "tender_a_requirements.jsonl"

_MIME_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


async def _setup_tender() -> uuid.UUID:
    # A fresh org/tender every run, keyed by a random suffix, rather than a
    # fixed id - repeated eval runs shouldn't collide on unique constraints
    # (organizations.name, tenders) or silently reuse a previous run's
    # already-extracted requirements.
    run_id = uuid.uuid4().hex[:8]
    register_result = await auth_service.register(
        email=f"eval-{run_id}@tenderos.local",
        password="eval-run-not-a-real-account",
        organization_name=f"Eval Org {run_id}",
    )
    tender = await tender_service.create_tender(
        organization_id=register_result.organization.id,
        name=f"B5 Eval Tender {run_id}",
        created_by=register_result.user.id,
    )
    return tender.id


async def _upload_and_ingest(tender_id: uuid.UUID) -> None:
    async with UnitOfWork() as uow:
        tender = await uow.tenders.get(tender_id)
    assert tender is not None

    for path in sorted(DOCS_DIR.iterdir()):
        if path.suffix not in _MIME_TYPES:
            continue
        content = path.read_bytes()
        document, is_new = await document_service.create_document(
            tender_id=tender_id,
            organization_id=tender.organization_id,
            actor_user_id=uuid.uuid4(),  # no real user for a duplicate-upload audit row here
            filename=path.name,
            content=content,
            mime_type=_MIME_TYPES[path.suffix],
        )
        if not is_new:
            continue
        # Direct call, not via the outbox/RQ queue - this script is a
        # self-contained CLI tool, not a request that line 538 constrains,
        # and it should not require a live worker container just to run.
        await parse_and_embed_document(str(document.id))
        print(f"ingested {path.name}")


async def main() -> int:
    if not settings.gemini_api_key or settings.gemini_api_key == "changeme":
        print(
            "GEMINI_API_KEY is not set in your .env - the stop-gate needs a real "
            "model run, not the fake test client. Aborting before spending anything.",
            file=sys.stderr,
        )
        return 2

    tender_id = await _setup_tender()
    print(f"created eval tender {tender_id}")

    await _upload_and_ingest(tender_id)

    async with UnitOfWork() as uow:
        chunks = await uow.documents.list_chunks_for_tender(tender_id)
    print(f"{len(chunks)} chunks ready for extraction")

    redis_client = AsyncRedis.from_url(settings.redis_url)
    client = GeminiClient(
        api_key=settings.gemini_api_key,
        redis_client=redis_client,
        cache_ttl_seconds=settings.llm_cache_ttl_seconds,
    )

    extraction = await extract_requirements(client, settings.extractor_model, chunks, tender_id)
    requirements = await persist_extraction_results(tender_id, extraction.requirements)
    print(f"{len(requirements)} requirements persisted")

    ground_truth = load_requirements_ground_truth(GROUND_TRUTH_PATH)
    report = await evaluate_extraction(ground_truth, requirements)
    print()
    print(format_extraction_report(report))

    gate_passed = report.mandatory_recall >= 0.90 and report.citation_validity >= 0.95
    return 0 if gate_passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
