import logging
import uuid
from dataclasses import dataclass, field

from tenderos_backend.agents.llm.client import LLMClient, LLMError, LLMResult, call_with_repair
from tenderos_backend.agents.prompts.loader import bundle_version, load_prompt
from tenderos_backend.agents.prompts.untrusted_content import wrap_untrusted_content
from tenderos_backend.agents.requirements.batch import ExtractionBatch, build_batches
from tenderos_backend.agents.requirements.mandatory_classifier import (
    MandatoryClassification,
    classify_mandatory_language,
)
from tenderos_backend.agents.schemas.requirements import ExtractionOutput
from tenderos_backend.db.models.document import DocumentChunk
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services.requirement_service import (
    ClarificationDraft,
    RequirementDraft,
    SubmissionItemDraft,
)

logger = logging.getLogger(__name__)

_AGENT_NAME = "requirement_extraction"


@dataclass
class ExtractionResult:
    requirements: list[RequirementDraft] = field(default_factory=list)
    submission_items: list[SubmissionItemDraft] = field(default_factory=list)
    clarifications: list[ClarificationDraft] = field(default_factory=list)


def _render_batch_prompt(batch: ExtractionBatch) -> str:
    # B15: ref.text is raw text from an uploaded tender document - the
    # single most directly attacker-reachable content in this whole
    # pipeline (a bidder never controls resumes/knowledge-base content,
    # but the buyer's own RFP is exactly what this function batches). Routed
    # through wrap_untrusted_content instead of the old bare
    # f"[{label}]\n{text}" interpolation - see that module's own docstring.
    return "\n\n".join(wrap_untrusted_content(ref.label, ref.text) for ref in batch.chunk_refs)


def _resolve_mandatory(llm_mandatory: bool, classification: MandatoryClassification) -> bool:
    # The classifier's verdict wins when it reached one (Build Guide Part J
    # names it as its own build item, a real second signal - not just an
    # advisory cross-check); when it can't reach one (ambiguous=True), the
    # LLM's own contextual judgment is the only signal available, so that's
    # what's used. This combination lives here, not inside the classifier or
    # the schema, because it's specifically about how the two independent
    # signals get reconciled into one persisted value - the classifier itself
    # stays a pure text->verdict function with no knowledge this combining
    # step even exists.
    if classification.ambiguous:
        return llm_mandatory
    return classification.mandatory


async def _write_trace(
    *,
    tender_id: uuid.UUID,
    workflow_run_id: uuid.UUID | None,
    task_id: uuid.UUID | None,
    model: str,
    status: str,
    result: LLMResult[ExtractionOutput] | None = None,
    error_code: str | None = None,
) -> None:
    async with UnitOfWork() as uow:
        await uow.agent_traces.create(
            tender_id=tender_id,
            workflow_run_id=workflow_run_id,
            task_id=task_id,
            agent_name=_AGENT_NAME,
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


async def extract_requirements(
    client: LLMClient,
    model: str,
    chunks: list[DocumentChunk],
    tender_id: uuid.UUID,
    workflow_run_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
) -> ExtractionResult:
    """Called once by orchestration/nodes/requirement.py's real extraction
    node - one LLM call per ExtractionBatch (agents/requirements/batch.py),
    never one call per document or per tender. Every call, success or
    failure, gets one agent_traces row (E1).
    """
    system_prompt = load_prompt("requirement_extraction")
    batches = build_batches(chunks)
    result = ExtractionResult()

    for batch in batches:
        user_prompt = _render_batch_prompt(batch)
        try:
            llm_result = await call_with_repair(
                client,
                agent_name=_AGENT_NAME,
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_schema=ExtractionOutput,
            )
        except LLMError as exc:
            await _write_trace(
                tender_id=tender_id,
                workflow_run_id=workflow_run_id,
                task_id=task_id,
                model=model,
                status="failed",
                error_code=type(exc).__name__,
            )
            raise

        await _write_trace(
            tender_id=tender_id,
            workflow_run_id=workflow_run_id,
            task_id=task_id,
            model=model,
            status="completed",
            result=llm_result,
        )

        for item in llm_result.parsed.requirements:
            ref = batch.resolve(item.source_chunk_ref)
            if ref is None:
                # A fabricated/mistyped label from the model - dropping this
                # one item costs recall on this specific requirement, but
                # failing the whole batch over it would cost recall on every
                # other, correctly-labeled item in the same batch. Logged,
                # not silent.
                logger.warning(
                    "extraction: unresolvable source_chunk_ref %r, dropping item",
                    item.source_chunk_ref,
                )
                continue
            classification = classify_mandatory_language(item.requirement_text)
            result.requirements.append(
                RequirementDraft(
                    requirement_text=item.requirement_text,
                    category=item.category,
                    mandatory=_resolve_mandatory(item.mandatory, classification),
                    source_chunk_id=ref.chunk_id,
                    document_id=ref.document_id,
                    confidence=item.confidence,
                )
            )

        for sub_item in llm_result.parsed.submission_items:
            sub_ref = (
                batch.resolve(sub_item.source_chunk_ref) if sub_item.source_chunk_ref else None
            )
            result.submission_items.append(
                SubmissionItemDraft(
                    name=sub_item.name,
                    mandatory=sub_item.mandatory,
                    source_chunk_id=sub_ref.chunk_id if sub_ref else None,
                )
            )

        for clarification in llm_result.parsed.clarifications:
            result.clarifications.append(
                ClarificationDraft(
                    question=clarification.question,
                    reason=clarification.reason,
                )
            )

    return result
