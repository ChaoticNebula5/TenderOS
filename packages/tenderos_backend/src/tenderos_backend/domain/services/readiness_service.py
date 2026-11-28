import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from tenderos_backend.core.evidence_validity import (
    REASON_EXPIRED,
    REASON_UNVERIFIED,
    REASON_WITHDRAWN,
    ItemValidity,
    today_utc,
)
from tenderos_backend.core.hashing import sha256_hex
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services.graph_service import (
    GraphStaleError,
    check_freshness_with_retry,
)

logger = logging.getLogger(__name__)

# Bounded retry for the state_version race documented on evaluate_readiness
# itself below - not arbitrary, matches the same "bounded, not infinite"
# posture B11's revision loop already established for a different race.
_MAX_READINESS_ATTEMPTS = 3


class TenderNotFoundError(Exception):
    # Same catchable-class convention graph_service.TenderNotFoundError
    # already establishes - routes/readiness.py catches this specifically
    # rather than letting a bare ValueError surface as an unhandled 500.
    pass


# The Build Guide's 7 required blockers (B12's own build list, verbatim
# categories) plus one addition this block's own Q5 answer motivates -
# "at minimum" in that list's own wording explicitly leaves room for more,
# and silently treating a FAILED red_team pass as "nothing open" would be
# the same "guard that looks complete" shape this project has hit three
# times already (see KNOWN_LIMITATIONS.md).
UNRESOLVED_MANDATORY_REQUIREMENT = "unresolved_mandatory_requirement"
COMPLIANT_DECISION_WITHOUT_VALID_EVIDENCE = "compliant_decision_without_valid_evidence"
CRITICAL_REDTEAM_FINDING = "critical_redteam_finding"
MISSING_MANDATORY_SUBMISSION_ITEM = "missing_mandatory_submission_item"
UNAPPROVED_REQUIRED_COMMERCIAL_ITEM = "unapproved_required_commercial_item"
ROLE_WITH_NO_SELECTED_CANDIDATE = "role_with_no_selected_candidate"
SELECTED_EMPLOYEE_REMOVED = "selected_employee_removed"
STALE_GRAPH_PROJECTION = "stale_graph_projection"
RED_TEAM_REVIEW_INCOMPLETE = "red_team_review_incomplete"
# Plan item 99: a tender document that is not parsed has had no requirements
# extracted, and a new run is refused until it is (DOCUMENTS_NOT_READY).
DOCUMENT_NOT_PARSED = "document_not_parsed"
# Plan item 104: a parsed tender document whose text no completed
# requirement extraction has read (added after the run's extraction, or
# changed by a re-parse), or that has no readable text at all.
DOCUMENT_NOT_EXTRACTED = "document_not_extracted"
# Plan item 121: a reviewer's compliance decision reopened because the
# analysis's own answer changed after it was made.
REOPENED_COMPLIANCE_DECISION = "reopened_compliance_decision"
# Plan item 124: a knowledge selection change whose reanalysis failed, with
# no later completed reanalysis of the same item.
REANALYSIS_NOT_APPLIED = "reanalysis_not_applied"

# Every blocker-10 message ends on how to get a new run. A run still open
# (one waiting at the final gate, say) refuses a new one (WORKFLOW_RUN_ACTIVE)
# and the Upload page then shows that run's tasks and Cancel instead of
# Start processing, so the message says how to close it first.
_NEW_RUN_STEPS = (
    "Start a new run on the Upload page to extract its requirements. If a run is still open, "
    "first cancel it there or request changes at its gate."
)
_AMENDMENT_STEP = " Then review the requirements it changes."


def _not_applied_message(title: str | None, knowledge_item_id: uuid.UUID, selected: bool) -> str:
    """Blocker 12's message (plan item 124, PR 40's review). A re-run is two
    selection changes, in the order that leaves the item as the person has
    it now; an item that no longer exists cannot be selected, so the final
    gate's waiver is the only way on."""
    name = f"'{title}'" if title is not None else f"knowledge item {knowledge_item_id}"
    failed = (
        f"A selection change of {name} was not applied to this bid: its reanalysis failed, so "
        "compliance decisions, approvals and claim grounding may not reflect it. "
    )
    if title is None:
        return failed + (
            "The item no longer exists, so its reanalysis cannot be re-run. To proceed, approve "
            "the final gate with Waive outstanding blockers ticked."
        )
    if selected:
        now, steps = "selected", "deselect it and then select it again"
    else:
        now, steps = "deselected", "select it and then deselect it again"
    return failed + (
        f"{name} is {now} for this bid. To re-run it, {steps} on the Knowledge selections page."
    )


