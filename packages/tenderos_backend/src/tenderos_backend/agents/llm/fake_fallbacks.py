import re
import uuid

from tenderos_backend.agents.llm.factory import fake_llm_client
from tenderos_backend.agents.schemas.commercial import FindingExplanation
from tenderos_backend.agents.schemas.compliance import ComplianceProposal
from tenderos_backend.agents.schemas.knowledge import (
    CapabilityStatementExtraction,
    ExtractedEmployeeProfile,
    RelationshipExtractionOutput,
)
from tenderos_backend.agents.schemas.proposal import ProposalClaimDraft, ProposalSectionDraft
from tenderos_backend.agents.schemas.redteam import RedteamCritique
from tenderos_backend.agents.schemas.requirements import (
    ExtractedRequirement,
    ExtractionOutput,
    VerificationOutput,
)
from tenderos_backend.agents.schemas.talent import (
    CandidateScoring,
    ExtractedRole,
    RoleExtractionOutput,
)
from tenderos_backend.core.config import settings

# B17: every (agent_name, response_schema) pair the golden path's own
# agent modules actually call complete_structured with - enumerated by
# reading each module's own `agent_name = "..."` and `response_schema=`
# call site directly (agents/requirements/extract.py, verify.py;
# agents/compliance/propose.py; agents/talent/extract_roles.py,
# score_candidates.py; agents/commercial/explain_findings.py;
# agents/proposal/draft_section.py; agents/redteam/critique.py;
# agents/knowledge/extract.py, relationships.py), not guessed or copied
# from a schema list. This is what makes a fake-provider E2E run able to
# answer an UNKNOWN number of calls per pair (real chunk/requirement/
# candidate counts depend on real parsing of real synthetic documents,
# not a fixed fixture) - see FakeLLMClient.set_fallback's own docstring.
#
# Values are deliberately minimal but real enough to keep the golden path
# moving - one requirement, one role, one claim, non-empty where an empty
# collection would leave a downstream screen with nothing to click through
# (Confirm requirement, select a candidate, inspect a claim trace) -  not
# an attempt at realistic content. A fake-provider run proves reachability,
# never correctness; see KNOWN_LIMITATIONS.md's own exit-criteria note for
# why real quality/grounding numbers still require a live provider run.


# B17 Phase 0: one candidate/fact line, as both prompts render it -
# agents/compliance/propose.py::_format_candidates writes
# "- evidence_3 [employee, validity=valid]: <wrapped excerpt>" and
# agents/proposal/draft_section.py::_format_facts writes
# "- requirement_1 [requirement]: <wrapped excerpt>". Anchored to line
# start, and the label shape is the callers' own f"{kind}_{i}". Excerpts
# are wrapped by wrap_untrusted_content, so a line inside one would also
# need this exact shape to match; the synthetic fixture text has none.
_PROMPT_REF_LINE = re.compile(
    r"^- (?P<label>[a-z]+_\d+) \[(?P<kind>[a-z_]+)(?:, validity=(?P<validity>[a-z]+))?\]:",
    re.MULTILINE,
)


def _first_label(user_prompt: str, kind: str, validity: str | None = None) -> str | None:
    """The first label of `kind` (and `validity`, when given) actually
    present in this call's own prompt, or None. Citing only labels the
    prompt offered is what lets write-time enforcement accept the citation
    (compliance_service.materialize_evidence / proposal_service resolve
    labels back to real rows and skip any they were not given)."""
    for m in _PROMPT_REF_LINE.finditer(user_prompt):
        if m["kind"] == kind and (validity is None or m["validity"] == validity):
            return m["label"]
    return None


def _compliance_proposal_fallback(user_prompt: str) -> ComplianceProposal:
    # B17 Phase 0, the fifth fake-harness error (KNOWN_LIMITATIONS.md's
    # fake-client entry): the static fallback cited nothing, so no employee
    # ever backed a compliance decision and E2E step 14 (an employee whose
    # impact preview names a Requirement, full-flow.spec.ts:1175-1178) was
    # structurally unreachable under the fake provider. Cites exactly ONE
    # candidate - the first [employee, validity=valid] line - so the
    # step-14 deselection leaves that decision with no alternate evidence.
    # "valid" is the only signal the prompt shows; enforce_evidence_
    # sufficiency also requires profile_review_status == "verified", which
    # the E2E sets for every imported employee at step 0b before any run.
    label = _first_label(user_prompt, "employee", validity="valid")
    if label is None:
        return ComplianceProposal(
            status="fully_compliant",
            evidence_refs=[],
            confidence=0.9,
            reasoning_summary="Fake-provider fallback: no valid employee candidate offered.",
        )
    return ComplianceProposal(
        status="fully_compliant",
        evidence_refs=[label],
        confidence=0.9,
        reasoning_summary="Fake-provider fallback: cites the first valid employee candidate.",
    )


