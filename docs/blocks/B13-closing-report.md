# B13 — Functional UI: Closing Report

This is the durable record of B13's own close. The version of this report
that existed before now lived only in chat — two of its numbers were
wrong, both caught by CodeRabbit's review of the block's diff and
confirmed by direct query against the real post-run database dump, not by
re-reading code. Both corrections are folded in below, not set aside as
an addendum, since the whole point of writing this down is that it stops
being re-derivable from memory.

## Part J's gate, and what ran end to end

Part J's own stop-gate for this block is a negative: *"do not proceed if
the system can only be demonstrated through direct database edits or
Swagger."* Met. A live Playwright run (`apps/web/e2e/full-flow.spec.ts`)
drove the full golden path through a real browser against the real
`web`/`api`/`worker` containers — no mocked network layer, no seeded
state via `UnitOfWork`, no Swagger calls anywhere in the run:

login → create bid → upload the synthetic RFP package → wait for real
extraction → approve requirements → approve compliance → select a
candidate → inspect a pricing finding → generate a real proposal →
inspect a real claim trace → run red team → resolve the seeded issues →
reach a real readiness result — plus the plan's own added 14th step
(knowledge-selection impact preview), including the `409 GRAPH_STALE` →
project → retry sequence firing organically on a tender that had never
been projected.

17 screens built, covering every route in Part G. All 71 routes across 15
modules carry route-level `TestClient` coverage in `tests/route/`,
closing the gap that let `set_knowledge_selection`'s dead route (a stale
6-argument call, `TypeError` on every call) survive twelve blocks
undetected — route-level coverage was uneven, not absent, and
`knowledge.py` specifically had none.

## The 5 readiness blockers from the live run — corrected classifications

The final E2E run reached a real `ready: false` result with five distinct
blocker categories firing. The table below replaces the version of this
table that shipped in the original closing report; blocker 6's label was
wrong there.

| # | `readiness_service.py` category | Classification | Note |
|---|---|---|---|
| 1 | `compliant_decision_without_valid_evidence` | Predicted | Instrumented in advance (§7 step 11a's own pre-flight named this exact seam) — fired on entries the run overrode without an evidence link, as expected. |
| 2 | `missing_mandatory_submission_item` | New finding | No screen in the 17 manages submission-item status; every mandatory item starts and stays at `missing`. Real, load-bearing product gap — see KNOWN_LIMITATIONS. |
| 3 | `role_with_no_selected_candidate` | **Harness artifact — corrected from "residual"** | Fired once, on Project Manager. **Not** a product-level finding: the E2E setup's own employee-discovery poll waited for `count() > 0` rather than the real expected count, so only the first of four imported employees was ever verified. Every role in step 7 was matched against a real but artificially truncated pool of exactly one candidate for the entire run — confirmed by query against the post-run dump (one `role_matches` row for Project Manager, one `employee_verify` audit event at 04:11:09, three seconds after the first employee's row was created and roughly ninety seconds before the other three existed). The system reported readiness correctly given the data it was handed; the data was wrong because of a test bug, not a product one. Fixed this pass (the poll now asserts the real count). |
| 4 | `stale_graph_projection` | Reasoning error | `readiness_service.py` worked exactly as designed. The plan's own §1 proved `project_graph` runs unconditionally before `readiness_generation` on the *automatic* workflow path, then that guarantee was silently applied to the E2E spec's *manual* `POST /readiness/generate` call, which has no such guarantee. Recorded as the reasoning error, not a bug. |
| 5 | `critical_redteam_finding` | Background-work race | 3 red-team findings were unresolved at step 13 despite step 12's own resolve-loop clicking every visible `Resolve` button. Diagnosis (read-only, against `agent_traces`/`redteam_findings`): the loop missed nothing on screen — B11's own bounded revision loop kept running automatically in the background after the test moved on and created new findings later. The real finding: every screen in this UI is a snapshot: the system keeps running after a human looks away, and nothing tells them. Owner: B14 (SSE). |

## Bug counts by discovery method

Four methods were named going into this block as non-overlapping ways a
bug could surface; one produced no B13-specific finding, and roughly half
of what B13 found came from a fifth method (direct inspection while
building or planning) that doesn't fit any of the four. Presented as
measured, not forced into a clean total that overstates what any one
method actually caught.