_COMPLIANT_STATUSES = ("fully_compliant", "partially_compliant")
_UNRESOLVED_COMPLIANCE_STATUSES = ("not_reviewed", "clarification_required", "evidence_unavailable")
_OPEN_COMMERCIAL_SEVERITIES = ("critical", "major")
_OPEN_REDTEAM_SEVERITIES = ("critical", "major")


@dataclass(frozen=True)
class ReadinessResult:
    report_id: uuid.UUID
    tender_id: uuid.UUID
    tender_state_version: int
    ready: bool
    generated_at: datetime
    blockers: list[dict[str, Any]] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    # When this content was last computed (plan item 93: a reuse moves it).
    # PR 31: the final gate's refusal line names it for both reports W2
    # compared. Not in the API response.
    last_generated_at: datetime | None = None
    # Plan item 120: the report's place in its tender's order, which decides
    # the current report (the highest is current). The refusal line names it
    # beside last_generated_at (Q4 a). Not in the API response.
    generation: int | None = None


def _result_from_report(report: Any) -> ReadinessResult:
    return ReadinessResult(
        report_id=report.id,
        tender_id=report.tender_id,
        tender_state_version=report.tender_state_version,
        ready=report.ready,
        generated_at=report.generated_at,
        blockers=report.blockers,
        counts=report.counts,
        last_generated_at=report.last_generated_at,
        generation=report.generation,
    )