def _proposal_drafting_fallback(user_prompt: str) -> ProposalSectionDraft:
    # "Company Overview" gets a narrative, non-factual claim (is_factual=
    # False -> enforce_claim_grounding's own first branch, status=
    # "not_required", never "unsupported" - proposal_service.py, checked
    # directly, not assumed) - matching what section_is_data_gated's own
    # exclusion implies about the section's real nature: real drafted
    # overview content isn't the kind of specific, evidence-checkable
    # claim a technical/experience section makes. Every other section
    # keeps a real is_factual claim, which is what the E2E spec's own step
    # 10 precondition ("at least one unsupported factual claim exists")
    # needs - this fallback still produces one, just never from the one
    # section template that was never supposed to be checked for it.
    if user_prompt.startswith("SECTION: Company Overview"):
        return ProposalSectionDraft(
            content="Fake-provider fallback: company overview content.",
            claims=[
                ProposalClaimDraft(
                    claim_text="Our organization is well-positioned to deliver this engagement.",
                    claim_type="other",
                    is_factual=False,
                    claim_kind="current",
                )
            ],
        )
    claims = [
        ProposalClaimDraft(
            claim_text="We have delivered comparable engagements.",
            claim_type="experience",
            is_factual=True,
            claim_kind="current",
        )
    ]
    # B17 Phase 0: the claim above stays deliberately uncited (step 10
    # needs an unsupported factual claim). This second one cites the first
    # evidence and requirement facts this call was actually offered, so a
    # fake run can produce a grounded claim at all; claim_type="compliance"
    # puts it on enforce_claim_grounding's requirement-status branch
    # (proposal_service.py), the path that turns it needs_review if that
    # requirement's compliance later becomes non-affirmative - which is
    # what step 14's post-deselection needs_review assertion counts.
    evidence_label = _first_label(user_prompt, "evidence")
    requirement_label = _first_label(user_prompt, "requirement")
    if evidence_label is not None and requirement_label is not None:
        claims.append(
            ProposalClaimDraft(
                claim_text="We meet this requirement, as the cited evidence shows.",
                claim_type="compliance",
                is_factual=True,
                claim_kind="current",
                requirement_refs=[requirement_label],
                evidence_refs=[evidence_label],
            )
        )
    return ProposalSectionDraft(content="Fake-provider fallback section content.", claims=claims)


