import logging
import uuid

from redis.asyncio import Redis as AsyncRedis
from tenderos_backend.agents.llm.client import LLMClient
from tenderos_backend.agents.llm.gemini_adapter import GeminiClient
from tenderos_backend.core.config import settings
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.events.outbox_events import (
    AGGREGATE_COMPLIANCE_ENTRY,
    AGGREGATE_PROPOSAL_CLAIM,
    EVENT_COMPLIANCE_CHANGED,
    EVENT_PROPOSAL_CHANGED,
    compliance_changed_idempotency_key,
    compliance_changed_payload,
    proposal_changed_idempotency_key,
    proposal_changed_payload,
)
from tenderos_backend.domain.services import graph_service
from tenderos_backend.domain.services.compliance_service import (
    evaluate_compliance_for_requirement,
)
from tenderos_backend.domain.services.proposal_service import reground_claim

logger = logging.getLogger(__name__)


def _build_llm_client() -> LLMClient:
    # Fresh client per job invocation, same as verification.py's and every
    # other worker job's identical helper - see requirement_extraction.py's
    # own docstring for why this is a function, not a module-level
    # singleton (test overridability).
    redis_client = AsyncRedis.from_url(settings.redis_url)
    return GeminiClient(
        api_key=settings.gemini_api_key,
        redis_client=redis_client,
        cache_ttl_seconds=settings.llm_cache_ttl_seconds,
    )


def _affected_ids(rows: list[dict]) -> tuple[set[uuid.UUID], set[uuid.UUID]]:
    """Splits graph_service.preview_impact's own result rows into the two id
    sets this job acts on. Reuses preview_impact itself (not a second query)
    so the blast-radius list a reviewer saw in the impact-preview screen and
    the one this job actually reevaluates are the same query, not two that
    could silently diverge. Requirement/ProposalClaim nodes carry their real
    Postgres id as a plain "id" property (graph_projection/cypher/nodes.py:
    27,63) - confirmed by reading the projection Cypher directly, not
    assumed from the trace-endpoint's own node_labels convention, which is
    a different property naming question (KNOWN_LIMITATIONS.md's B10 entry).
    Role/Compliance/ProposalSection rows preview_impact can also return are
    not this job's concern - only Requirement (feeds compliance
    reevaluation) and ProposalClaim (feeds reground_claim) are.
    """
    requirement_ids: set[uuid.UUID] = set()
    claim_ids: set[uuid.UUID] = set()
    for row in rows:
        labels = row.get("labels") or []
        affected = row.get("affected") or {}
        node_id = affected.get("id")
        if node_id is None:
            continue
        if "Requirement" in labels:
            requirement_ids.add(uuid.UUID(node_id))
        elif "ProposalClaim" in labels:
            claim_ids.add(uuid.UUID(node_id))
    return requirement_ids, claim_ids


async def reanalyze_knowledge_selection_change(
    tender_id: str, knowledge_item_id: str, state_version: int
) -> None:
    """Enqueued on KnowledgeSelectionChanged (outbox_dispatcher.py's routing
    table) - the targeted re-evaluation set_knowledge_selection's own
    docstring has named as out of scope since B6, now built.

    Sequence: (1) reproject the graph at the selection change's own
    state_version - graph_service.project_tender, the same service function
    both the manual POST .../graph/project route and the LangGraph
    project_graph node already call, not a new capability; (2) run the same
    preview_impact query the impact-preview screen already used to get the
    real, current blast radius; (3) for each affected requirement, re-run
    evaluate_compliance_for_requirement - persist_compliance_decision
    (already built, B7/pre-B12) is what actually "reopens" a compliance
    task, by correctly clearing a stale human_approved when the reevaluated
    status differs, so nothing here has to reimplement that; (4) for each
    affected claim, reground_claim (proposal_service.py, pinned separately)
    re-derives current evidence validity and rewrites grounding_status only
    if it changed. Each requirement/claim result gets its own ComplianceChanged/
    ProposalChanged outbox event, keyed by state_version so a genuine retry
    of this same job is idempotent and two different selection changes never
    collide on the same key.

    One asymmetry worth naming rather than silently accepting: reground_claim's
    own write and its ProposalChanged event share one transaction (atomic by
    construction, same pattern as every other outbox write in this codebase).
    evaluate_compliance_for_requirement commits its own decision internally
    (a full, self-contained pipeline, not a shared-uow helper) before this
    function ever sees the result - its ComplianceChanged event is therefore
    NOT atomic with the compliance write it describes. A crash in that
    narrow window leaves a correct compliance decision with no matching
    event. Not fixed here: closing it means threading an outbox-write hook
    through persist_compliance_decision's own internal UnitOfWork, a real
    change to already-tested B7 core code for an event that is a progress/
    audit signal, not something this block's own stop-gate depends on
    (compliance_entries.human_approved is what the stop-gate actually
    checks, and that write is already correct and atomic on its own).
    """
    tid = uuid.UUID(tender_id)
    kid = uuid.UUID(knowledge_item_id)

    await graph_service.project_tender(tid)
    rows = await graph_service.preview_impact(tid, kid)
    requirement_ids, claim_ids = _affected_ids(rows)

    client = _build_llm_client()
    for requirement_id in requirement_ids:
        try:
            entry = await evaluate_compliance_for_requirement(
                client, settings.reasoning_model, requirement_id
            )
        except Exception:
            # One requirement's reevaluation failing (a transient provider
            # error, say) should not block reanalysis of the other affected
            # requirements or claims - same posture requirement_extraction_
            # node's own mandatory-verification loop already takes.
            logger.exception(
                "reanalysis: compliance evaluation failed for requirement %s "
                "(tender=%s, knowledge_item=%s)",
                requirement_id, tid, kid,
            )
            continue
        async with UnitOfWork() as uow:
            await uow.outbox.create(
                aggregate_type=AGGREGATE_COMPLIANCE_ENTRY,
                aggregate_id=entry.id,
                event_type=EVENT_COMPLIANCE_CHANGED,
                payload=compliance_changed_payload(entry.id, requirement_id, tid, entry.status),
                idempotency_key=compliance_changed_idempotency_key(requirement_id, state_version),
            )
            await uow.commit()

    for claim_id in claim_ids:
        try:
            async with UnitOfWork() as uow:
                result = await reground_claim(uow, claim_id)
                await uow.outbox.create(
                    aggregate_type=AGGREGATE_PROPOSAL_CLAIM,
                    aggregate_id=claim_id,
                    event_type=EVENT_PROPOSAL_CHANGED,
                    payload=proposal_changed_payload(
                        claim_id, tid, result.status, result.evidence_link_ids_considered
                    ),
                    idempotency_key=proposal_changed_idempotency_key(claim_id, state_version),
                )
                await uow.commit()
        except Exception:
            logger.exception(
                "reanalysis: regrounding failed for claim %s (tender=%s, knowledge_item=%s)",
                claim_id, tid, kid,
            )
            continue

    logger.info(
        "reanalysis complete: tender=%s knowledge_item=%s state_version=%s "
        "requirements=%d claims=%d",
        tid, kid, state_version, len(requirement_ids), len(claim_ids),
    )
