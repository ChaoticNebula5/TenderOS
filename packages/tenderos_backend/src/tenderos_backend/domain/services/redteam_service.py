import uuid

from tenderos_backend.agents.llm.client import LLMClient
from tenderos_backend.agents.redteam.critique import ClaimForCritique, critique_proposal
from tenderos_backend.core.hashing import sha256_hex
from tenderos_backend.db.models.redteam import RedteamFinding
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import redteam_checks
from tenderos_backend.domain.services.redteam_checks import RedteamFindingDraft

# The revision-eligible severity set (Q5's flagged interpretation: R3 locks
# the state field name open_critical_finding_ids, critical only, not
# "critical_or_major" - major findings always surface via finding_gate for
# human judgment, never auto-revised).
_AUTO_REVISION_SEVERITIES = ("critical",)
# E8: "Critical findings must be explicitly resolved, accepted, or
# escalated" - major findings need a human's eyes too, just never trigger
# the automatic loop.
_OPEN_SEVERITIES = ("critical", "major")


class FindingNotFoundError(Exception):
    pass


class FindingTenderMissingError(Exception):
    """A red-team finding whose tender row does not exist. redteam_findings.
    tender_id is a cascading foreign key, so this cannot happen without a
    database integrity failure - raised (not mapped to an HTTP status, so it
    surfaces as a 500) instead of an anonymous AttributeError, and BEFORE any
    write, so nothing is half-applied.
    """

    def __init__(self, finding_id: uuid.UUID, tender_id: uuid.UUID) -> None:
        super().__init__(f"redteam finding {finding_id} references missing tender {tender_id}")


def finding_fingerprint(
    tender_id: uuid.UUID, target_type: str, target_id: uuid.UUID | None,
    finding_category: str, violated_condition: str,
) -> str:
    # E8: "Finding identity is based on the violated condition, not
    # free-text wording" - same _fingerprint(*parts) shape pricing_checks.py
    # already uses, deliberately excluding finding_text/rebuttal so a
    # differently-worded re-detection of the SAME underlying problem still
    # upserts onto the same row instead of creating a duplicate.
    return sha256_hex(
        "|".join(
            [str(tender_id), target_type, str(target_id), finding_category, violated_condition]
        ).encode("utf-8")
    )


async def persist_finding_draft(
    uow: UnitOfWork, tender_id: uuid.UUID, draft: RedteamFindingDraft
) -> RedteamFinding:
    """Public since B16: a second real caller (apps/worker/jobs/reanalysis.py)
    needs this exact upsert-by-fingerprint logic for one category
    (deselected_fact_in_use) without running run_red_team's full 5-check
    sweep. Never call auto_retire_undetected_findings from that narrower
    caller - its own docstring is explicit that retirement is only safe
    because run_red_team's check list covers a tender's ENTIRE current
    state every pass; a caller checking one category would wrongly retire
    every other open finding it never even looked at.
    """
    fingerprint = finding_fingerprint(
        tender_id, draft.target_type, draft.target_id, draft.finding_category,
        draft.violated_condition,
    )
    existing = await uow.redteam.get_by_fingerprint(tender_id, fingerprint)
    if existing is not None:
        # A human's resolved/resolution_note on this exact fingerprint
        # survives a finding reappearing unchanged across repeated
        # red_team passes (Q4's "MERGE-not-delete" design) - never
        # overwritten here. But a SYSTEM retirement (auto_retire_
        # undetected_findings, resolution_source="system") means "this
        # wasn't detected last pass," not "this is resolved" - if the same
        # fingerprint is being persisted again right now, the defect is
        # back, and leaving it resolved would silently hide a
        # currently-real finding (worse than never auto-retiring it at
        # all: a critical finding sitting unseen in the resolved pile,
        # in exactly the revision loop this mechanism exists to drive).
        if existing.resolved and existing.resolution_source == "system":
            return await uow.redteam.reopen_finding(
                existing.id, draft.severity, draft.finding_text, draft.evidence_refs
            )
        return existing
    return await uow.redteam.create_finding(
        tender_id=tender_id,
        target_type=draft.target_type,
        target_id=draft.target_id,
        finding_category=draft.finding_category,
        severity=draft.severity,
        finding_text=draft.finding_text,
        violated_condition=draft.violated_condition,
        content_fingerprint=fingerprint,
        evidence_refs=draft.evidence_refs,
    )