def install_fake_fallback_responses() -> None:
    """Call once, at process startup, at the same three real entrypoints
    configure_logging() already documents (apps/api/main.py, apps/worker/
    worker.py, apps/worker/outbox_dispatcher.py) - guarded by
    settings.llm_provider so this is a real no-op (not even the import
    cost of constructing these objects) in every real deployment, which
    never sets LLM_PROVIDER=fake at all.
    """
    if settings.llm_provider != "fake":
        return

    client = fake_llm_client()

    client.set_fallback(
        "requirement_extraction",
        ExtractionOutput,
        ExtractionOutput(
            requirements=[
                ExtractedRequirement(
                    requirement_text="The bidder shall provide the requested service.",
                    category="technical",
                    mandatory=True,
                    source_chunk_ref="chunk_1",
                    confidence=0.9,
                ),
                # B17 Phase 0, the sixth fake-harness error: role extraction
                # only runs over category == "staffing" requirements
                # (talent_service.extract_and_persist_roles), and this
                # fallback used to return only a "technical" one - so no
                # fake run ever had a role, and E2E step 7's candidate-
                # selection loop ran zero times, passing vacuously until a
                # Phase 0 count guard (full-flow.spec.ts) made it fail.
                ExtractedRequirement(
                    requirement_text="The bidder shall name a qualified delivery lead.",
                    category="staffing",
                    mandatory=True,
                    source_chunk_ref="chunk_1",
                    confidence=0.9,
                ),
            ],
        ),
    )
    client.set_fallback(
        "requirement_verification",
        VerificationOutput,
        VerificationOutput(
            supported_by_source=True,
            mandatory_language_supported=True,
            verification_confidence=0.9,
            reasoning_summary="Fake-provider fallback: treated as verified.",
        ),
    )
    # A callable since B17 Phase 0 - see _compliance_proposal_fallback.
    client.set_fallback(
        "compliance_proposal",
        ComplianceProposal,
        _compliance_proposal_fallback,
    )
    client.set_fallback(
        "role_extraction",
        RoleExtractionOutput,
        RoleExtractionOutput(
            # "req_1" always exists when this call is made at all:
            # talent_service.extract_and_persist_roles labels the staffing
            # requirements req_1.. (enumerate(..., start=1)) and returns
            # before calling the model if there are none. Citing it gives the
            # role a SATISFIES edge to a real Requirement (D2) instead of
            # floating free.
            roles=[ExtractedRole(role_name="Generalist", source_requirement_refs=["req_1"])],
        ),
    )
    client.set_fallback(
        "talent_scoring",
        CandidateScoring,
        CandidateScoring(score=0.8, strengths=[], gaps=[]),
    )
    client.set_fallback(
        "commercial_explanation",
        FindingExplanation,
        FindingExplanation(severity="minor", explanation="Fake-provider fallback explanation."),
    )
    # A callable, not a fixed instance - found necessary empirically (see
    # fake_client.py's own docstring, instance 2): a static fallback gave
    # EVERY section, including "Company Overview" (the one template
    # proposal_service.py's own section_is_data_gated deliberately excludes
    # from grounding checks), the identical is_factual claim - which real
    # grounding-enforcement logic then correctly marked "unsupported" in
    # every section identically. draft_section.py's own prompt puts
    # "SECTION: <name>" as user_prompt's first line, which is what makes
    # this distinguishable at all without this module knowing anything
    # about drafting internals beyond that one convention.
    client.set_fallback(
        "proposal_drafting",
        ProposalSectionDraft,
        _proposal_drafting_fallback,
    )
    client.set_fallback(
        "redteam_critique",
        RedteamCritique,
        RedteamCritique(findings=[]),
    )
    # A callable, not a fixed instance - found necessary empirically (see
    # fake_client.py's own docstring): a static "Fake Candidate" name for
    # every call collapsed 4 real, distinct imported résumés into 1 real
    # employee, because knowledge_service.py's own duplicate-adjudication
    # logic (resolve_employee_duplicate) correctly treats two profiles with
    # the same name as the same person.
    #
    # uuid4, not a shared counter - also found necessary empirically, a
    # second real gap in the first fix, not a defensive guess: a counter
    # closed over in this function's own scope produced "Fake Candidate 1"
    # for ALL FOUR résumés instead of 1/2/3/4, because RQ's default Worker
    # forks a fresh child PROCESS per job (confirmed - apps/worker/worker.py
    # uses plain rq.Worker, not SimpleWorker), and each résumé import is a
    # separate job. install_fake_fallback_responses() runs once in the
    # parent before any forking, so every child inherits an identical COPY
    # of the counter at 0 via fork() - each child's own increment to 1 is
    # invisible to every other child and the parent, so all four calls
    # independently produced the same "1". uuid4() needs no shared state
    # across processes at all - each call, in each process, draws fresh
    # entropy, which is what actually guarantees distinctness here.
    client.set_fallback(
        "resume_extraction",
        ExtractedEmployeeProfile,
        lambda _user_prompt: ExtractedEmployeeProfile(
            name=f"Fake Candidate {uuid.uuid4().hex[:8]}", confidence=0.9
        ),
    )
    client.set_fallback(
        "capability_statement_extraction",
        CapabilityStatementExtraction,
        CapabilityStatementExtraction(past_projects=[], capabilities=[]),
    )
    client.set_fallback(
        "worked_on_extraction",
        RelationshipExtractionOutput,
        RelationshipExtractionOutput(relationships=[]),
    )
    client.set_fallback(
        "demonstrates_extraction",
        RelationshipExtractionOutput,
        RelationshipExtractionOutput(relationships=[]),
    )
