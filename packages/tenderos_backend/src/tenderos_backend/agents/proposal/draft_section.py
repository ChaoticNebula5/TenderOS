import uuid
from dataclasses import dataclass

from tenderos_backend.agents.llm.client import LLMClient, LLMError, LLMResult, call_with_repair
from tenderos_backend.agents.prompts.loader import bundle_version, load_prompt
from tenderos_backend.agents.prompts.untrusted_content import wrap_untrusted_content
from tenderos_backend.agents.schemas.proposal import ProposalSectionDraft
from tenderos_backend.db.unit_of_work import UnitOfWork

_AGENT_NAME = "proposal_drafting"


@dataclass(frozen=True)
class GroundedFactRef:
    """One already-approved fact offered to the drafting call, labeled the
    same way EvidenceCandidateRef/RequirementRef already are elsewhere - the
    model only ever sees `label` and `excerpt`, never the real UUID behind
    it. `kind` distinguishes the three ref lists a claim can cite
    (requirement / evidence / employee) so _format_facts can group them
    under the headings proposal_drafting.md's own prompt describes.
    """

    label: str
    kind: str  # "requirement" | "evidence" | "employee"
    excerpt: str


def _fact_input_refs(facts: list[GroundedFactRef]) -> dict:
    """B16: the fact set as OFFERED to drafting, not just what a claim
    cited - the drafting-stage half of the same instrumentation added to
    propose.py's _candidate_input_refs (same pass, same reason: this
    project's own citation-correctness investigation, B9/B12, could never
    tell "nothing was offered" apart from "something was offered and not
    cited" because only the cited side was ever recorded). No f.excerpt -
    same document-content-out-of-logs reasoning as propose.py's own
    version of this function.
    """
    return {
        "fact_count": len(facts),
        "facts": [{"label": f.label, "kind": f.kind} for f in facts],
    }


def _draft_output_refs(draft: ProposalSectionDraft) -> dict:
    """What claims in this section actually cited, deduped across claims -
    per-claim detail already survives in the persisted proposal_claims
    rows once proposal_service.py materializes them; this trace only needs
    "was anything from the offered fact set cited at all," paired against
    _fact_input_refs above.
    """
    return {
        "claim_count": len(draft.claims),
        "factual_claim_count": sum(1 for c in draft.claims if c.is_factual),
        "cited_evidence_labels": sorted({label for c in draft.claims for label in c.evidence_refs}),
        "cited_requirement_labels": sorted(
            {label for c in draft.claims for label in c.requirement_refs}
        ),
        "cited_employee_labels": sorted({label for c in draft.claims for label in c.employee_refs}),
    }


async def _write_trace(
    *,
    tender_id: uuid.UUID,
    workflow_run_id: uuid.UUID | None,
    task_id: uuid.UUID | None,
    model: str,
    status: str,
    facts: list[GroundedFactRef],
    result: LLMResult[ProposalSectionDraft] | None = None,
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
            input_refs=_fact_input_refs(facts),
            output_refs=_draft_output_refs(result.parsed) if result else {},
            latency_ms=result.latency_ms if result else None,
            input_tokens=result.input_tokens if result else None,
            output_tokens=result.output_tokens if result else None,
            estimated_cost_usd=result.estimated_cost_usd if result else None,
            status=status,
            error_code=error_code,
        )
        await uow.commit()


def _format_facts(facts: list[GroundedFactRef]) -> str:
    if not facts:
        return "(no approved facts are available to ground this section)"
    lines = []
    for f in facts:
        # B15: f.excerpt is document/knowledge-base-sourced (a requirement,
        # an evidence excerpt, or an employee profile field).
        lines.append(f"- {f.label} [{f.kind}]: {wrap_untrusted_content(f.label, f.excerpt)}")
    return "\n".join(lines)


async def draft_section(
    client: LLMClient,
    model: str,
    section_name: str,
    section_guidance: str,
    facts: list[GroundedFactRef],
    tender_id: uuid.UUID,
    workflow_run_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
) -> ProposalSectionDraft:
    """E7's proposal-drafting call - one section, its own narrow grounded
    fact bundle, one call. Returns the LLM's raw draft unmodified; it does
    not decide grounding_status or persist anything - that split mirrors
    propose_compliance's own division of labor, kept for the same reason
    (a thin, swappable-provider layer here, invariant enforcement one layer
    up in proposal_service.py).
    """
    system_prompt = load_prompt(_AGENT_NAME)
    user_prompt = (
        f"SECTION: {section_name}\n{section_guidance}\n\n"
        f"AVAILABLE FACTS:\n{_format_facts(facts)}"
    )
    try:
        llm_result = await call_with_repair(
            client,
            agent_name=_AGENT_NAME,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=ProposalSectionDraft,
        )
    except LLMError as exc:
        await _write_trace(
            tender_id=tender_id,
            workflow_run_id=workflow_run_id,
            task_id=task_id,
            model=model,
            status="failed",
            facts=facts,
            error_code=type(exc).__name__,
        )
        raise

    await _write_trace(
        tender_id=tender_id,
        workflow_run_id=workflow_run_id,
        task_id=task_id,
        model=model,
        status="completed",
        facts=facts,
        result=llm_result,
    )
    return llm_result.parsed
