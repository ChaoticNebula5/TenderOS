"""B6's stop-gate: run real hybrid retrieval against the synthetic tender_a
package and report Recall@10 / citation correctness against evidence keys
in Part L2 ground truth (extended by tender_a_knowledge.jsonl's name
fields, since the requirements file only carries evidence *keys*).

Needs a real extracted requirement for every [REQ-nnn] token evidence is
attached to (retrieval queries with a requirement's own text), so this
script runs B5's extraction first, then B6's full knowledge import
(employee-master, certifications, capability statement, resumes,
WORKED_ON) against a fresh org, then evaluates retrieval. Same
in-process, real-LLM posture as scripts/run_eval.py - this is not run
against the fake client, the number has to mean something.

Run: uv run python scripts/run_retrieval_eval.py
"""

import asyncio
import sys
import uuid
from datetime import date
from pathlib import Path

from redis.asyncio import Redis as AsyncRedis
from sqlalchemy import select

from tenderos_backend.agents.knowledge.certifications_xlsx import parse_certifications
from tenderos_backend.agents.knowledge.employee_master import parse_employee_master
from tenderos_backend.agents.knowledge.extract import (
    extract_capability_statement,
    extract_employee_profile,
)
from tenderos_backend.agents.knowledge.relationships import (
    ProjectCandidateRef,
    extract_worked_on,
    resolve_worked_on_verification,
)
from tenderos_backend.agents.llm.gemini_adapter import GeminiClient
from tenderos_backend.agents.requirements.extract import extract_requirements
from tenderos_backend.core.config import settings
from tenderos_backend.db.models.knowledge import PastProject
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import auth_service, document_service, tender_service
from tenderos_backend.domain.services.knowledge_service import (
    import_capability,
    import_certification_row,
    import_employee_master_row,
    import_past_project,
    import_resume,
    import_worked_on_relationship,
)
from tenderos_backend.domain.services.requirement_service import persist_extraction_results
from tenderos_backend.evals.datasets import load_evidence_key_names, load_requirements_ground_truth
from tenderos_backend.evals.report import format_retrieval_report
from tenderos_backend.evals.retrieval import evaluate_retrieval
from tenderos_worker.jobs.ingestion import parse_and_embed_document

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "data" / "synthetic" / "tender_a"
REQUIREMENTS_GROUND_TRUTH_PATH = REPO_ROOT / "data" / "ground_truth" / "tender_a_requirements.jsonl"
KNOWLEDGE_GROUND_TRUTH_PATH = REPO_ROOT / "data" / "ground_truth" / "tender_a_knowledge.jsonl"

_RFP_MIME_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
RESUME_FILES = [
    "resume_dana_whitfield.docx",
    "resume_marcus_ilori.docx",
    "resume_priya_ostrowski.docx",
    "resume_tomas_reyes.docx",
    "resume_dana_whitfield_resubmit.docx",
]


async def _setup_tender() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    run_id = uuid.uuid4().hex[:8]
    register_result = await auth_service.register(
        email=f"retrieval-eval-{run_id}@tenderos.local",
        password="eval-run-not-a-real-account",
        organization_name=f"Retrieval Eval Org {run_id}",
    )
    tender = await tender_service.create_tender(
        organization_id=register_result.organization.id,
        name=f"B6 Retrieval Eval Tender {run_id}",
        created_by=register_result.user.id,
    )
    return tender.id, register_result.organization.id, register_result.user.id


async def _run_requirement_extraction(
    client: GeminiClient, tender_id: uuid.UUID, org_id: uuid.UUID
) -> None:
    for path in sorted(DOCS_DIR.iterdir()):
        if path.suffix not in _RFP_MIME_TYPES:
            continue
        content = path.read_bytes()
        document, is_new = await document_service.create_document(
            organization_id=org_id,
            actor_user_id=uuid.uuid4(),
            tender_id=tender_id,
            filename=path.name,
            content=content,
            mime_type=_RFP_MIME_TYPES[path.suffix],
        )
        if is_new:
            await parse_and_embed_document(str(document.id))

    async with UnitOfWork() as uow:
        chunks = await uow.documents.list_chunks_for_tender(tender_id)
    extraction = await extract_requirements(client, settings.extractor_model, chunks, tender_id)
    requirements = await persist_extraction_results(tender_id, extraction.requirements)
    print(f"{len(requirements)} requirements extracted and persisted")


async def _upload_and_ingest_org_doc(org_id, user_id, filename, mime_type, doc_type):
    content = (DOCS_DIR / filename).read_bytes()
    document, _is_new = await document_service.create_document(
        organization_id=org_id,
        actor_user_id=user_id,
        filename=filename,
        content=content,
        mime_type=mime_type,
        side="company",
        doc_type=doc_type,
    )
    await parse_and_embed_document(str(document.id))
    return document, content


