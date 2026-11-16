import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

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


def _result_from_report(report: Any) -> ReadinessResult:
    return ReadinessResult(
        report_id=report.id,
        tender_id=report.tender_id,
        tender_state_version=report.tender_state_version,
        ready=report.ready,
        generated_at=report.generated_at,
        blockers=report.blockers,
        counts=report.counts,
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


async def evaluate_readiness(
    tender_id: uuid.UUID, degraded_stages: list[str] | None = None
) -> ReadinessResult:
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
        result = await _evaluate_readiness_once(tender_id, degraded_stages)
        if result is not None:
            return result
    raise RuntimeError(
        f"readiness evaluation for tender {tender_id} did not converge after "
        f"{_MAX_READINESS_ATTEMPTS} attempts - tender.state_version kept "
        "changing while blockers were being computed"
    )


async def _evaluate_readiness_once(
    tender_id: uuid.UUID, degraded_stages: list[str] | None = None
) -> ReadinessResult | None:
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
        # Plan item 88: a link's stored validity_status is as of the last
        # evaluation that cited it, and archiving its source rewrites
        # nothing. So the source's archived state is read here, now, for
        # every link at once (one query), the same check the proposal's
        # evidence filter makes. A document excerpt has no source item and
        # is unaffected. Every generate computes this afresh (plan item 93).
        archived_sources = await uow.knowledge.archived_item_ids(
            link.knowledge_item_id
            for links in evidence_by_entry.values()
            for link in links
            if link.knowledge_item_id is not None
        )
        awaiting_approval = 0
        without_valid_evidence = 0
        for entry in compliance_entries:
            if not entry.human_approved:
                awaiting_approval += 1
            if entry.status not in _COMPLIANT_STATUSES:
                continue
            evidence = evidence_by_entry.get(entry.id, [])
            has_valid = any(
                e.verified
                and e.validity_status == "valid"
                and e.knowledge_item_id not in archived_sources
                for e in evidence
            )
            if not has_valid:
                without_valid_evidence += 1
                blockers.append(
                    _blocker(
                        COMPLIANT_DECISION_WITHOUT_VALID_EVIDENCE,
                        "compliance_entry",
                        entry.id,
                        f"Compliance entry {entry.id} is marked {entry.status} "
                        "with no verified, currently-valid evidence.",
                    )
                )
        counts["compliance_decisions_awaiting_approval"] = awaiting_approval
        counts["requirements_lacking_valid_evidence"] = without_valid_evidence

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
    except GraphStaleError:
        blockers.append(
            _blocker(STALE_GRAPH_PROJECTION, "tender", tender_id, "Graph projection is stale.")
        )
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
        # (graph_projection/driver.py) applies its own default timeouts
        # (connection_timeout/connection_acquisition_timeout, no explicit
        # override in this codebase) - this does not hang forever, but a
        # real outage can still take close to a minute to surface.
        logger.exception("graph freshness check failed for tender %s", tender_id)
        blockers.append(
            _blocker(
                STALE_GRAPH_PROJECTION,
                "tender",
                tender_id,
                "Graph freshness could not be verified (the graph store was unreachable).",
            )
        )

    # ------------------------------------------------------------
    # 8. Red-team review degraded (this block's own addition, Q5).
    # degraded_stages is LangGraph checkpoint state, not a persisted
    # column (final_gate_node's own docstring: it's only ever written into
    # approval_gates.request_payload, per-run) - readiness_generation_node
    # passes state["degraded_stages"] through directly rather than this
    # function querying a workflow_runs column that doesn't exist. A
    # standalone call (e.g. from the manual POST /readiness/generate route,
    # outside any live graph run) has no state to pass and gets None -
    # correctly treated as "nothing known to be degraded," not "not stale."
    # ------------------------------------------------------------
    # Entries are "{stage}:{ExceptionType}" (red_team_node/proposal_
    # drafting_node/project_graph_node all format degraded_stages the same
    # way) - a bare `"red_team" in degraded_stages` checks LIST membership
    # of the exact string "red_team", which never matches an entry that
    # always carries an exception-type suffix. Caught by this file's own
    # test (test_red_team_degraded_stage_blocks failed against exactly
    # this before the prefix check was added) rather than left silently
    # broken - the blocker this was meant to add would never have fired.
    if degraded_stages and any(stage.startswith("red_team:") for stage in degraded_stages):
        blockers.append(
            _blocker(
                RED_TEAM_REVIEW_INCOMPLETE,
                "tender",
                tender_id,
                "The most recent red-team pass failed and its findings may be stale.",
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
        current_tender = await uow.tenders.get(tender_id)
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
    # ordered by last_generated_at, ties by id). Read by GET
    # /tenders/{id}/readiness (the Readiness page) and by the final gate's
    # approval in routes/gates.py, which reads it before its own generate
    # and refuses when the generate lands on a different report. None means
    # no report was ever generated for this tender.
    async with UnitOfWork() as uow:
        report = await uow.readiness.get_latest_for_tender(tender_id)
    return _result_from_report(report) if report is not None else None
