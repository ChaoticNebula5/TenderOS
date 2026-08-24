import uuid
from dataclasses import dataclass, field

from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import graph_service


@dataclass(frozen=True)
class RedteamFindingDraft:
    """The deterministic/graph engine's own output - no LLM involved in any
    function in this file, same "code decides, not fuzzy" posture as
    pricing_checks.py's PricingFindingDraft. Every field here is either
    read directly from a Postgres/Neo4j row or computed by a plain
    comparison; only the adversarial-LLM step (agents/redteam/) is allowed
    to add anything beyond what a deterministic check already found.
    """

    target_type: str
    target_id: uuid.UUID | None
    finding_category: str
    severity: str
    finding_text: str
    violated_condition: str
    evidence_refs: list[str] = field(default_factory=list)


# ----------------------------------------------------------------------
# 1. deterministic invariants - re-surfaces enforcement flags B7/B9 already
# computed at write time, as of red-team time (a drift-defense re-check,
# not a redundant recomputation - the underlying data can change between
# when a decision was first evaluated and when red-team runs).
# ----------------------------------------------------------------------


async def check_missing_mandatory_requirements(
    uow: UnitOfWork, tender_id: uuid.UUID
) -> list[RedteamFindingDraft]:
    requirements = await uow.requirements.list_for_tender(tender_id)
    drafts: list[RedteamFindingDraft] = []
    for requirement in requirements:
        if not requirement.mandatory:
            continue
        entry = await uow.compliance.get_compliance_entry_by_requirement(requirement.id)
        if entry is not None:
            continue
        drafts.append(
            RedteamFindingDraft(
                target_type="requirement",
                target_id=requirement.id,
                finding_category="missed_mandatory_requirement",
                severity="critical",
                finding_text=(
                    f"Mandatory requirement {requirement.display_code} has no "
                    "compliance decision recorded."
                ),
                violated_condition=f"requirement_id={requirement.id}; compliance_entry_count=0",
            )
        )
    return drafts


async def check_unsupported_claims(
    uow: UnitOfWork, tender_id: uuid.UUID
) -> list[RedteamFindingDraft]:
    # Re-surfaces B9's own enforce_claim_grounding output, as of red-team
    # time - the drift-defense re-check this module's docstring describes,
    # not a redundant recomputation. is_factual=False claims (opinion/
    # framing text) are exempt by design, same as grounding_status=
    # 'not_required' - neither was ever supposed to carry evidence.
    sections = await uow.proposal.list_proposal_sections_for_tender(tender_id)
    drafts: list[RedteamFindingDraft] = []
    for section in sections:
        claims = await uow.proposal.list_proposal_claims_for_section(section.id)
        for claim in claims:
            if not claim.is_factual or claim.grounding_status in ("grounded", "not_required"):
                continue
            drafts.append(
                RedteamFindingDraft(
                    target_type="proposal_claim",
                    target_id=claim.id,
                    finding_category="unsupported_claim",
                    severity="critical",
                    finding_text=(
                        f"Factual claim has grounding_status={claim.grounding_status!r}."
                    ),
                    violated_condition=(
                        f"proposal_claim_id={claim.id}; grounding_status={claim.grounding_status}"
                    ),
                )
            )
    return drafts


async def check_role_qualification_gaps(
    uow: UnitOfWork, tender_id: uuid.UUID
) -> list[RedteamFindingDraft]:
    role_requirements = await uow.talent.list_role_requirements_for_tender(tender_id)
    drafts: list[RedteamFindingDraft] = []
    for role_requirement in role_requirements:
        matches = await uow.talent.list_role_matches_for_role(role_requirement.id)
        selected = next((m for m in matches if m.human_selected), None)
        if selected is None or not selected.gaps:
            continue
        gap_criteria = sorted(gap["criterion"] for gap in selected.gaps)
        drafts.append(
            RedteamFindingDraft(
                target_type="role_match",
                target_id=selected.id,
                finding_category="role_qualification_gap",
                severity="major",
                finding_text=(
                    f"The human-selected candidate for role {role_requirement.role_name} "
                    f"has {len(selected.gaps)} unmet requirement(s)."
                ),
                violated_condition=f"role_match_id={selected.id}; gap_criteria={gap_criteria}",
            )
        )
    return drafts


