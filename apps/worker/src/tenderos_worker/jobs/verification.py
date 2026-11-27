import logging
import uuid

from tenderos_backend.agents.llm.client import LLMClient
from tenderos_backend.agents.llm.factory import build_llm_client
from tenderos_backend.agents.requirements.verify import verify_and_persist
from tenderos_backend.core.config import settings
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services.requirement_service import (
    RequirementNotFoundError,
    verification_recorded_for_event,
)

logger = logging.getLogger(__name__)


def _build_llm_client(organization_id: uuid.UUID) -> LLMClient:
    # Overridable by tests the same way orchestration/nodes/
    # requirement_extraction.py's identical helper is - see that module's
    # docstring for why this is a function, not a module-level singleton.
    # B17: delegates to agents/llm/factory.py::build_llm_client, the real
    # gemini-vs-fake decision.
    return build_llm_client(organization_id=organization_id)


async def verify_requirement_job(requirement_id: str, event_id: str | None = None) -> None:
    """Enqueued on RequirementVerificationRequested (see
    outbox_dispatcher.py's routing table) - G5's on-demand
    POST /requirements/{id}/verify, run here rather than inside the API
    request per line 538. A fresh GeminiClient per job invocation, same as
    every other worker job constructs its own storage/DB clients rather than
    sharing a module-level singleton across jobs.
    """
    req_id = uuid.UUID(requirement_id)

    # Phase 4 consumer guard. The outbox delivers
    # RequirementVerificationRequested at least once; a second delivery
    # of the same event would repeat the LLM call. Skip only when a
    # completed verification already carries this exact event id: a
    # different, later request still runs. event_id is optional so a job
    # queued before this change (requirement id only) still runs.
    if event_id is not None and await verification_recorded_for_event(req_id, uuid.UUID(event_id)):
        logger.info(
            "requirement %s already verified for event %s; duplicate delivery, skipping",
            req_id,
            event_id,
        )
        return

    async with UnitOfWork() as uow:
        requirement = await uow.requirements.get(req_id)
        if requirement is None:
            # Plan item 126: a person's merge or split deletes requirements,
            # and a verification requested before it may still be queued.
            # Nothing is left to verify: a skip, never a failed job (one in
            # RQ's failed registry, tenderos_rq_failed_jobs).
            logger.info("requirement %s no longer exists; verification skipped", req_id)
            return
        tender_id = requirement.tender_id
        # PR 35: the client's cache entries are the tender's organization's.
        tender = await uow.tenders.get(tender_id)
        if tender is None:
            raise ValueError(f"Tender {tender_id} not found")
        organization_id = tender.organization_id

    client = _build_llm_client(organization_id)

    try:
        output = await verify_and_persist(
            client,
            settings.verifier_model,
            req_id,
            tender_id,
            event_id=uuid.UUID(event_id) if event_id is not None else None,
        )
    except RequirementNotFoundError:
        # Deleted by a merge or split while the model judged it: its verdict
        # has nothing to land on (the trace still records the call).
        logger.info("requirement %s was deleted during verification; nothing written", req_id)
        return
    logger.info(
        "verification completed for requirement %s (supported=%s, mandatory_ok=%s)",
        req_id,
        output.supported_by_source,
        output.mandatory_language_supported,
    )
