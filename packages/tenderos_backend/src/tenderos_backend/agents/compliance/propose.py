import uuid
from dataclasses import dataclass

from tenderos_backend.agents.llm.client import LLMClient, LLMError, LLMResult, call_with_repair
from tenderos_backend.agents.prompts.loader import bundle_version, load_prompt
from tenderos_backend.agents.prompts.untrusted_content import wrap_untrusted_content
from tenderos_backend.agents.schemas.compliance import ComplianceProposal
from tenderos_backend.db.unit_of_work import UnitOfWork

_AGENT_NAME = "compliance_proposal"


@dataclass(frozen=True)
class EvidenceCandidateRef:
    """One retrieved evidence candidate shown to the LLM, and enough of its
    own metadata (not yet a persisted evidence_link - retrieval results are
    not evidence until something actually cites them) for the model to
    judge it. Same label -> real-object resolution pattern B6's
    ProjectCandidateRef/CapabilityCandidateRef already established: the
    model only ever sees `label`, never a UUID, and the caller
    (compliance_service.py) resolves cited labels back to the underlying
    KnowledgeSearchResult after parsing, to materialize real evidence_links
    only for what was actually used.
    """

    label: str
    knowledge_item_id: uuid.UUID
    source_kind: str
    excerpt: str
    validity_status: str


def _candidate_input_refs(candidates: list[EvidenceCandidateRef]) -> dict:
    """B16: the candidate set as OFFERED to the model, not just what it
    cited - added so the citation-correctness investigation (this project's
    largest known open quality gap, KNOWN_LIMITATIONS.md) can distinguish
    "retrieval returned nothing" (candidate_count=0) from "retrieval
    returned candidates the model declined to cite" (candidate_count>0,
    output_refs.cited_labels empty) - only one of the two hypotheses is
    distinguishable if only the cited labels are recorded, which is all
    this trace wrote before this pass. Structural metadata only - label,
    knowledge_item_id, source_kind, validity_status - never c.excerpt,
    which is knowledge-base-sourced text; this is a durable trace record
    a scorecard artifact may read from, not a place to duplicate document
    content B15 spent real, deliberate effort keeping out of logs.
    """
    return {
        "candidate_count": len(candidates),
        "candidates": [
            {
                "label": c.label,
                "knowledge_item_id": str(c.knowledge_item_id),
                "source_kind": c.source_kind,
                "validity_status": c.validity_status,
            }
            for c in candidates
        ],
    }


def _proposal_output_refs(proposal: ComplianceProposal) -> dict:
    """What the model actually did with the offered candidates, paired at
    read time against _candidate_input_refs above. A non-empty
    cited_labels against candidate_count=0 would itself be a real,
    distinguishable anomaly (a fabricated label - E1's own "no fabricated
    IDs" violated), not just one of the two hypotheses this pass exists
    to separate - materialize_evidence's own by_label.get(label) is None
    skip (compliance_service.py) already handles that case safely; this
    trace just makes it visible rather than silent.
    """
    return {"status": proposal.status, "cited_labels": proposal.evidence_refs}


async def _write_trace(
    *,
    tender_id: uuid.UUID,
    workflow_run_id: uuid.UUID | None,
    task_id: uuid.UUID | None,
    model: str,
    status: str,
    candidates: list[EvidenceCandidateRef],
    result: LLMResult[ComplianceProposal] | None = None,
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
            input_refs=_candidate_input_refs(candidates),
            output_refs=_proposal_output_refs(result.parsed) if result else {},
            latency_ms=result.latency_ms if result else None,
            input_tokens=result.input_tokens if result else None,
            output_tokens=result.output_tokens if result else None,
            estimated_cost_usd=result.estimated_cost_usd if result else None,
            status=status,
            error_code=error_code,
        )
        await uow.commit()


def _format_candidates(candidates: list[EvidenceCandidateRef]) -> str:
    if not candidates:
        return "(no candidate evidence was retrieved for this requirement)"
    lines = []
    for c in candidates:
        # B15: c.excerpt is knowledge-base-sourced text (a resume, a
        # certification document, a company document excerpt).
        lines.append(
            f"- {c.label} [{c.source_kind}, validity={c.validity_status}]: "
            f"{wrap_untrusted_content(c.label, c.excerpt)}"
        )
    return "\n".join(lines)


async def propose_compliance(
    client: LLMClient,
    model: str,
    requirement_text: str,
    candidates: list[EvidenceCandidateRef],
    tender_id: uuid.UUID,
    workflow_run_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
) -> ComplianceProposal:
    """E4's compliance-proposal call - one requirement, its retrieved
    evidence candidates, one call. Never asked to echo requirement_id back
    (same omission VerificationOutput already established), and never given
    real evidence_link UUIDs to cite, only labels - this function returns
    the LLM's raw proposal unmodified; it does not adjudicate it against
    PRD 8.4's evidence-sufficiency invariant or persist anything. That
    happens one layer up, in compliance_service.py, deliberately kept
    separate so the LLM-calling code stays a thin, swappable-provider layer
    and the invariant enforcement lives in one place regardless of which
    provider answered.
    """
    system_prompt = load_prompt(_AGENT_NAME)
    # B15: requirement_text is tender-document-sourced (extracted from the
    # buyer's own RFP) - the same untrusted-content category as
    # requirements/extract.py's own chunk text.
    user_prompt = (
        f"REQUIREMENT:\n{wrap_untrusted_content('requirement', requirement_text)}\n\n"
        f"CANDIDATE EVIDENCE:\n{_format_candidates(candidates)}"
    )
    try:
        llm_result = await call_with_repair(
            client,
            agent_name=_AGENT_NAME,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=ComplianceProposal,
        )
    except LLMError as exc:
        await _write_trace(
            tender_id=tender_id,
            workflow_run_id=workflow_run_id,
            task_id=task_id,
            model=model,
            status="failed",
            candidates=candidates,
            error_code=type(exc).__name__,
        )
        raise

    await _write_trace(
        tender_id=tender_id,
        workflow_run_id=workflow_run_id,
        task_id=task_id,
        model=model,
        status="completed",
        candidates=candidates,
        result=llm_result,
    )
    return llm_result.parsed