async def _claims_for_critique(uow: UnitOfWork, tender_id: uuid.UUID) -> tuple[
    list[ClaimForCritique], dict[str, uuid.UUID]
]:
    sections = await uow.proposal.list_proposal_sections_for_tender(tender_id)
    claims_for_critique: list[ClaimForCritique] = []
    label_to_id: dict[str, uuid.UUID] = {}
    index = 0
    for section in sections:
        claims = await uow.proposal.list_proposal_claims_for_section(section.id)
        for claim in claims:
            index += 1
            label = f"claim_{index}"
            label_to_id[label] = claim.id
            requirement_links = await uow.proposal.list_requirements_for_claim(claim.id)
            requirement_text = None
            if requirement_links:
                requirement = await uow.requirements.get(requirement_links[0].requirement_id)
                requirement_text = requirement.requirement_text if requirement else None
            claims_for_critique.append(
                ClaimForCritique(
                    label=label,
                    claim_text=claim.claim_text,
                    claim_type=claim.claim_type,
                    grounding_status=claim.grounding_status,
                    requirement_text=requirement_text,
                )
            )
    return claims_for_critique, label_to_id


async def run_red_team(
    client: LLMClient,
    model: str,
    tender_id: uuid.UUID,
    workflow_run_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
) -> list[str]:
    """Red Team's full 5-step check list (B11 build item), run in the order
    E8 requires: "Deterministic checks and graph queries run before LLM
    critique." Persists every finding via the upsert-by-fingerprint pattern
    (persist_finding_draft) and returns the tender's CURRENT full set of open
    critical+major finding ids - queried fresh after every upsert, not
    accumulated locally, since a finding from a previous pass that's no
    longer being re-detected must not linger in what this function reports
    as open.

    No write path in this function (or anywhere else in this module) ever
    touches proposal_claims/compliance_entries/role_matches/pricing_findings -
    only uow.redteam.create_finding. That is the enforcement half of "Red
    Team cannot silently edit approved content" (the schema half is
    RedteamCritique having no content-mutation field at all, see
    agents/schemas/redteam.py).
    """
    async with UnitOfWork() as uow:
        drafts: list[RedteamFindingDraft] = []
        drafts.extend(await redteam_checks.check_missing_mandatory_requirements(uow, tender_id))
        drafts.extend(await redteam_checks.check_unsupported_claims(uow, tender_id))
        drafts.extend(await redteam_checks.check_role_qualification_gaps(uow, tender_id))
        drafts.extend(await redteam_checks.check_submission_checklist(uow, tender_id))
        drafts.extend(await redteam_checks.check_commercial_findings(uow, tender_id))
        claims_for_critique, label_to_id = await _claims_for_critique(uow, tender_id)

    drafts.extend(await redteam_checks.check_graph_contradictions(tender_id))

    critique = await critique_proposal(
        client, model, claims_for_critique, tender_id,
        workflow_run_id=workflow_run_id, task_id=task_id,
    )
    for finding in critique.findings:
        target_id = label_to_id.get(finding.target_claim_label)
        if target_id is None:
            # The model referenced a label that was never offered to it -
            # same "never trust a model-echoed identifier" posture as every
            # other agent in this codebase; drop it rather than persist a
            # finding pointed at nothing.
            continue
        drafts.append(
            RedteamFindingDraft(
                target_type="proposal_claim",
                target_id=target_id,
                finding_category=finding.finding_category,
                severity=finding.severity,
                finding_text=finding.finding_text,
                violated_condition=finding.violated_condition,
                evidence_refs=finding.evidence_refs,
            )
        )

    async with UnitOfWork() as uow:
        for draft in drafts:
            await persist_finding_draft(uow, tender_id, draft)

        # All 5 checks above (plus the LLM critique) re-derive fully fresh
        # from this tender's CURRENT state on every pass, never
        # incrementally - so an unresolved finding whose fingerprint isn't
        # among what was just (re-)persisted is provably no longer present,
        # not merely unchecked this time. Without this, a corrected defect
        # left its finding open forever: a phantom blocker in a readiness
        # report and a false "no progress" stall in route_after_red_team's
        # own revision-loop check (both compare against open_critical_
        # finding_ids, which list_open_for_tender feeds).
        still_detected_fingerprints = {
            finding_fingerprint(
                tender_id, draft.target_type, draft.target_id,
                draft.finding_category, draft.violated_condition,
            )
            for draft in drafts
        }
        await uow.redteam.auto_retire_undetected_findings(tender_id, still_detected_fingerprints)
        await uow.commit()

    async with UnitOfWork() as uow:
        open_findings = await uow.redteam.list_open_for_tender(tender_id, _OPEN_SEVERITIES)
    return [str(f.id) for f in open_findings if f.severity in _AUTO_REVISION_SEVERITIES]


