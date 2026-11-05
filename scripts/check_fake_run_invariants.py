"""B17 Phase 0: post-run invariants for a fake-provider run of the golden path.

Why this exists: the fake-client harness has been wrong seven times (see
KNOWN_LIMITATIONS.md's fake-client entry). Each time, a Playwright step went
red late in a long run, and only reading the database afterwards showed the
fake responses had never produced the data that step needed. This script
reads that data directly, so a harness gap shows up as a named, failed
invariant instead of as a timeout at step 14.

It checks the six things the golden path's later steps structurally
depend on, for one tender (the sixth is listed at the end):

  1. at least one requirement was extracted;
  1b. at least one role was extracted (E2E step 7 selects candidates per
      role; roles only come from staffing-category requirements);
  2. an affirmed compliance decision (fully_ or partially_compliant - the
     same set as proposal_service._AFFIRMATIVE_COMPLIANCE_STATUSES) has at
     least one compliance_evidence row behind it;
  3. at least one proposal claim is grounding_status = 'grounded';
  4. Neo4j holds a DIRECTED path from an Employee to one of this tender's
     Requirements (D2's edges all point downstream, e.g. Employee
     -PROVIDES-> Evidence -SUPPORTS-> Compliance -DECIDES-> Requirement) -
     what E2E step 14's impact-preview assertion needs.
  5. (harness error 7) at least one employee whose impact preview, through
     graph_service.preview_impact, includes a Compliance node - the
     evidence-bearing employee step 14 deselects.

Pre-flight use: run it against the most recent fake tender before
launching Playwright - it answers in seconds, where a missing data shape
would otherwise surface minutes into a Playwright run. Then run it again
against the new run's own tender.

Read-only: SELECTs and MATCH queries, nothing written. It proves the harness
produced the data shape, never that any content is correct - a fake run
proves reachability, not quality.

Usage, from the repo root, against the running stack's published ports:

    POSTGRES_DSN=postgresql+asyncpg://tenderos:changeme@127.0.0.1:5433/tenderos \
    NEO4J_URI=bolt://127.0.0.1:7687 REDIS_URL=redis://127.0.0.1:6379/0 \
    S3_ENDPOINT_URL=http://127.0.0.1:9000 \
    uv run python scripts/check_fake_run_invariants.py [TENDER_ID]

With no TENDER_ID, checks the most recently created tender. Exits 1 if any
invariant fails.
"""

import asyncio
import sys
import uuid

from sqlalchemy import text
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import graph_service
from tenderos_backend.graph_projection.driver import get_session as get_neo4j_session

_SQL_CHECKS = {
    "requirements": (
        "select count(*) from requirements where tender_id = :t",
        "at least one requirement extracted",
    ),
    # Added after the sixth harness error: roles are extracted only from
    # staffing-category requirements, and a fake run with none silently
    # skipped E2E step 7's whole candidate-selection loop.
    "roles": (
        "select count(*) from role_requirements where tender_id = :t",
        "at least one role extracted (needs a staffing requirement)",
    ),
    "affirmed_with_evidence": (
        "select count(distinct ce.id) from compliance_entries ce "
        "join requirements r on r.id = ce.requirement_id "
        "join compliance_evidence cev on cev.compliance_entry_id = ce.id "
        "where r.tender_id = :t "
        "and ce.status in ('fully_compliant', 'partially_compliant')",
        "an affirmed compliance decision with non-empty evidence",
    ),
    "grounded_claims": (
        "select count(*) from proposal_claims pc "
        "join proposal_sections ps on ps.id = pc.section_id "
        "where ps.tender_id = :t and pc.grounding_status = 'grounded'",
        "at least one grounded proposal claim",
    ),
}

_EMPLOYEE_PATH = (
    "MATCH (e:Employee)-[*1..4]->(r:Requirement {tender_id: $tenderId}) "
    "RETURN count(DISTINCT e) AS employees, count(DISTINCT r) AS requirements"
)


async def _latest_tender_id() -> uuid.UUID:
    async with UnitOfWork() as uow:
        latest = text("select id from tenders order by created_at desc limit 1")
        row = (await uow.session.execute(latest)).first()
    if row is None:
        sys.exit("no tenders in this database")
    return row[0]


async def check(tender_id: uuid.UUID) -> bool:
    print(f"tender {tender_id} (label: fake - this script is for fake-provider runs)")
    ok = True
    async with UnitOfWork() as uow:
        for name, (sql, meaning) in _SQL_CHECKS.items():
            count = (await uow.session.execute(text(sql), {"t": tender_id})).scalar_one()
            passed = count > 0
            ok &= passed
            print(f"  [{'PASS' if passed else 'FAIL'}] {name}: {count}  ({meaning})")

    async with get_neo4j_session() as session:
        record = await (await session.run(_EMPLOYEE_PATH, tenderId=str(tender_id))).single()
    employees, requirements = record["employees"], record["requirements"]
    passed = employees > 0
    ok &= passed
    print(
        f"  [{'PASS' if passed else 'FAIL'}] employee_to_requirement_path: "
        f"{employees} employee(s) -> {requirements} requirement(s)  "
        "(directed, within 4 hops, in Neo4j)"
    )
    ok &= await _evidence_bearing_employee(tender_id)
    print("ALL INVARIANTS PASS" if ok else "INVARIANTS FAILED")
    return ok


async def _evidence_bearing_employee(tender_id: uuid.UUID) -> bool:
    """Invariant 6 (added with harness error 7): at least one of the tender
    org's employees has an impact preview that includes a Compliance node -
    an evidence-bearing employee. E2E step 14 deselects exactly such an
    employee (Requirement AND Compliance labels) and then waits for a
    deselected_fact_in_use finding; a role-filler with no evidence reaches a
    Requirement too, but through FILLS -> Role, and correctly yields
    removed_employee_committed instead. Uses graph_service.preview_impact -
    the same call the impact-preview screen makes - so this answers the
    question step 14 asks, not an approximation of it.
    """
    async with UnitOfWork() as uow:
        rows = await uow.session.execute(
            text(
                "select ki.id from knowledge_items ki "
                "join tenders t on t.organization_id = ki.organization_id "
                "where t.id = :t and ki.item_type = 'employee'"
            ),
            {"t": tender_id},
        )
        employee_ids = [r[0] for r in rows]
    bearing = 0
    try:
        for kid in employee_ids:
            impact = await graph_service.preview_impact(tender_id, kid)
            if any("Compliance" in row["labels"] for row in impact):
                bearing += 1
    except graph_service.GraphStaleError:
        print(
            "  [FAIL] evidence_bearing_employee: graph is stale for this tender - "
            "project it first (scripts/project_all_tenders.py); not checked"
        )
        return False
    passed = bearing > 0
    print(
        f"  [{'PASS' if passed else 'FAIL'}] evidence_bearing_employee: "
        f"{bearing} of {len(employee_ids)} employee(s) show Compliance in their impact preview  "
        "(what E2E step 14 deselects)"
    )
    return passed


async def main() -> None:
    tender_id = uuid.UUID(sys.argv[1]) if len(sys.argv) > 1 else await _latest_tender_id()
    sys.exit(0 if await check(tender_id) else 1)


if __name__ == "__main__":
    asyncio.run(main())
