import logging
import uuid

from tenderos_backend.agents.llm.client import LLMClient, LLMError, LLMResult, call_with_repair
from tenderos_backend.agents.prompts.loader import bundle_version, load_prompt
from tenderos_backend.agents.schemas.knowledge import (
    CapabilityStatementExtraction,
    ExtractedEmployeeProfile,
)
from tenderos_backend.db.models.document import DocumentChunk
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services.knowledge_service import ResolvedCapability, ResolvedPastProject

logger = logging.getLogger(__name__)


async def _write_trace(
    *,
    agent_name: str,
    organization_id: uuid.UUID,
    model: str,
    status: str,
    result: LLMResult | None = None,
    error_code: str | None = None,
) -> None:
    async with UnitOfWork() as uow:
        # organization_id, not tender_id - both resumes and capability
        # statements belong to the org's reusable knowledge library, not any
        # one tender. This is the call site the agent_traces.tender_id-XOR-
        # organization_id split exists for.
        await uow.agent_traces.create(
            organization_id=organization_id,
            agent_name=agent_name,
            provider=result.provider if result else None,
            model=model,
            prompt_version=bundle_version(),
            latency_ms=result.latency_ms if result else None,
            input_tokens=result.input_tokens if result else None,
            output_tokens=result.output_tokens if result else None,
            estimated_cost_usd=result.estimated_cost_usd if result else None,
            status=status,
            error_code=error_code,
        )
        await uow.commit()


async def extract_employee_profile(
    client: LLMClient,
    model: str,
    resume_chunks: list[DocumentChunk],
    organization_id: uuid.UUID,
) -> ExtractedEmployeeProfile:
    """One call per resume, not one call per chunk-batch the way B5's
    requirement extraction works - a resume is a single bounded document
    describing one coherent profile, so agents/requirements/batch.py's
    token-budget packer (built for a whole tender's worth of chunks, where
    no single call could hold everything) is the wrong tool here. Callers
    pass every chunk belonging to one resume document
    (uow.documents.list_chunks_for_document, already ordered by
    chunk_index) and get back exactly one profile.
    """
    system_prompt = load_prompt("resume_extraction")
    # heading_path carries structural context chunk_text alone doesn't -
    # confirmed live against the synthetic fixtures: a resume's name is
    # rendered as the document's H1 (_render_resume_docx), which
    # ingestion/parse_docx.py tracks as heading_path, not body text. Without
    # this prefix the LLM never sees the candidate's name at all, since
    # every resume in this template puts it nowhere else.
    user_prompt = "\n\n".join(
        f"[{' > '.join(chunk.heading_path)}]\n{chunk.chunk_text}"
        if chunk.heading_path
        else chunk.chunk_text
        for chunk in resume_chunks
    )

    agent_name = "resume_extraction"
    try:
        llm_result = await call_with_repair(
            client,
            agent_name=agent_name,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=ExtractedEmployeeProfile,
        )
    except LLMError as exc:
        await _write_trace(
            agent_name=agent_name,
            organization_id=organization_id,
            model=model,
            status="failed",
            error_code=type(exc).__name__,
        )
        raise

    await _write_trace(
        agent_name=agent_name,
        organization_id=organization_id,
        model=model,
        status="completed",
        result=llm_result,
    )
    return llm_result.parsed


class _CapabilityStatementResult:
    def __init__(
        self, past_projects: list[ResolvedPastProject], capabilities: list[ResolvedCapability]
    ) -> None:
        self.past_projects = past_projects
        self.capabilities = capabilities


async def extract_capability_statement(
    client: LLMClient,
    model: str,
    document_chunks: list[DocumentChunk],
    organization_id: uuid.UUID,
) -> _CapabilityStatementResult:
    """One call per capability-statement document, same
    one-document-one-call shape as extract_employee_profile above (a
    company's capability statement is one bounded document, not a whole
    tender's worth of chunks needing token-budget batching).

    Unlike extract_employee_profile, this resolves each item's
    source_chunk_ref back to a real chunk (retrieval needs to know exactly
    which chunk a past_project/capability came from to set
    knowledge_items.source_chunk_id - an employee profile has no per-field
    citation need like this since B6's own gate only ever cites
    certifications/past_projects as evidence, confirmed by inspecting every
    valid_evidence_keys entry in the ground truth).
    """
    system_prompt = load_prompt("capability_statement_extraction")
    labels = [f"chunk_{i + 1}" for i in range(len(document_chunks))]
    user_prompt = "\n\n".join(
        f"[{label}]\n{chunk.chunk_text}" for label, chunk in zip(labels, document_chunks, strict=True)
    )

    agent_name = "capability_statement_extraction"
    try:
        llm_result = await call_with_repair(
            client,
            agent_name=agent_name,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=CapabilityStatementExtraction,
        )
    except LLMError as exc:
        await _write_trace(
            agent_name=agent_name,
            organization_id=organization_id,
            model=model,
            status="failed",
            error_code=type(exc).__name__,
        )
        raise

    await _write_trace(
        agent_name=agent_name,
        organization_id=organization_id,
        model=model,
        status="completed",
        result=llm_result,
    )

    chunk_by_label = dict(zip(labels, document_chunks, strict=True))

    past_projects: list[ResolvedPastProject] = []
    for item in llm_result.parsed.past_projects:
        chunk = chunk_by_label.get(item.source_chunk_ref)
        if chunk is None:
            logger.warning(
                "capability_statement extraction: unresolvable source_chunk_ref %r for "
                "project %r, dropping item",
                item.source_chunk_ref,
                item.name,
            )
            continue
        past_projects.append(
            ResolvedPastProject(
                name=item.name,
                client=item.client,
                year=item.year,
                description=item.description,
                source_ref=item.source_ref,
                chunk_id=chunk.id,
                document_id=chunk.document_id,
            )
        )

    capabilities: list[ResolvedCapability] = []
    for item in llm_result.parsed.capabilities:
        chunk = chunk_by_label.get(item.source_chunk_ref)
        if chunk is None:
            logger.warning(
                "capability_statement extraction: unresolvable source_chunk_ref %r for "
                "capability %r, dropping item",
                item.source_chunk_ref,
                item.name,
            )
            continue
        capabilities.append(
            ResolvedCapability(
                name=item.name,
                description=item.description,
                source_ref=item.source_ref,
                chunk_id=chunk.id,
                document_id=chunk.document_id,
            )
        )

    return _CapabilityStatementResult(past_projects=past_projects, capabilities=capabilities)