# ----------------------------------------------------------------------
# G11 - human-facing read/resolve, same shape as
# commercial_service.list_pricing_findings_for_tender/resolve_pricing_finding.
# ----------------------------------------------------------------------


async def list_findings_for_tender(tender_id: uuid.UUID) -> list[RedteamFinding]:
    async with UnitOfWork() as uow:
        return await uow.redteam.list_for_tender(tender_id)


async def resolve_finding(
    finding_id: uuid.UUID, actor_user_id: uuid.UUID, resolution_note: str | None
) -> RedteamFinding:
    async with UnitOfWork() as uow:
        finding = await uow.redteam.get(finding_id)
        if finding is None:
            raise FindingNotFoundError()
        # Looked up before any write (it used to be read inline, after the
        # resolve update): the audit row needs the tender's organization.
        tender = await uow.tenders.get(finding.tender_id)
        if tender is None:
            raise FindingTenderMissingError(finding_id, finding.tender_id)
        before = {"resolved": finding.resolved, "resolution_note": finding.resolution_note}
        updated = await uow.redteam.resolve_finding(finding_id, resolution_note)
        await uow.audit.create(
            organization_id=tender.organization_id,
            actor_type="user",
            actor_id=str(actor_user_id),
            action="redteam_finding_resolve",
            tender_id=finding.tender_id,
            target_type="redteam_finding",
            target_id=finding_id,
            before=before,
            after={"resolved": True, "resolution_note": resolution_note},
        )
        await uow.commit()
        return updated


# TODO(human): map_finding_to_corrective_task
#
# Steps (plain-English):
#   1. Given one finding_category string, decide which task_type (if any)
#      would automatically fix the underlying problem if re-run.
#   2. "missed_mandatory_requirement" and "expired_certification" both
#      indicate a compliance decision that's missing or stale -> both map
#      to "compliance_analysis" (re-running it produces a fresh decision
#      from current data).
#   3. "unsupported_claim" and "removed_employee_committed" (both target a
#      proposal_claim) -> map to "proposal_revision". proposal_drafting
#      already rebuilds its grounded context fresh from current DB state
#      every run, so re-invoking it is a legitimate full redraft, not a
#      partial edit.
#   4. Everything else - "deselected_fact_in_use", "unsupported_grounded_claim",
#      "pricing_mismatch", "missing_mandatory_submission_item",
#      "role_qualification_gap", any category from the adversarial LLM step
#      not in this list (e.g. "overstated_claim", "requirement_misalignment") -
#      has no deterministic "right answer" a re-run could compute (a human
#      has to re-upload a document, pick a different candidate, or judge a
#      subjective critique). Return None for these.
#   5. Return the task_type string, or None if nothing here is auto-correctable.
#
# What's already available to use:
#   - `finding_category: str` - the only parameter.
#   - R4's real task_type strings (orchestration/supervisor.py's TASK_TYPES
#     tuple) - only use ones already listed there.
#
# Why this is worth writing by hand: it's the one real judgment call in
# this block - which finding categories are trustworthy enough to
# auto-correct without a human back in the loop, versus which ones always
# need a human decision. Getting it wrong either direction has a real cost:
# too permissive silently re-drafts content nobody asked to change again;
# too conservative burns through max_revisions on findings a human could
# have resolved in one click via finding_gate instead.
def map_finding_to_corrective_task(finding_category: str) -> str | None:
    if finding_category in ["missed_mandatory_requirement", "expired_certification"]:
        return "compliance_analysis"
    if finding_category in ["unsupported_claim", "removed_employee_committed"]:
        return "proposal_revision"
    return None
