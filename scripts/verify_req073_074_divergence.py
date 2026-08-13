"""One-off live-model verification for KNOWN_LIMITATIONS.md's "[B5] Silent
suppression of restated obligations" entry - deliberately NOT part of the
pytest suite (which runs against FakeLLMClient exclusively, for the reasons
agents/llm/fake_client.py's own docstring gives: no network dependency, no
flake from a live model's non-determinism in CI).

REQ-073 ("[REQ-073] Audit logs shall be retained for a minimum of one
year.", planted in rfp.pdf) and REQ-074 ("[REQ-074] Audit logs shall be
retained for a minimum of three years.", planted in technical_annexure.docx)
are a deliberate divergent-restatement pair - same obligation, materially
different duration, split across two documents so they land in two separate
extraction batches (agents/requirements/batch.py splits on document_id
boundaries). The open question since B5: does live Gemini extraction ever
drop one of the two, the way it silently dropped one of a near-identical
filler pair during B5's own investigation?

This script runs REAL extraction (uv run python scripts/run_eval.py's own
GeminiClient/EXTRACTOR_MODEL setup, not FakeLLMClient) against just these
two source documents, N times in a row against fresh tenders, and reports
per-run whether both requirements survived with their distinct durations
intact. Recall is already known to be nondeterministic at temperature=0
(KNOWN_LIMITATIONS.md: 0.941-0.959 across B5's own runs) - a single pass
cannot distinguish "this case is safe" from "this run got lucky," which is
why this script defaults to repeating the check rather than running once.

Run: uv run python scripts/verify_req073_074_divergence.py [N]
     (N = number of runs, default 3)
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
from tenderos_worker.jobs.ingestion import parse_and_embed_document

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "data" / "synthetic" / "tender_a"

_SOURCE_FILES = [
    ("rfp.pdf", "application/pdf"),
    ("technical_annexure.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
]


def _build_client_for_run(run_index: int) -> GeminiClient:
    # GeminiClient's response cache is keyed purely on (model, schema,
    # system_prompt, user_prompt) content (agents/llm/gemini_adapter.py's
    # _cache_key) - no tender/run scoping at all. rfp.pdf and technical_
    # annexure.docx produce byte-identical chunk text every run, so sharing
    # one Redis DB across all N runs would make run 2+ silently replay run
    # 1's cached response instead of making an independent live call -
    # exactly the kind of false "consistency" this script exists to rule
    # out. Each run gets its own logical Redis DB (16-99, well clear of the
    # app's real DB 0) so every run is a genuinely fresh, independent call.
    # Redis' default config only provides DBs 0-15, so this stays in 1-15,
    # not adjacent to anything else this project uses.
    base_url = settings.redis_url.rsplit("/", 1)[0]
    redis_client = AsyncRedis.from_url(f"{base_url}/{run_index}")
    return GeminiClient(
        api_key=settings.gemini_api_key,
        redis_client=redis_client,
        cache_ttl_seconds=settings.llm_cache_ttl_seconds,
    )


async def _run_once(run_index: int) -> dict:
    client = _build_client_for_run(run_index)
    suffix = f"{run_index}-{uuid.uuid4().hex[:6]}"
    registered = await auth_service.register(
        email=f"req073-074-verify-{suffix}@tenderos.local",
        password="verification-run-not-a-real-account",
        organization_name=f"REQ-073-074 Verify Org {suffix}",
    )
    tender = await tender_service.create_tender(
        organization_id=registered.organization.id,
        name=f"REQ-073-074 Verify Tender {suffix}",
        created_by=registered.user.id,
    )

    for filename, mime_type in _SOURCE_FILES:
        content = (DOCS_DIR / filename).read_bytes()
        document, is_new = await document_service.create_document(
            organization_id=registered.organization.id,
            actor_user_id=registered.user.id,
            tender_id=tender.id,
            filename=filename,
            content=content,
            mime_type=mime_type,
        )
        if is_new:
            await parse_and_embed_document(str(document.id))

    async with UnitOfWork() as uow:
        chunks = await uow.documents.list_chunks_for_tender(tender.id)

    extraction = await extract_requirements(
        client, settings.extractor_model, chunks, tender.id
    )
    requirements = await persist_extraction_results(tender.id, extraction.requirements)

    one_year = [r for r in requirements if "[REQ-073]" in r.requirement_text]
    three_years = [r for r in requirements if "[REQ-074]" in r.requirement_text]

    return {
        "tender_id": str(tender.id),
        "total_extracted": len(requirements),
        "req_073_present": len(one_year) == 1,
        "req_073_text": one_year[0].requirement_text if one_year else None,
        "req_073_has_one_year": bool(one_year) and "one year" in one_year[0].requirement_text,
        "req_074_present": len(three_years) == 1,
        "req_074_text": three_years[0].requirement_text if three_years else None,
        "req_074_has_three_years": bool(three_years) and "three years" in three_years[0].requirement_text,
    }


async def main() -> int:
    n_runs = int(sys.argv[1]) if len(sys.argv) > 1 else 3

    if not settings.gemini_api_key or settings.gemini_api_key == "changeme":
        print(
            "GEMINI_API_KEY is not set in your .env - this check needs a real "
            "model run, not the fake test client. Aborting before spending anything.",
            file=sys.stderr,
        )
        return 2

    results = []
    for i in range(1, n_runs + 1):
        print(f"--- run {i}/{n_runs} ---")
        result = await _run_once(i)
        results.append(result)
        print(f"  total requirements extracted this run: {result['total_extracted']}")
        print(
            f"  REQ-073 (1yr): present={result['req_073_present']} "
            f"duration_intact={result['req_073_has_one_year']}"
        )
        if result["req_073_text"]:
            print(f"    text: {result['req_073_text']!r}")
        print(
            f"  REQ-074 (3yr): present={result['req_074_present']} "
            f"duration_intact={result['req_074_has_three_years']}"
        )
        if result["req_074_text"]:
            print(f"    text: {result['req_074_text']!r}")
        print()

    both_ok = [
        r["req_073_present"] and r["req_073_has_one_year"]
        and r["req_074_present"] and r["req_074_has_three_years"]
        for r in results
    ]

    print("=== SUMMARY ===")
    for i, ok in enumerate(both_ok, start=1):
        print(f"  run {i}: {'BOTH PRESENT, DURATIONS INTACT' if ok else 'FAILED - see detail above'}")

    if all(both_ok):
        print(f"\nResult: {n_runs}/{n_runs} runs kept both requirements distinct. VERIFIED for this case.")
        return 0
    elif not any(both_ok):
        print(f"\nResult: 0/{n_runs} runs kept both requirements distinct. CONFIRMED DEFECT.")
        return 1
    else:
        passed = sum(both_ok)
        print(
            f"\nResult: INCONSISTENT - {passed}/{n_runs} runs kept both requirements distinct, "
            f"{n_runs - passed}/{n_runs} did not. This is itself the finding."
        )
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
