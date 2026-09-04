"""orchestration/nodes/supervisor_node.py - real Postgres, no LangGraph
runtime and no LLM (build_plan is a static B3 stub, not a model call).
Focused on one thing: B16's fix for workflow_runs.plan_version never
advancing - the checkpoint's own copy (returned in the node's dict) always
worked; the Postgres column sat at its default 0 forever, since nothing
ever wrote it back.
"""

import uuid

import pytest
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.orchestration.nodes.supervisor_node import supervisor_node

pytestmark = pytest.mark.asyncio


async def _make_run() -> uuid.UUID:
    async with get_session() as session:
        org = Organization(name=f"Supervisor Node Test Org {uuid.uuid4().hex[:8]}")
        session.add(org)
        await session.flush()
        tender = Tender(organization_id=org.id, name="Supervisor Node Test Tender")
        session.add(tender)
        await session.flush()
        await session.commit()
        tender_id = tender.id

    async with UnitOfWork() as uow:
        run = await uow.workflow.create_run(
            tender_id=tender_id, prompt_bundle_version="v1", model_config={}
        )
        await uow.commit()
        return run.id


async def test_supervisor_node_writes_plan_version_to_postgres_not_only_the_checkpoint():
    run_id = await _make_run()

    async with UnitOfWork() as uow:
        before = await uow.workflow.get_run(run_id)
    assert before.plan_version == 0, "sanity check: starts at the column's own default"

    result = await supervisor_node({"workflow_run_id": str(run_id), "plan_version": 0})

    assert result["plan_version"] == 1, "the checkpoint's own copy already worked - regression guard"

    async with UnitOfWork() as uow:
        after = await uow.workflow.get_run(run_id)
    assert after.plan_version == 1, (
        "workflow_runs.plan_version must advance in Postgres too, not only in the "
        "LangGraph checkpoint's own state"
    )
