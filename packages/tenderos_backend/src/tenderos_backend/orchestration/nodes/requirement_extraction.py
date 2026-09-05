import logging
import uuid

from tenderos_backend.agents.llm.client import LLMClient
from tenderos_backend.agents.llm.factory import build_llm_client
from tenderos_backend.agents.requirements.extract import extract_requirements
from tenderos_backend.agents.requirements.verify import verify_and_persist
from tenderos_backend.core.config import settings
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services.requirement_service import (
    persist_clarifications,
    persist_extraction_results,
    persist_submission_items,
)
from tenderos_backend.orchestration.state import TenderState

logger = logging.getLogger(__name__)


def _build_llm_client() -> LLMClient:
    # A function, not a module-level singleton, specifically so tests can
    # monkeypatch this one name (monkeypatch.setattr(module, "_build_llm_client",
    # lambda: fake_client)) instead of a real GeminiClient - every automated
    # test in this repo runs against FakeLLMClient (see agents/llm/fake_client.py's
    # own docstring), never a live model. B17: the actual gemini-vs-fake
    # decision now lives in agents/llm/factory.py::build_llm_client, shared
    # by all nine call sites that used to each make this decision
    # independently - this is a one-line delegate, kept only so existing
    # tests' monkeypatch target (this exact module-level name) still works.
    return build_llm_client()


async def requirement_extraction_node(state: TenderState) -> dict:
    """Replaces B3's no-op requirement_stub_node - the graph's first real
    LLM-backed work, between supervisor (which marks status
    "extracting_requirements") and requirement_gate (the human review pause).

    Runs once per workflow run: load every chunk for the tender, extract,
    auto-verify every mandatory item, persist, mark the workflow_task
    completed. All of this happens inside the worker job that's already
    running this graph (jobs/workflow.py's run_workflow) - never inside a
    FastAPI request (line 538).
    """
    workflow_run_id = uuid.UUID(state["workflow_run_id"])
    tender_id = uuid.UUID(state["tender_id"])
    task_key = f"requirement_extraction:{workflow_run_id}"

    async with UnitOfWork() as uow:
        task = await uow.workflow.get_task_by_key(workflow_run_id, task_key)
        if task is None:
            task = await uow.workflow.create_task(
                workflow_run_id=workflow_run_id,
                task_key=task_key,
                task_type="requirement_extraction",
                assigned_agent="requirements",
                priority=10,
                input_refs={},
            )
        chunks = await uow.documents.list_chunks_for_tender(tender_id)
        await uow.commit()

    try:
        client = _build_llm_client()
        extraction = await extract_requirements(
            client,
            settings.extractor_model,
            chunks,
            tender_id,
            workflow_run_id=workflow_run_id,
            task_id=task.id,
        )

        requirements = await persist_extraction_results(tender_id, extraction.requirements)
        await persist_submission_items(tender_id, extraction.submission_items)
        await persist_clarifications(tender_id, extraction.clarifications)

        # 10.4/B5's extract-then-verify pass, scoped to mandatory items
        # ("load-bearing" - a missed or wrongly-mandatory item can invalidate
        # a bid, per PRD 10.3). Sequential, not gathered in parallel - the
        # same free-tier rate-limit concern that shaped the extraction
        # batch size applies here too, and the adapter's own backoff already
        # paces a sequential loop without a separate rate limiter.
        for requirement in requirements:
            if not requirement.mandatory:
                continue
            try:
                await verify_and_persist(
                    client,
                    settings.verifier_model,
                    requirement.id,
                    tender_id,
                    workflow_run_id=workflow_run_id,
                    task_id=task.id,
                )
            except Exception:
                # One requirement's verification failing (a transient
                # provider error surviving I4's retries, say) should not
                # block every other mandatory item from being verified, nor
                # the gate from opening - it's left unverified, which is
                # already a visible, honest state a reviewer can act on.
                logger.exception(
                    "verification failed for requirement %s during extraction pass",
                    requirement.id,
                )

        async with UnitOfWork() as uow:
            await uow.workflow.mark_task_completed(
                task.id,
                output_refs={
                    "requirement_count": len(requirements),
                    "submission_item_count": len(extraction.submission_items),
                    "clarification_count": len(extraction.clarifications),
                },
            )
            await uow.commit()

    except Exception as exc:
        async with UnitOfWork() as uow:
            await uow.workflow.mark_task_failed(task.id, error_code=type(exc).__name__)
            await uow.commit()
        raise

    return {"status": "awaiting_requirement_review"}
