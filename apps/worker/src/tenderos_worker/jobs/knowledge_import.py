import logging
import uuid
from datetime import date

from redis.asyncio import Redis as AsyncRedis
from sqlalchemy import select
from tenderos_backend.agents.knowledge.certifications_xlsx import parse_certifications
from tenderos_backend.agents.knowledge.employee_master import parse_employee_master
from tenderos_backend.agents.knowledge.extract import (
    extract_capability_statement,
    extract_employee_profile,
)
from tenderos_backend.agents.knowledge.relationships import (
    CapabilityCandidateRef,
    ProjectCandidateRef,
    extract_demonstrates,
    extract_worked_on,
    resolve_demonstrates_verification,
    resolve_worked_on_verification,
)
from tenderos_backend.agents.llm.client import LLMClient
from tenderos_backend.agents.llm.gemini_adapter import GeminiClient
from tenderos_backend.core.config import settings
from tenderos_backend.db.models.knowledge import PastProject
from tenderos_backend.db.storage import storage
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services.knowledge_service import (
    import_capability,
    import_certification_row,
    import_demonstrates_relationship,
    import_employee_master_row,
    import_past_project,
    import_resume,
    import_worked_on_relationship,
)

from tenderos_worker.jobs.ingestion import parse_and_embed_document

logger = logging.getLogger(__name__)


def _build_llm_client() -> LLMClient:
    # Same per-job-invocation client construction as verification.py's
    # identical helper - no shared module-level singleton across jobs.
    redis_client = AsyncRedis.from_url(settings.redis_url)
    return GeminiClient(
        api_key=settings.gemini_api_key,
        redis_client=redis_client,
        cache_ttl_seconds=settings.llm_cache_ttl_seconds,
    )


async def process_knowledge_document_import(document_id: str, batch_id: str) -> None:
    """Enqueued on KnowledgeDocumentUploaded (outbox_dispatcher.py) - the
    mechanical/LLM import step for one uploaded knowledge document, run
    here rather than inside the upload request (line 538). Calls
    parse_and_embed_document itself (idempotent, safe even if the
    ordinary DocumentUploaded event already triggered ingestion) rather
    than depending on dispatch order between the two events - this job is
    self-sufficient regardless of which fires first.
    """
    doc_id = uuid.UUID(document_id)
    batch_uuid = uuid.UUID(batch_id)

    async with UnitOfWork() as uow:
        batch = await uow.knowledge.get_import_batch(batch_uuid)
        if batch is None:
            raise ValueError(f"KnowledgeImportBatch {batch_uuid} not found")
        document = await uow.documents.get(doc_id)
        if document is None:
            raise ValueError(f"Document {doc_id} not found")

    await parse_and_embed_document(document_id)

    org_id = batch.organization_id

    if batch.import_type in ("employee_master_csv", "employee_master_xlsx"):
        content = await storage.get(document.storage_key)
        rows = parse_employee_master(content)
        for row in rows:
            await import_employee_master_row(org_id, batch_uuid, row)
        logger.info("employee-master import complete for batch %s (%d rows)", batch_uuid, len(rows))
        return

    if batch.import_type == "certification_documents":
        content = await storage.get(document.storage_key)
        rows = parse_certifications(content)
        for row in rows:
            await import_certification_row(org_id, batch_uuid, doc_id, row, today=date.today())
        logger.info("certification import complete for batch %s (%d rows)", batch_uuid, len(rows))
        return

    if batch.import_type == "manual":
        # Capability statement - "manual" is the closest fit in Part C's
        # locked import_type enum (no dedicated capability_statement
        # value); see knowledge_service's create_import_batch call site
        # for this same reasoning.
        await _process_capability_statement(doc_id, batch_uuid, org_id)
        return

    if batch.import_type == "bulk_resumes":
        await _process_resume(doc_id, batch_uuid, org_id, document.filename)
        return

    logger.warning(
        "unrecognized knowledge import batch type %r for batch %s", batch.import_type, batch_uuid
    )


async def _process_capability_statement(
    doc_id: uuid.UUID, batch_id: uuid.UUID, org_id: uuid.UUID
) -> None:
    client = _build_llm_client()
    async with UnitOfWork() as uow:
        chunks = await uow.documents.list_chunks_for_document(doc_id)
    extraction = await extract_capability_statement(
        client, settings.extractor_model, chunks, org_id
    )

    project_items: list[tuple[uuid.UUID, object]] = []
    for i, project in enumerate(extraction.past_projects):
        item = await import_past_project(org_id, batch_id, f"project_{i}", project)
        if item.target_knowledge_item_id is not None:
            project_items.append((item.target_knowledge_item_id, project))

    capability_items: list[tuple[uuid.UUID, object]] = []
    for i, capability in enumerate(extraction.capabilities):
        item = await import_capability(org_id, batch_id, f"capability_{i}", capability)
        if item.target_knowledge_item_id is not None:
            capability_items.append((item.target_knowledge_item_id, capability))

    candidate_capabilities = [
        CapabilityCandidateRef(
            label=f"capability_{i + 1}",
            knowledge_item_id=knowledge_item_id,
            name=capability.name,
            description=capability.description,
        )
        for i, (knowledge_item_id, capability) in enumerate(capability_items)
    ]

    for project_knowledge_item_id, project in project_items:
        demonstrates = await extract_demonstrates(
            client,
            settings.extractor_model,
            project.name,
            project.client,
            project.description,
            candidate_capabilities,
            org_id,
        )
        for candidate in demonstrates:
            resolution = resolve_demonstrates_verification(candidate, project.description)
            await import_demonstrates_relationship(
                org_id, project_knowledge_item_id, candidate, resolution
            )

    logger.info(
        "capability statement import complete for batch %s (%d projects, %d capabilities)",
        batch_id,
        len(project_items),
        len(capability_items),
    )


async def _process_resume(
    doc_id: uuid.UUID, batch_id: uuid.UUID, org_id: uuid.UUID, filename: str
) -> None:
    client = _build_llm_client()
    async with UnitOfWork() as uow:
        resume_chunks = await uow.documents.list_chunks_for_document(doc_id)
        project_items = await uow.knowledge.list_knowledge_items_for_org(
            org_id, item_type="past_project"
        )
        candidate_projects = []
        for i, knowledge_item in enumerate(project_items):
            past_project = (
                await uow.session.execute(
                    select(PastProject).where(PastProject.knowledge_item_id == knowledge_item.id)
                )
            ).scalar_one()
            candidate_projects.append(
                ProjectCandidateRef(
                    label=f"project_{i + 1}",
                    knowledge_item_id=knowledge_item.id,
                    name=knowledge_item.title,
                    client=past_project.client_name,
                )
            )

    profile = await extract_employee_profile(
        client, settings.extractor_model, resume_chunks, org_id
    )
    item = await import_resume(org_id, batch_id, filename, doc_id, profile)
    if item.target_knowledge_item_id is None:
        logger.info("resume import for %s produced no employee match, skipping WORKED_ON", filename)
        return

    worked_on = await extract_worked_on(
        client, settings.extractor_model, resume_chunks, candidate_projects, org_id
    )
    for candidate in worked_on:
        resolution = resolve_worked_on_verification(candidate, profile.project_mentions)
        await import_worked_on_relationship(
            org_id, item.target_knowledge_item_id, candidate, resolution
        )
    logger.info("resume import complete for %s", filename)
