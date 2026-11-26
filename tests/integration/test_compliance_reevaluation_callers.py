"""Plan item 121: both callers of a compliance re-evaluation keep an
override whose analysis answer is unchanged and reopen one whose answer
changed. They are separate ways in, so each is tested on its own:

- the workflow's revision pass: compliance_analysis_node on a
  `compliance_analysis:<run>:revision-1` task, the task targeted_replan
  creates (orchestration/nodes/redteam.py);
- the reanalysis job: reanalyze_knowledge_selection_change, for the
  requirements preview_impact returns.

Real Postgres. The model and retrieval are replaced at compliance_service
(propose_compliance answers from a table, search_company_knowledge finds
nothing), so the rest of evaluate_compliance_for_requirement runs as it is.
The reanalysis job's graph steps are replaced as in
test_llm_cache_organization_sites.py.
"""

import uuid
from datetime import date

import pytest
from sqlalchemy import select
from tenderos_backend.agents.schemas.compliance import ComplianceProposal
from tenderos_backend.db.models.audit import AuditEvent
from tenderos_backend.db.models.compliance import ComplianceEntry
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import compliance_service
from tenderos_backend.domain.services.compliance_service import (
    SufficiencyResult,
    override_compliance_entry,
    persist_compliance_decision,
)
from tenderos_backend.orchestration.nodes import compliance as compliance_node
from tenderos_worker.jobs import reanalysis

pytestmark = pytest.mark.asyncio

UNCHANGED_TEXT = "Bidders must hold a valid tax clearance certificate."
CHANGED_TEXT = "Bidders must hold a quality management certificate."


async def _tender_with_two_overrides() -> tuple[uuid.UUID, uuid.UUID, dict[str, uuid.UUID]]:
    """A tender whose two requirements the analysis answered
    evidence_unavailable and a reviewer overrode to fully_compliant.
    Returns (organization, tender, {requirement text: entry id})."""
    async with get_session() as session:
        org = Organization(name=f"Reevaluation Callers {uuid.uuid4().hex[:8]} Org")
        session.add(org)
        await session.flush()
        tender = Tender(
            organization_id=org.id, name="Reevaluation Callers", submission_date=date(2026, 12, 1)
        )
        session.add(tender)
        await session.commit()
        org_id, tender_id = org.id, tender.id
    async with UnitOfWork() as uow:
        document = await uow.documents.create(
            side="client",
            doc_type="rfp",
            filename="rfp.docx",
            storage_key=f"test/{uuid.uuid4()}",
            sha256=uuid.uuid4().hex,
            mime_type="application/octet-stream",
            file_size_bytes=10,
            tender_id=tender_id,
        )
        chunk = await uow.documents.create_chunk(
            document_id=document.id,
            chunk_index=0,
            chunk_text=f"{UNCHANGED_TEXT} {CHANGED_TEXT}",
            normalized_text=f"{UNCHANGED_TEXT} {CHANGED_TEXT}".lower(),
            content_hash=uuid.uuid4().hex,
        )
        requirement_ids = {}
        for i, requirement_text in enumerate((UNCHANGED_TEXT, CHANGED_TEXT), start=1):
            requirement = await uow.requirements.create(
                tender_id=tender_id,
                display_code=f"REQ-00{i}",
                requirement_text=requirement_text,
                normalized_text=requirement_text.lower(),
                category="eligibility",
                mandatory=True,
                source_chunk_id=chunk.id,
                content_fingerprint=uuid.uuid4().hex,
            )
            requirement_ids[requirement_text] = requirement.id
        await uow.commit()

    entries = {}
    for requirement_text, requirement_id in requirement_ids.items():
        proposal = ComplianceProposal(
            status="evidence_unavailable",
            evidence_refs=[],
            confidence=0.5,
            reasoning_summary="No evidence found.",
        )
        entry = await persist_compliance_decision(
            requirement_id,
            proposal,
            SufficiencyResult(status="evidence_unavailable", downgraded=False, reason="first pass"),
            [],
        )
        await override_compliance_entry(
            entry.id, uuid.uuid4(), status="fully_compliant", reasoning_summary="Held on paper."
        )
        entries[requirement_text] = entry.id
    return org_id, tender_id, entries