# ----------------------------------------------------------------------
# 2. submission checklist checks
# ----------------------------------------------------------------------


async def check_submission_checklist(
    uow: UnitOfWork, tender_id: uuid.UUID
) -> list[RedteamFindingDraft]:
    items = await uow.requirements.list_submission_items_for_tender(tender_id)
    drafts: list[RedteamFindingDraft] = []
    for item in items:
        if not item.mandatory or item.status not in ("missing", "needs_review"):
            continue
        drafts.append(
            RedteamFindingDraft(
                target_type="submission_item",
                target_id=item.id,
                finding_category="missing_mandatory_submission_item",
                severity="major",
                finding_text=f"Mandatory submission item '{item.name}' is {item.status}.",
                violated_condition=f"submission_item_id={item.id}; status={item.status}",
            )
        )
    return drafts


# ----------------------------------------------------------------------
# 3. commercial findings - wraps existing, already-computed pricing_findings.
# No new arithmetic here; B8B's deterministic engine already did that work.
# ----------------------------------------------------------------------


async def check_commercial_findings(
    uow: UnitOfWork, tender_id: uuid.UUID
) -> list[RedteamFindingDraft]:
    findings = await uow.commercial.list_pricing_findings_for_tender(tender_id)
    drafts: list[RedteamFindingDraft] = []
    for finding in findings:
        if finding.resolved:
            continue
        drafts.append(
            RedteamFindingDraft(
                target_type="pricing_finding",
                target_id=finding.id,
                finding_category="pricing_mismatch",
                severity=finding.severity,
                finding_text=finding.note,
                violated_condition=f"pricing_finding_id={finding.id}; type={finding.finding_type}",
            )
        )
    return drafts


# ----------------------------------------------------------------------
# 4. graph contradiction checks - wraps B10's D6 finders +
# find_contradictions_for_redteam's D7 severity enrichment.
# ----------------------------------------------------------------------

# D6's own category names (graph_service.find_contradictions), used
# verbatim as finding_category - see the plan's Q4 answer for why this
# project doesn't introduce a second, renamed vocabulary on top of them.
_GRAPH_TARGET_TYPE: dict[str, str] = {
    "expired_certification": "compliance_entry",
    "deselected_fact_in_use": "evidence",
    "removed_employee_committed": "proposal_claim",
    "unsupported_grounded_claim": "proposal_claim",
}


async def check_graph_contradictions(tender_id: uuid.UUID) -> list[RedteamFindingDraft]:
    """One of run_red_team's 5 independent checks - the other 4 have no
    graph dependency and can always contribute findings, so this one being
    unable to run yet (no projection has succeeded since the tender's last
    change - GraphStaleError, `graph_service.check_freshness`'s documented
    behaviour for the never-projected case too) must not crash the other
    4 or the LLM critique step alongside it. Contrast with routes/graph.py's
    own read routes, which correctly surface GraphStaleError as 409
    GRAPH_STALE to the caller - those routes exist specifically to answer
    "what does the graph say right now," so staleness IS the answer.
    run_red_team's callers (in particular the manual POST .../redteam/run
    trigger, routes/redteam.py) are asking a broader question ("what can
    Red Team find right now"), where a stale graph just means this one
    check has nothing to add this pass, not that the whole run should fail.
    """
    try:
        rows = await graph_service.find_contradictions_for_redteam(tender_id)
    except graph_service.GraphStaleError:
        return []
    drafts: list[RedteamFindingDraft] = []
    for row in rows:
        category = row["category"]
        node = row.get("compliance") or row.get("claim") or row.get("target") or row.get("fact")
        target_id = uuid.UUID(node["id"]) if node and node.get("id") else None
        drafts.append(
            RedteamFindingDraft(
                target_type=_GRAPH_TARGET_TYPE.get(category, "unknown"),
                target_id=target_id,
                finding_category=category,
                severity=row["severity"],
                finding_text=f"Graph contradiction detected: {category.replace('_', ' ')}.",
                violated_condition=f"category={category}; node_id={target_id}",
            )
        )
    return drafts
