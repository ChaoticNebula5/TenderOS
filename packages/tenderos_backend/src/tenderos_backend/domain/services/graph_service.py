import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from tenderos_backend.core.config import settings
from tenderos_backend.db.models.graph_projection import GraphProjectionRun
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.graph_projection import queries
from tenderos_backend.graph_projection.driver import (
    SHARED_LABELS,
    TENDER_SCOPED_LABELS,
    Neo4jSession,
)
from tenderos_backend.graph_projection.driver import get_session as get_neo4j_session
from tenderos_backend.graph_projection.projector import project_tender as _write_projection

# get_graph_trace's node_label comes from the API caller (a query param on
# GET /graph/trace), unlike driver.py's own use of these same label tuples
# for constraint setup - that one only ever interpolates fixed internal
# constants. Here the caller-influenced value gets validated against this
# allowlist BEFORE it's allowed anywhere near the Cypher f-string; anything
# not in this set is rejected outright, not passed through.
_ALLOWED_TRACE_LABELS = frozenset(TENDER_SCOPED_LABELS) | frozenset(SHARED_LABELS)


class TenderNotFoundError(Exception):
    pass


class GraphStaleError(Exception):
    """Raised whenever a caller asks for graph-derived data and the latest
    successful projection is behind the tender's current state_version (the
    Build Guide's "Graph freshness invariant"). routes/graph.py catches this
    and returns 409 GRAPH_STALE (G12) - never silently returns a possibly-
    wrong impact/contradiction/trace result (the invariant's own wording).
    """

    def __init__(
        self,
        tender_id: uuid.UUID,
        current_state_version: int,
        projected_state_version: int | None,
        reason: str = "stale",
    ) -> None:
        self.tender_id = tender_id
        self.current_state_version = current_state_version
        self.projected_state_version = projected_state_version
        self.reason = reason
        if reason == "damaged":
            # Postgres's own state_version comparison passed - the two ARE
            # equal here - but major finding #8's fix (queries.
            # is_projection_complete) found the graph itself incomplete.
            # Same exception type as a real version mismatch (routes/
            # graph.py's own 409 GRAPH_STALE handling is correct for both:
            # neither case is safe to read from), distinct message so a
            # log reader isn't misled into thinking the versions disagreed.
            super().__init__(
                f"Graph for tender {tender_id} at state_version "
                f"{current_state_version} is damaged: the latest projection "
                f"run completed in Postgres but the graph itself is missing "
                f"its completion marker - a crash partway through "
                f"reprojection, not a stale version."
            )
        else:
            super().__init__(
                f"Graph for tender {tender_id} is stale: current state_version="
                f"{current_state_version}, last projected={projected_state_version}"
            )


async def project_tender(tender_id: uuid.UUID) -> GraphProjectionRun:
    """D3 steps 1-2 and 10-11. Three separate UnitOfWork scopes, each
    committed independently - same "commit as you go, not one giant
    transaction" shape draft_proposal_for_tender uses across its own
    per-section loop. Step 1's "read tender + state_version" (in the first
    scope) fixes the source_state_version this run claims to represent
    BEFORE any Neo4j write starts, which is what check_freshness later
    compares against. If the projector fails partway through, the run is
    marked 'failed' in its own committed scope (not left 'running' forever,
    and NOT treated as the latest successful run by
    get_latest_completed_run's own status filter) and the exception
    propagates - a partial write must never be mistaken for a fresh graph.
    """
    async with UnitOfWork() as uow:
        tender = await uow.tenders.get(tender_id)
        if tender is None:
            raise TenderNotFoundError(f"Tender {tender_id} not found")
        run = await uow.graph_projection.create_run(tender_id, tender.state_version)
        await uow.commit()

    try:
        async with UnitOfWork() as uow, get_neo4j_session() as session:
            node_count, relationship_count = await _write_projection(uow, session, tender_id)
    except Exception as exc:
        async with UnitOfWork() as uow:
            await uow.graph_projection.mark_failed(run.id, str(exc))
            await uow.commit()
        raise

    async with UnitOfWork() as uow:
        result = await uow.graph_projection.mark_completed(run.id, node_count, relationship_count)
        await uow.commit()
        return result