def report_content_hash(ready: bool, blockers: list[dict[str, Any]], counts: dict[str, int]) -> str:
    """Plan item 93: what makes two reports at one version the same report.
    Canonical JSON over ready, the blockers sorted on a fixed key and the
    counts, with sorted keys and no whitespace, then SHA-256. Every field is
    content: a blocker carries only its category, target and a message built
    from ids, codes and names, and counts are integers, so no timestamp or
    per-generate id enters the hash. Blocker order is not content, so the
    same blockers in another order hash the same."""
    ordered = sorted(
        blockers,
        key=lambda b: (
            b["category"],
            b["target_type"],
            b["target_id"] or "",
            b["message"],
        ),
    )
    canonical = json.dumps(
        {"ready": ready, "blockers": ordered, "counts": counts},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return sha256_hex(canonical.encode("utf-8"))


def _blocker(category: str, target_type: str, target_id: uuid.UUID | None, message: str) -> dict:
    return {
        "category": category,
        "target_type": target_type,
        "target_id": str(target_id) if target_id else None,
        "message": message,
    }


def _evidence_counts(link: Any, live: dict[uuid.UUID, ItemValidity]) -> bool:
    """Whether one evidence link counts now (plan items 89 and 91): a link
    with a knowledge item counts when that item does, judged now; a document
    excerpt (no item) by its stored values."""
    if link.knowledge_item_id is not None:
        return live[link.knowledge_item_id].counts
    return bool(link.verified and link.validity_status == "valid")


_REASON_WORDING = {
    REASON_EXPIRED: "expired",
    REASON_UNVERIFIED: "is unverified",
    REASON_WITHDRAWN: "was withdrawn",
}


def _no_valid_evidence_message(entry: Any, links: list, live: dict[uuid.UUID, ItemValidity]) -> str:
    """Blocker 2's message. When every link of the entry has a knowledge item
    and all fail for the same reason, it says which and why (each item once,
    sorted by title, so the text, and the report's content hash, depend only
    on the facts); otherwise the generic message."""
    generic = (
        f"Compliance entry {entry.id} is marked {entry.status} "
        "with no verified, currently-valid evidence."
    )
    if not links or any(link.knowledge_item_id is None for link in links):
        return generic
    judged = [live[link.knowledge_item_id] for link in links]
    reasons = {v.reason for v in judged}
    if len(reasons) != 1:
        return generic
    (reason,) = reasons
    if reason not in _REASON_WORDING:
        return generic
    parts = []
    items = sorted({(v.title or "untitled", v.expired_on) for v in judged}, key=str)
    for title, expired_on in items:
        if reason == REASON_EXPIRED:
            when = f" on {expired_on.isoformat()}" if expired_on is not None else ""
            parts.append(f"'{title}' expired{when}")
        else:
            parts.append(f"'{title}' {_REASON_WORDING[reason]}")
    return generic[:-1] + ": its evidence " + " and ".join(parts) + "."


async def _log_stale_projection_blocker(stale: GraphStaleError) -> None:
    """PR 30, plan item 113: one WARNING each time readiness adds the
    stale-projection blocker, naming the tender, the state version the
    freshness check read, why it was stale ("stale": no completed projection
    at that version; "damaged": one completed but the graph is incomplete),
    and whether a projection at that version is recorded as running. Item
    113's watch is the "damaged" case read while a re-projection at the same
    version is still writing; these lines count it. The same facts go in
    `extra`, so the JSON line can be filtered on them. A failure to look the
    projection up never changes the report: it is logged as unknown."""
    version = stale.current_state_version
    running: bool | None
    try:
        async with UnitOfWork() as uow:
            running = await uow.graph_projection.has_running_run(stale.tender_id, version)
    except Exception:
        logger.exception("readiness: could not read projection runs for tender %s", stale.tender_id)
        running = None
    logger.warning(
        "readiness: stale graph projection blocker added for tender %s at state version %s "
        "(reason %s, latest completed projection %s); a projection at this version is "
        "running: %s",
        stale.tender_id,
        version,
        stale.reason,
        stale.projected_state_version,
        "unknown" if running is None else running,
        extra={
            "state_version": version,
            "stale_reason": stale.reason,
            "projection_running": running,
        },
    )


async def evaluate_readiness(tender_id: uuid.UUID) -> ReadinessResult:
    """B12's deterministic readiness policy - the Build Guide makes readiness
    a deterministic function of the current records: an LLM can describe the
    blockers in words, but never sets ready=true. No LLM call anywhere in
    this function.

    Public entry point - a thin bounded-retry wrapper around
    _evaluate_readiness_once. The retry exists for a real race that
    single-attempt code can't avoid: tender.state_version is read at the
    START of computation (so blockers/counts reflect a specific version)
    but the report is only persisted at the END, after several separate
    reads across Postgres and Neo4j - if something bumps state_version in
    between, what was just computed no longer describes the tender's
    current state and must not be persisted as if it does.
    _evaluate_readiness_once returns None exactly in that case (detected by
    re-reading state_version immediately before persisting); this wrapper
    then retries the WHOLE computation fresh against whatever the tender's
    state now is, up to _MAX_READINESS_ATTEMPTS times. Chose "recompute and
    retry" over "compute once and abort" specifically because this function
    has no natural way to hand a caller a half-finished result - readiness_
    generation_node and the manual POST /readiness/generate route both
    expect one real ReadinessResult back, not a "try again yourself" signal.
    A true single cross-store transaction (Postgres + Neo4j together) isn't
    possible here, so bounded retry is the practical alternative to a
    genuine distributed transaction.
    """
    for _attempt in range(_MAX_READINESS_ATTEMPTS):
        result = await _evaluate_readiness_once(tender_id)
        if result is not None:
            return result
    raise RuntimeError(
        f"readiness evaluation for tender {tender_id} did not converge after "
        f"{_MAX_READINESS_ATTEMPTS} attempts - tender.state_version kept "
        "changing while blockers were being computed"
    )


async def _evaluate_readiness_once(tender_id: uuid.UUID) -> ReadinessResult | None:
    """One attempt. Returns None (signaling "retry me") if tender.state_
    version moved between the read at the top and the persist at the
    bottom, rather than persisting a report whose contents may already be
    stale relative to the version it would claim to represent.

    Blockers 1-7 are the Build Guide's own required list, verbatim
    categories; blocker 8 (red_team_review_incomplete) is this block's own
    addition - see the module docstring above and the B12 plan's Q5 answer
    for why.
    """
    blockers: list[dict[str, Any]] = []
    counts: dict[str, int] = {}

    async with UnitOfWork() as uow:
        tender = await uow.tenders.get(tender_id)
        if tender is None:
            raise TenderNotFoundError(f"Tender {tender_id} not found")
        state_version = tender.state_version

        # ------------------------------------------------------------
        # 1. Unresolved mandatory requirement. Deliberately inline rather
        # than calling redteam_checks.check_missing_mandatory_requirements
        # (the B12 plan's original reuse idea): that function only covers
        # "zero compliance_entries at all," but this blocker is broader -
        # a requirement WITH an entry stuck at not_reviewed/clarification_
        # required/evidence_unavailable is just as unresolved. Both cases
        # fall out of one pass here instead of composing two separate
        # requirement-iteration queries and unioning their results.
        #
        # CodeRabbit review: this used to call get_compliance_entry_by_
        # requirement once PER requirement inside the loop -
        # O(requirements) sequential round-trips, invisible on the small
        # synthetic tender this block was built and tested against, real
        # cost on a tender with hundreds of requirements - paid TWICE per
        # check, since evaluate_readiness runs both from the manual POST
        # /readiness/generate route and from readiness_generation_node.
        # One batched query up front (list_compliance_entries_by_
        # requirement_ids), then the loop below reads from the resulting
        # dict in memory instead of awaiting per item.
        # ------------------------------------------------------------
        requirements = await uow.requirements.list_for_tender(tender_id)
        mandatory_requirement_ids = [r.id for r in requirements if r.mandatory]
        entries_by_requirement = await uow.compliance.list_compliance_entries_by_requirement_ids(
            mandatory_requirement_ids
        )
        mandatory_unresolved = 0
        requirements_completed = 0
        for requirement in requirements:
            if requirement.human_reviewed:
                requirements_completed += 1
            if not requirement.mandatory:
                continue
            entry = entries_by_requirement.get(requirement.id)
            if entry is None or entry.status in _UNRESOLVED_COMPLIANCE_STATUSES:
                mandatory_unresolved += 1
                blockers.append(
                    _blocker(
                        UNRESOLVED_MANDATORY_REQUIREMENT,
                        "requirement",
                        requirement.id,
                        f"Mandatory requirement {requirement.display_code} has no "
                        "resolved compliance decision.",
                    )
                )
        counts["requirements_total"] = len(requirements)
        counts["requirements_completed"] = requirements_completed
        counts["mandatory_requirements_unresolved"] = mandatory_unresolved

        # ------------------------------------------------------------
        # 2. Compliant decision without valid evidence
        #
        # Deliberately a plain Postgres query, NOT graph_service's
        # evaluate_evidence_coverage - that function reads Neo4j-projected
        # facts (deselection/knowledge-item status), which couples it to
        # graph freshness. This blocker asks a narrower, graph-independent
        # question - does this compliance decision have ANY evidence_links
        # row that is itself verified+valid - and must stay answerable even
        # when blocker 7 (stale graph) is already true, not be silently
        # entangled with it.
        #
        # Same batching fix as blocker 1 above, for the sibling N+1: one
        # list_evidence_for_compliance_entries call for every compliance
        # entry at once, instead of list_evidence_for_compliance_entry
        # once per entry inside the loop.
        # ------------------------------------------------------------
        compliance_entries = await uow.compliance.list_compliance_entries_for_tender(tender_id)
        evidence_by_entry = await uow.compliance.list_evidence_for_compliance_entries(
            [e.id for e in compliance_entries]
        )
        # Plan items 89 and 91: a link's stored verified/validity_status are
        # as of the last evaluation that cited it, and nothing rewrites them
        # when its item is archived, its date passes or its profile review
        # changes. So a link with a knowledge item counts only if the item
        # does now (core/evidence_validity.py), read for every link at once
        # (one query), the same judgment the proposal's evidence filter and
        # both traces make. A document excerpt has no item and keeps its
        # stored values. Every generate computes this afresh (plan item 93),
        # so a date passing needs no bump to reach it.
        live = await uow.knowledge.item_validity(
            (
                link.knowledge_item_id
                for links in evidence_by_entry.values()
                for link in links
                if link.knowledge_item_id is not None
            ),
            today_utc(),
        )
        awaiting_approval = 0
        without_valid_evidence = 0
        reopened = 0
        display_codes = {r.id: r.display_code for r in requirements}
        for entry in compliance_entries:
            if not entry.human_approved:
                awaiting_approval += 1
            # Blocker 11 (plan item 121, Q2 a): a reviewer's decision that a
            # changed analysis answer reopened. An override always approves
            # (compliance_service.override_compliance_entry), so a reviewer's
            # decision is unapproved only once a re-evaluation reopened it; a
            # reviewer part-way through a review has no such entry. Unlike a
            # plain unapproved decision (only counted above), it blocks: its
            # status is the reviewer's, so blockers 1 and 2 may not see it.
            if entry.decided_by == "reviewer" and not entry.human_approved:
                reopened += 1
                code = display_codes.get(entry.requirement_id, str(entry.requirement_id))
                blockers.append(
                    _blocker(
                        REOPENED_COMPLIANCE_DECISION,
                        "compliance_entry",
                        entry.id,
                        f"The compliance decision for requirement {code} was reopened: the "
                        f"analysis now says {entry.automated_status}, the reviewer decided "
                        f"{entry.status}. Approve it again or change it on the Compliance page.",
                    )
                )
            if entry.status not in _COMPLIANT_STATUSES:
                continue
            evidence = evidence_by_entry.get(entry.id, [])
            has_valid = any(_evidence_counts(e, live) for e in evidence)
            if not has_valid:
                without_valid_evidence += 1
                blockers.append(
                    _blocker(
                        COMPLIANT_DECISION_WITHOUT_VALID_EVIDENCE,
                        "compliance_entry",
                        entry.id,
                        _no_valid_evidence_message(entry, evidence, live),
                    )
                )
        counts["compliance_decisions_awaiting_approval"] = awaiting_approval
        counts["requirements_lacking_valid_evidence"] = without_valid_evidence
        counts["compliance_decisions_reopened"] = reopened

        # ------------------------------------------------------------
        # 3. Critical red-team finding (reuses B11's own repository query)
        # ------------------------------------------------------------
        open_redteam = await uow.redteam.list_open_for_tender(tender_id, _OPEN_REDTEAM_SEVERITIES)
        for finding in open_redteam:
            if finding.severity == "critical":
                blockers.append(
                    _blocker(
                        CRITICAL_REDTEAM_FINDING,
                        "redteam_finding",
                        finding.id,
                        f"Unresolved critical red-team finding: {finding.finding_category}.",
                    )
                )
        counts["critical_major_redteam_findings"] = len(open_redteam)

        # ------------------------------------------------------------
        # 4. Missing mandatory submission item
        # ------------------------------------------------------------
        submission_items = await uow.requirements.list_submission_items_for_tender(tender_id)
        missing_items = 0
        for item in submission_items:
            if item.mandatory and item.status == "missing":
                missing_items += 1
                blockers.append(
                    _blocker(
                        MISSING_MANDATORY_SUBMISSION_ITEM,
                        "submission_item",
                        item.id,
                        f"Mandatory submission item '{item.name}' is missing.",
                    )
                )
        counts["missing_submission_artefacts"] = missing_items

        # ------------------------------------------------------------
        # 5. Unapproved required commercial item
        # ------------------------------------------------------------
        pricing_findings = await uow.commercial.list_pricing_findings_for_tender(tender_id)
        open_commercial = 0
        for pf in pricing_findings:
            if pf.resolved:
                continue
            open_commercial += 1
            if pf.severity in _OPEN_COMMERCIAL_SEVERITIES:
                blockers.append(
                    _blocker(
                        UNAPPROVED_REQUIRED_COMMERCIAL_ITEM,
                        "pricing_finding",
                        pf.id,
                        f"Unresolved {pf.severity} commercial finding: {pf.finding_type}.",
                    )
                )
        counts["commercial_findings_open"] = open_commercial

        # ------------------------------------------------------------
        # 6. Required role with no human-selected candidate
        # ------------------------------------------------------------
        role_requirements = await uow.talent.list_role_requirements_for_tender(tender_id)
        role_matches_by_role = [
            await uow.talent.list_role_matches_for_role(role.id) for role in role_requirements
        ]
        resource_gaps = 0
        for role, matches in zip(role_requirements, role_matches_by_role, strict=True):
            if not any(m.human_selected for m in matches):
                resource_gaps += 1
                blockers.append(
                    _blocker(
                        ROLE_WITH_NO_SELECTED_CANDIDATE,
                        "role_requirement",
                        role.id,
                        f"Role '{role.role_name}' has no human-selected candidate.",
                    )
                )
        counts["resource_gaps"] = resource_gaps

        # ------------------------------------------------------------
        # 6b. A role filled by an employee removed from the roster (plan
        # item 86). Read from the knowledge item now: archiving writes
        # nothing else a report could see. Its own blocker, not a step 6
        # gap, which would tell the user the role has no candidate.
        # ------------------------------------------------------------
        selected = [
            (role, match)
            for role, matches in zip(role_requirements, role_matches_by_role, strict=True)
            for match in matches
            if match.human_selected
        ]
        archived = await uow.knowledge.archived_item_ids(
            match.employee_knowledge_item_id for _, match in selected
        )
        removed_selected = 0
        for role, match in selected:
            if match.employee_knowledge_item_id not in archived:
                continue
            removed_selected += 1
            blockers.append(
                _blocker(
                    SELECTED_EMPLOYEE_REMOVED,
                    "role_match",
                    match.id,
                    f"Role '{role.role_name}' is filled by an employee removed from the roster.",
                )
            )
        counts["selected_employees_removed"] = removed_selected

        # ------------------------------------------------------------
        # Proposal claims requiring review (PRD count, not a Build Guide
        # blocker) and open clarifications - both plain counts.
        # ------------------------------------------------------------
        sections = await uow.proposal.list_proposal_sections_for_tender(tender_id)
        claims_needing_review = 0
        for section in sections:
            claims = await uow.proposal.list_proposal_claims_for_section(section.id)
            claims_needing_review += sum(
                1 for c in claims if c.grounding_status in ("needs_review", "unsupported")
            )
        counts["proposal_claims_needing_review"] = claims_needing_review

        clarifications = await uow.requirements.list_clarification_items_for_tender(tender_id)
        counts["open_clarifications"] = sum(1 for c in clarifications if c.status == "open")

        # ------------------------------------------------------------
        # 8. Red-team review (plan items 94 and 96). Read from the passes
        # recorded by run_red_team, never from the caller: the Readiness
        # page, the run's node and the final gate's approval all ask the same
        # question, so they agree. The question: has a complete pass, graph
        # checks included, seen this state version? A later failed attempt
        # does not undo a complete pass at this version (user decision, Q1):
        # it wrote no findings, so that pass's findings are still current.
        # Otherwise the message says why, from the newest pass.
        # ------------------------------------------------------------
        if not await uow.redteam.has_complete_pass_at(tender_id, state_version):
            latest_pass = await uow.redteam.latest_pass(tender_id)
            if latest_pass is None:
                message = "No red-team pass has been recorded for this bid. Run the red team."
            elif latest_pass.outcome == "failed":
                message = "The most recent red-team pass failed. Run the red team again."
            elif latest_pass.graph_checks == "skipped":
                message = (
                    "The most recent red-team pass could not check the graph (it was stale). "
                    "Run the red team again."
                )
            else:
                message = (
                    f"The red-team review checked graph version {latest_pass.graph_state_version}; "
                    f"the bid is now at version {state_version}. Run the red team again."
                )
            blockers.append(_blocker(RED_TEAM_REVIEW_INCOMPLETE, "tender", tender_id, message))

        # ------------------------------------------------------------
        # 9. Documents not parsed (plan item 99). Every tender document
        # counts: tender documents are never superseded (only an employee's
        # resume, an organization document, sets supersedes_document_id). A
        # failed parse says how to retry it. Any other unfinished status is
        # named as it is, never as "still being processed": a document left
        # in uploaded or queued may be stuck. A bid with no documents is
        # blocked too, as a new run is (user decision, Q4).
        # ------------------------------------------------------------
        documents = await uow.documents.list_for_tender(tender_id)
        if not documents:
            blockers.append(
                _blocker(DOCUMENT_NOT_PARSED, "tender", tender_id, "The bid has no documents.")
            )
        not_parsed = [doc for doc in documents if doc.parse_status != "parsed"]
        for doc in not_parsed:
            blockers.append(
                _blocker(
                    DOCUMENT_NOT_PARSED,
                    "document",
                    doc.id,
                    f"Document '{doc.filename}' failed to parse. Retry it on the Upload page."
                    if doc.parse_status == "failed"
                    else f"Document '{doc.filename}' has not finished processing "
                    f"(status: {doc.parse_status}).",
                )
            )
        counts["documents_not_parsed"] = len(not_parsed)

        # ------------------------------------------------------------
        # 10. Documents no extraction has read (plan item 104). Each parsed
        # chunk records the extraction task that read it, set when that
        # task completed (migration 033); readiness never guesses from
        # times. Only parsed documents: one not parsed has blocker 9 alone.
        # Only the file's own (parsed) chunks: a synthetic chunk is never
        # extracted. A parsed document with no parsed chunks blocks too
        # (user decision, Q6): a scan whose OCR found nothing looks exactly
        # like an empty file. doc_type picks the wording only, never whether
        # a document blocks (Q4): a renamed amendment must not slip through.
        # ------------------------------------------------------------
        coverage = await uow.documents.extraction_coverage_for_tender(tender_id)
        not_extracted = 0
        for doc in documents:
            if doc.parse_status != "parsed":
                continue
            total, unread = coverage.get(doc.id, (0, 0))
            if total == 0:
                message = (
                    f"Document '{doc.filename}' has no readable text, so no requirements could "
                    "be extracted from it. Check the file; a scan may not have been read."
                )
            elif unread == 0:
                continue
            elif unread == total:
                message = (
                    f"Document '{doc.filename}' has not been read by requirement extraction. "
                    + _NEW_RUN_STEPS
                )
            else:
                message = (
                    f"Document '{doc.filename}' changed after the last requirement extraction "
                    f"({unread} of {total} parts not read). " + _NEW_RUN_STEPS
                )
            if total > 0 and doc.doc_type == "amendment":
                message += _AMENDMENT_STEP
            not_extracted += 1
            blockers.append(_blocker(DOCUMENT_NOT_EXTRACTED, "document", doc.id, message))
        counts["documents_not_extracted"] = not_extracted

        # ------------------------------------------------------------
        # 12. A knowledge change not applied (plan item 124, the user's
        # addition). A selection change's reanalysis re-evaluates the
        # compliance decisions and claims the item touches and looks for
        # graph contradictions; one that failed left them not reflecting
        # the change, with nothing on screen. Blocks while the item's latest
        # failed claim has no completed claim at a later state version: a
        # later change of the same item, completed, re-ran it. A claim
        # failing moves no state version, and needs none: the final gate's
        # approval recomputes readiness (plan item 93, W2), so a blocker
        # that appears after the last report reaches the gate.
        #
        # The message says the item's selection now and both steps of a
        # re-run in order (PR 40's review): "change it again" would leave a
        # person who deselected the item with it selected. The selection is
        # read as the Knowledge selections page reads it: selected only
        # where the bid's row says so, no row being not selected.
        # ------------------------------------------------------------
        not_applied = await uow.reanalysis_claims.items_not_applied(tender_id)
        not_applied_items = await uow.knowledge.get_knowledge_items(not_applied)
        selected_now = {
            s.knowledge_item_id
            for s in await uow.knowledge.list_selections_for_tender(tender_id, selected_only=True)
        }
        for knowledge_item_id in not_applied:
            known = not_applied_items.get(knowledge_item_id)
            blockers.append(
                _blocker(
                    REANALYSIS_NOT_APPLIED,
                    "knowledge_item",
                    knowledge_item_id,
                    _not_applied_message(
                        known.title if known is not None else None,
                        knowledge_item_id,
                        knowledge_item_id in selected_now,
                    ),
                )
            )

    # ------------------------------------------------------------
    # 7. Stale graph projection (reuses graph_service.check_freshness
    # directly - "when graph checks are required" reads as "whenever
    # project_graph sits in this run's fixed path," true unconditionally
    # in the current topology; see the B12 plan's Q5/design notes).
    # ------------------------------------------------------------
    # Plan item 85 (folded into 93): through check_freshness_with_retry, so a
    # TransientError (a read overlapping a projection) is retried instead of
    # becoming "could not be verified"; an outage still fails fast. Since
    # item 93 that blocker is not stored for good at this version either:
    # the next generate after Neo4j recovers stores a new report.
    try:
        await check_freshness_with_retry(tender_id)
    except GraphStaleError as stale:
        blockers.append(
            _blocker(STALE_GRAPH_PROJECTION, "tender", tender_id, "Graph projection is stale.")
        )
        await _log_stale_projection_blocker(stale)
    except Exception:
        # check_freshness reaches Neo4j - a driver error, connection
        # failure, or timeout is a different failure mode than a
        # genuinely-stale-but-reachable graph, and previously wasn't
        # caught at all: it would propagate out of this function, losing
        # every blocker already computed from Postgres (1-6, 8) along with
        # it, and readiness_generation_node would fail the whole run over
        # an outage in a store readiness only asks ONE narrow question of.
        # Fail closed instead - same posture as a genuinely stale graph
        # (this tender is not ready), distinct wording so a reader can tell
        # "confirmed stale" from "couldn't check." The neo4j driver
        # (graph_projection/driver.py) has explicit timeouts since B16:
        # connection_timeout=5 for the TCP connect and
        # connection_acquisition_timeout=10, which since driver 6 spans
        # getting a connection from the pool or opening a new one, so an
        # outage surfaces here in about 10 s (read in code, not measured).
        logger.exception("graph freshness check failed for tender %s", tender_id)
        blockers.append(
            _blocker(
                STALE_GRAPH_PROJECTION,
                "tender",
                tender_id,
                "Graph freshness could not be verified (the graph store was unreachable).",
            )
        )

    ready = not blockers

    async with UnitOfWork() as uow:
        # Re-read state_version right before persisting - the value
        # captured at the top of this function is now potentially minutes
        # old (Postgres reads, then a separate Neo4j round-trip, all
        # awaited in between). If something bumped it in the meantime,
        # what was just computed no longer describes the tender's CURRENT
        # state - returning None tells evaluate_readiness's own retry
        # wrapper to recompute fresh against the new version, rather than
        # this function silently persisting a report under a version
        # number that may already be behind reality.
        #
        # Plan item 120 (Q2 a): read under the tender's row lock (FOR NO KEY
        # UPDATE, the lock a state-version bump takes), held to the commit.
        # A second generate for this tender waits here, so its generation
        # comes after this one commits: one tender's generations follow
        # commit order, and "latest" is the last report committed. A bump in
        # flight finishes first, so the version compared is the committed
        # one. Only the version read, the upsert and its read-back run under
        # it; the reads above are outside. No caller holds this lock in
        # another session meanwhile (the route, the run's node and the
        # final gate's approval each call this with no session open).
        current_tender = await uow.tenders.get_for_update(tender_id)
        if current_tender is None:
            raise TenderNotFoundError(f"Tender {tender_id} not found")
        if current_tender.state_version != state_version:
            return None

        # Plan item 93: computed on every generate, and stored as one row
        # per distinct content at this version. The upsert reuses the row
        # that already has this content, or appends one, and either way
        # makes it the newest, so the current report always describes what
        # was just computed. Two concurrent generates of the same content
        # meet on uq_readiness_report_content and end on one row.
        report = await uow.readiness.upsert_report(
            tender_id,
            state_version,
            report_content_hash(ready, blockers, counts),
            counts,
            blockers,
            ready,
        )
        await uow.commit()
        return _result_from_report(report)


async def get_report(report_id: uuid.UUID) -> ReadinessResult | None:
    # final_gate_node's own read - the EXACT report readiness_generation_
    # node just persisted this run (state["readiness_report_id"]), not
    # possibly a newer one, though in the current single-threaded-per-run
    # topology those are always the same report anyway.
    async with UnitOfWork() as uow:
        report = await uow.readiness.get(report_id)
    return _result_from_report(report) if report is not None else None


async def get_latest_readiness(tender_id: uuid.UUID) -> ReadinessResult | None:
    # The current report: the newest generated or reused (plan item 93,
    # ordered by generation since plan item 120). Read by GET
    # /tenders/{id}/readiness (the Readiness page) and by the final gate's
    # approval in routes/gates.py, which reads it before its own generate
    # and refuses when the generate lands on a different report. None means
    # no report was ever generated for this tender.
    async with UnitOfWork() as uow:
        report = await uow.readiness.get_latest_for_tender(tender_id)
    return _result_from_report(report) if report is not None else None
