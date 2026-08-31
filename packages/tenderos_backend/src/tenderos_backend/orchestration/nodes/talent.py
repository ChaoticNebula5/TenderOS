import logging
import uuid

from langgraph.types import interrupt
from redis.asyncio import Redis as AsyncRedis

from tenderos_backend.agents.llm.client import LLMClient
from tenderos_backend.agents.llm.gemini_adapter import GeminiClient
from tenderos_backend.core.config import settings
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services.talent_service import (
    evaluate_role_match,
    extract_and_persist_roles,
    find_candidate_pool,
)
from tenderos_backend.domain.services.workflow_service import is_cancelled, sync_run_status
from tenderos_backend.orchestration.gates import build_gate_payload, derive_gate_key
from tenderos_backend.orchestration.state import SpecialistTaskState, TenderState

logger = logging.getLogger(__name__)


class AllItemsFailedError(Exception):
    """Raised when every attempted (role, candidate) pair failed - not when
    there was simply nothing to attempt (0 roles or 0 candidates, where
    evaluated=0 is correct). See compliance_analysis_node's identical
    AllItemsFailedError for the same reasoning."""

    pass


def _build_llm_client() -> LLMClient:
    # Same "function, not module-level singleton" shape compliance_analysis_
    # node's own helper documents.
    # B16: talent_gemini_api_key first, falling back to the shared
    # gemini_api_key when unset - see Settings.talent_gemini_api_key's own
    # comment for why this branch specifically needs its own key.
    redis_client = AsyncRedis.from_url(settings.redis_url)
    return GeminiClient(
        api_key=settings.talent_gemini_api_key or settings.gemini_api_key,
        redis_client=redis_client,
        cache_ttl_seconds=settings.llm_cache_ttl_seconds,
    )


async def talent_analysis_node(state: SpecialistTaskState) -> dict:
    """Replaces B3's talent_stub_node. Two phases, both idempotent on
    retry: (1) extract_and_persist_roles once per tender - a no-op if
    role_requirements already exist for every staffing requirement (their
    own content_fingerprint UNIQUE constraint makes re-extraction a pure
    upsert-or-skip); (2) for every extracted role, score every candidate in
    the organization's talent pool, sequentially, same free-tier
    rate-limit reason requirement_extraction_node's own per-chunk loop and
    compliance_analysis_node's per-requirement loop already are. One
    (role, candidate) pair's failure is logged and skipped, not fatal to
    the rest - the task itself only fails if something before the loop
    breaks entirely (e.g. the tender can't be loaded at all).
    """
    workflow_run_id = uuid.UUID(state["workflow_run_id"])
    tender_id = uuid.UUID(state["tender_id"])
    organization_id = uuid.UUID(state["organization_id"])
    task_id = uuid.UUID(state["task_id"])

    try:
        client = _build_llm_client()
        roles = await extract_and_persist_roles(
            client, settings.reasoning_model, tender_id, workflow_run_id=workflow_run_id, task_id=task_id
        )
        candidates = await find_candidate_pool(organization_id)

        evaluated = 0
        attempted = 0
        for role in roles:
            for employee in candidates:
                attempted += 1
                try:
                    await evaluate_role_match(client, settings.reasoning_model, role.id, employee)
                    evaluated += 1
                except Exception:
                    logger.exception(
                        "talent scoring failed for role %s / employee %s",
                        role.id,
                        employee.knowledge_item_id,
                    )

        if attempted and evaluated == 0:
            # Every attempted (role, candidate) pair failed - see
            # AllItemsFailedError's own docstring. attempted, not
            # len(roles)/len(candidates), since 0 roles or 0 candidates is a
            # legitimate empty case, not a failure.
            raise AllItemsFailedError(
                f"all {attempted} (role, candidate) pairs failed talent scoring"
            )

        async with UnitOfWork() as uow:
            await uow.workflow.mark_task_completed(
                task_id,
                output_refs={
                    "role_count": len(roles),
                    "candidate_count": len(candidates),
                    "evaluated_count": evaluated,
                },
            )
            await uow.commit()

    except Exception as exc:
        async with UnitOfWork() as uow:
            await uow.workflow.mark_task_failed(task_id, error_code=type(exc).__name__)
            await uow.commit()
        raise

    return {"completed_task_ids": [str(task_id)]}


async def resource_selection_gate_node(state: TenderState) -> dict:
    """R5's resource_selection human gate. Sits AFTER compliance_gate, in
    the same safe sequential slot - never nested inside talent_analysis's
    own Send()-dispatched branch (B7's own topology bug: an
    interrupt()-bearing node upstream of a Send() fan-in replays on every
    later, unrelated resume). Otherwise identical shape to
    compliance_gate_node: deterministic gate_key from plan_version,
    create-once, interrupt(), route on decision, no status write on
    approve (commercial_approval_gate_node, the next node in this same
    un-Send()'d continuation, sets its own status moments later).
    """
    workflow_run_id = uuid.UUID(state["workflow_run_id"])
    gate_key = derive_gate_key("resource_selection", state["plan_version"])

    if await is_cancelled(workflow_run_id):
        return {"status": "rejected"}

    tender_id = uuid.UUID(state["tender_id"])

    async with UnitOfWork() as uow:
        gate = await uow.gates.get_by_key(workflow_run_id, gate_key)

        if gate is None:
            roles = await uow.talent.list_role_requirements_for_tender(tender_id)
            all_matches = []
            for role in roles:
                all_matches.extend(await uow.talent.list_role_matches_for_role(role.id))
            pending = [m for m in all_matches if not m.human_selected]

            gate_id = uuid.uuid4()
            payload = build_gate_payload(
                gate_id,
                "resource_selection",
                workflow_run_id,
                targets=[{"target_type": "role_requirement", "target_id": str(r.id)} for r in roles],
                summary={
                    "role_count": len(roles),
                    "candidate_count": len(all_matches),
                    "unselected_count": len(pending),
                },
            )
            gate = await uow.gates.create(
                gate_id=gate_id,
                tender_id=tender_id,
                workflow_run_id=workflow_run_id,
                gate_key=gate_key,
                gate_type="resource_selection",
                request_payload=payload,
            )
        payload = gate.request_payload
        await uow.commit()

    await sync_run_status(workflow_run_id, "awaiting_resource_selection")

    decision_payload = interrupt(payload)
    if decision_payload["decision"] == "reject":
        # KNOWN_LIMITATIONS.md: "[B7/B8] Post-join gate rejection kills the
        # whole run" - same accepted, B11-scoped limitation compliance_gate_
        # node already carries, now shared by all three post-join gates.
        await sync_run_status(workflow_run_id, "rejected")
        return {"status": "rejected"}
    if decision_payload["decision"] == "request_changes":
        # Same posture as reject - do not advance to commercial_approval_gate.
        # route_after_resource_selection_gate routes "changes_requested" to
        # END, same as "rejected". Does not yet re-open this gate for a
        # corrective resubmission - see final_gate_node's identical note.
        await sync_run_status(workflow_run_id, "changes_requested")
        return {"status": "changes_requested"}

    return {}