async def check_freshness(tender_id: uuid.UUID, session: Neo4jSession) -> GraphProjectionRun:
    """Every read-path function below calls this first. Returns the latest
    completed run on success (callers that already need it, e.g.
    get_graph_status, avoid a second lookup); raises GraphStaleError
    otherwise - including when no successful projection has ever run.

    Major finding #8's fix: a matching Postgres state_version alone no
    longer means fresh - it only proves a projection was ATTEMPTED for
    this state_version, not that it finished (Neo4j auto-commits per
    statement; a crash partway through leaves real, permanent damage a
    Postgres-only check has no way to see). `session` is now required -
    every caller already opens one immediately after this call anyway
    (to run its own query), so this reuses that same session rather than
    opening a second one just for this check.
    """
    async with UnitOfWork() as uow:
        tender = await uow.tenders.get(tender_id)
        if tender is None:
            raise TenderNotFoundError(f"Tender {tender_id} not found")

        latest = await uow.graph_projection.get_latest_completed_run(tender_id)
        if latest is None or latest.source_state_version != tender.state_version:
            raise GraphStaleError(
                tender_id,
                tender.state_version,
                latest.source_state_version if latest else None,
            )

    if not await queries.is_projection_complete(session, str(tender_id), tender.state_version):
        raise GraphStaleError(
            tender_id, tender.state_version, latest.source_state_version, reason="damaged"
        )
    return latest


async def get_graph_status(tender_id: uuid.UUID) -> dict[str, Any]:
    async with UnitOfWork() as uow:
        tender = await uow.tenders.get(tender_id)
        if tender is None:
            raise TenderNotFoundError(f"Tender {tender_id} not found")
        latest = await uow.graph_projection.get_latest_completed_run(tender_id)
        version_matches = latest is not None and latest.source_state_version == tender.state_version

    # Same fix as check_freshness: a matching state_version alone isn't
    # enough, the graph itself might be damaged from a crashed
    # reprojection. This function's own contract (never raises, always
    # returns a status dict) is unchanged - it just folds the same
    # Neo4j-side check into "fresh" rather than trusting Postgres alone.
    fresh = False
    if version_matches:
        async with get_neo4j_session() as session:
            fresh = await queries.is_projection_complete(
                session, str(tender_id), tender.state_version
            )
    return {
        "fresh": fresh,
        "current_state_version": tender.state_version,
        "projected_state_version": latest.source_state_version if latest else None,
        "last_projected_at": latest.completed_at if latest else None,
    }


async def preview_impact(
    tender_id: uuid.UUID, knowledge_item_id: uuid.UUID
) -> list[dict[str, Any]]:
    async with get_neo4j_session() as session:
        await check_freshness(tender_id, session)
        return await queries.preview_impact(
            session, str(tender_id), str(knowledge_item_id), settings.graph_max_hops
        )


async def find_contradictions(tender_id: uuid.UUID) -> list[dict[str, Any]]:
    async with get_neo4j_session() as session:
        await check_freshness(tender_id, session)
        tid = str(tender_id)
        contradictions: list[dict[str, Any]] = []
        for finder, category in (
            (queries.find_expired_certification_contradictions, "expired_certification"),
            (queries.find_deselected_fact_still_used_contradictions, "deselected_fact_in_use"),
            (
                queries.find_removed_employee_still_committed_contradictions,
                "removed_employee_committed",
            ),
            (queries.find_unsupported_grounded_claim_contradictions, "unsupported_grounded_claim"),
        ):
            for row in await finder(session, tid):
                contradictions.append({"category": category} | row)
        return contradictions