| Method | Result |
|---|---|
| Route-level tests (`tests/route/`) | 5 bugs: `PATCH /tenders/{id}` 500ing (`updated_at` not repopulated after an ORM-tracked update — also confirmed in `PATCH /proposal/sections/{id}`, same fix); `queue_knowledge_document_import` callers omitting `request_id`, colliding on re-upload; the Neo4j driver singleton breaking under per-call event loops (test-infrastructure, not product); `POST /tenders/{id}/redteam/run` crashing on an unhandled `GraphStaleError`. |
| Live browser verification | 3 bugs: CORS never configured (the entire browser-facing surface was unverified by construction through B12); two distinct knowledge-document upload bugs found uploading a real file through the actual UI (crash and silent mis-ingestion). |
| E2E run (`full-flow.spec.ts`) | **0 crashes, 3 product-level findings** (submission-item gap, stale-graph reasoning conflation, UI-is-a-snapshot/background-work race — see the blocker table above), plus **3 harness bugs found in the spec itself**, not the product: premature navigation cancelling in-flight requests (twice), `page.reload()` not waiting for React's own data-fetching effects, and the employee-discovery poll's `count() > 0` truncation. The "0 crashes" framing matters on its own: it is not "E2E found nothing" — it means the product survived a full real run intact while the harness driving it did not, three separate times, each traced to hard evidence (network logs, timestamps, DOM state) before being called a harness bug rather than a product one. |
| Worker-through-real-queue | No B13-specific finding attributable to this method alone. The block's one worker-touching bug (the upload crash/mis-ingestion above) surfaced through the browser check that triggered it, not a queue-specific test. |
| Direct inspection (reading code or spec, before or instead of running anything) | The remaining ~13 entries: the RBAC pattern's fourth instance (`list_gates`/`get_gate` gated on `create_bid`, found while typing `GateResponse`, not auditing RBAC); typed gate/readiness schemas (a design decision, pinned against real persisted output first); the `resource_selection` gate-targets/`target_updates` asymmetry (accepted, not fixed); `settings.llm_provider` dead configuration; Part G/code drift (three instances: the never-built `/impact-preview` route, no single-employee `GET`, no route exposing a section's claim ids); `make test`'s incomplete host-DSN override; the missing Idempotency-Key on the knowledge-selection `PUT`; and the test-database guard existing in two files with no single source of truth. |

## KNOWN_LIMITATIONS entries added this block — corrected count

**26, not 21.** The 21 figure was stated at some point before this
block's own final wrap-up step and never re-verified afterward — that
step alone added 5 more (the submission-item gap, the stale-graph
reasoning conflation, both `page.reload()`/premature-navigation harness
bugs, and the corrected background-work-race entry). Verified here by a
direct count of every `## [B13...]` heading in KNOWN_LIMITATIONS.md
(excluding `[B13-prep]`, which predates this block), not by recalling a
number from an earlier report — the same discipline this document exists
to hold going forward. A 27th entry (the employee-discovery-poll harness
bug) was added during the CodeRabbit-fix pass that produced this
document's own corrections; it is listed separately at the end since it
belongs to that pass, not B13's original close.

