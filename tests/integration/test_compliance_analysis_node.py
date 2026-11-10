"""compliance_analysis_node against real chunks/requirements and a scripted
FakeLLMClient - task completion, one compliance_entries row per
requirement, and the REQ-073/074 divergent-restatement regression test
(KNOWN_LIMITATIONS.md: "[B5] Silent suppression of restated obligations",
scheduled for verification here once evidence-linking exists)."""

import io
import uuid

import docx
import pytest
from tenderos_backend.agents.llm.fake_client import FakeLLMClient
from tenderos_backend.agents.schemas.compliance import ComplianceProposal
from tenderos_backend.agents.schemas.requirements import (
    ExtractedRequirement,
    ExtractionOutput,
    VerificationOutput,
)
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import document_service
from tenderos_backend.orchestration.nodes import compliance, requirement_extraction
from tenderos_backend.orchestration.nodes.compliance import compliance_analysis_node
from tenderos_backend.orchestration.nodes.requirement_extraction import (
    requirement_extraction_node,
)
from tenderos_worker.jobs.ingestion import parse_and_embed_document

pytestmark = pytest.mark.asyncio


async def _make_org_and_tender(name: str) -> tuple[uuid.UUID, uuid.UUID]:
    async with get_session() as session:
        org = Organization(name=f"{name} Org")
        session.add(org)
        await session.flush()
        tender = Tender(organization_id=org.id, name=f"{name} Tender")
        session.add(tender)
        await session.flush()
        await session.commit()
        return org.id, tender.id