async def get_graph_trace(tender_id: uuid.UUID, node_label: str, node_id: str) -> dict[str, Any]:
    """Not locked precisely by either doc (B10's build list just says "graph
    trace endpoint") - implemented as "show this node's immediate graph
    neighborhood" (the node itself plus every directly connected node/edge,
    both directions), matching PRD 27.12's "click any material decision or
    claim to inspect its origin" without inventing traversal semantics
    neither doc specifies. See KNOWN_LIMITATIONS.md.

    Two things a caller-facing lookup-by-label-and-id endpoint must never
    skip, both fixed after an automated security review caught the first
    draft missing them: (1) node_label is checked against
    _ALLOWED_TRACE_LABELS before it goes anywhere near the query text -
    otherwise a caller could pass an arbitrary label string straight into
    the Cypher; (2) the query itself enforces that the requested node
    actually belongs to tender_id, not just that node_id exists somewhere in
    the whole graph - without this, a caller with legitimate graph access to
    ANY tender could read another tenant's node by UUID alone, since raising
    403 earlier in the route only proves they can see the requested tender,
    not that the node they asked for is IN it.
    """
    if node_label not in _ALLOWED_TRACE_LABELS:
        raise ValueError(f"Unknown graph node label: {node_label!r}")

    tid = str(tender_id)
    # Tender-scoped labels carry their own tender_id property (set by the
    # projector); shared labels never do (D1) - they're only "in" this
    # tender at all via a USED_IN edge, so that's what has to be checked
    # instead. Also note the key property differs: tender-scoped nodes are
    # keyed by `id`, shared nodes by `knowledge_item_id` (same distinction
    # cypher/nodes.py's MERGE statements already draw).
    if node_label in TENDER_SCOPED_LABELS:
        match_clause = f"MATCH (n:{node_label} {{id: $nodeId, tender_id: $tenderId}})"
    else:
        match_clause = (
            f"MATCH (n:{node_label} {{knowledge_item_id: $nodeId}})"
            "-[:USED_IN]->(:Tender {id: $tenderId})"
        )

    async with get_neo4j_session() as session:
        await check_freshness(tender_id, session)
        result = await session.run(
            match_clause + " "
            "OPTIONAL MATCH (n)-[r]-(neighbor) "
            "RETURN n, labels(n) AS node_labels, collect({relationship: type(r), "
            "neighbor: neighbor, neighbor_labels: labels(neighbor)}) AS neighbors",
            nodeId=node_id,
            tenderId=tid,
        )
        record = await result.single()
        if record is None:
            return {"node": None, "node_labels": [], "neighbors": []}
        return record.data()


@dataclass(frozen=True)
class EvidenceCoverageResult:
    valid_path_count: int
    valid_evidence_ids: list[str] = field(default_factory=list)
    invalid_evidence_ids: list[str] = field(default_factory=list)
    recommended_action: str = "compliance_re_evaluation_required"


def evaluate_evidence_coverage(
    rows: list[dict[str, Any]], today: date | None = None
) -> EvidenceCoverageResult:
    """D7's evidence-coverage decision function. `rows` is exactly
    queries.evidence_coverage_rows's output for one compliance decision -
    one dict per attached evidence link, already fetched from Neo4j:
    {evidence_id, verified, validity_status, knowledge_item_status,
    valid_until, selected}. `knowledge_item_status`/`selected` come back
    None when the evidence is a bare document excerpt with no backing
    knowledge item (PROVIDES was never created for it in the projector -
    see cypher/relationships.py's own comment on this).

    Steps:
    1. For each row, decide whether it counts as a currently-valid support
       path: the evidence link itself must be verified=true AND
       validity_status='valid'. If the row DOES have a backing fact
       (knowledge_item_status is not None), that fact's status must also be
       'active', its USED_IN.selected must not be False (None counts as
       passing - a fact with no selection row at all was never deselected),
       AND its valid_until (if set at all) must not be in the past.
    2. Split evidence_id values into valid_evidence_ids vs
       invalid_evidence_ids based on step 1's per-row verdict.
    3. valid_path_count = len(valid_evidence_ids).
    4. recommended_action = 'review_optional' if valid_path_count > 0 else
       'compliance_re_evaluation_required' (D7's own rule, verbatim).
    5. Return an EvidenceCoverageResult with all four fields set.

    The edge case that makes this worth writing by hand rather than a
    one-line filter: a document-excerpt evidence row (no knowledge_item_id
    at all) must NOT be penalized for having no fact to check - treat a
    missing knowledge_item_status/selected as "no disqualifying fact
    condition applies," not as an automatic failure. Getting this backwards
    would make every document-excerpt-only compliance decision
    permanently un-coverable regardless of how solid the excerpt itself is.

    valid_until fix (B16): queries.evidence_coverage_rows already fetches
    fact.valid_until fresh on every call - this function simply never read
    it (KNOWN_LIMITATIONS.md's "a check doesn't actually verify what it
    claims to" standing pattern, shape B, instance 4). A certification
    whose valid_until has passed but whose knowledge_item_status hasn't
    separately been flipped to archived/expired was scored fact_is_valid=
    True regardless. `today` follows this codebase's own established
    pattern (compliance_service.prepare_evidence_candidates) rather than
    calling date.today() directly, so a test can pin it without mocking
    the clock. Neo4j's driver returns fact.valid_until as its own
    neo4j.time.Date, not a plain datetime.date - directly comparable
    against one (the driver's temporal types support this), same as
    find_expired_certification_contradictions already compares two Neo4j
    Date properties server-side; nothing here needs an explicit conversion.
    """
    today = today or date.today()

    valid_evidence_ids: list[str] = []
    invalid_evidence_ids: list[str] = []
    for row in rows:
        evidence_id = str(row["evidence_id"])
        evidence_is_valid = row["verified"] is True and row["validity_status"] == "valid"

        valid_until = row.get("valid_until")
        fact_not_expired = valid_until is None or valid_until >= today
        fact_is_valid = row["knowledge_item_status"] is None or (
            row["knowledge_item_status"] == "active"
            and row["selected"] is not False
            and fact_not_expired
        )

        path_is_valid = evidence_is_valid and fact_is_valid
        
        if path_is_valid:
            valid_evidence_ids.append(evidence_id)
        else:
            invalid_evidence_ids.append(evidence_id)
            
    valid_path_count = len(valid_evidence_ids)
    if valid_path_count > 0:
        recommended_action = "review_optional"
    else:
        recommended_action = "compliance_re_evaluation_required"
        
    return EvidenceCoverageResult(
        valid_path_count,
        valid_evidence_ids,
        invalid_evidence_ids,
        recommended_action=recommended_action,
    )