1. The test-database guard now exists in two files, with no single source of truth
2. CORS was never configured
3. `updated_at` not repopulated after an ORM-tracked update (`PATCH /tenders/{id}` 500s)
4. mypy baseline record (see the corrected numbers below)
5. `GateResponse`/`ReadinessReportResponse` untyped dicts — resolved (typed as a discriminated union)
6. `list_gates`/`get_gate` gated on `create_bid` — 4th instance of the read-action-on-authoring-action pattern
7. `resource_selection`'s gate targets vs. `target_updates` — accepted asymmetry
8. `target_updates` batch approval — revived, working, never called — a decision
9. Why `requirement`/`redteam_finding` aren't in `_KNOWN_TARGET_TYPES` — inference, not documented decision
10. `make test`'s host-DSN override only patches Postgres
11. `queue_knowledge_document_import` callers omitting `request_id` — resolved, all four sites
12. Part G's G6 list never named a single-employee `GET` — resolved
13. Every knowledge-document upload crashed or silently mis-ingested — two bugs, live-browser-found
14. The Neo4j driver singleton breaks under `tests/route/`'s per-call event loops
15. Missing Idempotency-Key on the knowledge-selection `PUT` — resolved
16. No route ever exposed a proposal section's claim ids — resolved
17. `PATCH /proposal/sections/{id}` `DetachedInstanceError` — predicted, then confirmed
18. Traceability drawer verified as an instrument — not yet pointed at real citation data
19. `POST /tenders/{id}/redteam/run` crashed on unhandled `GraphStaleError` — resolved
20. §1's own dependency analysis missed that step 7 needs a pre-seeded company-knowledge item
21. `settings.llm_provider` is dead configuration
22. No screen manages submission-item status — final readiness unreachable by construction
23. §1's own stale-graph reasoning was correct for the automatic path, wrongly applied to the manual one
24. E2E harness bug: premature navigation cancelled in-flight requests, twice
25. E2E harness bug: `page.reload()` does not wait for React's data-fetching effects
26. Every screen in this UI is a snapshot — background-work-race, owner B14

*Added afterward, during the CodeRabbit-fix pass:*

27. E2E harness bug: waiting for "at least one" instead of the real expected count truncated the candidate pool

## Final numbers

- Backend: **447 tests passing**, 0 failed.
- mypy: **59 errors, 12 files, 234 source files** via `uv run mypy` (bare —
  CLAUDE.md's own documented command; scoped by `pyproject.toml`'s
  `[tool.mypy] files = [...]` to `apps/api/src`, `apps/worker/src`,
  `packages/tenderos_backend/src`). This is the canonical baseline.
  `uv run mypy .` (the literal `.` argument overrides `files` and scans
  the whole repo, including `tests/` and `scripts/`) reports **63 errors,
  14 files, 310 source files** — a real, different, also-stable number
  for a wider scope, not a second baseline in conflict with the first.
  Six consecutive B13 step reports called the 59-to-63 gap "unexplained"
  because a step-5 invocation switch from bare `mypy` to `mypy .` went
  unnoticed; both counts were correct the whole time, for different
  questions.
- Frontend: 17/17 screens built, `tsc -b` clean, `eslint .` clean, Vitest
  16/16 real component tests passing with no failed suites.
- 71/71 routes across 15 modules carry route-level `TestClient` coverage.

## What B13 did not close

**The citation-correctness gap, first.** The traceability drawer is
verified as a correct *instrument* — both render branches (cited
evidence, and "currently in scope but not cited") confirmed against real
and hand-inserted data — but it has never been pointed at a real,
organically-drafted claim with real available-and-uncited evidence. The
original B9/B12 question — was evidence thin, or did the model have it
and not cite it? — remains unsettled. This is the block's own largest
open quality question, not a side note.

Also open:
- No owner for the submission-item surface. Checked against both the PRD
  and the Build Guide before leaving this unassigned, per CLAUDE.md's own
  conflict rule — both documents are silent on a human marking a
  submission item received, not in conflict with each other.
- `settings.llm_provider` is dead configuration: 8 call sites construct
  `GeminiClient` directly, so no future block can rehearse a full E2E run
  for free until someone wires the provider-neutral interface it was
  built to select.
- `make test`'s host-DSN override was incomplete (fixed in the
  CodeRabbit-fix pass, not B13's own original close) — Redis/Neo4j/S3
  still pointed at Docker-internal hostnames unreachable from the host.
- The test-database guard is duplicated across two `conftest.py` files
  with no single source of truth.
- The Neo4j driver singleton's `TestClient` event-loop trap is accepted
  as test-infrastructure, not fixed at the source.

## Provenance

Original close: B13 build, §7 steps 1–11b (this repository's own commit
`7f38611`, "B13: functional UI"). This document: written after a
follow-up CodeRabbit review of that same commit's diff surfaced two
errors in the closing report as it existed only in chat at the time —
blocker 6's classification, and the mypy baseline's "unexplained" delta
— both corrected here from direct evidence (a database query against the
run's own post-run `pg_dump`, and a side-by-side reproduction of both
mypy invocations), not from re-reasoning about the original claims.