async def _run_knowledge_import(client: GeminiClient, org_id: uuid.UUID, user_id: uuid.UUID) -> None:
    em_doc, em_content = await _upload_and_ingest_org_doc(
        org_id, user_id, "employee_master.xlsx", XLSX_MIME, "employee_master"
    )
    async with UnitOfWork() as uow:
        batch = await uow.knowledge.create_import_batch(
            organization_id=org_id, import_type="employee_master_xlsx", source_document_id=em_doc.id
        )
        await uow.commit()
        em_batch_id = batch.id
    for row in parse_employee_master(em_content):
        await import_employee_master_row(org_id, em_batch_id, row)

    cert_doc, cert_content = await _upload_and_ingest_org_doc(
        org_id, user_id, "certifications.xlsx", XLSX_MIME, "certification_documents"
    )
    async with UnitOfWork() as uow:
        batch = await uow.knowledge.create_import_batch(
            organization_id=org_id,
            import_type="certification_documents",
            source_document_id=cert_doc.id,
        )
        await uow.commit()
        cert_batch_id = batch.id
    for row in parse_certifications(cert_content):
        await import_certification_row(org_id, cert_batch_id, cert_doc.id, row, today=date.today())

    cap_doc, _cap_content = await _upload_and_ingest_org_doc(
        org_id, user_id, "capability_statement.docx", DOCX_MIME, "capability_statement"
    )
    async with UnitOfWork() as uow:
        cap_chunks = await uow.documents.list_chunks_for_document(cap_doc.id)
    extraction = await extract_capability_statement(client, settings.extractor_model, cap_chunks, org_id)
    async with UnitOfWork() as uow:
        batch = await uow.knowledge.create_import_batch(
            organization_id=org_id, import_type="manual", source_document_id=cap_doc.id
        )
        await uow.commit()
        cap_batch_id = batch.id
    for i, project in enumerate(extraction.past_projects):
        await import_past_project(org_id, cap_batch_id, f"project_{i}", project)
    for i, capability in enumerate(extraction.capabilities):
        await import_capability(org_id, cap_batch_id, f"capability_{i}", capability)

    async with UnitOfWork() as uow:
        project_items = await uow.knowledge.list_knowledge_items_for_org(org_id, item_type="past_project")
        candidates = []
        for i, ki in enumerate(project_items):
            pp = (
                await uow.session.execute(select(PastProject).where(PastProject.knowledge_item_id == ki.id))
            ).scalar_one()
            candidates.append(
                ProjectCandidateRef(
                    label=f"project_{i + 1}", knowledge_item_id=ki.id, name=ki.title, client=pp.client_name
                )
            )

    async with UnitOfWork() as uow:
        batch = await uow.knowledge.create_import_batch(organization_id=org_id, import_type="bulk_resumes")
        await uow.commit()
        resume_batch_id = batch.id

    for filename in RESUME_FILES:
        resume_doc, _content = await _upload_and_ingest_org_doc(org_id, user_id, filename, DOCX_MIME, "resume")
        async with UnitOfWork() as uow:
            resume_chunks = await uow.documents.list_chunks_for_document(resume_doc.id)
        profile = await extract_employee_profile(client, settings.extractor_model, resume_chunks, org_id)
        item = await import_resume(
            org_id, resume_batch_id, filename, resume_doc.id, profile, [c.id for c in resume_chunks]
        )
        if item.target_knowledge_item_id is None:
            # Ambiguous multi-candidate match with nothing to attach a
            # WORKED_ON edge to - a needs_review row that DID resolve to
            # one candidate employee still proceeds (see the KNOWN_LIMITATIONS-
            # worthy gap this fixed relative to the first ad-hoc verification
            # run in this session, where this check was wrongly status-gated).
            continue
        worked_on = await extract_worked_on(
            client, settings.extractor_model, resume_chunks, candidates, org_id
        )
        for wo_candidate in worked_on:
            resolution = resolve_worked_on_verification(wo_candidate, profile.project_mentions)
            await import_worked_on_relationship(
                org_id, item.target_knowledge_item_id, wo_candidate, resolution
            )
    print("knowledge import complete")


async def main() -> int:
    if not settings.gemini_api_key or settings.gemini_api_key == "changeme":
        print(
            "GEMINI_API_KEY is not set in your .env - the stop-gate needs a real "
            "model run, not the fake test client. Aborting before spending anything.",
            file=sys.stderr,
        )
        return 2

    tender_id, org_id, user_id = await _setup_tender()
    print(f"created eval tender {tender_id} / org {org_id}")

    redis_client = AsyncRedis.from_url(settings.redis_url)
    client = GeminiClient(
        api_key=settings.gemini_api_key,
        redis_client=redis_client,
        cache_ttl_seconds=settings.llm_cache_ttl_seconds,
    )

    await _run_requirement_extraction(client, tender_id, org_id)
    await _run_knowledge_import(client, org_id, user_id)

    ground_truth = load_requirements_ground_truth(REQUIREMENTS_GROUND_TRUTH_PATH)
    evidence_names = load_evidence_key_names(KNOWLEDGE_GROUND_TRUTH_PATH)
    report = await evaluate_retrieval(
        ground_truth,
        evidence_names,
        org_id,
        tender_id,
        retrieval_top_k=settings.retrieval_top_k,
        rerank_top_n=settings.rerank_top_n,
    )
    print()
    print(format_retrieval_report(report))

    gate_passed = report.recall_at_10 >= 0.90 and report.citation_correctness >= 0.95
    return 0 if gate_passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