def _answer_from_a_table(monkeypatch) -> None:
    """The re-evaluation's answers: the same as the first pass for one
    requirement, changed for the other."""
    answers = {UNCHANGED_TEXT: "evidence_unavailable", CHANGED_TEXT: "non_compliant"}

    async def _propose(_client, _model, requirement_text, _candidates, _tender_id, **_kwargs):
        return ComplianceProposal(
            status=answers[requirement_text],
            evidence_refs=[],
            confidence=0.5,
            reasoning_summary="Re-evaluated.",
        )

    async def _no_knowledge(*_args, **_kwargs):
        return []

    monkeypatch.setattr(compliance_service, "propose_compliance", _propose)
    monkeypatch.setattr(compliance_service, "search_company_knowledge", _no_knowledge)


async def _assert_kept_and_reopened(entries: dict[str, uuid.UUID]) -> None:
    async with get_session() as session:
        rows = {
            text: (
                await session.execute(select(ComplianceEntry).where(ComplianceEntry.id == entry_id))
            ).scalar_one()
            for text, entry_id in entries.items()
        }
        cleared = (
            (
                await session.execute(
                    select(AuditEvent.target_id).where(
                        AuditEvent.action == "compliance_approval_cleared_on_reevaluation",
                        AuditEvent.target_id.in_(list(entries.values())),
                    )
                )
            )
            .scalars()
            .all()
        )
    kept, reopened = rows[UNCHANGED_TEXT], rows[CHANGED_TEXT]
    assert (kept.status, kept.human_approved, kept.automated_status) == (
        "fully_compliant",
        True,
        "evidence_unavailable",
    ), "an unchanged answer keeps the override and its approval"
    assert (reopened.status, reopened.human_approved, reopened.automated_status) == (
        "fully_compliant",
        False,
        "non_compliant",
    ), "a changed answer reopens the override, keeping the reviewer's status"
    assert cleared == [entries[CHANGED_TEXT]]


async def test_the_revision_pass_keeps_an_unchanged_override_and_reopens_a_changed_one(
    monkeypatch,
):
    org_id, tender_id, entries = await _tender_with_two_overrides()
    _answer_from_a_table(monkeypatch)
    monkeypatch.setattr(compliance_node, "_build_llm_client", lambda _organization_id: None)
    async with UnitOfWork() as uow:
        run = await uow.workflow.create_run(
            tender_id=tender_id, prompt_bundle_version="test", model_config={}
        )
        task = await uow.workflow.create_task(
            workflow_run_id=run.id,
            task_key=f"compliance_analysis:{run.id}:revision-1",
            task_type="compliance_analysis",
            assigned_agent="evidence_compliance",
            priority=10,
            input_refs={"reason": "redteam_revision"},
        )
        await uow.commit()
        run_id, task_id = run.id, task.id

    await compliance_node.compliance_analysis_node(
        {
            "tender_id": str(tender_id),
            "organization_id": str(org_id),
            "workflow_run_id": str(run_id),
            "task_id": str(task_id),
        }
    )

    async with UnitOfWork() as uow:
        task = await uow.workflow.get_task_by_key(
            run_id, f"compliance_analysis:{run_id}:revision-1"
        )
        assert task is not None and task.status == "completed"
    await _assert_kept_and_reopened(entries)


async def test_the_reanalysis_job_keeps_an_unchanged_override_and_reopens_a_changed_one(
    monkeypatch,
):
    _org_id, tender_id, entries = await _tender_with_two_overrides()
    _answer_from_a_table(monkeypatch)
    monkeypatch.setattr(reanalysis, "_build_llm_client", lambda _organization_id: None)
    async with UnitOfWork() as uow:
        affected = [
            {"labels": ["Requirement"], "affected": {"id": str(entry.requirement_id)}}
            for entry in await uow.compliance.list_compliance_entries_for_tender(tender_id)
        ]

    async def _no_projection(_tender_id):
        return None

    async def _affected(_tender_id, _knowledge_item_id):
        return affected

    async def _no_check(_tender_id):
        return None

    monkeypatch.setattr(reanalysis.graph_service, "project_tender", _no_projection)
    monkeypatch.setattr(reanalysis.graph_service, "preview_impact", _affected)
    monkeypatch.setattr(reanalysis.redteam_checks, "check_graph_contradictions", _no_check)

    await reanalysis.reanalyze_knowledge_selection_change(str(tender_id), str(uuid.uuid4()), 1)

    await _assert_kept_and_reopened(entries)
