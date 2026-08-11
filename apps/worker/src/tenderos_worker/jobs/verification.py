import logging
import uuid

from redis.asyncio import Redis as AsyncRedis
from tenderos_backend.agents.llm.client import LLMClient
from tenderos_backend.agents.llm.gemini_adapter import GeminiClient
from tenderos_backend.agents.requirements.verify import verify_and_persist
from tenderos_backend.core.config import settings
from tenderos_backend.db.unit_of_work import UnitOfWork

logger = logging.getLogger(__name__)


def _build_llm_client() -> LLMClient:
    # Overridable by tests the same way orchestration/nodes/
    # requirement_extraction.py's identical helper is - see that module's
    # docstring for why this is a function, not a module-level singleton.
    redis_client = AsyncRedis.from_url(settings.redis_url)
    return GeminiClient(
        api_key=settings.gemini_api_key,
        redis_client=redis_client,
        cache_ttl_seconds=settings.llm_cache_ttl_seconds,
    )


async def verify_requirement_job(requirement_id: str) -> None:
    """Enqueued on RequirementVerificationRequested (see
    outbox_dispatcher.py's routing table) - G5's on-demand
    POST /requirements/{id}/verify, run here rather than inside the API
    request per line 538. A fresh GeminiClient per job invocation, same as
    every other worker job constructs its own storage/DB clients rather than
    sharing a module-level singleton across jobs.
    """
    req_id = uuid.UUID(requirement_id)

    async with UnitOfWork() as uow:
        requirement = await uow.requirements.get(req_id)
        if requirement is None:
            raise ValueError(f"Requirement {req_id} not found")
        tender_id = requirement.tender_id

    client = _build_llm_client()

    output = await verify_and_persist(
        client,
        settings.verifier_model,
        req_id,
        tender_id,
    )
    logger.info(
        "verification completed for requirement %s (supported=%s, mandatory_ok=%s)",
        req_id,
        output.supported_by_source,
        output.mandatory_language_supported,
    )