async def find_contradictions_for_redteam(tender_id: uuid.UUID) -> list[dict[str, Any]]:
    """B11's graph-contradiction check step (Red Team build item 4) - a thin
    wrapper around find_contradictions(), enriched with D7's own coverage
    decision for the two categories that name a specific compliance
    decision (expired_certification, deselected_fact_in_use). Kept here
    rather than in domain/services/redteam_checks.py so every Neo4j session
    this project opens stays inside graph_service.py, the same boundary
    B10 already established (redteam_checks.py never imports get_neo4j_session
    itself).

    For those two categories, "the fact went stale" doesn't by itself say
    how bad it is - a compliance decision can still have OTHER valid
    evidence backing it. evaluate_evidence_coverage's own recommended_action
    answers that: 'review_optional' (>=1 valid path remains) downgrades the
    finding to major; 'compliance_re_evaluation_required' (zero valid paths
    left) is critical - the compliant decision this tender is relying on may
    no longer be actually supported. removed_employee_committed and
    unsupported_grounded_claim don't name a single compliance decision to
    re-check coverage for, so they stay critical unconditionally - the
    contradiction itself already IS the disqualifying fact.

    The flagged evidence row itself is excluded from coverage_rows before
    evaluate_evidence_coverage runs - real bug found while testing this
    function against a seeded expired-cert case with no other evidence: D7's
    own evidence_coverage_rows trusts evidence_links.validity_status
    verbatim, which can be stale (set when the link was created, never
    revisited as the backing cert aged past its own valid_until - that
    drift is exactly what the expired_certification query just freshly
    detected by comparing cert.valid_until against tender.submission_date
    directly). Without this exclusion, a compliant decision resting on
    nothing but one now-expired certificate could be scored "review_optional"
    just because its own stale evidence row still says validity_status=
    'valid'. Excluding it asks the right question: ignoring the piece of
    evidence that just got flagged, is there something else backing this
    decision?
    """
    contradictions = await find_contradictions(tender_id)
    async with get_neo4j_session() as session:
        for row in contradictions:
            category = row["category"]
            if category not in ("expired_certification", "deselected_fact_in_use"):
                row["severity"] = "critical"
                continue
            compliance_node = row.get("compliance") or (
                row.get("target") if row.get("via") == "SUPPORTS" else None
            )
            if compliance_node is None:
                row["severity"] = "critical"
                continue
            flagged_evidence_id = row["evidence"]["id"]
            coverage_rows = [
                r
                for r in await queries.evidence_coverage_rows(session, str(compliance_node["id"]))
                if r["evidence_id"] != flagged_evidence_id
            ]
            coverage = evaluate_evidence_coverage(coverage_rows)
            row["coverage"] = {
                "valid_path_count": coverage.valid_path_count,
                "recommended_action": coverage.recommended_action,
            }
            row["severity"] = (
                "critical"
                if coverage.recommended_action == "compliance_re_evaluation_required"
                else "major"
            )
    return contradictions