async def _upload_chunk(org_id: uuid.UUID, tender_id: uuid.UUID, filename: str, text: str) -> None:
    doc = docx.Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    document, _ = await document_service.create_document(
        tender_id=tender_id,
        organization_id=org_id,
        actor_user_id=uuid.uuid4(),
        filename=filename,
        content=buf.getvalue(),
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    await parse_and_embed_document(str(document.id))


def _base_state(tender_id: uuid.UUID, workflow_run_id: uuid.UUID) -> dict:
    return {
        "tender_id": str(tender_id),
        "organization_id": "unused-by-these-nodes",
        "workflow_run_id": str(workflow_run_id),
        "langgraph_thread_id": str(workflow_run_id),
        "status": "extracting_requirements",
        "plan_version": 0,
        "active_gate_id": None,
        "revision_count": 0,
        "max_revisions": 2,
        "retry_budget_remaining": 10,
        "dispatch_task_ids": [],
        "completed_task_ids": [],
        "failed_task_ids": [],
        "unverified_requirement_ids": [],
        "open_critical_finding_ids": [],
        "changed_knowledge_item_ids": [],
        "readiness_report_id": None,
        "graph_projection_version": None,
        "degraded_stages": [],
    }


async def _make_workflow_run_row(tender_id: uuid.UUID) -> uuid.UUID:
    async with UnitOfWork() as uow:
        run = await uow.workflow.create_run(
            tender_id=tender_id, prompt_bundle_version="test", model_config={}
        )
        await uow.commit()
        return run.id


async def _make_compliance_task(workflow_run_id: uuid.UUID) -> uuid.UUID:
    # compliance_analysis_node is normally Send()-dispatched with a task_id
    # supervisor_node already created during planning - a direct node-level
    # test has to create that row itself, same as this file's own
    # requirement_extraction setup creates its workflow_run row directly.
    async with UnitOfWork() as uow:
        task = await uow.workflow.create_task(
            workflow_run_id=workflow_run_id,
            task_key=f"compliance_analysis:{workflow_run_id}",
            task_type="compliance_analysis",
            assigned_agent="evidence_compliance",
            priority=5,
            input_refs={},
        )
        await uow.commit()
        return task.id


async def test_node_creates_one_compliance_entry_per_requirement_and_completes_task(monkeypatch):
    org_id, tender_id = await _make_org_and_tender("Compliance Node Test")
    await _upload_chunk(
        org_id, tender_id, "rfp.docx", "Bidders must hold a valid data protection certification."
    )
    workflow_run_id = await _make_workflow_run_row(tender_id)

    extraction_client = FakeLLMClient()
    extraction_client.script(
        "requirement_extraction",
        ExtractionOutput,
        [
            ExtractionOutput(
                requirements=[
                    ExtractedRequirement(
                        requirement_text="Bidders must hold a valid data protection certification.",
                        category="eligibility",
                        mandatory=True,
                        source_chunk_ref="chunk_1",
                        confidence=0.9,
                    )
                ]
            )
        ],
    )
    monkeypatch.setattr(requirement_extraction, "_build_llm_client", lambda: extraction_client)
    await requirement_extraction_node(_base_state(tender_id, workflow_run_id))

    async with UnitOfWork() as uow:
        requirements = await uow.requirements.list_for_tender(tender_id)
    assert len(requirements) == 1

    compliance_client = FakeLLMClient()
    compliance_client.script(
        "compliance_proposal",
        ComplianceProposal,
        [
            ComplianceProposal(
                status="evidence_unavailable",
                evidence_refs=[],
                confidence=0.4,
                reasoning_summary="No supporting evidence was retrieved.",
            )
        ],
    )
    monkeypatch.setattr(compliance, "_build_llm_client", lambda: compliance_client)

    task_id = await _make_compliance_task(workflow_run_id)
    state = {**_base_state(tender_id, workflow_run_id), "task_id": str(task_id)}

    result = await compliance_analysis_node(state)

    assert result == {"completed_task_ids": [str(task_id)]}
    async with UnitOfWork() as uow:
        task = await uow.workflow.get_task_by_key(
            workflow_run_id, f"compliance_analysis:{workflow_run_id}"
        )
        entries = await uow.compliance.list_compliance_entries_for_tender(tender_id)
    assert task is not None
    assert task.status == "completed"
    assert task.output_refs["requirement_count"] == 1
    assert task.output_refs["evaluated_count"] == 1
    assert len(entries) == 1
    assert entries[0].requirement_id == requirements[0].id
    assert entries[0].status == "evidence_unavailable"


async def test_req073_074_divergent_restatements_get_independent_compliance_entries(monkeypatch):
    """See KNOWN_LIMITATIONS.md's "[B5] Silent suppression of restated
    obligations" entry, scheduled for verification here. Two assertions,
    in order:

    1. PRECONDITION - after running the real requirement_extraction_node
       (not a synthetic direct-insert of two Requirement rows), both
       divergent restatements actually survived extraction with their
       distinct durations intact. If this fails, that IS the finding -
       the test fails here, loudly, rather than silently passing on the
       compliance-layer assertion alone with only one requirement ever
       having existed.
    2. Given the precondition holds, the compliance layer processes both
       independently: two distinct compliance_entries rows, and neither's
       reasoning_summary references the other's duration.

    The two source paragraphs are uploaded as two separate documents
    specifically to mirror agents/requirements/batch.py's provenance-key
    boundary (different document_id forces two separate batches/LLM calls,
    not one) - the same "split across two separate calls with zero
    co-location" shape B5's own investigation used as a discriminator.
    """
    org_id, tender_id = await _make_org_and_tender("REQ-073-074 Divergence Test")
    await _upload_chunk(
        org_id,
        tender_id,
        "rfp.docx",
        "[REQ-073] Audit logs must be retained for a minimum of one year.",
    )
    await _upload_chunk(
        org_id,
        tender_id,
        "technical_annexure.docx",
        "[REQ-074] Audit logs must be retained for a minimum of three years.",
    )
    workflow_run_id = await _make_workflow_run_row(tender_id)

    extraction_client = FakeLLMClient()
    extraction_client.script(
        "requirement_extraction",
        ExtractionOutput,
        [
            ExtractionOutput(
                requirements=[
                    ExtractedRequirement(
                        requirement_text=(
                            "[REQ-073] Audit logs must be retained for a minimum of one year."
                        ),
                        category="security",
                        mandatory=True,
                        source_chunk_ref="chunk_1",
                        confidence=0.9,
                    )
                ]
            ),
            ExtractionOutput(
                requirements=[
                    ExtractedRequirement(
                        requirement_text=(
                            "[REQ-074] Audit logs must be retained for a minimum of three years."
                        ),
                        category="security",
                        mandatory=True,
                        # batch.py's label_counter runs continuously across
                        # ALL batches, not reset per batch - the second
                        # document's chunk is "chunk_2", not "chunk_1".
                        source_chunk_ref="chunk_2",
                        confidence=0.9,
                    )
                ]
            ),
        ],
    )
    extraction_client.script(
        "requirement_verification",
        VerificationOutput,
        [
            VerificationOutput(
                supported_by_source=True,
                mandatory_language_supported=True,
                verification_confidence=0.95,
                reasoning_summary="Source text directly states this obligation.",
            )
        ]
        * 2,
    )
    monkeypatch.setattr(requirement_extraction, "_build_llm_client", lambda: extraction_client)
    await requirement_extraction_node(_base_state(tender_id, workflow_run_id))

    # --- 1. Precondition: both survived real extraction, distinct durations intact ---
    async with UnitOfWork() as uow:
        requirements = await uow.requirements.list_for_tender(tender_id)
    one_year = [r for r in requirements if "one year" in r.requirement_text]
    three_years = [r for r in requirements if "three years" in r.requirement_text]
    assert len(requirements) == 2, (
        f"precondition failed: expected both REQ-073 and REQ-074 to survive extraction, "
        f"got {len(requirements)} requirement(s) - {[r.requirement_text for r in requirements]}"
    )
    assert len(one_year) == 1, (
        "precondition failed: REQ-073 (1-year duration) missing after extraction"
    )
    assert len(three_years) == 1, (
        "precondition failed: REQ-074 (3-year duration) missing after extraction"
    )
    req_073, req_074 = one_year[0], three_years[0]

    # --- 2. Compliance layer: independent entries, no cross-contamination ---
    compliance_client = FakeLLMClient()
    compliance_client.script(
        "compliance_proposal",
        ComplianceProposal,
        [
            ComplianceProposal(
                status="evidence_unavailable",
                evidence_refs=[],
                confidence=0.4,
                reasoning_summary="No evidence found for the one-year retention obligation.",
            ),
            ComplianceProposal(
                status="evidence_unavailable",
                evidence_refs=[],
                confidence=0.4,
                reasoning_summary="No evidence found for the three-year retention obligation.",
            ),
        ],
    )
    monkeypatch.setattr(compliance, "_build_llm_client", lambda: compliance_client)

    task_id = await _make_compliance_task(workflow_run_id)
    state = {**_base_state(tender_id, workflow_run_id), "task_id": str(task_id)}
    await compliance_analysis_node(state)

    async with UnitOfWork() as uow:
        entry_073 = await uow.compliance.get_compliance_entry_by_requirement(req_073.id)
        entry_074 = await uow.compliance.get_compliance_entry_by_requirement(req_074.id)

    assert entry_073 is not None
    assert entry_074 is not None
    assert entry_073.id != entry_074.id, "REQ-073 and REQ-074 must not share one compliance entry"
    assert "three-year" not in (entry_073.reasoning_summary or ""), (
        "REQ-073's compliance entry must not reference REQ-074's duration"
    )
    assert "one-year" not in (entry_074.reasoning_summary or ""), (
        "REQ-074's compliance entry must not reference REQ-073's duration"
    )
