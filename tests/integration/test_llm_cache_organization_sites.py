"""PR 35: each call site builds its LLM client for the right organization.

The response cache is keyed by the organization the client was built for
(tests/unit/test_llm_cache_scope.py proves two organizations never share an
entry). These tests prove the other half: at every way a call site finds
the organization, the id it hands the factory is its tender's (or its
batch's) own. Each runs the real site for two tenders in two organizations
and checks the ids against the organizations read back from the database,
so an id that ignored the data (a constant, the wrong tender's) fails.

- a workflow node: the graph state, set by the workflow job from the
  tender (the requirement extraction node, run to its first gate);
- an operation: its row's organization (a red-team operation);
- on-demand verification: its requirement's tender;
- reanalysis: its tender (a separate read from verification's);
- a knowledge import: its batch.

The factory is replaced in each module by a spy that records the id and
hands back a scripted fake, so no network and no real LLM cost.
"""

import io
import uuid
from types import SimpleNamespace

import docx
import pytest
import pytest_asyncio
from _helpers import ingest_one_document, make_ready_run_inputs, register_org_and_tender
from tenderos_backend.agents.llm.fake_client import FakeLLMClient
from tenderos_backend.agents.schemas.redteam import RedteamCritique
from tenderos_backend.agents.schemas.requirements import ExtractionOutput
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import (
    document_service,
    knowledge_service,
    operation_service,
    workflow_service,
)
from tenderos_backend.graph_projection.driver import get_session as get_neo4j_session
from tenderos_backend.orchestration.nodes import requirement_extraction
from tenderos_worker.jobs import knowledge_import, reanalysis
from tenderos_worker.jobs import operations as operation_jobs
from tenderos_worker.jobs import verification as verification_job
from tenderos_worker.jobs.operations import run_operation
from tenderos_worker.jobs.workflow import run_workflow

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _clean_neo4j():
    # The red-team operation and reanalysis read the graph; the same
    # cleanup as test_llm_call_owners.py and test_reanalysis_job.py.
    yield
    async with get_neo4j_session() as session:
        await session.run("MATCH (n) DETACH DELETE n")


class _FactorySpy:
    """Stands in for build_llm_client inside one module: records the
    organization each client is built for, hands back `client`."""

    def __init__(self, client: object) -> None:
        self.client = client
        self.organization_ids: list[uuid.UUID] = []

    def __call__(self, *, organization_id: uuid.UUID, api_key: str | None = None) -> object:
        self.organization_ids.append(organization_id)
        return self.client


async def _organization_of(tender_id: uuid.UUID) -> uuid.UUID:
    async with UnitOfWork() as uow:
        tender = await uow.tenders.get(tender_id)
    assert tender is not None
    return tender.organization_id


async def test_a_workflow_node_builds_its_client_for_the_runs_tender(monkeypatch):
    fake = FakeLLMClient()
    fake.script(
        "requirement_extraction",
        ExtractionOutput,
        [ExtractionOutput(requirements=[], submission_items=[], clarifications=[])] * 10,
    )
    spy = _FactorySpy(fake)
    monkeypatch.setattr(requirement_extraction, "build_llm_client", spy)

    tender_ids = []
    for name in ("cache-node-a", "cache-node-b"):
        registered, tender = await make_ready_run_inputs(
            f"{name}@example.com", f"{name} org", f"{name} tender"
        )
        run = await workflow_service.create_workflow_run(
            tender_id=tender.id,
            organization_id=registered.organization.id,
            created_by=registered.user.id,
        )
        await run_workflow(str(run.id))
        tender_ids.append(tender.id)

    expected = [await _organization_of(t) for t in tender_ids]
    assert expected[0] != expected[1]
    assert spy.organization_ids == expected


async def test_an_operation_builds_its_client_for_its_tender(monkeypatch):
    fake = FakeLLMClient()
    fake.script("redteam_critique", RedteamCritique, [RedteamCritique(findings=[])] * 2)
    spy = _FactorySpy(fake)
    monkeypatch.setattr(operation_jobs, "build_llm_client", spy)

    tender_ids = []
    for name in ("cache-op-a", "cache-op-b"):
        registered, tender = await register_org_and_tender(
            f"{name}@example.com", f"{name} org", f"{name} tender"
        )
        request = await operation_service.request_operation(
            registered.organization.id, tender.id, "redteam_run", registered.user.id
        )
        await run_operation(str(request.operation.id))
        tender_ids.append(tender.id)

    expected = [await _organization_of(t) for t in tender_ids]
    assert expected[0] != expected[1]
    assert spy.organization_ids == expected


async def _requirement_on_a_new_tender(name: str) -> tuple[uuid.UUID, uuid.UUID]:
    registered, tender = await register_org_and_tender(
        f"{name}@example.com", f"{name} org", f"{name} tender"
    )
    document = await ingest_one_document(tender.id, registered.organization.id)
    async with UnitOfWork() as uow:
        chunks = await uow.documents.list_chunks_for_document(document.id)
        requirement = await uow.requirements.create(
            tender_id=tender.id,
            display_code="REQ-001",
            requirement_text="Bidders must hold a valid tax clearance certificate.",
            normalized_text="bidders must hold a valid tax clearance certificate",
            category="legal",
            mandatory=True,
            source_chunk_id=chunks[0].id,
            content_fingerprint=str(uuid.uuid4()),
        )
        await uow.commit()
    return tender.id, requirement.id


async def test_on_demand_verification_builds_its_client_for_the_requirements_tender(
    monkeypatch,
):
    spy = _FactorySpy(FakeLLMClient())
    monkeypatch.setattr(verification_job, "build_llm_client", spy)
    built_with = []

    async def _verify(client, model, requirement_id, tender_id, **kwargs):
        built_with.append(client)
        return SimpleNamespace(supported_by_source=True, mandatory_language_supported=True)

    monkeypatch.setattr(verification_job, "verify_and_persist", _verify)

    tender_ids = []
    for name in ("cache-verify-a", "cache-verify-b"):
        tender_id, requirement_id = await _requirement_on_a_new_tender(name)
        await verification_job.verify_requirement_job(str(requirement_id))
        tender_ids.append(tender_id)

    expected = [await _organization_of(t) for t in tender_ids]
    assert expected[0] != expected[1]
    assert spy.organization_ids == expected
    assert built_with == [spy.client, spy.client]


async def test_reanalysis_builds_its_client_for_its_tender(monkeypatch):
    spy = _FactorySpy(FakeLLMClient())
    monkeypatch.setattr(reanalysis, "build_llm_client", spy)

    async def _no_projection(_tender_id):
        return None

    async def _nothing_affected(_tender_id, _knowledge_item_id):
        return []

    monkeypatch.setattr(reanalysis.graph_service, "project_tender", _no_projection)
    monkeypatch.setattr(reanalysis.graph_service, "preview_impact", _nothing_affected)

    tender_ids = []
    for name in ("cache-reanalysis-a", "cache-reanalysis-b"):
        async with get_session() as session:
            org = Organization(name=f"{name} org")
            session.add(org)
            await session.flush()
            tender = Tender(organization_id=org.id, name=f"{name} tender")
            session.add(tender)
            await session.commit()
            tender_id = tender.id
        await reanalysis.reanalyze_knowledge_selection_change(str(tender_id), str(uuid.uuid4()), 1)
        tender_ids.append(tender_id)

    expected = [await _organization_of(t) for t in tender_ids]
    assert expected[0] != expected[1]
    assert spy.organization_ids == expected


async def test_a_knowledge_import_builds_its_client_for_its_batch(monkeypatch):
    spy = _FactorySpy(FakeLLMClient())
    monkeypatch.setattr(knowledge_import, "build_llm_client", spy)

    async def _extract(client, model, chunks, organization_id):
        return SimpleNamespace(past_projects=[], capabilities=[])

    async def _demonstrates(*args, **kwargs):
        return []

    monkeypatch.setattr(knowledge_import, "extract_capability_statement", _extract)
    monkeypatch.setattr(knowledge_import, "extract_demonstrates", _demonstrates)

    batch_orgs = []
    for name in ("cache-import-a", "cache-import-b"):
        # A registered user: the batch's created_by is a real foreign key.
        registered, _tender = await register_org_and_tender(
            f"{name}@example.com", f"{name} org", f"{name} tender"
        )
        org_id, user_id = registered.organization.id, registered.user.id
        doc = docx.Document()
        doc.add_paragraph("We delivered the Harbour Bridge retrofit in 2022.")
        buf = io.BytesIO()
        doc.save(buf)
        document, _ = await document_service.create_document(
            organization_id=org_id,
            actor_user_id=user_id,
            filename="capability-statement.docx",
            content=buf.getvalue(),
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            side="company",
            doc_type="capability_statement",
        )
        batch = await knowledge_service.create_import_batch(org_id, "manual", document.id, user_id)
        await knowledge_import.process_knowledge_document_import(str(document.id), str(batch.id))
        async with UnitOfWork() as uow:
            stored = await uow.knowledge.get_import_batch(batch.id)
        assert stored is not None
        batch_orgs.append(stored.organization_id)

    assert batch_orgs[0] != batch_orgs[1]
    assert spy.organization_ids == batch_orgs
