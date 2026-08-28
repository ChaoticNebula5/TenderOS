# Known Limitations

Append-only log of behaviors discovered during the build that aren't
contract violations or stop-gate failures, but are worth carrying forward
deliberately rather than losing to memory. Referenced by the Build Guide's
B16 release gate ("known limitations are documented honestly").

Each entry:

```
## [Bxx] Short title
- **Discovered:** which block, how (test/eval/smoke/review)
- **Behaviour:** what actually happens
- **Risk:** what could go wrong downstream, and in which block
- **Status:** unverified / accepted / mitigated / resolved-in-Bxx
- **Test:** does a known-answer case exist for it, and where
```

---

## [B5] Silent suppression of restated obligations

- **Discovered:** B5, during the mandatory-recall stop-gate investigation.
  A batch-size experiment (`MAX_EXTRACTION_BATCH_TOKENS` 1200 → 400) plus
  manual chunk/batch/template analysis of the false negatives.
- **Behaviour:** the extraction LLM (Gemini) appears to drop one of two
  near-identical restatements of the same obligation when both are visible
  to it, with no confidence signal, no dedup log entry, and no
  `agent_traces` note distinguishing "not present in source" from "present
  but suppressed as a duplicate." Two discriminators from the B5
  investigation rule out batch density as the cause: REQ-066 was missed
  even alone in a 3-item batch, and a template-identical pair (REQ-031 /
  REQ-058) was split across two separate LLM calls with zero co-location
  and both were still missed.
- **Risk:** harmless in the case actually observed — the ground-truth
  generator had mislabeled two identical-substance restatements (same
  subject/object, differing only in modal verb) as two distinct planted
  items, and fixing the generator (see `scripts/synthetic_tender_a.py`)
  resolved the recall gap. But real tenders restate obligations across the
  RFP body, annexures, and compliance checklists with materially
  different deadlines, scopes, or amounts. If the same suppression
  applies to genuinely divergent restatements, the stricter or
  authoritative version could be silently dropped with no indication
  anything was discarded — feeding B6 (compliance mapping) and B7
  (evidence linking) a decision built on the wrong requirement text.
- **Status:** verified for this specific seeded case, on this model, as of
  B7 (see "Scheduled verification" below) — 3/3 live Gemini extraction
  runs kept both REQ-073 and REQ-074 distinct with exact durations intact.
  This is a narrow result, not a general guarantee: see the scoping note
  below before reading it as "the suppression risk is closed."
- **Test:** REQ-073 (RFP body: audit logs retained a minimum of one year)
  / REQ-074 (annexure: audit logs retained a minimum of three years) are
  planted as a deliberate divergent-restatement pair — same obligation,
  materially different duration. See `tender_a_requirements.jsonl` and the
  `requirement_divergence` record in `tender_a_knowledge.jsonl`. B5's own
  eval doesn't assert anything special about this pair (plain token-match
  recall/precision only, same as every other requirement). A future block
  should check whether both are extracted and, ideally, whether the
  divergence itself is surfaced to a reviewer rather than one silently
  winning.
- **Scheduled verification — result (B7), code-path check:**
  `tests/integration/test_compliance_analysis_node.py::
  test_req073_074_divergent_restatements_get_independent_compliance_entries`
  runs both REQ-073/REQ-074 through the real `requirement_extraction_node`
  (not a synthetic direct-insert) and then the real `compliance_analysis_node`,
  against `FakeLLMClient` (scripted, deterministic responses), same as every
  other automated test in this repo. Two things confirmed, in order: (1)
  **precondition** — both requirements survive real extraction with their
  distinct durations ("one year" / "three years") intact, checked as its own
  assertion so this test fails loudly on that line, not silently, if
  extraction ever collapses them; (2) given the precondition holds, the
  compliance layer produces two distinct `compliance_entries` rows, and
  neither's `reasoning_summary` references the other's duration — no
  cross-contamination in the compliance layer itself. This confirms the
  *code path* is safe when both survive extraction; it says nothing about
  whether a *live* model actually lets both survive.

- **Scheduled verification — result (B7), live-model check:**
  `scripts/verify_req073_074_divergence.py` (one-off, not part of the
  pytest suite — deliberately excluded from the "every test runs against
  FakeLLMClient" rule, since its entire purpose is to exercise a real
  model) runs real `extract_requirements` against real Gemini
  (`EXTRACTOR_MODEL`), against fresh tenders built from the actual
  `rfp.pdf`/`technical_annexure.docx` fixture documents (the same two
  files REQ-073/REQ-074 are planted in), **3 times**, not once — recall is
  already known to be nondeterministic at temperature=0 (0.941-0.959
  across B5's own runs), so a single pass cannot distinguish "this case is
  safe" from "this run got lucky." Two infrastructure issues had to be
  ruled out first, both live-Docker-stack artifacts unrelated to the
  question being tested: (a) `GeminiClient`'s response cache is keyed
  purely on prompt content with no tender/run scoping
  (`agents/llm/gemini_adapter.py::_cache_key`), so all 3 runs would have
  silently replayed the first run's cached response instead of making
  independent live calls — worked around by giving each run its own Redis
  DB, not a fix to the cache itself (see the standalone entry below); (b)
  running the script while the real `worker`/`outbox_dispatcher`
  containers were also live caused both to independently process the same
  uploaded document via the async outbox path *at the same time* as the
  script's own direct `parse_and_embed_document` call, producing a
  `ForeignKeyViolationError` on `requirements.source_chunk_id` (a
  concurrent-reprocessing race, the same shape B6's own stale-RQ-backlog
  investigation found once already) — worked around by stopping those two
  containers before running the script, not a fix to the race itself.

  **Result: 3/3 runs kept both REQ-073 and REQ-074 present, each with its
  planted duration intact, byte-for-byte** ("Audit logs shall be retained
  for a minimum of one year." / "...three years.", 79 total requirements
  extracted each run). Full output preserved in this session's transcript.

  **Scope of what this does and doesn't establish:** this is a real,
  live-model result — not a simulation — for exactly the case seeded:
  two sentences, same template, differing only in one duration phrase,
  each alone in its own document. It is **not** a general guarantee that
  Gemini extraction never suppresses divergent restatements. Real tenders
  restate obligations far messier than this — different phrasing entirely,
  restated across three or more places, buried in dense paragraphs rather
  than isolated sentences, or divergent in ways subtler than a single
  number. B5's original filler-pair suppression (near-identical text) is
  still a confirmed, real phenomenon; this result only shows that the
  *specific* divergent-restatement shape B7 was asked to check does not
  currently trigger it. If a genuinely different divergence shape shows
  suppression in the future, that would be a new, separate finding, not a
  contradiction of this one.

## [B7] LLM response cache has no tender/run scoping

- **Discovered:** while running `scripts/verify_req073_074_divergence.py`
  (see the entry above) — 3 "independent" runs against byte-identical
  source documents would have silently collapsed into 1 real call plus 2
  cache replays, defeating the entire point of running 3 times.
- **Behaviour:** `agents/llm/gemini_adapter.py::_cache_key` hashes only
  `(model, schema_name, system_prompt, user_prompt)`. Two calls with
  identical prompt content hit the same Redis key regardless of which
  tender, organization, or run they came from. By the time this was found,
  186 cached responses already existed in this session's Redis instance
  from ordinary B5/B6/B7 live testing.
- **Risk:** low for the product itself — within one real tender, prompt
  content is naturally unique (real chunk text differs per document), so
  production traffic is unlikely to collide. The real risk is entirely on
  the *evaluation/verification* side: any future live-model script that
  re-runs the same fixture documents multiple times to check
  determinism (exactly what this entry's own verification script needed
  to do) will silently get cached replays instead of independent calls
  unless it explicitly works around this, the way
  `verify_req073_074_divergence.py` now does (a fresh Redis DB per run).
  A script that didn't know to work around this would report false
  "consistency" - N identical results from what looks like N live calls
  but is actually 1 live call plus N-1 cache hits.
- **Status:** unverified / accepted as a known gap, not fixed. The correct
  fix (if this becomes load-bearing beyond one-off verification scripts) is
  scoping the cache key to include a caller-supplied identifier - not
  attempted here since the workaround (isolated Redis DB per run) fully
  solved the immediate need without touching shared adapter code B5/B6/B7
  all already depend on working as-is.
- **Test:** none dedicated. `verify_req073_074_divergence.py`'s own
  per-run Redis DB isolation is the direct evidence this gap is real and
  exploitable, not a test asserting the cache's own key-collision
  behavior.
- **See also:** "Standing pattern: measurement/verification tooling is a
  recurring source of false signals" below — this is instance 3 of that
  pattern, and the one that would have produced the cleanest-looking false
  result of the three (a perfect 3/3 from what was actually one trial).

## [B5] REQ-018 missed in every run

- **Discovered:** B5. Present as a false negative in all three eval runs
  this block — the original run (batch=1200), the batch-size experiment
  (batch=400), and the fixed-generator re-run (batch=1200) — the only
  requirement to miss under every configuration tried.
- **Behaviour:** `[REQ-018] Additional case studies beyond the required
  minimum are welcome but will not affect the technical score.` is never
  extracted, regardless of batching or the generator fix.
- **Risk:** low for now — REQ-018 is optional, not mandatory, so it has
  never affected the stop-gate (0.959 mandatory recall, well above the
  0.90 floor). Its consistency across every configuration change is more
  likely a stable, sentence- or position-specific cause (it sits
  immediately after REQ-017 in the same source chunk) than run-to-run
  noise, but this hasn't been root-caused.
- **Status:** unverified / accepted. The gate passes with margin and the
  explicit call this block was not to chase the remaining misses further.
- **Test:** none dedicated — it's a standing item in the existing ground
  truth (`technical_annexure.docx`, optional, category `other`).

## [B5] Extraction is not fully deterministic at temperature=0

- **Discovered:** B5, comparing two back-to-back eval runs on the same
  fixed generator and the same `MAX_EXTRACTION_BATCH_TOKENS=1200`, the
  only difference being the second run added the REQ-073/074
  divergent-restatement pair (49 -> 51 mandatory ground-truth items).
- **Behaviour:** mandatory recall moved 0.959 (47/49) -> 0.941 (48/51)
  between the two runs, and the specific miss membership changed, not
  just the count: REQ-046 missed in run 1, correctly extracted in run 2;
  REQ-037 and REQ-038 correctly extracted in run 1, missed in run 2.
  REQ-039 missed in both. Both runs used temperature=0. This isn't a
  cache-hit artifact — batch composition changed between runs (new items
  added), so both were fresh, uncached LLM calls, not a replay of stored
  output.
- **Risk:** temperature=0 is often assumed to guarantee identical output
  for identical input; that assumption doesn't hold here, at least not at
  the batch level. Mandatory recall has landed in a 0.941-0.959 band
  across this block's fixed-generator runs, comfortably above the 0.90
  floor each time, but any single run that happened to land close to the
  boundary — passing or failing — should not be read as decisive on its
  own. A future block re-running this eval near the boundary could see
  either outcome without any code change.
- **Status:** unverified / accepted. Observed, not root-caused (plausibly
  provider-side non-determinism in Gemini's sampling, which is documented
  as a real phenomenon for some providers even at temperature=0). Not
  something this block will chase further.
- **Test:** none dedicated. The two fixed-generator runs in this block's
  history are the only evidence so far. A future block could add a
  repeated-run variance check if determinism becomes load-bearing
  somewhere (e.g. a CI gate that must be stable, not just passing once).

## [B6] Employee dedup matches on normalized name, not fuzzy similarity

- **Discovered:** B6, live test of `import_employee_master_row` against the
  seeded `EMP-01`/`EMP-05` duplicate fixture (`Dana Whitfield` /
  `Dana Whitfield (dup)`). The parenthetical suffix meant the two names
  weren't byte-identical, which surfaced the gap.
- **Behaviour:** `agents/knowledge/dedup.py`'s `normalize_employee_name`
  strips parenthetical content, accents, casing, and extra whitespace, then
  compares for exact equality. This correctly catches parenthetical/
  whitespace/case/accent variants of the same name (confirmed: `Dana
  Whitfield (dup)` now matches `Dana Whitfield` and routes to
  `needs_review`, pointing at the correct existing employee). It does
  **not** catch nicknames (`Dan` vs `Daniel`), typos, or transpositions —
  those require similarity scoring, not exact matching on a normalized
  form, and no such scoring is implemented.
- **Risk:** low and deliberate, not an oversight. Build Guide line 3069
  requires duplicate detection "using employee code, email, normalized
  name, and resume hash" — normalized-name matching is exactly what's
  implemented. Fuzzy/similarity matching (nicknames, typos) is not on the
  B6 build list and was never in scope; adding it later would mean a
  similarity threshold and likely the Postgres `pg_trgm` extension, both
  real design decisions, not a bug fix.
- **Deliberate non-normalization, worth preserving:** suffix/title tokens
  (`Jr`, `Sr`, `III`, `Dr`, `Mr`, `Mrs`, ...) are intentionally **not**
  stripped. `John Smith Jr.` and `John Smith Sr.` are frequently two real,
  different people at the same company (often related) — collapsing that
  distinction would silently merge two employees, which is a worse failure
  than missing a duplicate. If this is ever "improved" to strip suffixes
  for better recall, that trade-off needs to be made consciously, not
  rediscovered as a regression.
- **Status:** accepted — a deliberate scope boundary, not an open question.
- **Test:** `EMP-01`/`EMP-05` in the synthetic fixture is the standing case
  for what normalized-name matching *does* catch. No fixture exists yet for
  a nickname/typo near-duplicate, since that's explicitly out of scope.

## Standing pattern: measurement/verification tooling is a recurring source of false signals

- **Discovered:** first observed B5, confirmed as a genuine pattern (not a
  one-off) B6, confirmed a third time B7. Three independent instances now:
  1. **B5** — the ground-truth generator itself had a page/template-index
     coupling bug that mislabeled two identical-substance restatements as
     distinct planted items, producing an apparent recall gap that was
     actually a generator bug, not an extraction defect.
  2. **B6** — `evals/retrieval.py::evaluate_retrieval`'s ground-truth
     lookup had no `tender_id` filter, so it could silently score a stale
     row from a *different* eval run's tender instead of the current run's
     own extraction (see the entry immediately below for full detail —
     kept as its own entry since it's the specific bug/fix, this entry is
     the pattern the three instances together establish).
  3. **B7** — `scripts/verify_req073_074_divergence.py`, built specifically
     to check whether live Gemini extraction ever drops one of two
     divergent restatements, would have reported a clean "3/3 consistent"
     result from `GeminiClient`'s response cache (keyed on prompt content
     alone, no tender/run scoping — see the `[B7] LLM response cache has
     no tender/run scoping` entry) silently replaying run 1's single real
     call as runs 2 and 3. Caught only because the cache-hit path was
     inspected before trusting the number, not because anything failed
     loudly.
- **The heuristic this establishes:** when a verification number comes back
  looking suspiciously clean — no misses, no variance, "3/3 consistent,"
  a suspiciously round or expected figure — suspect the measurement
  tooling before concluding the pipeline is actually that good. A broken
  pipeline tends to fail loudly or obviously (wrong shape, exception,
  visibly wrong output). Broken *measurement* code reports a confident,
  plausible, clean number instead — which is a worse failure mode
  precisely because a clean result is the one result nobody double-checks.
  All three instances above share this exact shape: the tooling was wrong
  in a way that made the pipeline look *better* than it actually was (B5:
  hid a real gap behind a mislabeled ground-truth item; B6: could have
  hidden a real regression behind a stale cross-run score; B7: would have
  hidden the fact that only one real trial ever ran behind an apparent
  3-for-3).
- **Status:** accepted as a standing risk, not something any one fix
  closes out — this is a statement about how measurement code fails, not a
  single bug with a single patch. Concretely: any future block writing a
  new eval script, verification script, or repeated-trial check should
  budget time to inspect the tooling's own correctness (does it scope
  queries correctly, does it bypass caches when independence matters, does
  the ground-truth data actually assert what it claims to) with the same
  seriousness given to the pipeline it's measuring, *especially* when the
  first result looks good.
- **Test:** none dedicated to the pattern itself (it's a process note, not
  a code path). Each instance's own fix/workaround is tested or documented
  at its own entry.

## [B6] Eval/measurement code needs the same scrutiny as pipeline code

- **Discovered:** B6, `evals/retrieval.py`'s first real gate run. A fresh,
  correctly-built retrieval pipeline reported Recall@10 = 0.600 (FAIL) on a
  properly-scoped re-run, which looked like a pipeline defect at first —
  the actual cause turned out to be `RETRIEVAL_TOP_K` in `.env` (10, not
  the 20 the B6 plan proposed and had not yet been confirmed against a
  real run). Along the way, a second, independent bug surfaced in the same
  function: `evaluate_retrieval`'s lookup of a ground-truth requirement's
  text used `select(Requirement).where(...).limit(1)` with **no
  `tender_id` filter** — since the `requirements` table accumulates rows
  across every past eval run in this database (each `scripts/run_eval.py`/
  `run_retrieval_eval.py` invocation creates a fresh tender), this could
  silently pull a stale row from a *different* run's tender instead of the
  current run's own extraction. Fixed by requiring `tender_id` as a
  parameter and scoping the query to it.
- **Behaviour:** the data-leakage bug did not change the reported number in
  this instance (the affected requirement's text is a hand-authored,
  verbatim-preserved sentence, so it was byte-identical across every past
  run) — but it was a real defect that could silently score a different
  run's data under the current run's report, especially for anything where
  extraction *isn't* perfectly verbatim across runs (see the temperature=0
  non-determinism entry above, which shows extraction output does vary
  run-to-run for at least some items).
- **Risk:** this is the **second** time in this build that a wrong-looking
  number traced back to measurement code, not the pipeline it was
  measuring — B5's ground-truth generator confound (page/template index
  coupling) was the first. A broken pipeline tends to fail loudly or
  obviously; broken *measurement* code reports a confident, plausible,
  wrong number instead, which is a worse failure mode because nothing
  prompts a second look. Eval/harness code should get the same "verify by
  running it, don't just review it" treatment as pipeline code — it isn't
  exempt just because it's test infrastructure rather than product code.
- **Status:** resolved for the specific bug found (tender_id scoping now
  required, not optional, in `evaluate_retrieval`'s signature). The general
  pattern (measurement code as a distinct source of false signals) is
  recorded here as an accepted standing risk, not something one fix closes
  out.
- **Test:** none dedicated yet for the scoping bug specifically — the fix
  is a required parameter (a caller *must* pass `tender_id`, so the
  unscoped-query shape can't silently return), which closes the specific
  hole structurally rather than via a regression test. A future block
  could add an explicit test asserting `evaluate_retrieval` only ever
  reads `Requirement` rows for the given `tender_id` if this becomes
  load-bearing enough to warrant one.

## [B6] DEMONSTRATES never auto-verifies under its own corroboration signal

- **Discovered:** B6, live test of `extract_demonstrates` +
  `resolve_demonstrates_verification` against all 4 real past-project
  records in the synthetic fixture. Every LLM-proposed edge came back
  `needs_review`, including ones at LLM confidence 1.00.
- **Behaviour:** `resolve_demonstrates_verification` reuses
  `WORKED_ON`'s exact decision matrix (`VERIFIED` requires both a
  confident LLM *and* a corroborating deterministic signal), but its
  corroboration check — do the capability's own meaningful words appear
  in the project's description text — never fires in practice. Confirmed
  directly: "Citizen Services Portal"'s description ("Built and operated
  a public-facing citizen services portal...") shares zero tokens with
  "Public-Sector Delivery," even though the LLM's link is a reasonable
  one.
- **Why this is structural, not a mis-set threshold:** `WORKED_ON`'s
  corroboration works because "did this person work on this project"
  leaves a lexical trace — the same project gets named, however loosely,
  in both the resume and the canonical record. `DEMONSTRATES` asks
  whether a project demonstrates a *capability*, and a capability is an
  abstraction over the description, not a paraphrase of it — "migrated
  40TB from Oracle to PostgreSQL" demonstrates "database migration" with
  zero token overlap by construction, because the whole point of the
  capability label is to name a category the concrete text never states
  directly. No overlap threshold, loose or strict, changes this: loosening
  it would make the signal fire on word coincidences unrelated to whether
  the inference was correct, producing edges that auto-verify for reasons
  disconnected from their truth — the same failure shape as B5's
  character-count-as-token-count proxy (a cheap heuristic standing in for
  the real property, with an invisible failure mode). An honest "cannot
  corroborate this" is more useful than a signal that looks like
  corroboration but isn't.
- **Practical cost:** every `DEMONSTRATES` edge this policy proposes
  currently routes to human review, regardless of LLM confidence — there
  is no auto-verify path for this relationship type as built. If real
  usage volume makes 100% manual review of project×capability pairs
  unworkable, the identified fix is a *better* signal, not a looser one:
  embedding cosine similarity between the capability's name/description
  and the project's description would actually capture the semantic
  relationship token overlap structurally cannot. Not implemented — noted
  as the known path forward, not a task in progress.
- **Status:** accepted — a deliberate conservative default (100% review
  for an inherently abstractive relationship is defensible, not an
  oversight), not unverified.
- **Test:** the 4 real project/capability pairs from this session's live
  run are the standing evidence for the current behaviour. No fixture
  exists yet asserting the embedding-similarity alternative, since it
  isn't built.

## [B6, re-confirmed B9] knowledge_import_batches.status never advances past 'queued'

- **Discovered:** B6's own Docker smoke test, run end to end against the
  live API/worker/outbox_dispatcher stack. Every upload's batch showed
  `status: "queued"` via `GET /knowledge/import-batches/{id}` even after
  `process_knowledge_document_import` had fully completed and every item
  in the batch showed `approved`/`needs_review`. **Re-confirmed
  independently in B9** (this session did not check this file for an
  existing entry before re-investigating from scratch, which it should
  have - flagging that process gap plainly rather than quietly merging
  it away) while the citation-correctness measurement script's own
  polling loop hung indefinitely against a real employee-master upload.
  The B9 pass added: a full-repo grep confirming zero writes to
  `total_items`/`completed_items`/`failed_items`/`status` anywhere
  outside the migration and the ORM model's own defaults (not just
  `knowledge_import.py` - `knowledge_service.py` and its repository too),
  and a live reproduction via direct Postgres query (`status='queued'`,
  `total_items=0`, all zero, on a batch whose own
  `knowledge_import_items` rows already numbered 6, all at a terminal
  status).
- **Behaviour:** the worker job (`apps/worker/jobs/knowledge_import.py`)
  updates individual `knowledge_import_items` rows as it processes each
  row/extraction, but never writes back to the parent
  `knowledge_import_batches.status`/`total_items`/`completed_items`/
  `failed_items` fields at all - there is no code path anywhere in the
  service, repository, or worker layer that ever sets them to anything
  but their creation-time defaults. The import itself is not broken -
  every employee, certification, and resume in both B6's and B9's live
  runs persisted correctly - only the batch's own aggregate
  progress-reporting columns are dead.
- **Risk:** `GET /organizations/{org_id}/knowledge/import-batches/{id}`'s
  own docstring says explicitly "re-poll ... for progress" - any caller
  that follows that instruction (a UI progress bar, a future automated
  script, this session's own first two B9 eval attempts before the
  workaround) hangs or spins forever reading `queued`/`0 items` no matter
  how long the import has actually finished. `GET .../items` is
  unaffected and gives the real, current state - callers that poll that
  instead never hit this. Originally scoped "low risk" in B6 on the
  assumption real callers would poll items directly; B9's own live
  measurement script did exactly that assumption's opposite (polled the
  batch, per its documented contract) and hung for real, so the risk is
  more concrete than the original B6 entry scoped it.
- **Status:** resolved in the B0-B11 catch-up review (pre-B12), via the
  second of the two fix options this entry already named:
  `knowledge_service.get_import_batch`/`list_import_batches` now compute
  `status`/`total_items`/`completed_items`/`failed_items` live from
  `knowledge_import_items` on every read (`_compute_batch_progress`)
  instead of returning the batch row's own dead columns. Not fixed by
  adding batch-column writes to `process_knowledge_document_import` (the
  other option this entry named) — that would need a write at every one
  of that job's four import-type branches (employee-master rows,
  certification rows, capability statement, resumes) to stay correct, a
  second copy of the same fact with its own chance to drift. See
  "Standing pattern: a reader trusts a column its writer never keeps
  current" below — grouped with the `tenders.state_version` catch-up fix
  above as the same shape, fixed the same way (recompute from source of
  truth at read time, matching D7's `evaluate_evidence_coverage`
  precedent) rather than chasing every writer that would need to keep a
  denormalized copy current.
- **Test:** none dedicated yet — the fix removes the dead columns from the
  read path entirely rather than making them correct, so the previous
  "assert the batch's own status reflects reality" regression test this
  entry called for is no longer the right shape; a future test should
  instead assert `_compute_batch_progress` against a batch with a mix of
  pending/approved/failed items.

## [B7/B8] Post-join gate rejection kills the whole run, including unrelated completed branches

- **Discovered:** B7 planning (flagged before implementation, not found via
  testing) — reviewing how `compliance_gate_node` should fit into
  `orchestration/graph.py`'s existing fan-out (`specialist_dispatch` sends
  `compliance_analysis`, `talent_analysis`, `commercial_analysis` in
  parallel, joining at `specialist_join`). Revisited at B8 planning as
  explicitly scheduled, once talent/commercial stopped being stubs.
- **Topology note (found during B7 implementation, not part of this
  limitation itself):** B7's original design nested `compliance_gate_node`
  *inside* `compliance_analysis`'s own Send()'d branch, between it and
  `specialist_join`. That was a real bug, not just a design question — an
  `interrupt()`-bearing node upstream of a Send() fan-in join replays its
  entire function (including re-delivering a fresh trigger into the join)
  every time a *later*, unrelated interrupt elsewhere in the same
  continuation gets resumed, which left the whole workflow permanently
  stuck after approving `final_readiness` (caught via a live step-by-step
  debug run, not by an assertion alone). Fixed by moving
  `compliance_gate_node` to *after* `specialist_join`, the same safe
  sequential slot `final_gate_node` already occupies. B8's
  `resource_selection_gate_node` and `commercial_approval_gate_node` were
  built directly in that same sequential slot from the start, precisely to
  avoid re-discovering this bug a second and third time.
- **Behaviour:** all three post-join gates — `compliance_gate_node`,
  `resource_selection_gate_node`, `commercial_approval_gate_node` — mirror
  `requirement_gate_node`'s only precedent in this codebase for a rejected
  gate: on `decision == "reject"`, the node marks the whole `workflow_run`
  `"rejected"` (coarse status `"failed"`) and returns. For
  `requirement_gate_node` this is a clean fit — it gates entry into the
  specialist branches, so nothing has run yet. For all three post-join
  gates it's structurally different: by the time any of them can reject,
  every specialist branch (`compliance_analysis`, `talent_analysis`,
  `commercial_analysis`) has *already run to completion* — their
  `WorkflowTask` rows already show `completed`, and as of B8 that
  completion represents real work (compliance decisions, role matches,
  pricing findings), not stub output. A rejection at any of the three
  gates doesn't stop the others from having run — it aborts the whole run
  after the fact, so whatever the other branches produced is real,
  persisted, completed work the run then never proceeds to use.
- **Risk:** real as of B8, not hypothetical. Rejecting `resource_selection`
  because a candidate match looks wrong now also strands a genuinely
  correct, already-approved `commercial_approval` decision for the same
  run (or vice versa) — the reviewer's objection to one branch has nothing
  to do with the other two, but all three are discarded together at the
  run level.
- **What is and isn't actually lost:** nothing is deleted. Every
  `compliance_entries` / `role_matches` / `pricing_findings` row a
  rejected run produced remains in Postgres, correctly attributed to its
  requirement/role/pricing document, fully queryable and auditable outside
  the workflow-run's own state machine. What's lost is only that specific
  run's ability to keep moving forward toward `completed` — a workflow
  progress loss, not a data loss.
- **Status:** accepted, not "for-now." Considered and rejected the
  alternative (building partial-branch rejection now) during B8 planning:
  that needs a replanning executor plus a finding-category →
  corrective-task mapping, which the Build Guide explicitly scopes to
  **B11** ("supervisor mapping from finding category to corrective task,"
  "bounded revision loop," B2.4's "Proposal ↔ Red-Team loop"). Building it
  in B8 would jump the build order for a mechanism that already has a
  named future home. Real fix: **B11's bounded revision loop.** Not
  revisited again before then.
- **Test:** `tests/integration/test_orchestration_nodes.py::
  test_compliance_gate_node_rejection_routes_to_rejected` (B7) plus its
  B8A/B8B analogs for `resource_selection_gate_node` and
  `commercial_approval_gate_node` cover the current (accepted) behavior at
  each individual gate; `test_graph_execution.py::
  test_full_smoke_path_start_to_complete` exercises the full corrected
  five-gate topology end to end. No test exists for the
  stranded-sibling-output scenario itself (e.g. reject resource_selection,
  assert commercial's already-approved data survives in Postgres) — worth
  adding when B11 is being built, as the "before" half of that fix's own
  regression test.
- **B11 update:** the mechanism this entry named as the real fix now
  exists — `targeted_replan_node`/`redteam_service.map_finding_to_
  corrective_task` (finding-category → corrective-task mapping) and the
  bounded revision loop (`route_after_red_team`/`route_after_targeted_
  replan`, `revision_count`/`max_revisions`). **This limitation stays
  open, deliberately, not because the mechanism is missing but because
  B11's own build list scopes it to red-team findings only** —
  `compliance_gate_node`/`resource_selection_gate_node`/
  `commercial_approval_gate_node`'s own reject paths are a separate call
  site B11 doesn't touch, and rewiring them to route through
  `targeted_replan` instead of killing the run is additional scope not
  named in B11's build list or stop-gate. No later block in Part J
  revisits those three gates specifically, so this is left explicitly
  open rather than assigned a block name it doesn't actually have -
  revisit if/when those three gates' reject behavior is deliberately
  changed, not before.

## [B7/B8] tenders.state_version was never bumped, despite the locked invariant requiring it since B7

- **Discovered:** B8 planning — while proactively checking whether
  `_resolve_organization_id` needed new branches for B8A/B8B's routes
  (KNOWN_LIMITATIONS' own standing pattern of checking this function every
  block), also re-read Part C's State-version invariant: "every
  graph-relevant mutation increments `tenders.state_version` in the same
  transaction," listing knowledge selection change, evidence change,
  compliance change, role assignment change, proposal claim change, and
  amendment impact change. `TenderRepository.bump_state_version` has
  existed since B1/B2, but a repo-wide search found **zero call sites** —
  B7's `approve_compliance_entry` and `override_compliance_entry` never
  called it, despite "compliance change" being explicitly on that list.
- **Behaviour:** every compliance approval and override since B7 shipped
  has left `tenders.state_version` unchanged. The column exists, defaults
  to 0, and has silently stayed there through B7's entire live smoke test
  and 145-test suite — nothing in either checked it.
- **Why nothing caught it:** no reader existed yet. The invariant's whole
  purpose is B10's graph-freshness check (`graph_projection_runs.
  source_state_version` compared against the tender's current
  `state_version` to detect a stale Neo4j projection). Until B10 exists,
  an unbumped `state_version` produces no visible symptom at all — every
  test that exercises compliance approve/override asserts on the
  compliance data itself, never on the counter nothing yet reads.
- **Risk (why this matters more than a typical missed-field bug):** this
  is exactly the shape B5's ground-truth generator coupling and B7's
  cache-scoping bug already showed — no crash, a confidently wrong answer
  far from the cause. Concretely, if left unfixed until B10: every
  `graph_projection_runs` freshness check would have compared its
  recorded `source_state_version` against a `tenders.state_version` that
  never moved, so every projection would read as current no matter how
  much compliance/role/proposal data changed underneath it in the
  meantime. B10's own required test #10 ("stale projection query returns
  `GRAPH_STALE`") would either never fire or fire on the wrong condition,
  and the failure would surface two blocks later as "why is impact
  analysis returning outdated results" — with the actual cause sitting in
  B7 code nobody would think to re-open.
- **Status:** resolved-in-B8, extended in B9 and B10, and corrected again
  in the B0-B11 catch-up review below. B8 fixed
  `approve_compliance_entry`/`override_compliance_entry` (covering
  "compliance change" and "evidence change" together, one bump per
  decision write rather than once per evidence link) and B8A's
  `select_candidate`/`deselect_candidate` ("role assignment change").
  Commercial/pricing changes are not on the invariant's list, so B8B needs
  no bump. B9's `persist_section_and_claims` covers "proposal claim
  change." **B10 update:** `knowledge_service.set_knowledge_selection`
  (B6's own route, `PUT /tenders/{tender_id}/knowledge-selections/
  {knowledge_item_id}`) was still missing its bump — B6's docstring had
  deliberately deferred it, explicitly citing graph projection (B10) as
  the blocking dependency. Found while designing B10's live staleness
  demo (the demo's own mutation — deselecting a knowledge item — would
  have silently proven nothing, since `state_version` never moved), fixed
  in the same transaction as the selection write, matching the other
  three call sites. Of the six listed mutation types, only **"amendment
  impact change" remains with no call site** — amendments as a concept
  aren't built in any block through B10, so there's nothing yet to wire
  this into; revisit when that block exists, same "reminder, not a closed
  claim" posture this entry has held since B8.
  **Catch-up review correction (pre-B12):** every call site fixed above is
  a *human* review/override/select action. The automated pipeline that
  actually *originates* compliance and evidence rows —
  `compliance_service.persist_compliance_decision`, called from
  `evaluate_compliance_for_requirement` on every ordinary compliance run,
  with no human action involved — never bumped `state_version` at all.
  This is not a new mutation type outside the original six; it is the
  same "compliance change"/"evidence change" pair this entry already
  claimed as fixed, just missed for the path that creates the data rather
  than the path that reviews it afterward. Worse, B10's own live
  staleness demo (the test cited below) exercised only the human-approval
  call site, so it proved the mechanism for human mutations, and this
  entry's "resolved-in-B8, extended in B9/B10" status was read as the
  invariant holding end to end — it wasn't. A compliance run that writes
  dozens of new evidence/compliance rows with no human step in between
  left `graph_projection_runs.source_state_version` matching
  `tenders.state_version` throughout, so `check_freshness` reported the
  graph fresh while real compliance data had just changed underneath it —
  precisely what B10 exists to prevent, on what is the *more* common path
  (an automated run), not an edge case. Fixed by adding the same
  `bump_state_version` call, once per decision write, directly to
  `persist_compliance_decision` — see "Standing pattern: a reader trusts
  a column its writer never keeps current" below for the general shape
  this belongs to, and why an invariant proven only against its
  human-approval call site should not be read as proven end to end.
- **Test:** new direct tests assert the bump itself, not just that the
  surrounding write succeeded — `tests/integration/test_compliance_service.py`
  (approve/override bump `tenders.state_version` by exactly 1),
  `tests/integration/test_talent_service.py` (select/deselect do the
  same), `tests/integration/test_proposal_service.py` (claim persist
  bumps once, a no-op retry does not bump again), and
  `tests/integration/test_knowledge_service.py` (select and deselect each
  bump by exactly 1). B10's own freshness check is additionally verified
  against a REAL mutation through `approve_compliance_entry` (not a
  manual `UPDATE` on `state_version`) in
  `tests/integration/test_graph_projection.py::
  test_stale_projection_raises_graph_stale_after_real_mutation` — the
  graph is projected fresh, a real approval bumps the counter, and the
  test asserts `GraphStaleError` is raised before re-projection and
  clears after it.

## [B7] Gemini free-tier quota varies enormously by model — verify per-day, not just per-minute, before a live run

- **Discovered:** B7's live Docker smoke test. `gemini-3-pro-preview` is
  retired (404). `gemini-3.1-pro-preview` exists but returned 429 with
  `limit: 0` on this account's free tier — the "pro" tier isn't reachable
  at all, at any per-minute rate. `gemini-3.5-flash` (non-lite, one tier
  below pro) looked viable from a single verification call, but under a
  real 74-requirement compliance pass it hit `GenerateRequestsPerDay
  PerProjectPerModel-FreeTier, limit: 20` — a **daily**, not per-minute,
  cap. Every subsequent call retried with the provider's own ~30s
  `retryDelay` before failing, so the worker sat retrying calls that could
  not possibly succeed again until the daily quota reset, for potentially
  an hour or more, across two separate interrupted runs' worth of queued
  jobs.
- **Behaviour:** a single successful verification call (Part P discipline)
  is not sufficient evidence that a model is viable for a real multi-call
  run — it doesn't distinguish a per-minute limit (survivable: the call
  just gets slower) from a per-day limit (fatal: no number of retries
  within the session will ever succeed once exhausted). The two failure
  modes look identical from one test call and only differ once you either
  read the quota metric name in a 429's error body (`...PerMinute...` vs
  `...PerDay...`) or exhaust the quota for real.
  `gemini-3.5-flash-lite` (same tier already proven reliable across B5/B6's
  own extensive live use as `VERIFIER_MODEL`) turned out to be the
  correct choice — a per-minute-only limit (15 RPM), not a low daily wall.
- **Risk:** low ongoing, now that the correct model is set — but the
  failure mode (a worker silently burning real wall-clock time on
  unrecoverable-today retries) will recur for any future block that picks
  a new model without checking this specifically. `REASONING_MODEL` is now
  `gemini-3.5-flash-lite` in `.env`, live-verified against a full
  74-requirement run end to end via the real Docker smoke test.
- **Status:** resolved for `REASONING_MODEL` specifically. The general
  risk (a future block choosing a new model alias without checking whether
  its free-tier limit is per-minute or per-day) is recorded here as a
  standing process note, not something one model choice closes out.
- **Test:** none automated (this is an operational/cost concern, not a
  code-correctness one) — the live Docker smoke test itself is the
  evidence: full RFP (74 requirements) processed end to end with
  `gemini-3.5-flash-lite` as `REASONING_MODEL`, no quota exhaustion.

## [B8B] Commercial false-positive rate is measured, but on a narrow fixture

- **Discovered:** B8B's own stop-gate wording is explicit that false
  positives be "measured and documented," not just that seeded defect
  recall be checked. `tests/unit/test_pricing_checks.py::
  test_correct_rows_produce_zero_findings_no_false_positives` is that
  measurement: `evaluate_row_arithmetic` produces zero findings on the
  synthetic fixture's two ordinary, non-defective rows ("Project Manager,"
  "Site Survey" on `pricing_schedule_1.xlsx`).
- **Behaviour:** 0/2 false positives on the rows actually checked. Every
  other row in the fixture is a deliberately seeded defect, so 2 is the
  entire population of "should produce nothing" cases the current
  synthetic package offers to measure against.
- **Risk:** this is an honest measurement of a narrow case, not a general
  false-positive-rate claim. Real pricing workbooks are far messier than
  two clean rows differing only in description — unusual-but-legitimate
  formulas, intentional rounding, multi-currency line items that are
  correct on purpose, or non-standard column layouts could all trip
  `evaluate_row_arithmetic`'s formula-shape/tolerance policy into a false
  `wrong_total`/`needs_human_review` on data that was actually fine. The
  synthetic fixture, by construction, doesn't exercise that variety.
- **Status:** accepted, measured honestly for what it is. No plan to
  expand the synthetic fixture's "correct row" population specifically to
  chase a bigger false-positive sample — the real signal on this will come
  from B16-era or post-launch real-workbook usage, not from inventing more
  synthetic clean rows that would still only test what this project itself
  imagined as "ordinary."
- **Test:** `test_correct_rows_produce_zero_findings_no_false_positives`
  (unit), plus the live Docker smoke test's own 8-finding count matching
  the pytest suite's expected count exactly (no extra live-only findings
  beyond the 6 seeded categories).

## Standing pattern: local dev/test environment state can silently invalidate a real verification

- **Discovered:** two during B8B's own live Docker smoke test, back to
  back, in the same session; a third during B9's citation-correctness
  measurement; a fourth during B11, despite this entry's own "Status"
  section (point 4 below) already writing down the exact discipline that
  would have prevented it; a fifth during the CodeRabbit-fixes pass
  following the pre-B12 audit - notable because this one is the first
  where the documented practice actually caught the problem in the
  moment, not just named after the fact:
  1. **Stale Docker image.** After writing all of B8B's code, the worker/
     api containers were only `docker compose restart`ed, not rebuilt.
     `restart` reuses the existing image; it does not pick up new source
     changes (worker/api have no dev-mode volume mount - source is baked
     in at build time). The first live smoke test attempt ran against B8A's
     image - `commercial_approval_gate` didn't exist in that compiled
     graph, so `route_after_resource_selection_gate` fell straight through
     to `final_gate`, silently skipping the entire commercial approval
     step. It happened to fail loudly *this time*, because the smoke
     script explicitly waited for `commercial_approval` as its own named
     gate and timed out when it never appeared - but a less specific check
     (e.g. "did the run reach `completed`") would have reported success on
     a run that silently skipped an entire human approval gate.
  2. **Port-squatting native Postgres.** A native Windows PostgreSQL 18
     service was bound to `0.0.0.0:5432`, ahead of Docker's own port
     forward for `tenderos-postgres-1` (also mapped to host `5432`). Every
     host-side script/pytest connection to `localhost:5432` was silently
     reaching the native instance, not the project's Postgres 16 container
     - confirmed by querying `version()` and getting `PostgreSQL 18.0 on
     x86_64-windows`, not the Debian/Linux 16.13 the container actually
     runs. This one also failed loudly *this time* (`role "tenderos" does
     not exist` - the native instance genuinely had no such role), but a
     native instance that happened to already have a same-named role (a
     stale one from unrelated prior work, or coincidence) would have let
     tests run to completion against the wrong database entirely, silently
     validating nothing about the actual code under test. `docker exec`
     into the container bypasses host networking entirely, which is why
     that path kept working the whole time and made the underlying cause
     harder to spot at first.
  3. **Test-suite teardown destroying a live diagnostic.** During B9,
     right after a real, hour-plus live pipeline run produced real
     `proposal_sections`/`proposal_claims`/`compliance_entries`/
     `evidence_links` rows worth inspecting further, the full automated
     test suite (`uv run pytest tests/`) was run against that same live
     `POSTGRES_DSN` "to confirm everything's still green."
     `tests/integration/conftest.py` has an autouse `_clean_tables`
     fixture that deletes every table's rows after *each* test - not a
     bug, exactly the isolation every other integration test in this
     repo depends on - but it means running the suite against a database
     you still need to inspect wipes it unconditionally, with no warning
     and no distinction between "scratch test data" and "the live
     evidence I was just about to examine." This one failed *silently* in
     a different sense than the other two: no error, no exception - the
     very next `psql` query just returned zero rows, and it took a
     separate round of debugging (checking `docker ps`, re-querying
     `organizations`) to realize the data was gone rather than never
     having existed.
  4. **Same trap, third time in its exact documented shape, during B11.**
     Immediately after B11's own live smoke test completed, `uv run pytest
     tests/` was run against the same live `POSTGRES_DSN` "to confirm
     everything's still green" - the identical phrase, and the identical
     mistake, as incident 3. This wiped the just-completed live run's
     tender/proposal_claims/requirements before a follow-up question
     ("did red-team's deterministic checks actually fire against the real
     Gemini draft, or only against the manually-seeded finding?") could be
     answered from that data. Recovered by re-running the live smoke test
     once more and inspecting the database immediately afterward, before
     touching pytest again - not by reconstructing the lost run. Notable
     specifically because this entry's own "Status" section already named
     the exact preventive practice ("do that inspection before running the
     automated test suite against the same `POSTGRES_DSN`") before this
     incident happened, and it still happened - the discipline being
     written down once is not the same as it being followed automatically
     in a later, unrelated-feeling moment (running the full suite as a
     routine sanity check, not consciously flagged as "touching the same
     database as the live run I still need").
  5. **Port-squatting native Postgres, recurrence - but caught THIS time by
     the documented practice, not discovered after the fact.** During the
     CodeRabbit-fixes pass following the pre-B12 audit (a new
     `redteam_findings.resolution_source` migration), the Docker stack had
     been stopped between conversation turns; after bringing it back up,
     the first `alembic upgrade head` attempt failed with `role "tenderos"
     does not exist` - the same native PostgreSQL 18 service from incident
     2, still installed, still running, still winning the race for
     `localhost:5432` on at least the first connection right after the
     stack restarted. Rather than assume a fluke and retry, practice (2)
     below was actually run: a direct `SELECT version(), current_database()`
     against the same DSN, which came back as the real Docker Postgres
     16.13 container - confirming the failure was a genuine transient race
     during restart, not a wrong-server problem masquerading as one, before
     trusting the retried migration. This is the first of the five
     incidents in this entry where the written-down practice was the thing
     that actually caught the problem in the moment, rather than the
     problem being noticed afterward (loudly, per incidents 1-2, or
     silently, per incidents 3-4) and the practice only being written down
     in response.
  6. **Container running and healthy, but its host port publish silently
     missing - recurred a second time in B13 (incident 8 below); no longer
     read as a one-off.** During B12's CodeRabbit-fixes pass, `docker compose
     up -d` first failed outright (`port is already allocated` - an
     unrelated project's own Postgres container, not this project's
     native-service history, had grabbed 5432 first) - the very port
     conflict incidents 2/5 already describe, but this time from a
     *different* source. Stopping that container and retrying brought the
     stack up "Healthy," but every subsequent host-side connection (`uv
     run pytest`, a direct `asyncpg.connect`) failed - first with
     `InvalidPasswordError` (a second, still-unidentified process was
     transiently answering on `[::1]:5432` at that exact moment), then,
     once that process was also stopped, with a flat `ConnectionRefusedError`
     even though `docker ps`/`docker exec ... psql` both showed the real
     container running and internally healthy. `docker port
     tenderos-postgres-1` was the diagnostic that actually explained it -
     empty output, no `0.0.0.0:5432->5432/tcp` line at all, meaning the
     container's own host port publish had never been (re-)applied,
     apparently a side effect of the FIRST failed `up` attempt leaving the
     container in a state `docker compose`'s own idempotency logic then
     treated as "already correctly up" on the retry, skipping the publish
     step. `docker compose up -d --force-recreate postgres` (data
     preserved - a named volume, not tied to container lifecycle) fixed
     it; `/health/ready` and a real host-side connection both confirmed
     before trusting the next test run. Same "caught in the moment via a
     direct check, not discovered afterward" character as incident 5, but
     the check that caught THIS one was `docker port`, not a SQL identity
     query - worth knowing as its own diagnostic for this specific
     symptom (container healthy, host connection refused) the next time
     it appears, since incident 5's own SQL-identity check answers a
     different question (which server answered) than this one (did a
     server answer at all).
  7. **The test-teardown trap, a third time in its exact documented shape -
     this time destroying data explicitly meant to be preserved.** After
     B12's own live-verification session ended (that session's own report
     said its database was "not wiped... remains available for direct
     inspection," deliberately breaking the pattern the second occurrence,
     incident 4, had just failed to break), a later session ran `uv run
     pytest tests/` against the same live `POSTGRES_DSN` - identical
     mistake to incidents 3 and 4, a session removed. Confirmed via direct
     Postgres inspection, not assumed: every ORM-mapped table (`tenders`,
     `organizations`, `requirements`, `proposal_claims`, `evidence_links`,
     ...) at zero rows; only LangGraph's own checkpoint tables (not
     `Base.metadata`-managed, so outside `_clean_tables`' reach) survived.
     What makes this the worst of the three: read-only forensics against
     those surviving checkpoint tables (cross-validated against real
     persisted rows in 3 threads) confirmed they hold no reconstructable
     content for what was lost - `TenderState` is ids/counts/status only,
     see the new entry below. Unlike incidents 3/4 (where the immediate
     next live run could re-produce comparable data), this one destroyed
     the specific run behind the project's largest known open quality gap
     (the "[B9]/[B12] Drafting mostly produces ungrounded text" entry),
     with no cached dump to fall back on. See that entry's own correction
     for the full account.
  8. **Incident 6's exact mechanism, a second time - now a confirmed
     defect, not an incident.** B13 §7 step 2's verification run: Docker
     Desktop itself restarted mid-session (unrelated to anything this
     project's own containers did), and bringing the stack back up hit
     incident 2/5's port-squatting trap first (an unrelated project's
     container on 5432, stopped with the user's OK), then incident 6's
     trap immediately after - `docker compose up -d` reported
     `tenderos-postgres-1` "Healthy," but every host-side connection
     (`uv run pytest`, a direct `asyncpg.connect`) failed with
     `OSError: Connect call failed`. `docker port tenderos-postgres-1`
     confirmed the same empty output incident 6 already documented -
     `"5432/tcp": []` in `docker inspect`'s own `NetworkSettings.Ports`.
     Identical fix (`docker compose up -d --force-recreate postgres`),
     confirmed the same way (a real host-side `asyncpg.connect`, not
     `docker exec`). **The actual root cause, named for the first time
     rather than only described symptomatically:** the compose healthcheck
     (`pg_isready`, Part A1's own `docker-compose.yml`) executes *inside*
     the container's own network namespace - it can only ever observe
     whether Postgres itself is accepting connections, never whether
     Docker has actually programmed the host-side NAT/port-forwarding rule
     that makes `localhost:5432` reach that same Postgres from outside the
     container. Those are two independent mechanisms with no coupling
     between them - a container can be genuinely, correctly "Healthy" by
     every internal measure while being completely unreachable from the
     host, and nothing in `docker compose`'s own status output says so.
     Two occurrences of the identical failure, from two different
     triggering circumstances (a CodeRabbit-fixes pass; an unrelated
     Docker Desktop restart), is what makes this a defect in the
     verification practice rather than a one-off worth another paragraph
     of description.
- **Why this belongs next to the measurement-tooling standing pattern
  above, not folded into it:** same "confidently wrong, not obviously
  broken" shape, but a different root-cause category - that pattern is
  about *code* (eval scripts, cache scoping) being wrong; this one is
  about *environment/process state* (which image is running, which
  process owns a port, which fixture just ran against the database you
  were about to inspect) silently substituting itself for, or destroying,
  the thing actually being verified. The first four are invisible to a
  result that merely "looks fine" - the first two produce a wrong answer
  that looks like a right one; the third and fourth produce no answer at
  all where a real one used to be, without saying so. The fifth and sixth
  are the outliers in exactly this dimension, not just in outcome: both
  were caught in the moment, before anything downstream trusted a wrong
  result, precisely because a direct check was actually run rather than
  assumed away - see incidents 5/6 and the "Status" section's own note on
  point (2) below. Two different checks caught them (a SQL identity query
  for 5, `docker port` for 6), which is itself worth noting - "verify
  before trusting" is the transferable practice, not any one specific
  command.
- **Risk - why this matters going forward, not just as a postmortem:** B10
  through B17 are all worker/graph-heavy blocks (graph projection,
  red-team, readiness, event-driven reanalysis) that will keep needing
  live Docker verification the same way B7-B9 did, and will keep producing
  real live-run data worth inspecting *after* the run, not just during it.
  The test-teardown trap specifically has now recurred once already (B9 to
  B11) despite being named and given a concrete practice after its first
  occurrence - it is the easiest of the three trap *types* to fall into
  again precisely because triggering it doesn't feel like touching live
  data, it feels like "running the test suite," a routine, low-stakes-
  seeming action. All three trap types recur on every future block that
  (a) changes orchestration/domain code and verifies via a rebuilt-assumed
  container, (b) runs host-side scripts/tests against `localhost` and
  trusts the port, or (c) runs the automated test suite against the same
  database a live run's evidence is still sitting in. None of the three
  produces a code-level exception in the product itself - all three are
  purely which-process-or-fixture-am-I-actually-touching failures, exactly
  the class of bug a "the tests passed" report does not surface on its own.
- **Status:** accepted as a standing operational risk, not something a
  one-time fix closes out. Concrete practice going forward: (1) before any
  live Docker smoke test, rebuild explicitly (`docker compose up -d
  --build`) rather than `restart` - never assume a running container has
  today's code; (2) before trusting a host-side test/script run against
  `localhost`, spot-check which server is actually being reached (a
  `SELECT version()`/`current_database()` is enough) rather than assuming
  the connection string implies the intended target; (3) prefer smoke
  tests that assert the *specific* expected sequence (named gate types, in
  order) over tests that only check a final coarse status like
  `completed` - the specificity is what turned the first two incidents
  into loud failures instead of silent false passes; (4) after a live run
  produces data worth inspecting further, either do that inspection
  *before* running the automated test suite against the same
  `POSTGRES_DSN`, or point the test run at a different database/schema
  entirely - `_clean_tables`'s teardown has no "are you sure" and no way
  to tell scratch data from evidence still in use. Point (4) has now
  failed to prevent a recurrence once already - treat it as something to
  actively check before running `pytest` after a live run, not as a
  practice that, once written down, can be trusted to happen on its own.
  Point (2), by contrast, has now actually worked as intended once
  (incident 5) - the practice isn't purely aspirational, it has a real,
  demonstrated case of catching the exact failure it was written for
  before anything downstream trusted a wrong result. Worth holding both
  readings at once rather than either extreme: written-down discipline
  doesn't enforce itself (point 4's own second failure proves that), but
  it isn't inert either (point 2's first real save proves that) - the
  difference so far looks like whether the check was still fresh/specific
  enough in mind at the moment it mattered, not whether the practice
  itself was sound.
  This entry's own prediction - "if it recurs a fifth time, the actual fix
  is structural, not another written reminder: point the test suite at a
  dedicated database by default... so `_clean_tables` physically cannot
  reach live-run data regardless of what anyone remembers to check" - held.
  It recurred a third time in this specific sub-shape (incident 7 above),
  and the structural fix is now built, not just named: a session-scoped
  `_guard_test_database` fixture in `tests/integration/conftest.py`
  (B13-owned, added alongside the new traceability-drawer work) queries
  `SELECT current_database()` on a real connection before `_schema`/
  `_clean_tables` run, and refuses loudly unless the database name ends in
  `_test`. `make test` points at a dedicated `tenderos_test` database by
  default; `uv run pytest` alone against the dev/live DSN now fails
  immediately with an explicit error instead of silently deleting
  anything. This closes incident 3/4/7's specific mechanism (pytest
  reaching a live database at all) - it does not address incidents 1/2/5/6
  (image staleness, port-squatting), which remain open per points (1)-(3)
  above. See the guard's own KNOWN_LIMITATIONS entry below for its one
  known residual gap.
  **Incident 6/8's own mechanism (healthy-but-unreachable) is a separate
  gap from incidents 1/2/5, not covered by anything above - owner B16
  (Reliability and Observability), not fixed now.** Points (1)-(3) are
  manual discipline (rebuild explicitly, spot-check the connection,
  prefer specific assertions); none of them would catch "Healthy" lying
  about host reachability, because the lie is in Docker's own status
  output, not in anything this project's own scripts choose to trust.
  The real fix is a startup/verification check that doesn't stop at
  `docker compose`'s own health status - a real host-side connection
  attempt (the same `asyncpg.connect`/`SELECT version()` both occurrences
  were actually caught with) run automatically after `up`, not left to
  whoever happens to remember point (2) in the moment. B16's own build
  list names reliability tests and Compose readiness explicitly (see the
  separate "CI, schema verification, worker image, and Compose readiness"
  entry below) - this is the same territory, not a new one: a real
  `/health/ready`-style check run from the host, or a compose healthcheck
  replaced with something that can't be satisfied by the container's own
  internal view of itself.
- **Test:** none automated for incidents 1/2/5/6/8 (host-machine/Docker-
  daemon state, not application code) - the incident write-up above and
  the discipline it dictates are the mitigation. Incident 3/4/7's own
  mechanism now has a real regression check: running `pytest` with
  `POSTGRES_DSN` pointed at the dev/live database name raises immediately,
  verified directly this pass; running it against `tenderos_test` runs
  normally. Incident 8 itself was caught and fixed live, the same way
  incident 6 was (`docker port` diagnosis, `--force-recreate`, a real
  host-side `asyncpg.connect` before trusting the next test run) - no
  automated test exists for the underlying defect, which is exactly what
  the new B16 ownership note above is for.

---

## [B9] Section templates are an interpretation, not a locked list

- **Discovered:** B9 planning. Neither the PRD nor the Build Guide specifies
  what proposal sections TenderOS should draft - B9's own build list just
  says "section templates" with no enumeration, the same shape B8A's role
  extraction schema faced with no locked E-part contract.
- **Behaviour:** `proposal_service.py`'s `SECTION_TEMPLATES` hardcodes six:
  Company Overview (always drafted, not data-gated), Relevant Experience,
  Technical Approach, Proposed Team, Compliance and Eligibility, and
  Commercial Summary - five of the six tied to `requirements.category` or a
  specialist's own approved output, and skipped entirely (no LLM call, no
  section row) when the tender has zero underlying approved facts to ground
  that section against.
- **Risk:** if a real tender needs a section outside these six - a
  client-mandated section with its own required heading, for instance -
  nothing in the current pipeline produces it. A human would have to add it
  entirely outside the system (no `proposal_sections` row, so no claim
  traceability for it either).
- **Status:** accepted interpretation. Not revisited unless a real tender's
  submission requirements demand a section shape this list can't express.
- **Test:** none - this is a scope statement about what the six templates
  cover, not a behavior with a pass/fail case.

---

## [B9] Resolved pricing findings cannot become claim evidence

- **Discovered:** B9, while writing `_facts_from_pricing` in
  `proposal_service.py` for the Commercial Summary section template.
- **Behaviour:** `evidence_links.source_kind`'s CHECK constraint (Part C)
  has no pricing-sourced value (`employee`, `certification`, `past_project`,
  `capability`, `company_document`, `client_reference`, `partner`,
  `document_excerpt` only), and every `evidence_links` row requires a real
  `knowledge_item_id` or `source_chunk_id` - a `pricing_findings` row is
  neither. So a resolved pricing finding can be offered to the Commercial
  Summary section as narrative context, but a factual claim that leans on
  one for support can never resolve a real `evidence_link_id` and is
  therefore always `unsupported`, even though the underlying finding is a
  real, human-resolved fact about this tender.
- **Risk:** none to correctness (a claim that can't cite real evidence
  correctly stays `unsupported` rather than being wrongly marked
  `grounded`) - the risk is a UX/completeness gap: the Commercial Summary
  section may end up thinner or more hedged than the underlying commercial
  validation actually justifies, because the one specialist output this
  section most wants to lean on structurally cannot ground a claim.
- **Status:** accepted. A real fix would add a pricing-sourced
  `evidence_links.source_kind` value (or a parallel junction table) -
  out of scope for B9, not blocking its stop-gate since the stop-gate
  measures unsupported-claims-wrongly-accepted-as-grounded, not proposal
  section richness.
- **Test:** none automated - covered by `enforce_claim_grounding`'s own
  unit tests indirectly (a factual claim with no resolved evidence is
  asserted `unsupported` regardless of cause).

---

## [B7/B8/B9] Gate approval does not apply GateDecisionRequest.target_updates

- **Discovered:** B9, while planning the citation-correctness live
  measurement script, by reading `compliance_gate_node`'s source directly
  and grepping the full repo for `target_updates` usage - **a code-level
  finding, not an observed live-run failure.** (This entry originally
  described it as having been directly observed via a gate-only-approval
  run; that was inaccurate and has been corrected here. No such run was
  ever executed - the citation-correctness script was written with the
  real per-target G7/G8/G9 approvals already built in, precisely because
  this code-reading pass had already surfaced the gap beforehand.) The
  code-level finding itself stands on its own evidence (below) and does
  not depend on a live run to be true.
- **Behaviour:** `GateDecisionRequest` (`apps/api/.../schemas/gates.py`)
  has carried a `target_updates: list[dict] = []` field since B3, matching
  R5's own locked example payload (`"target_updates": []`) and the code
  comment already on that field: *"Real per-target application arrives
  with the blocks that give gate targets real content (B7+)."* That never
  happened. `decide_gate` (`routes/gates.py`) reads `body.target_updates`
  nowhere - `gate_service.record_decision` doesn't take it as a parameter
  at all. Approving `compliance_review`, `resource_selection`, or
  `commercial_approval` only unblocks the *workflow* (writes a
  `gate_decision` audit event, routes to the next node) - it has zero
  effect on `compliance_entries.human_approved`,
  `role_matches.human_selected`, or `pricing_findings.resolved`. Those
  three flags are only ever set through their own separate G7/G8/G9
  actions (`POST /compliance/{id}/approve`, `POST /roles/{id}/select-
  candidate`, `POST /pricing-findings/{id}/resolve`), each with its own
  audit action name.
- **Risk:** a human reviewer can click "approve" on all three post-join
  gates and watch the workflow reach `final_readiness`/`completed` with
  the tender in a state where *no individual decision was ever actually
  approved* - the coarse workflow status gives no signal that this
  happened. B9 is the first block whose own output visibly depends on this
  (an under-approved tender silently drafts a thin or empty proposal), but
  the same dependency will recur for B10 (graph projection presumably
  wants to know which facts are human-approved) and B12 (readiness's own
  required blockers list "compliant decision without valid evidence" and
  "required role with no human-selected candidate" - exactly the two
  flags this gap can leave unset even after every gate shows `approved`).
  This is the same "confidently wrong, not obviously broken" shape as the
  standing environment-trap pattern already logged, but the root cause
  here is a genuine, uncompleted piece of B7's original design (the
  `target_updates` field was added for exactly this and never wired up),
  not environment state.
- **Status:** accepted as a known gap, not fixed in B9. **Scheduled to
  B12 (Readiness and Governance)**, not B11 and not B15 - reasoning below
  - matching the precedent already set by the gate-rejection limitation's
  own "real fix is B11" scheduling, so this doesn't stay open-ended the
  way it silently sat unowned from B7 through B9.

  **Why B12, not B11:** B11's build list (structured findings,
  finding-resolution gate, supervisor mapping from finding category to
  corrective task, bounded revision loop) is about *post-hoc* correction
  after a red-team finding, not the original approval path - it's
  thematically adjacent (already the scheduled home for the gate-
  rejection limitation, same "what does a gate decision actually do"
  territory) but nothing in B11's own stop-gate depends on target_updates
  being wired up. B11's own automated tests would almost certainly
  construct `human_approved`/`human_selected` fixture state directly
  through the repository layer, the same way every integration test in
  this repo already does (see `test_talent_service.py`,
  `test_compliance_service.py`) - so B11 could be built and pass its own
  stop-gate without ever forcing this fix to happen. B12 is different:
  its own locked "Required blockers" list (Build Guide, verbatim) names
  *"compliant decision without valid evidence"* and *"required role with
  no human-selected candidate"* - exactly the two flags this bug leaves
  silently unset even when every gate shows `approved`. B12's own
  stop-gate ("seed each blocker and confirm readiness refuses approval
  until resolved") cannot be honestly satisfied while this bug stands: a
  reviewer who approves every gate the normal way (no manual per-target
  G7/G8/G9 workaround - nothing in the current gate UI/API prompts for
  one) would hit a readiness report stuck refusing approval with no
  visible explanation, since every gate they can see already reads
  `approved`. That's a forcing function B11 doesn't have. B12 is also the
  last block before B13 (Functional UI, depends on B5-B12) designs the
  actual approve/reject screens - deciding the real policy question here
  (`target_updates` batch-applies per-target approvals in the same gate-
  decision call, vs. the UI requires per-target review before a gate can
  be approved at all) before B13 exists means B13 isn't left to
  rediscover the same gap while building the gate-approval screen.

  **Why not B15:** B15 is Security and Prompt-Injection Hardening -
  content/instruction separation, untrusted-document wrapping, tool
  allowlists, rate limiting, secret scanning. Nothing in its build list
  or stop-gate touches gate-decision or approval semantics; it does not
  fit at all, not even loosely.
- **Not causal for this block's own 18/22-unsupported measurement:** the
  citation-correctness run (see the merged entry below) was driven with
  real per-target G7/G8/G9 approvals from the start - 86/86 compliance
  entries, all 5 roles, all 8 pricing findings - completed *before*
  `proposal_drafting_node` ran, confirmed directly from the measurement
  script's own execution log (each batch of individual approvals is
  logged immediately before its gate's approval, and drafting only runs
  after the last of the three gates). So the grounded-context builder was
  handed real, human-approved data in that specific run. This gap is real
  and matters for any reviewer who does *not* do that manual workaround,
  but it is not the explanation for that run's own low grounding rate -
  see the merged entry below for what is.
- **Re-verified independently, pre-B12 (round 2 of the CodeRabbit-fixes
  pass):** a full-repo grep for `target_updates` outside its own schema
  field declaration and this entry's own citations found nothing - no
  service, repository, or route reads it. Confirms this entry's own
  standing claim still holds and sharpens it in one specific way worth
  recording for B12 planning: because there is genuinely **zero existing
  write path** for `target_updates` today, wiring it up is greenfield
  work, not a modification to something partially built. B12 doesn't need
  to audit or unwind any existing (possibly-load-bearing) per-target
  application logic before building the real one - there isn't any yet.
- **Resolved in B12.** `target_updates` now has a real write path:
  `domain/services/gate_target_service.py`'s `apply_target_update`/
  `apply_target_updates`, called from `routes/gates.py`'s `decide_gate`
  before `gate_service.record_decision` runs, unconditionally on the
  gate's own decision (a reviewer approving individual targets while
  still requesting changes on the gate overall is legitimate - see that
  file's own module comment). Each entry is `{target_type, target_id,
  decision}` (now a typed Pydantic model, `schemas/gates.py`'s
  `TargetUpdate`, not the bare `list[dict]` this field carried since B3);
  `target_type` is one of `compliance_entry`/`role_match`/`pricing_finding`
  - `role_match`, not `role_requirement`, since a role_requirement can have
  several candidate matches and `select_candidate` needs the specific one
  (a real divergence from the other two target_types, which reuse their
  own gate's `targets` list id directly). Dispatches to the three
  existing, already-tested, already-audited G7/G8/G9 service calls - no
  new business logic. Permissive on an unrecognized `target_type`/
  `decision` value (forward-compatibility); strict on ownership - every
  target id is resolved back to its true owning tender and compared
  against the gate's own tender before anything is applied, raising
  `TargetOwnershipError` (surfaced as a 403) on any mismatch, checked for
  the WHOLE batch before applying ANY of it (no partial writes when one
  entry in a multi-entry batch is foreign). This was caught and fixed
  during planning, not after: the first draft only checked ownership
  per-entry inside the dispatch loop itself, which would have let an
  earlier legitimate write commit before a later foreign entry in the
  same batch was rejected - the exact `select_candidate` cross-tenant
  shape (see the audit entry below) in a new location, closed before it
  ever shipped rather than found afterward.
- **Test:** `tests/integration/test_gate_target_service.py` - the three
  legitimate dispatch paths, permissive-on-unknown for both
  `target_type` and `decision`, the cross-tenant reject (single-entry and
  whole-batch), and the no-partial-writes proof (a batch with one
  legitimate entry listed BEFORE one foreign entry applies neither).

---

## [B9] Drafting mostly produces ungrounded text; enforcement catches it - one condition, not two

- **Discovered:** B9's own stop-gate ("claim citation correctness >= 0.95"),
  measured live against the real `synthetic_tender_a.py` fixtures with real
  Gemini: real company knowledge (employee master, certifications, 4
  resumes) imported and verified, a real tender with all 4 documents
  (RFP, technical annexure, both pricing schedules), driven through every
  gate with real per-target G7/G8/G9 approvals (86/86 compliance entries
  individually approved, all 5 roles given a selected candidate, all 8
  pricing findings resolved) *before* `proposal_drafting_node` ran -
  confirmed from the measurement script's own execution log, so the
  target_updates gap logged above was not active in this run (see that
  entry's own "Not causal" note).
- **Behaviour:** these are restated together deliberately, not as two
  findings - they are the same underlying condition described from two
  angles. The drafted proposal produced 22 total claims: 3 `not_required`
  (narrative), **18 `unsupported`**, and 1 `grounded`. The one grounded
  claim's citation genuinely supports it:
  ```
  CLAIM: "We hold a valid tax clearance certificate issued by the Revenue
  Authority, valid through September 2026."
  CITES: [certification, verified=true, validity=valid] "Tax Clearance
  Certificate, issued by Revenue Authority, valid from 2025-09-01 to
  2026-09-01."
  ```
  **Measured citation correctness on this run: 1/1 = 100%** - a real
  number, but not a validated `>= 0.95` rate; a sample of one grounded
  claim cannot establish that. **The reason the sample is this small is
  the same fact as the low grounding rate itself**: 18 of 22 real,
  live-drafted factual claims cited no evidence at all, so there was
  almost nothing else for the measurement to sample from.
  `enforce_claim_grounding` correctly withheld every one of them from
  `grounded` - the invariant is doing exactly what it's supposed to - but
  that means B9's current drafting behavior mostly produces ungrounded
  text, and enforcement is the thing making that visible rather than
  something to work around.

  **The cause of the 18/22 rate is unknown - not "the prompt," not
  anything else - and that is the honest finding, not a gap in this
  writeup.** Bug #1 (target_updates, above) has been specifically ruled
  out for this run: real per-target G7/G8/G9 approvals were confirmed
  complete, in order, before drafting ran. Beyond ruling that one thing
  out, there are exactly two live hypotheses and no evidence separating
  them: (a) the underlying approved compliance/role-match data was
  genuinely thin on verified evidence for most of these claims, so an
  honest empty-citation claim is the *correct* behavior per
  `proposal_drafting.md`'s own prompt ("still be written with empty ref
  lists rather than omitted"); or (b) real, citable evidence existed and
  the model didn't cite it, a genuine prompt/model gap. An earlier
  version of this entry asserted (a) for 11 of the 18 (Technical
  Approach, Relevant Experience, Commercial Summary) and (b) for the
  remaining 7 (Proposed Team, e.g. *"employee_1 is a Senior Engineer
  selected for the Senior Engineer role"*) as if both were confirmed.
  Neither was. Both were inferred from what the relevant code *can* do
  (`_facts_from_compliance`/`_facts_from_team`'s own logic), not from a
  query against what it *did* do for this specific run's actual rows -
  and the rows are now gone (this session ran the full test suite
  against the same live database immediately afterward;
  `tests/integration/conftest.py`'s autouse `_clean_tables` fixture
  deletes every table's rows after each test, wiping this exact tender's
  compliance/evidence/role-match data before the deeper check could run -
  see the environment-traps entry above, now updated with this as its
  third incident). What is actually confirmed: the model did not cite
  evidence for any of the 18. What is not confirmed: why, for any of
  them, individually or as a group. Distinguishing (a) from (b) needs a
  fresh live run with the resulting database preserved for inspection
  afterward, not more reasoning from code.
- **Risk:** none to correctness - `enforce_claim_grounding` behaved
  exactly as its unit tests specify against real, non-synthetic model
  output, and zero unsupported claims were wrongly accepted as grounded
  (the stop-gate's other bullet, which this run serves as live evidence
  for, on top of the 7/7 unit tests). The real consequence is downstream,
  not in B9 itself: **B11's red-team block will be operating on a
  proposal where most claims are unsupported.** That is an expected
  starting condition for B11 to plan around, not a surprise to diagnose
  fresh when B11 starts - whoever builds B11 should read this entry
  first. It is expected in the sense that it has now been observed once,
  live; it is not yet explained, and B11 should not assume a cause
  (prompt quality or otherwise) that this entry does not actually
  establish.
- **Status:** accepted as an honest partial result, not resolved. Per
  standing instruction (same rule as B5's own recall gate): no prompt
  tuning was attempted in response to the low grounded count - that would
  be adjusting the system to chase a passing number rather than reporting
  what was actually observed. A real citation-correctness measurement
  needs either a larger/more diverse live sample (multiple synthetic
  tenders, or repeated runs against a database that survives the run) or
  a seeded ground-truth set of claim/evidence pairs with known-correct
  labels, mirroring how B5's mandatory-recall gate and B7's REQ-073/074
  divergence check both eventually got real statistical grounding instead
  of a single anecdote. The open question above - was the underlying
  evidence thin, or was it available and uncited - needs a fresh live run
  (with the resulting database left untouched afterward) to close, not
  further reasoning from code alone.
- **B12 update - both leading hypotheses now eliminated. Cause still
  unknown. This is the project's largest known open quality gap.**
  B12's own live verification drove the identical `synthetic_tender_a.py`
  package through the real pipeline a second time, with every gate
  approved via real `target_updates` batch-approval through the actual
  gate-decision route (96 real per-target approvals genuinely applied and
  confirmed via before/after DB state - not the G7/G8/G9 workaround this
  entry's own B9 run used, functionally the same underlying writes via a
  different call path). B12's own review cycle separately fixed the
  talent-evidence-never-gets-`validity_status`-set bug in the same
  window (see the B0-B11 catch-up review) - the *other* live candidate
  cause this entry's hypothesis (a) named. Both fixes were live and
  active in this run. Result: 19 factual claims, **2 grounded, 17
  unsupported - roughly 90% of factual claims still lack valid
  evidence.** Not an improvement over B9's 1/19 (5.26%) worth reporting
  as progress - read plainly, it means neither fix meaningfully moved
  the number. Both of this entry's own named candidate causes are now
  fixed AND measured under real conditions, and the pipeline still fails
  to ground the overwhelming majority of factual claims it drafts. **The
  cause is elsewhere, and is currently unknown.** The two grounded claims
  this run (a tax-clearance certification citation, a PMP certification
  citation) are the same *kind* of claim as B9's one grounded case - a
  directly-verifiable certification fact - consistent with, but not
  proof of, a narrower version of hypothesis (a): Technical Approach
  capability claims, Proposed Team assignment claims, and Commercial
  Summary narrative claims still have no `evidence_link` `source_kind`
  that could ground them at all, a structural gap regardless of whether
  `target_updates` or talent-evidence was ever fixed. That narrower
  reading is plausible, not confirmed - nobody has traced it end to end.
  **Why this matters at the product level, not just as a metric:**
  claim-level evidence grounding is the PRD's own core thesis for this
  product (§5's "Grounded proposal generation" JTBD: "I want factual
  claims to remain linked to approved evidence rather than being
  invented by the model") - not one feature among many. Measured twice,
  independently, against the same real fixture package, under two
  different sets of upstream fixes: it currently works for roughly 1 in
  10 factual claims. Not investigated further here - B12 is complete and
  this was never its own stop-gate - but recorded at full strength so
  whoever picks it up next inherits the real severity and the two
  already-eliminated candidate causes, rather than re-deriving either.
  Unlike the B9 run, this run's database was **not** wiped afterward
  (the test-teardown trap this project has now hit five times), so the
  raw data remains available for direct inspection - tender_id and
  run_id are in the B12 build session's own live-verification output.

  **Correction (post-B12, next session): it was not preserved after
  all.** A later `uv run pytest tests/` invocation against this same live
  `POSTGRES_DSN`, outside the B12 build session itself, triggered
  `_clean_tables` and wiped it - the seventh occurrence of the
  environment-traps pattern below, not the sixth as this entry's own text
  above still says (that text is left as written at the time, corrected
  here rather than silently edited). Confirmed via direct Postgres
  inspection: `tenders`, `organizations`, `requirements`,
  `proposal_claims`, `evidence_links`, every ORM-mapped table, all at
  zero rows. What survived: LangGraph's own checkpoint tables
  (`checkpoints`/`checkpoint_blobs`/`checkpoint_writes`/
  `checkpoint_migrations`), which `_clean_tables` doesn't touch (it only
  iterates `Base.metadata.sorted_tables` - the checkpointer isn't a
  SQLAlchemy model). Read-only forensics against those surviving tables,
  cross-validated against real persisted rows in 3 threads, confirmed
  they hold nothing that could reconstruct what was lost - see "LangGraph
  checkpointer persists no reconstructable content" below. The specific
  tender_id/run_id this entry names, and everything about that run's
  actual claim/evidence data, cannot be recovered. This is the project's
  largest known open quality gap, now sitting with zero supporting data
  behind it - B13's own Playwright run is the next opportunity to
  measure it, and its live-verification run is the first one built with
  the structural fix (a session-scoped conftest guard, B13-owned) that
  makes this specific failure impossible rather than merely
  remembered-against.
- **Test:** `tests/unit/test_claim_grounding.py` (7 cases, synthetic,
  pins the invariant's logic) + two independent live runs (B9: 1 grounded/
  18 unsupported; B12: 2 grounded/17 unsupported, same fixture package,
  two different sets of upstream fixes active) - together they cover
  "the code never wrongly grounds an unsupported claim" thoroughly
  across two real samples, but
  neither individually nor combined is a statistically adequate
  measurement of citation semantic correctness at scale, and the
  evidence-availability question above still has no test at all.

---

## [B10] Two API-surface decisions Part H/G12 don't specify: RBAC action naming and /graph/trace's query shape

- **Discovered:** B10 planning and build. Part H's RBAC matrix has no
  "Graph" row at all (every other G-part surface reuses or extends an
  existing named action; this one had nothing to reuse), and G12's build
  list names `GET .../graph/trace` but neither the PRD nor the Build Guide
  specifies what a "trace" query actually returns for a given node.
- **Behaviour:** `rbac.ACTION_ROLES` gained two new keys not transcribed
  from any locked table: `view_graph` (all 4 read/compute routes - status,
  impact, contradictions, trace - full 7-role breadth, same width as
  `resolve_redteam_finding`, justified by PRD 27.13's own invariant that
  traceability must be accessible from normal workflows, not admin-only)
  and `trigger_graph_projection` (the manual `POST .../graph/project`
  trigger - narrower, same 4-role set as `edit_proposal`, since forcing a
  recompute reads closer to a content-management action than passive
  viewing). Separately, `graph_service.get_graph_trace` was implemented as
  "return this node's immediate neighborhood" (the node itself plus every
  directly connected node/edge, both directions) rather than any bounded
  multi-hop traversal - a reasonable reading of PRD 27.12's "click any
  material decision or claim to inspect its origin," but an invented
  shape, not a transcribed one.
- **Risk:** if a later block (B13's UI, most likely) needs trace results
  in a different shape - e.g. a multi-hop path rather than a 1-hop
  neighborhood, or wants impact-preview-style hop bounding on trace too -
  this will need revisiting, and the RBAC role assignments are a judgment
  call a real product/security review could reasonably override (e.g.
  restricting `trigger_graph_projection` further, or splitting
  contradiction-viewing from impact-viewing into separate actions).
- **Status:** RBAC breadth remains an accepted interpretation, unchanged.
  **The trace-shape question is resolved, §7 step 10, with an actual
  verdict rather than left open until "B13 has a real trace-view
  screen" (this entry's own prior text) - screen 17 now exists, and the
  1-hop neighborhood shape was checked against it directly, not assumed
  adequate.**

  **Verdict: the 1-hop shape was inadequate for the plan's own chosen
  click-to-expand design, confirmed by direct observation, not
  reasoning from code.** Called `graph_service.get_graph_trace` directly
  against real seeded data before building anything: the response
  contained `n` (the requested node's properties) and `neighbors` (each
  with `relationship` and `neighbor` properties) - **no label anywhere**
  for either the node or any neighbor. Click-to-expand requires calling
  `GET /graph/trace?node_label=X&node_id=Y` again for whichever neighbor
  a human clicks, and the response gave no way to know what `node_label`
  value that click should send - not "the shape is minimal but usable,"
  a genuine functional gap for the one screen that needed it.
  **Fixed, not worked around:** `get_graph_trace`'s Cypher now also
  returns `labels(n)` as `node_labels` and `labels(neighbor)` as
  `neighbor_labels` per collected neighbor (`graph_service.py`, one
  `RETURN` clause changed). Re-pinned against the same real data after
  the fix - `node_labels`/`neighbor_labels` now present and correct.
  Screen 17 was built against the fixed shape, with real click-to-expand
  (each neighbor's own label tells the frontend what to query next; a
  small client-side constant mirroring `TENDER_SCOPED_LABELS`/
  `SHARED_LABELS` decides whether to read that neighbor's `id` or
  `knowledge_item_id` as the id to send).
- **Test:** `tests/unit/test_graph_trace_labels.py` still pins the
  label-allowlist behavior. `tests/route/test_graph_routes.py::
  test_get_graph_trace_returns_the_real_requirement_node` now asserts
  `node_labels == ["Requirement"]` and the real node id, not just a 200 -
  the shape gap above would have shown up here as a missing key, not a
  passing-but-uninformative test, had this assertion existed before B13.

---

## [B11] Stop-gate proof covers detector correctness against known defects, not detection on organic pipeline output

- **Discovered:** during B11's own live Docker verification, following up
  a code-review question about whether `critical seeded defect recall =
  1.00` could be misread as "red-team catches defects in real runs."
- **Behaviour:** B11's stop-gate is satisfied by
  `tests/integration/test_redteam_service.py::
  test_all_seven_seeded_defect_categories_are_detected` - a tender where
  all 7 required defect categories are constructed directly via
  repository calls (missing compliance entries, ungrounded claims,
  expired certs, deselected-employee-still-named claims, pricing
  findings, missing submission items, gapped role matches), matching this
  block's own plan (Q4): 4 of the 7 are pipeline-behavior states a
  document generator can't plant, so the ground truth is asserted into
  existence, not produced by a real run. This is the right way to prove
  the detectors are logically correct - it is not a demonstration that
  they fire against a real, organically-drafted proposal.
  The live smoke test's own automatic `red_team` pass, run against a real
  Gemini-drafted proposal, found **zero** findings both times it was run.
  Confirmed empirically, not inferred from code (a first attempt at this
  same check destroyed the live run's own data via the test-teardown trap
  documented above as that entry's 4th incident, and had to be re-run):
  the live tender's drafted proposal contained exactly 2 claims, both
  `is_factual=False` and `grounding_status='not_required'` (generic
  "Company Overview" framing text, e.g. "commitment to delivering
  high-quality..."). `check_unsupported_claims`'s own query
  (`is_factual=True and grounding_status not in ('grounded',
  'not_required')`) correctly skipped both - there was nothing factual
  and ungrounded in this run's output to catch, not a missed detection.
  The reason: this smoke test's fixture is a near-empty placeholder
  document (1 requirement total, same minimal-fixture convention B9/B10's
  own smoke tests use), a structurally much thinner scenario than B9's
  own live run against the full `synthetic_tender_a.py` package (65
  requirements, 22 drafted factual claims, 18 unsupported - see "[B9]
  Drafting mostly produces ungrounded text" above). The manual
  `POST /tenders/{id}/redteam/run` trigger, called after directly seeding
  one `missed_mandatory_requirement` fact, did correctly detect it live
  through the real running API - proving the wiring works end to end, but
  that is still a manually-seeded fact, not organic model output.
- **Risk:** a reader who sees "critical recall = 1.00" without this entry
  could reasonably conclude red-team has been shown to catch defects
  arising naturally from real pipeline runs. It has not. What is shown:
  (1) the 5 check functions are logically correct against defects known
  to exist (the pytest proof), and (2) the full wiring - automatic firing
  after `project_graph`, the manual trigger, gate routing, persistence -
  works against a real Docker stack (the live proof). What is NOT shown:
  that any of the 5 checks, `check_unsupported_claims` specifically,
  actually fires when B9's own kind of organic condition (18 of 22 real
  drafted claims unsupported) is present in a live run. The one existing
  data point in this codebase for that condition (B9's live run) predates
  red-team's existence entirely, so it was never checked against it.
- **Status:** closed by B12's own live verification, exactly the way this
  entry said it would need to be. B12 drove the real
  `synthetic_tender_a.py` package (65 requirements, matching this entry's
  own "substantive fixture" requirement) all the way through to
  `red_team`, and inspected `redteam_findings` directly before anything
  else touched that database - no test suite run against the same DSN in
  between, learning from incidents 1-4 of the environment-traps entry.
  Result: `check_unsupported_claims` fired **17 times** against this
  run's organic Gemini output, and a direct `proposal_claims` query
  (`grounding_status='unsupported' AND is_factual=true`) independently
  counted **exactly 17** - a perfect match, not an approximation. This is
  no longer "the one existing data point predates red-team's existence" -
  it is a real, current, organic-output measurement, taken correctly:
  cross-checked against the database directly rather than trusted from
  the findings endpoint alone, matching this project's own "look, don't
  reason" standard. `check_unsupported_claims` catches what it's supposed
  to catch on real pipeline output. The other 4 checks remain unconfirmed
  on organic output (this run's data didn't happen to contain an organic
  expired-cert/removed-employee/role-gap/graph-contradiction case) - that
  narrower gap is real but distinct from what this entry originally
  flagged, and doesn't need its own entry unless a future run specifically
  goes looking and still finds nothing.
- **Test:** `test_all_seven_seeded_defect_categories_are_detected` (pytest,
  constructed defects, proves detector logic) + the B11 live smoke test
  (wiring, one manually-seeded fact) + B12's own live run (17 organic
  `unsupported_claim` findings, cross-checked 17/17 against a direct DB
  query) - together these now cover both halves this entry originally
  distinguished: detector correctness against known defects, AND
  detection firing on real organic output, at least for this one check.

---

## Standing pattern: a check doesn't actually verify what it claims to - two shapes, "stale column trusted" and "fresh data fetched then ignored"

- **Discovered:** four instances now, found across a B0-B11 catch-up code
  review done just before B12 (per-commit review hadn't been happening, so
  this was one pass over everything at once) and a follow-up audit of that
  review's own "clean" verdicts.
  1. **B11, shape A (stale column trusted)** —
     `graph_service.find_contradictions_for_redteam`'s D7 evidence-coverage
     check trusted `evidence_links.validity_status` verbatim, which is set
     once when the link is created and never revisited as the backing
     certificate ages past its own `valid_until` — exactly the drift the
     expired-certification query sitting right next to it had just freshly
     detected by comparing dates directly. Fixed in B11 itself by
     excluding the flagged evidence row before checking for other valid
     support paths, so the stale column's own bad answer can't feed back
     into the same decision it was just shown to be wrong about.
  2. **`tenders.state_version`, shape A (B7-B10, corrected pre-B12)** —
     the freshness *check* (`graph_service.check_freshness`) was proven
     correct against every *human* mutation (approve, override, select,
     deselect), and that was read as the invariant holding end to end.
     It didn't: `compliance_service.persist_compliance_decision`, the
     automated pipeline's own write path, never bumped the counter at
     all, so a compliance run could change real data while the graph
     kept reporting itself fresh. See the entry above.
  3. **`knowledge_import_batches` progress fields, shape A (B6, corrected
     pre-B12)** — `total_items`/`completed_items`/`failed_items`/
     `status` on the batch row had no writer anywhere outside their
     creation-time defaults; every caller that read them (a progress UI,
     a polling script) got a frozen `0/0/0` regardless of how much the
     import had actually done. See the entry above.
  4. **`evaluate_evidence_coverage`, shape B (D7, found post-B11 by an
     independent audit, not by this file's own earlier search)** — the
     inverse failure, and a distinct mechanism from the first three:
     `queries.evidence_coverage_rows`'s own docstring says it fetches
     `fact.valid_until` *specifically* so the decision function can use
     it, and the Cypher query does return it fresh on every call - but
     `evaluate_evidence_coverage`'s body never reads
     `row["valid_until"]` at all, only `row["knowledge_item_status"]`.
     A certification whose `valid_until` has passed but whose
     `knowledge_item_status` hasn't separately been flipped to
     `archived`/`expired` is scored `fact_is_valid=True`. Only safe in
     practice because the one call path this project's own tests exercise
     (`find_contradictions_for_redteam`, instance 1 above) happens to
     call `find_expired_certification_contradictions` first, via a
     completely separate direct date comparison - a standalone D7
     coverage read would not independently catch this. Not fixed as of
     this entry - see "Status" below.
- **Why this is two shapes, not one, despite both being named under the
  same pattern:** shape A (instances 1-3) is a column that's stale *at
  rest* - nothing kept it current, so reading it later reads old news.
  Shape B (instance 4) is the opposite mechanism - the fresh, live value
  was fetched successfully, on every call, and then the code simply never
  looked at it. Shape B is arguably worse to catch by inspection: a stale
  column at least LOOKS plausible as "probably fine, nobody's touched it"
  once you know to be suspicious of it, but a value that's fetched,
  named, documented as needed, and then silently unused looks like
  nothing is wrong at all - the query is "doing its job." The B0-B11
  catch-up review's own fourth-instance search explicitly grepped for
  shape A (reads of `validity_status`, `verified`, certification/employee
  validity dates, pricing-finding `resolved`, claim `grounding_status`)
  and missed instance 4 for exactly this reason - it was looking for the
  wrong shape.
- **What instances 1-3 (shape A) share, and how they were fixed:** a
  value that looks like live state (a status column, a validity flag, a
  version counter) but has exactly one writer, and that writer either
  only covers *some* of the paths that should update it (instances 2) or
  was never wired up as a writer at
  all (instances 1 and 3). The bug is invisible until something reads
  the value expecting it to be current — the same "confidently wrong, not
  obviously broken" shape as the measurement-tooling and environment-trap
  standing patterns already logged in this file, but this one is about
  denormalized *product* data, not test/verification infrastructure.
- **The fix applied to instances 2 and 3, and worth naming as the default
  going forward:** don't chase every writer that would need to keep a
  denormalized copy current — recompute the value from its real source
  of truth at read time instead (instance 1's own fix already did this
  implicitly; D7's `evaluate_evidence_coverage` was named here as the
  pattern to copy — see the correction below for why that praise was
  itself half wrong). A value with one real writer and N readers that
  recompute it is
  structurally safer than a value with N writers that all have to
  remember to keep it in sync — the second shape is exactly how
  instances 2 and 3 happened. This isn't universal (a counter like
  `state_version` that many different mutations must increment can't be
  "recomputed" the same way a batch's item-status rollup can — instance 2
  was fixed by finding the missing writer, not by removing the column),
  but wherever a column is a pure aggregate/derivation of other rows that
  already carry the real signal, prefer computing it live over trusting a
  stored copy.
- **What this means for future blocks:** when a new invariant is proven
  by demonstrating it against one call site (a live staleness demo, a
  regression test), check whether that call site is representative of
  *every* path that mutates the underlying data, not just the
  easiest-to-drive one to test — instance 2's own root cause was that
  B10's demo happened to use the one call site that already worked. A
  green test against a human-driven action says nothing about an
  automated pipeline that touches the same invariant through a different
  code path, and vice versa.
- **Correction this entry now carries (post-B11, found by an independent
  audit, not this file's own search):** the B0-B11 catch-up review's own
  "clean" verdict on `evaluate_evidence_coverage` — reported as one of the
  four enforcement functions confirmed to "fail closed and run against
  fresh data" — was wrong on this specific point, and that review's own
  fourth-instance search (see instance 4 above) missed it because it
  grepped for shape A (a stale value trusted), not shape B (a fresh value
  fetched and silently ignored). Retracted explicitly here rather than
  quietly widened, per that audit's own standard: a review that reports
  clean incorrectly is worse than one that finds nothing.
- **Status:** accepted as a standing pattern, not something instances 1-3's
  fixes close out. Instance 4 (`evaluate_evidence_coverage`/`valid_until`)
  is NOT fixed as of this entry - recorded here as a verification note
  from a follow-up audit, scoped to getting the finding right before any
  fix is attempted, per that audit's own "verify before fixing" ordering.
- **Test:** each of instances 1-3's own fixes is covered at its own entry
  above; this entry is the pattern note, not a test target itself. No test
  yet for instance 4 - worth one asserting a `knowledge_item_status=
  "active"` fact with a past `valid_until` is NOT scored `fact_is_valid`
  once it's fixed.

---

## Independent audit (pre-B12): triage of everything not fixed in the B12-prep pass

- **Discovered:** a full-repo independent audit run alongside the B0-B11
  catch-up review, covering ground the review's own three parallel passes
  didn't reach. Findings verified as real by code reading (some, like the
  pricing off-by-one and the cross-tenant knowledge-selection hole, by a
  live repro or a git-stash-verified failing test) before any fix was
  attempted. Six were fixed in the pre-B12 pass itself, each with its own
  entry elsewhere in this file: cross-tenant knowledge selection, the
  idempotency-key resource-identity gap, `request_changes` silently
  approving at all six gates, talent evidence never reaching proposal
  grounding, a stale human approval surviving compliance reevaluation, and
  corrected red-team findings never leaving the open set. Everything below
  is the rest - real, not fixed now, each given an honest owning block
  rather than left unassigned by default.
- **Why triage instead of fixing all of it now:** the six fixed above were
  chosen specifically because they'd make B12's own readiness report lie -
  a "this bid is ready" verdict built on a silently-approved gate, a
  phantom blocker, or a stale approval is worse than the report simply not
  existing yet. Everything below doesn't corrupt B12's own output the same
  direct way, and CLAUDE.md's own build-order discipline says fix the
  block that owns the problem, not patch it as a side effect of whichever
  block happens to be reviewing at the time.

### B12 (Readiness and Governance) - readiness-adjacent, not yet corrupting

- **Proposal regeneration retains obsolete claims and references.**
  Regenerating a section replaces its content but only adds new claims and
  junction links (`proposal_service.py`'s persistence, `db/repositories/
  proposal.py`) - claims from a superseded draft stay projected, traced,
  and red-teamable even though nothing in the current proposal references
  them. B12's readiness report will need an honest picture of "what does
  this proposal currently say," and a phantom claim from a prior draft is
  exactly the kind of thing that could feed a wrong blocker or a wrong
  all-clear.
- **`workflow_runs.plan_version` stays zero while the LangGraph checkpoint's
  own `plan_version` advances** (minor finding #4, `supervisor_node.py`).
  Not corrupting today (nothing reads the Postgres column), but B12 will
  likely want to know which planning epoch a run's readiness reflects, and
  a column that never moves would silently look wrong from Postgres alone.
- **Invalid gate decisions produce a bare 500, not 422** (minor finding
  #5, `schemas/gates.py`'s `decision: str` has no enum validation,
  `orchestration/gates.py:status_for_decision` raises `KeyError` on
  anything outside `ALLOWED_DECISIONS`). Slightly more relevant post-B12-
  prep now that a fourth decision value (`request_changes`) is real and
  load-bearing - worth a proper `Literal`/422 the same pass that builds
  B12's own gate-decision UI contract.
- **Open question, not yet a bug: should `red_team_node` failures be
  tracked as a degraded stage, like proposal drafting and graph
  projection are?** Found while checking finding #16's CodeRabbit
  follow-up (whether `_build_llm_client()` sits outside a try block the
  same way it did in `proposal_drafting_node` before that fix).
  `red_team_node` builds its client and calls `run_red_team` with no
  try/except at all - unlike `proposal_drafting_node`/`project_graph_node`
  (swallow, record `degraded_stages`, let the run reach `final_gate`
  anyway) and unlike `compliance_analysis_node`/`talent_analysis_node`/
  `commercial_analysis_node` (their own `workflow_tasks` row, marked
  failed, exception re-raised). An uncaught exception here propagates all
  the way to `run_workflow`'s own top-level handler (added this pass),
  which marks the whole run `failed` - a real, non-silent outcome, just a
  different one: the run stops outright rather than reaching `final_gate`
  in a visibly-degraded state the way a failed drafting/projection pass
  now does. Not clearly wrong - red-team failing outright arguably
  *should* be more serious than drafting or projection failing, since
  red-team is meant to be the thing that catches problems before
  `final_gate`, not one more thing a human reviews around - but this
  wasn't a deliberate design decision, just where the code already stood
  when the question was noticed. **Owner: B12**, since `degraded_stages`
  only exists for B12's own future readiness report to read, and deciding
  whether a third pattern belongs alongside "swallow + surface" and "task-
  level failure" is a call B12 should make deliberately, not inherit by
  default from whichever shape B11 happened to leave it in.

### B13 (Functional UI) - already named as the owner of the adjacent import-review screen

- **Knowledge imports are neither retryable nor accurately observable**
  (major finding #10) - **resolved in B13.** The "not accurately
  observable" half was already fixed pre-B12 (`ImportBatchProgress`
  computes live from items - see the standing-pattern entry above). The
  retry half is fixed now: `import_employee_master_row`/
  `import_certification_row` check `get_import_item_by_source_ref` before
  `create_import_item`, so a retried worker job resumes past rows a prior
  attempt already committed instead of crashing on
  `UNIQUE(batch_id, source_ref)`. A failure before any per-row item exists
  (the parse step itself raising) now calls the new
  `record_batch_parse_failure`, which writes one synthetic `failed` item so
  `_compute_batch_progress` reports a real terminal state instead of
  `queued` forever - both paths independently idempotent on their own
  retry. Tests: `tests/integration/test_knowledge_service.py` (4 cases -
  employee-master and certification retry-resume, parse-failure terminal
  status, parse-failure idempotency).

  **Correction, found by CodeRabbit's review of this same diff:** the
  "calls `record_batch_parse_failure`" claim above was true only for
  `EmployeeMasterValidationError` specifically, not for every way the
  parse step could fail. `parse_employee_master_csv`'s own
  `content.decode("utf-8-sig")` raises a bare `UnicodeDecodeError` on a
  non-UTF-8 file (a real path, not contrived - Excel's own "Save As CSV"
  under a non-English Windows locale writes cp1252/latin-1), which
  `knowledge_import.py`'s `except EmployeeMasterValidationError` never
  caught - the worker job died without calling
  `record_batch_parse_failure`, and the batch stayed `queued` forever,
  the exact failure mode this entry claimed was already closed. An entry
  marked resolved that overstates what actually closed is worse than
  leaving it open, since it stops anyone from looking again. Fixed now,
  for real: `parse_employee_master_csv` catches the decode failure
  itself and re-raises it as `EmployeeMasterValidationError`, so every
  caller (this worker job included) still only has one exception type to
  handle - not a second exception type added to `knowledge_import.py`'s
  own catch clause, which would have left the next parser that can fail
  in a new way free to reproduce the same gap. Test:
  `tests/unit/test_employee_master_parser.py::
  test_parse_employee_master_csv_rejects_invalid_encoding`.
- **Required knowledge-import inputs are broken** (major finding #11) -
  **resolved in B13.** Employee-master CSV: `parse_employee_master_csv`
  added (`agents/knowledge/employee_master.py`, shared header-validation
  logic extracted so xlsx/csv can't drift apart), `_validate_upload` now
  accepts `.csv`/`text/csv`, and the upload route determines
  `employee_master_csv` vs `_xlsx` from the actual uploaded filename
  instead of always assuming xlsx. Native Excel date/datetime cells:
  `_parse_date_cell` (`agents/knowledge/certifications_xlsx.py`) checks for
  an already-typed `datetime`/`date` object before ever stringifying -
  `datetime` checked first since it's a `date` subclass. Blank employee
  cell: `import_certification_row` now routes to `needs_review` with a
  message distinct from the "employee_ref given but not found" branch
  (`TODO(human)`, filled in by the user - the specific edge case being
  that the status vocabulary has no value more specific than
  `needs_review`, so the "missing" vs. "not found" distinction can only
  live in the error message text). Tests: `tests/unit/
  test_employee_master_parser.py` (5 cases), `tests/unit/
  test_certifications_parser.py` (5 cases, including a regression test
  built from a real openpyxl `datetime` cell, not a string standing in for
  one), `tests/integration/test_knowledge_service.py` (blank-employee-ref
  routing).
- **Knowledge selection lacks the required `Idempotency-Key`** (minor
  finding #1, `PUT /tenders/{tender_id}/knowledge-selections/
  {knowledge_item_id}`). Worth closing whenever B13 builds the selection
  UI's own request contract.

### B15 (Security and Prompt-Injection Hardening) - already named by the user for these two

- **Upload limits are checked after an unbounded read** (major finding
  #20): `await file.read()` (no size argument) runs before
  `_validate_upload`'s own size check, in both `routes/documents.py` and
  `routes/knowledge.py` - an authenticated user can force the API to
  materialize an arbitrarily large upload in memory before the 50MB limit
  is ever consulted.
- **Malformed LLM output can leak confidential text into logs** (major
  finding #21): pydantic `ValidationError`'s own `input_value` field
  (which can carry source-derived, potentially confidential text) reaches
  `logger.exception` after a repair retry fails, in both
  `agents/llm/gemini_adapter.py` and `agents/llm/client.py`.

### B16/B17 (reliability, crash/restart/load, observability) - concurrency and atomicity gaps

- **`bump_state_version` is read-increment-flush with no row lock**
  (part of major finding #2). Two concurrent transactions can both read
  `state_version=N` and independently write `N+1`, losing an increment.
  The *missing call sites* half of this finding was fixed for the
  automated compliance-origination path this pass (see the state_version
  entry above); this is the separate, still-open *race* half - real under
  concurrent writers, not yet reproduced live (would need two genuinely
  concurrent transactions, not just code reading, to confirm the lost
  update actually happens under this project's real connection pool
  behavior).
- **Idempotency-record acquisition is select-then-insert** (major finding
  #4, `idempotency.py`): concurrent requests with the same key can both
  observe "no existing record" and both proceed, or race into the unique
  constraint. `expires_at` is stored but never consulted by any read path.
- **`knowledge_service.py`'s own import-row idempotency has the same
  select-then-insert shape, at three call sites, not the one above's**
  (found by CodeRabbit's review of B13's own diff). `import_employee_
  master_row`, `import_certification_row`, and the resume-import path
  each do `get_import_item_by_source_ref` then `create_import_item` as
  two separate awaits, not one atomic operation. The retry-resume fix
  this pass made (KNOWN_LIMITATIONS.md's own "Knowledge imports are
  neither retryable nor accurately observable," resolved-in-B13) covers
  SEQUENTIAL retries correctly - a worker job re-processing the same
  batch after a crash resumes past already-committed rows. It does not
  cover TRUE CONCURRENT delivery of the same batch (two RQ workers, or
  one worker's retry racing a still-running original attempt): both
  could observe "no existing item" before either inserts, and the loser
  crashes on `uq_knowledge_import_item_source_ref` instead of returning
  the winner's row. Same root cause as the entry directly above, real
  under this project's own RQ at-least-once delivery model, not yet
  reproduced live (would need two genuinely concurrent deliveries of the
  same batch, not just code reading, to confirm). **Owner: B16.** Not
  B13's own fix, for the same reason the entry above is B16's and not
  the pass that touched `idempotency.py`: an atomic insert-or-return
  (Postgres `INSERT ... ON CONFLICT`, or `IntegrityError` recovery
  inside a savepoint) is a real design decision about how this
  project's concurrency-hardening pass wants to handle every
  select-then-insert site at once, not a one-file patch to make
  quietly during an unrelated block.
- **Tender creation and idempotency-record completion are two separate
  commits** (major finding #7): a crash between them lets a retry create
  a second tender. Real for `create_tender` specifically; some other
  idempotent operations have their own domain-level dedup (fingerprints)
  that would mask the same underlying gap.
- **A failed same-version graph reprojection can leave a damaged graph
  reading as fresh** (major finding #8): Neo4j writes auto-commit
  per-statement in this driver usage; if an optional reprojection at the
  *same* `state_version` partially rewrites the graph and then fails, the
  prior completed run's row is what `get_latest_completed_run` still
  returns, but the graph underneath it may now be the partially-rewritten
  one, not the one that run originally produced.
- **Re-ingestion destroys stable chunk provenance** (major finding #9,
  `jobs/ingestion.py`'s delete-then-recreate of `document_chunks`):
  knowledge-item/requirement provenance pointing at the old chunk ids
  goes through `ON DELETE SET NULL`, and a requirement still referencing
  a chunk can make the delete step itself fail. Concurrent workers
  re-ingesting the same document is a real path (not hypothetical) via
  the ordinary upload flow.
- **Workflow retry controls are inert** (major finding #17):
  `retry_budget_remaining`/`max_attempts` exist in `TenderState`/
  `WorkflowTask` but nothing reads or decrements either, and nothing
  requeues a failed task - one transient provider error fails a task
  outright with no retry.
- **Outbox events are not claimed atomically** (major finding #22,
  `db/repositories/outbox.py`/`outbox_dispatcher.py`): select-then-enqueue
  with no `FOR UPDATE SKIP LOCKED` - two dispatcher instances could both
  claim the same pending event. Lower real-world impact if the deployment
  only ever runs one dispatcher, but nothing in the code enforces that
  assumption.
- **Cancellation-during-execution race, remaining half** (major finding
  #14): this pass fixed the half where `run_workflow`/`resume_workflow`'s
  own new exception handler could overwrite a real `cancelled` status with
  `failed` (re-checks `is_cancelled()` before writing now). Still open: an
  in-flight node's own `sync_run_status` call can itself race a
  concurrent cancellation and write a non-terminal status over
  `cancelled` - needs either a compare-and-swap on `workflow_runs.status`
  or a cancellation check immediately before every such write, not
  reproduced live here.
- **CI, schema verification, worker image, and Compose readiness** (major
  findings #23/#24/#25 - CI provisions no service stack but runs all
  tests; integration tests build schema via `Base.metadata.create_all`,
  which can pass even with broken Alembic migrations, and the existing
  `scripts/verify_migrations.py` isn't run in CI; the worker image doesn't
  bake in the embedding model, so a fresh/offline deployment's first
  ingestion fails downloading it from Hugging Face; Compose has no
  migration/init service, and API readiness checks `SELECT 1` and MinIO's
  unauthenticated liveness endpoint rather than real schema/bucket
  access). Explicitly the user's own call: these are B16/B17's block, not
  pre-work - untouched here on purpose, not overlooked.
- **Compliance's state_version bump is unconditional even on an identical
  semantic retry** (minor finding #6): `persist_compliance_decision`
  bumps once per call regardless of whether `status`/evidence actually
  changed, producing a false-stale graph-freshness signal on a no-op
  re-run. Same root shape as the human_approved fix this pass made
  (distinguish "the decision materially changed" from "the same decision
  was recomputed"), just for `state_version` instead of `human_approved` -
  worth revisiting together with the read-increment-flush race above,
  since both touch the same function.

### No clean owning block named yet - flagged honestly rather than force-fit

- **Evidence links accumulate rather than being pruned on reevaluation**
  (part of major finding #6): a compliance reevaluation adds new
  `evidence_links`/`compliance_evidence` rows via fingerprinted upsert but
  never removes the old ones a prior evaluation attached - superseded
  evidence stays linked to the compliance entry indefinitely. Doesn't
  corrupt correctness the way a stale `human_approved` did (D7's coverage
  check and this pass's own reevaluation fix both operate on current
  status/evidence rows), but it's a real data-hygiene gap with no
  natural home in the current B12-B17 block list - flagging for a
  product/build-order decision rather than assigning it to a block that
  doesn't actually own it.
- **LLM-inferred `WORKED_ON`/`DEMONSTRATES` relationships carry no
  `source_chunk_id`** (major finding #12, `knowledge_service.py`'s
  relationship persistence, contrary to this project's own provenance
  invariant). B6's own scope boundary already accepted a related gap
  (normalized-name-only dedup, no fuzzy matching) as deliberate; this one
  wasn't - it looks like an oversight, not a stated boundary, but no
  future block currently owns "revisit B6 knowledge-relationship
  provenance."
- **`valid_only=True` ignores knowledge-item `status`** (minor finding
  #2, `agents/knowledge/retrieval.py`): an archived/expired-status item
  with no `valid_until` date set stays eligible for retrieval even when
  `valid_only=True` is requested.
- **DOCX headings are omitted from capability/relationship extraction
  prompts** (minor finding #3, `agents/knowledge/extract.py`/
  `relationships.py`): a project or capability name that appears only in
  a heading, never in body text, is invisible to the extraction LLM.
- **Red-team's adversarial critique step receives no linked evidence
  excerpts** (minor finding #7, `agents/redteam/critique.py`), despite
  its own stated purpose including evidence-overstatement detection.
  Moderate uncertainty on real impact - some overstatement is detectable
  from claim text and requirement text alone, without evidence context -
  but the gap between what the critic advertises and what it's actually
  given is real.
- **Every "no clean owning block" item above shares one root cause worth
  naming directly:** each is a genuine post-B_x completeness gap in a
  block that's already closed, with no later block in the current J-part
  list whose own stop-gate depends on it. Recording them here rather than
  inventing a plausible-sounding owner is deliberate - a home that isn't
  real is worse than an honest "unassigned," the same standard this
  file's evaluate_evidence_coverage correction above was held to.
- **Status:** all items in this entry are open, none fixed here. This
  entry is the record of the triage decision itself, not a substitute for
  each item eventually getting its own resolved-in-B_xx update the way
  every other entry in this file does once actually addressed.
- **Test:** none - this is a triage record, not a behavior. Each item's
  eventual fix should get its own test at the point it's actually done,
  matching this file's own convention everywhere else.

---

## Standing pattern: a two-resource guard that never checks the caller's own authorized organization

- **Discovered:** `knowledge_service.set_knowledge_selection` (CodeRabbit
  round 2 of the pre-B12 fixes pass), then confirmed as a repeating shape
  by an explicit audit of every other route with the same two-resource
  structure. **Rewritten during B12's own CodeRabbit-review pass** after
  discovering the first fix, below, had shipped incompletely: instance 1
  was genuinely resolved; instance 2 was not, despite this entry
  previously saying it was.
  1. **`set_knowledge_selection` - genuinely resolved.** The original bug
     compared `knowledge_item.organization_id` to `tender.organization_id`
     - the two RESOURCES to each other - but never to the org the CALLER
     was actually authorized for. A caller legitimately authorized against
     org B who happened to know a `tender_id`/`knowledge_item_id` pair
     that both belonged to a DIFFERENT org C would pass the old guard (the
     two resources agreed with each other) despite never being authorized
     for org C. **First fix attempt** added `organization_id` as an
     explicit parameter, supplied by the route from an independent lookup.
     **That attempt was itself defective** (see instance 2's own history
     below for why) and was replaced before shipping: the function now
     takes no `organization_id` parameter at all. It fetches `tender_id`'s
     own tender, then calls `uow.memberships.get(tender.organization_id,
     actor_user_id)` and checks the membership's role against
     `ACTION_ROLES["manage_organization_knowledge"]` - deriving and
     verifying the caller's real authorization internally, from
     `actor_user_id` and `tender_id` alone, trusting no argument a caller
     might supply.
  2. **`set_candidate_selection` - NOT resolved by the fix this entry
     previously described; genuinely resolved now, during B12.** Audited
     as "found and fixed the same way" as instance 1's *first* fix attempt
     - meaning it inherited that attempt's own defect. `routes/talent.py`'s
     route resolves `organization_id` from `role_id` (via
     `require_permission`), then separately fetches `tender = get_tender(
     role.tender_id)` - using that SAME `role_id` - and passed `tender.
     organization_id` into `set_candidate_selection`, which re-fetched the
     SAME role/tender and compared `organization_id` against itself. The
     comparison logic was correct in isolation (the function's own unit
     test injected a genuinely mismatched value directly and it was
     correctly rejected) - which is exactly why this looked fixed and
     nobody caught it: **the test proved the comparison works, not that
     any real caller could ever produce a mismatch for it to catch.** The
     only real caller (the route) derives both sides of the comparison
     from the identical resource, so the check could never fail in
     practice - functionally identical to not checking at all, the same
     conclusion `AuthorizedActor`'s own docstring in `dependencies.py`
     reaches by name. Found during B12 while updating this very entry to
     reflect instance 1's real fix - re-reading `set_candidate_selection`'s
     own docstring (which claimed parity with `set_knowledge_selection`'s
     "identical parameter," no longer true once that parameter was
     removed) surfaced the mismatch. Not exploitable through the one write
     path that exists today (`evaluate_role_match` only ever creates
     same-org role matches via `find_candidate_pool(organization_id)`) -
     the same non-exploitability caveat this entry has carried since it
     was first written, unchanged by any of this, because nothing about
     *why* it's currently safe ever depended on the guard actually working.
     **Fixed now, the same way as instance 1's real fix:** no
     `organization_id` parameter; derives `role_requirement_id`'s own
     tender, checks `actor_user_id`'s real membership against
     `ACTION_ROLES["select_resource"]`.
- **The transferable lesson, not the individual bug:** a fix can look
  complete because its comparison LOGIC is correct, while the thing that
  actually made the original bug safe-to-exploit - the caller's inability
  to ever supply a mismatched value - remains completely unaddressed. A
  unit test that injects an argument directly, bypassing the only real
  caller, will happily confirm the logic works and say nothing about
  whether that logic is ever reachable. The generic version: a route
  resolves `organization_id` from ONE path resource and stops there
  (`require_permission`'s own contract); handing that value to a service
  function as a security comparison, when the function derives its OWN
  comparison target from that SAME resource, produces a check that cannot
  ever fail - regardless of how correct the comparison itself is. The only
  fix that actually closes this is deriving and verifying the caller's
  real authorization (membership + role) from data the CALLER doesn't
  control, inside the function itself - never trusting a parameter for
  the comparison, no matter how independently it looks resolved upstream.
- **Audit performed twice now - once pre-B12 (three candidates, all
  genuinely clean), once during B12 specifically for this pattern
  (exhaustive over every `organization_id: uuid.UUID` parameter in
  `domain/services/`, no third live instance found):**
  - `POST /gates/{gate_id}/decision`'s `target_updates: list[dict]` -
    confirmed by full-repo grep to have zero write paths anywhere in the
    codebase at the time (now resolved for real in B12 - see the
    dedicated `target_updates` entry above).
  - `POST /tenders/{tender_id}/graph/impact`'s `body.knowledge_item_id` -
    not a service-level gap: `graph_projection/queries.py`'s own Cypher
    requires `MATCH (fact)-[:USED_IN]->(:Tender {id: $tenderId})` before
    returning anything, so a knowledge item from another org can only
    even appear in a result if it was previously selected into THIS
    tender via `set_knowledge_selection` - which (post-fix) already
    enforces same-org. The query's own join is the check.
  - `POST /requirements/merge`'s `body.requirement_ids: list[UUID]` -
    `require_merge_permission` only authorizes off `requirement_ids[0]`,
    but `requirement_service.merge_requirements` independently collects
    every id's own `tender_id` and raises `CrossTenderOperationError`
    unless exactly one tender is represented across ALL of them - a real,
    explicit, whole-list check, not a self-comparison.
  - B12's own exhaustive sweep: every function across `domain/services/`
    taking `organization_id: uuid.UUID` as an explicit parameter was
    checked individually. `document_service.create_document` and
    `tender_service.create_tender` are plain creates (no pre-existing
    resource to compare against - the parameter says which org a NEW row
    belongs to, nothing is verified against it). `gate_service.
    record_decision`, `requirement_service.patch_requirement`/
    `merge_requirements`/`split_requirement`, `workflow_service.
    create_workflow_run`/`cancel_workflow_run` all use it ONLY for
    `audit.create`'s own `organization_id=` attribution field, never in a
    security comparison. `membership_service.add_membership` establishes a
    NEW relationship (no existing resource to compare against). None of
    these share the vacuous shape - `set_candidate_selection` really was
    the only other live instance.
- **Status:** resolved for both instances, for real, as of B12.
  `set_knowledge_selection` was already correctly fixed before B12 started
  (this entry's own prior claim about it was accurate); `set_candidate_
  selection` was not, and is fixed now, the identical way.
- **Test:** `test_set_knowledge_selection_rejects_a_tender_outside_the_
  authorized_organization` (`tests/integration/test_knowledge_service.py`)
  and `test_set_candidate_selection_rejects_a_role_outside_the_authorized_
  organization` (`tests/integration/test_talent_service.py`) - both
  rewritten during this same B12 pass to use a REAL registered actor with
  real membership in one org, attempting the write against a role/
  knowledge-item genuinely belonging to a different org, going through
  the function's own real membership derivation rather than injecting an
  argument no real caller could ever produce. The old
  `set_candidate_selection` test (a bare `uuid.uuid4()` actor, an injected
  `organization_id` argument) is exactly the shape of test this entry's
  own transferable lesson warns about, and has been replaced, not just
  left alongside the new one.

---

## [B12] `routes/knowledge.py`'s `set_knowledge_selection` route called the service with a stale 6-argument shape - broken on every call

- **Discovered:** while rewriting the entry above, tracing
  `set_knowledge_selection`'s real current signature to describe it
  accurately. The route (`PUT /tenders/{tender_id}/knowledge-selections/
  {knowledge_item_id}`) still called `knowledge_service.
  set_knowledge_selection(tender_id, knowledge_item_id, body.selected,
  current_user.id, tender.organization_id, body.change_reason)` - six
  positional arguments - against a function whose real signature (fixed
  before B12 started, per the entry above) takes exactly five
  (`tender_id, knowledge_item_id, selected, actor_user_id, change_reason=
  None`). `tender.organization_id` landed in the `change_reason` slot and
  `body.change_reason` had nowhere left to go.
- **Behaviour:** `TypeError: set_knowledge_selection() takes from 4 to 5
  positional arguments but 6 were given`, raised unconditionally on
  every single call to this route. The endpoint has been completely
  non-functional since whichever commit dropped the `organization_id`
  parameter from the service function without updating this one call
  site. Never caught because every integration test in this codebase
  calls the service function directly (`await knowledge_service.
  set_knowledge_selection(...)`), never through the route - this
  codebase has zero FastAPI TestClient/route-level tests (see the
  logging-configuration entry above for the same "nothing exercises this
  layer" shape, different layer). No live smoke test in any block
  (B6 through B12) happened to exercise real knowledge-selection changes
  through the actual HTTP route either - every smoke test's own selection
  changes went through direct `UnitOfWork`/repository calls instead.
- **Risk:** was P0 in effect (a core, PRD-listed write path - "knowledge
  selection change" is one of Part C's six state-version-bump mutation
  types - completely unreachable via its only real route) but is not a
  data-integrity risk in the way the entry above is: nothing was ever
  written wrong, because nothing could be written at all.
- **Status:** fixed - the route now passes the correct five arguments,
  matching the real signature. General lesson worth naming explicitly:
  a signature change to a function with real callers needs every call
  site verified, not just the ones a grep for the function name happens
  to surface in the file someone's already looking at - this one hid in
  a route file nobody was editing when the signature changed underneath
  it, and stayed hidden because nothing at the route layer is tested.
- **Test:** none added - closing this gap for real means route-level
  (TestClient) tests, which don't exist anywhere in this codebase yet;
  out of scope to introduce as a side effect of one bug fix. Worth
  flagging for whichever block first adds route-level testing
  infrastructure: this specific class of bug (a correct service function,
  a stale caller) is invisible to service-level tests by construction.

---

## Route-level test coverage was uneven, not absent - `knowledge.py` specifically had none, which is how a completely dead route survived twelve blocks

- **Discovered:** the entry directly above (`set_knowledge_selection`'s
  stale 6-argument call site) is the symptom; this entry originally named
  the structural cause as "no route-level test coverage exists anywhere
  in this codebase," per a targeted audit performed right after B12's
  CodeRabbit-fixes round.
- **Correction (B13 §7 step 5 planning, before writing more route tests):**
  that claim was false as written, and had been false since before B13
  started - not something that became true only once B13 began adding its
  own `tests/route/` coverage. `tests/integration/test_documents_api.py`,
  `test_workflow_api.py`, `test_cross_org_access.py`, `test_idempotency.py`,
  and `test_requirements_api.py` all already drove the real ASGI app via
  `httpx.AsyncClient`/`ASGITransport` - genuine HTTP-level tests, not
  service-function calls - predating B13 entirely. B13's own original
  planning inventory (§3a) checked route decorators and which screen
  consumes which route, but never opened these test files' actual
  contents, so it inherited this entry's overgeneralization instead of
  correcting it. Found only once a full, deliberate re-audit was done
  (grepping every test file for `ASGITransport`/`AsyncClient`/`TestClient`
  usage and extracting every real path each one calls) rather than
  rediscovering the gap module by module.
- **The corrected picture, per module (71 routes, 15 modules):**
  - **Fully covered already, before or independent of B13's own work:**
    `documents.py` (4/4, `test_documents_api.py`), `requirements.py`
    (6/6, `test_requirements_api.py`).
  - **Fully covered, mixed pre-existing + B13:** `auth.py` (4/4, all
    B13/`tests/route/`), `workflow.py` (5/5 - create+cancel pre-existing
    in `test_workflow_api.py`, get/tasks/events added by B13),
    `organizations.py` (3/3 - create_tender/list_tenders pre-existing,
    `add_membership` net-new B13), `tenders.py` (2/2 - `get` pre-existing,
    `patch` net-new B13 - and `patch` is where B13 found the
    `DetachedInstanceError` 500-on-every-call bug, in a route this
    project had never route-tested until then).
  - **Partially covered, real gaps remain:** `gates.py` (1/3 - `POST
    .../decision` covered by `test_workflow_api.py`; `GET
    /workflow-runs/{id}/gates` and `GET /gates/{id}` have no HTTP
    coverage anywhere), `graph.py` (1/5 - `POST .../graph/project`
    covered by `test_idempotency.py`, idempotency-focused rather than
    exhaustive; `status`/`impact`/`contradictions`/`trace` have none).
  - **Zero coverage anywhere, confirmed by grep, not assumed:**
    `compliance.py` (5), `talent.py` (3), `commercial.py` (4),
    `proposal.py` (4 - including `PATCH /proposal/sections/{id}`, the
    named suspect for the same `onupdate`/detached-session bug shape as
    `tenders.py`'s), `redteam.py` (3), `readiness.py` (2), `knowledge.py`
    (18 - **the module the actual dead route lived in**).
  - Arithmetic: 26 of 71 routes had real HTTP coverage before this
    correction (24 from fully-covered modules + the 2 partial hits in
    gates/graph); 45 did not.
- **Why `knowledge.py` specifically having none is the real explanation,
  not "route testing doesn't happen here":** every other early-covered
  module (documents, requirements, workflow, cross-org access,
  idempotency) happens to be exactly the surface those five pre-existing
  test files were written to prove something about - upload validation,
  requirement review, workflow lifecycle, tenant isolation, idempotency
  semantics. `knowledge.py` - a large, 18-route module - never had a test
  file written with that same "drive it over real HTTP" intent, for
  reasons this file has no record of. The dead route's own bug (a stale
  6-argument call against a 5-argument function) could only have been
  caught by a route-level test of `knowledge.py` specifically; the fact
  that *other* modules had such tests never had any chance of catching it.
- **Risk:** unchanged from the original entry - any future signature
  change to a service function with a real route caller carries this risk
  for every route not specifically exercised by a route-level test, and
  the corrected inventory above shows that's still most of them (45 of
  71) even after this correction.
- **Status:** **owner still B13**, unchanged - the correction is to the
  diagnosis, not the assignment. B13's own `tests/route/` work continues
  against the corrected inventory above: build coverage for the 45
  routes that genuinely lack it, not the ones that already have it via a
  different (equally real) test file. Full-module scope stays the
  standard - a module already fully covered by a pre-existing
  `test_*_api.py` file needs no new `tests/route/` file of its own.

  **Update, §7 step 6: `knowledge.py` now has real coverage (18/18,
  `tests/route/test_knowledge_routes.py`), and it's worth recording why
  full-module scope was the right call here specifically, not just
  screen-driven. Building this file's own test fixtures hit a bug of the
  identical shape to `set_candidate_selection`'s own old test** (this
  entry's sibling above, `:2024`): a helper seeded `knowledge_import_
  batches.created_by` with a bare `uuid.uuid4()` instead of a real
  registered user's id - state no real caller could ever produce, since
  `created_by` is a genuine FK to `users`. The difference is what caught
  it: `set_candidate_selection`'s old test called the SERVICE function
  directly with a hand-built argument, so nothing ever checked whether
  that argument was real - the vacuous check passed silently for however
  long it stood. This one went through the real HTTP route and real
  Postgres constraints (`ForeignKeyViolationError` on the actual insert),
  so it failed loudly on the first run instead of asserting nothing was
  ever wrong. Route-level tests don't just cover more surface than
  service-level ones - they can't accidentally launder a fake actor
  through a real foreign key the way a hand-built service call can. Fixed
  in the test itself (threaded `register_actor`'s own real `user_id`
  through), not the product - this one was purely a test-fixture defect.

  **Second update, same step: the live-browser check on screen 3 found
  two real bugs that route-level coverage itself structurally cannot
  reach.** `process_knowledge_document_import` (the worker job
  `KnowledgeDocumentUploaded` dispatches to) had zero test coverage at
  any level - not route, not integration, not unit - before this step,
  and both bugs it turned out to have (the unconditional `DocumentUploaded`
  firing in `document_service.create_document`, and its own unconditional
  `parse_and_embed_document` call before branching on import type) sit
  entirely past the HTTP boundary. `tests/route/test_knowledge_routes.py`
  passing 19/19 the first time it was written proved every route accepts
  a valid upload and returns the right shape - it says nothing about what
  happens to that upload once a real worker, on a real queue, actually
  processes it, because nothing in a route test's own contract reaches
  that far. This block has now found real bugs in three distinct layers
  by three distinct methods: route tests (test_knowledge_routes.py
  itself - HTTP shape, RBAC wiring, request/response contracts),
  browser-against-real-stack verification (this block's own CORS finding,
  origin/preflight behavior no route test can exercise), and a real file
  through a real worker (this pair of bugs - queue dispatch and worker-
  side branching). None of the three is a superset of the others, and
  none would have caught what the others did - which is the actual,
  concrete argument for why B13's own stop-gate is the full Playwright
  E2E scenario (§1) and not "the unit/integration/route suites all pass."
  A green `uv run pytest` was true at every point during this bug's
  entire lifetime, start to finish.

  **Third update, §7 step 7: `compliance.py` (5/5), `talent.py` (3/3),
  and `commercial.py` (4/4) now have real coverage** (`tests/route/
  test_compliance_routes.py`/`test_talent_routes.py`/
  `test_commercial_routes.py`) - all three previously zero. Unlike
  `knowledge.py`, this pass found no bugs - all 21 tests passed on the
  first run. Recorded plainly rather than only ever reporting the passes
  that found something: a clean full-module pass is itself real
  information (these three modules' routes wire correctly to their
  services), not a sign the exercise wasn't worth doing - it was the only
  way to know that, not an assumption going in.

  **Fourth update, §7 step 8: `graph.py` (5/5) now has real coverage**
  (`tests/route/test_graph_routes.py`) - previously 1/5 (only `POST
  .../graph/project`, idempotency-focused). Both RBAC actions
  (`view_graph`, `trigger_graph_projection`) were already correctly
  scoped by B10 - checked, not assumed, no fix needed. This is also the
  file that surfaced the Neo4j-driver event-loop entry immediately
  above - real test-infrastructure work, not a product bug, but the
  reason this module took real debugging to get to 5/5 rather than
  passing clean like step 7's three modules did.

  **Fifth update, §7 step 9: `proposal.py` (5/5, including B13's own new
  claims-list route) now has real coverage** (`tests/route/
  test_proposal_routes.py`) - previously 0/4. This module is also where
  the DetachedInstanceError entry immediately below was found - the
  fourth distinct real bug this block's route testing has caught (after
  the four `request_id` duplicate-upload sites in §7 step 6, the two
  live-browser-check bugs in `document_service.py`/`knowledge_import.py`,
  and the Neo4j test-infrastructure issue in step 8 - this one, unlike
  that last one, is a real product bug, not test-only).

  **Sixth update, §7 step 10: `redteam.py` (3/3) and `readiness.py`
  (2/2) now have real coverage** (`tests/route/test_redteam_routes.py`,
  `tests/route/test_readiness_routes.py`) - both previously 0/3 and 0/2.
  `redteam.py` is where the `GraphStaleError` crash entry below was
  found - the fifth distinct real bug this block's route testing has
  caught. `readiness.py` passed clean on its first run: `evaluate_
  readiness` already caught the identical `GraphStaleError` correctly
  (`readiness_service.py:322-329`, turning it into a real
  `stale_graph_projection` blocker rather than crashing) - checked and
  confirmed by the route test asserting that blocker category actually
  appears on a fresh tender's generated report, not just that the route
  returned 200. Also this step: `GET /proposal/claims/{id}/trace` and
  `GET /compliance/{id}/trace` moved off `edit_proposal`/`approve_
  compliance` onto the new `view_proposal_trace`/`view_compliance_trace`
  (both 7-role) - both existing test modules gained a new test asserting
  the *target* RBAC state (a role outside the old narrow action
  succeeding on the trace route specifically), not just a re-run of the
  existing admin-path tests.
- **Test:** the inventory above is itself the audit; no automated test
  asserts route coverage completeness (that would need something like a
  coverage-of-coverage tool, not attempted here).

## [B12] Neither app calls `logging.basicConfig()` anywhere - every `logger.info()` call in the entire codebase is silently dropped

- **Discovered:** during B12's own live verification of `gate_target_
  service.apply_target_updates`'s ownership check. A `logger.info(...)`
  line was added specifically so a live run could confirm the check
  executed against real ids (the user's own explicit ask, not something
  self-initiated). The live run applied 96 real `target_updates` entries
  (84 `compliance_entry` + 4 `role_match` + 8 `pricing_finding`) through
  the real API - confirmed by the actual DB state afterward (`compliance_
  decisions_awaiting_approval: 0`, `resource_gaps: 0`, `commercial_
  findings_open: 0` in the readiness report) - but `docker logs
  tenderos-api-1 | grep "target ownership verified"` returned zero
  matches. Checked both apps directly: neither `apps/api` nor `apps/
  worker` calls `logging.basicConfig()`, `logging.config.dictConfig()`,
  or sets a root logger level anywhere. Python's own default behavior
  applies - the root logger sits at `WARNING` with no handler attached,
  so any `logger.info()` call anywhere in either app is silently
  discarded before it reaches any output stream, container-captured or
  otherwise.
- **Behaviour:** every `.exception()`/`.warning()`/`.error()` call in
  this codebase DOES surface (they clear the default `WARNING`
  threshold via Python's own "handler of last resort") - which is
  exactly why worker logs already looked informative during every prior
  block's own live verification (`logger.exception(...)` calls in
  `red_team_node`, `proposal_drafting_node`, `project_graph_node`, etc.
  all show up). But every `.info()` call - and there are many across this
  codebase, most written as the kind of "here's what happened, for
  visibility" line this new one was - has never once been emitted,
  in any block, since B0. This was never caught before because no prior
  block's own live verification specifically needed to observe an
  `.info()`-level line as its proof; every prior live-verification step
  either checked real API/DB state directly or relied on an `.exception()`
  call surfacing on the failure path.
- **Risk:** none for correctness - every write path this project has
  live-verified (B7 through B12) has been proven by checking real
  database/API state, never by trusting a log line, so nothing has
  actually depended on `.info()` output being real. The risk is purely
  operational: in a real deployment, an operator watching container logs
  for `.info()`-level visibility into what the system is doing (which
  request touched which resource, which specialist ran, etc.) would see
  none of it - only exceptions and warnings. B12's own PRD-derived
  "cost and trace views for developers/admins" (Build Guide, "Demo
  deployment" list) and B14's live-progress work both implicitly assume
  some of this visibility exists.
- **Status:** not fixed here - out of B12's own scope (nothing in its
  build list or stop-gate concerns application-wide logging
  configuration), and the fix is a small, generic one (`logging.
  basicConfig(level=logging.INFO)` or an equivalent structured-logging
  setup) that belongs wherever this project's observability/production-
  readiness work actually lands, not bolted onto this block as a side
  effect of one line needing to be visible. For THIS block's own
  verification need, the behavioral proof (96 real target_updates
  entries correctly applied, confirmed via actual before/after DB state,
  matching exactly what "the ownership check passed for every one of
  them" requires - any single failure would have aborted the whole
  batch per `apply_target_updates`' own all-or-nothing validation pass)
  stands on its own without the log line, and is arguably a stronger
  proof than a log message would have been anyway.
- **Test:** none automated - this is a codebase-wide configuration gap,
  not a per-function defect; whichever block adds real logging
  configuration should add a smoke-level test asserting an `.info()`
  call actually reaches a configured handler, so this class of gap
  cannot silently regress again.

---

## [B12] Neo4j driver has no explicit request timeout - relies entirely on library defaults

- **Discovered:** during CodeRabbit's review of `readiness_service.py`'s
  `check_freshness` call (B12's blocker 7, stale-graph-projection check) -
  asked to confirm the Neo4j driver applies a request timeout on that
  call, since without one it could block indefinitely.
- **Behaviour:** checked `graph_projection/driver.py` directly - the
  module-level driver is constructed as `AsyncGraphDatabase.driver(
  settings.neo4j_uri, auth=(...))` with no `connection_timeout`,
  `connection_acquisition_timeout`, `max_transaction_retry_time`, or any
  other timeout parameter passed. The `neo4j` Python driver does apply
  its own built-in defaults when none are given (`connection_timeout`
  ~30s, `connection_acquisition_timeout` ~60s) - so a genuine Neo4j
  outage or unreachable host does NOT hang the calling request forever,
  but a real failure can still take close to a minute to surface before
  the driver gives up and raises, on every code path that touches Neo4j:
  `check_freshness` (now caught broadly by readiness_service.py's own
  CodeRabbit-review fix, degrading gracefully into a blocker), but also
  `project_tender`, `find_contradictions`, `get_graph_trace`, and every
  other `graph_service.py` function that opens a Neo4j session - none of
  which have been individually audited for how well they tolerate a
  minute-long stall on their own request path.
- **Risk:** not a correctness bug - nothing silently returns wrong data,
  and B12's own fix means a Neo4j outage degrades readiness rather than
  crashing it. The risk is purely a latency/availability characteristic
  nobody has deliberately decided on: an API request that happens to hit
  a stalled Neo4j call (graph read routes, `project_graph_node`,
  `readiness_generation_node`) could sit for up to roughly a minute
  before failing, which is a real user-facing hang even though it
  eventually resolves. Whether that's acceptable, or whether it needs a
  shorter explicit timeout (at the cost of more false "unreachable"
  verdicts on a genuinely slow-but-alive Neo4j under load), is a real
  product/ops tradeoff, not a bug to silently patch with an arbitrary
  number.
- **Status:** not fixed - out of B12's own scope (a driver-wide
  configuration decision, not specific to the readiness blocker that
  surfaced it). **Owner: B16 (Full Evaluation, Reliability, and Load
  Tests)**, not B15 and not B17 - B16's own build list names both
  "latency" (its scorecard) and "Neo4j wiped and rebuilt" / "provider
  timeout" (its reliability tests) explicitly; deciding the right
  timeout value, and confirming request-path behavior under a real
  Neo4j outage, is exactly what that block's reliability-test pass is
  for. B15 (Security and Prompt-Injection Hardening) and B17 (UI
  Polish, Observability, and Deployment) don't fit - neither's build
  list touches driver-level timeout/resilience configuration.
- **Test:** none - this is precisely the gap. Worth a reliability test
  in B16 mirroring its own "Neo4j wiped and rebuilt" scenario: assert a
  request touching Neo4j fails within a bounded, deliberately-chosen
  time rather than whatever the library defaults happen to be.

---

## [B13-prep] LangGraph checkpointer persists no reconstructable content

- **Discovered:** read-only forensics against the live `checkpoints`/
  `checkpoint_blobs`/`checkpoint_writes` tables, done specifically to
  characterize what was lost in incident 7 above (the B12 data wipe)
  before concluding it was unrecoverable.
- **Behaviour:** `TenderState` (`orchestration/state.py`) declares exactly
  21 fields - ids (`tender_id`, `organization_id`, `workflow_run_id`,
  `readiness_report_id`, ...), counts (`revision_count`,
  `retry_budget_remaining`), a status string, and lists of ids
  (`dispatch_task_ids`, `open_critical_finding_ids`, ...). No field of any
  kind holds document text, drafted claim text, evidence excerpts, or the
  grounded-context facts handed to the drafting model. Verified directly
  against real persisted checkpoint rows in 3 separate threads (not
  inferred from the schema declaration alone, using LangGraph's own
  `JsonPlusSerializer` to decode the actual msgpack blobs): the
  `proposal_drafting` node's own checkpoint write, in every thread
  checked, touches exactly one channel - `branch:to:project_graph`, an
  internal LangGraph routing marker with no relation to `TenderState` at
  all. Every other field at that checkpoint held exactly what the schema
  promises and nothing more.
- **Risk:** this is not a gap that showed up as a symptom - it's a
  structural fact about how much a "durable checkpoint" actually
  preserves, worth naming explicitly because durability is easy to
  over-read as "there's a forensic trail." There isn't one, for content.
  Concretely: **a wiped Postgres database and a drafting run that never
  happened are indistinguishable in this project.** The checkpoint history
  that survived incident 7's wipe (12,700+ rows across 226 threads) could
  not reconstruct one word of what the B12 run's drafting model actually
  produced or was shown - only that some run with some ids happened, at
  some step, reaching some status.
- **Status:** accepted as a structural fact, not a bug - LangGraph's
  checkpointer is doing exactly what its own design promises (durable
  *state*, meaning the declared TypedDict fields, not an audit log of
  every value a node ever touched). The absence of a separate audit
  mechanism is the actual gap - see the next entry.
- **Test:** none automated (this is a characterization of existing
  behavior, not a regression to guard). The forensics session's own
  cross-thread verification (3 threads, `proposal_drafting`'s write
  checked in each) is the standing evidence.

---

## [B13-prep, owner B16] Nothing persists what the drafting model was actually shown, per section, per revision

- **Discovered:** alongside the checkpointer-content finding above, while
  designing the traceability drawer's `currently_in_scope_evidence` (this
  pass) and recognizing it can only answer "what's in scope *now*," never
  "what was *actually* handed to the model on the run that produced this
  claim."
- **Behaviour:** `_build_grounded_context` constructs a `list[PreparedFact]`
  per section, per drafting run, and hands it to `draft_section` as prompt
  content - then that list is discarded. Not written to Postgres, not
  written to LangGraph state (confirmed by the entry above), not logged
  anywhere. This is the exact uninstrumented middle step that made B9's
  original citation-correctness question unanswerable from code alone: "18
  of 22 claims unsupported - was the underlying evidence thin, or did the
  model have it and not cite it?" has no way to be settled after the fact,
  because the thing that would settle it (what the model was actually
  shown) is never kept.
- **Risk:** every future citation-correctness investigation hits the same
  wall B9 and B12 both did - forced to re-derive "what would have been
  offered" from current data (which may have changed since) rather than
  read what was actually offered at the time. This pass's
  `currently_in_scope_evidence` (re-derived at view time, explicitly
  labeled as such - see the traceability-drawer entries) is the closest
  thing to a substitute that exists today, and it is explicitly not a
  record.
- **Status:** not fixed - out of scope for a prep pass ahead of B13.
  **Owner: B16 (Full Evaluation, Reliability, and Load Tests).** This
  pass's own refactor (`_prepare_evidence_facts`, shared by
  `_facts_from_compliance`/`_facts_from_team`/`get_proposal_trace`) is a
  natural, cheap hook for B16 to add persistence to - one place that
  already produces the exact fact list, rather than three. Not built now;
  named so B16 doesn't have to rediscover where the hook belongs.
- **Test:** none - nothing to test until the persistence mechanism exists.

---

## [B13-prep, owner B16] Unbounded LangGraph checkpoint growth

- **Discovered:** while enumerating threads during the same forensics
  session, to find the B12 run's own thread among the survivors.
- **Behaviour:** 226 distinct `thread_id`s in `checkpoints`, totalling
  ~12,700 rows across `checkpoints`/`checkpoint_blobs`/`checkpoint_writes`
  as of this session (confirmed via direct count, not estimated). No code
  path anywhere in `apps/worker`, `apps/api`, or `packages/tenderos_backend`
  deletes, archives, or otherwise bounds these tables - every workflow run
  this project has ever driven through Docker, live or test, has left its
  full checkpoint history in place indefinitely.
- **Risk:** purely operational, not a correctness bug - nothing reads stale
  checkpoint data incorrectly. Left alone, storage grows without bound in
  proportion to workflow-run volume, and (per the entry above) every one of
  those rows holds only ids/status, so the growth buys no forensic value
  in exchange for the space - a genuinely bad trade if it's ever measured
  against retaining, say, the drafting-context log the entry above
  describes instead.
- **Status:** not fixed - out of scope for a prep pass. **Owner: B16**,
  same reasoning as the entry above: B16's own reliability/load-test pass
  is where retention policy for infrastructure tables like this belongs,
  not a side effect of unrelated work.
- **Test:** none - the count above (226 threads, ~12,700 rows) is the
  standing evidence of the gap, not a regression check.

---

## [B13-prep, owner B13] Approver has no read path to proposal claim trace

- **Discovered:** while verifying the traceability drawer's RBAC against
  Part H rather than assuming the existing route's binding was adequate.
- **Behaviour:** Part H (`TenderOS-Build-Guide.md`), quoted exactly: `|
  Edit proposal | ✓ | ✓ | | | ✓ | | ✓ |` - Bid manager, Architect,
  Proposal writer, Admin. Not Talent reviewer, Commercial reviewer, or
  Approver. `routes/proposal.py`'s `_EDIT = "edit_proposal"` gates all of
  G10, including `GET /proposal/claims/{claim_id}/trace` - the only
  traceability-drawer route that exists. An Approver, the role that makes
  the `final_gate` decision, has no way to open the trace drawer for a
  claim they're being asked to approve.
- **Why this is not read as an oversight in Part H:** Approver lacking
  `edit_proposal` is coherent on its own terms - the role approves, it
  does not author, and `edit_proposal` is named as an authoring action
  (`PATCH /proposal/sections/{section_id}`, `POST
  /tenders/{tender_id}/proposal/generate` share the same action). What's
  arguably missing is a *read* action, a different, narrower thing than
  adding Approver to an authoring one. PRD 27.13's own invariant
  ("Traceability must be accessible from normal workflows, not hidden in
  an admin-only screen") is arguably satisfied as written even without
  Approver - 3 of the 4 roles `edit_proposal` grants are non-admin.
- **Risk:** an approver approving a bid whose claims they structurally
  cannot inspect sits directly against B13's own functional requirements
  ("traceability is clickable," "no hidden auto-approval") once B13
  actually builds the readiness screen and the `final_gate` approval
  controls around this route. Not yet concrete - B13's UI doesn't exist
  yet - but the gap is exact and named, not theoretical once it does.
- **Status:** not fixed - Part H is locked, and amending a locked RBAC
  table inside a prep pass for a test guard and a drawer field is a scope
  jump this project's own build-order discipline doesn't allow. **Owner:
  B13**, not left unassigned: B13 is the first block where this gap
  becomes concrete (it builds the approval screens that would make an
  Approver actually need this), the same reasoning that already assigned
  the route-level-testing gap to B13. Candidate resolution named, not
  decided: a narrower read-only `view_proposal_trace` action granted to
  the current four roles plus Approver - Part H has precedent for an added
  row (B10's Graph row, added where nothing existed to reuse). Whether to
  do this, and how, is B13's own call when it designs the approval
  screens.
- **Test:** none - this is an RBAC gap, not a behavior to pin until B13
  decides the resolution.

---

## [B13-prep] Test-database guard verifies a connection built from the same setting, not the connection that deletes

- **Discovered:** during design review of `_guard_test_database`
  (`tests/integration/conftest.py`), the fixture built to close incident 7
  above - flagged before implementation, not found via testing.
- **Behaviour:** `_schema` and `_clean_tables` each independently call
  `create_async_engine(settings.postgres_dsn)` and dispose it locally -
  confirmed by reading both; no shared engine or sessionmaker exists
  between them. `_guard_test_database` opens a third, separately
  constructed connection from the same DSN and asks Postgres directly
  (`SELECT current_database()`) before either of the other two fixtures
  runs - real verification, not a parse of the DSN string, and strictly
  better than trusting config text alone. But it is still three
  independently-constructed connections trusted to agree because they
  share one config value, not one connection verified and then reused -
  the same underlying shape as `set_candidate_selection`'s own two-resource
  guard defect (see that standing pattern entry), scaled down to
  "verifies A, trusts that B agrees with A" rather than "compares A to A."
- **Risk:** low in practice for a plain Postgres connection with no
  proxy/pooler in front of it - a connection's named database and
  `SELECT current_database()` cannot actually diverge within that
  connection's own lifetime, so the residual risk is architectural
  (three separate connections that happen to agree) rather than a live
  exploit path. Would matter more if a connection pooler doing
  virtual-database routing were ever introduced in front of Postgres,
  which nothing in this project's locked stack currently does.
- **Status:** accepted, not fixed - closing it for real means
  restructuring `conftest.py` so `_schema`/`_clean_tables`/the guard share
  one session-scoped engine, out of scope for a guard whose only job is to
  check before anything destructive runs. Named explicitly rather than
  letting the guard read as fully closed when it isn't - the same standard
  this file already holds `evaluate_evidence_coverage`'s "clean" verdict
  correction to.
- **Test:** `_guard_test_database`'s own behavior is verified directly
  (refuses against the dev/live database name, passes against
  `tenderos_test`) - see the environment-traps entry's own "Test" line
  above. No test exists for the residual gap itself, since it isn't
  reproducible against a plain single-instance Postgres setup.

---

## [B13] The test-database guard now exists in two files, with no single source of truth

- **Discovered:** B13 §7 step 1, adding `tests/route/conftest.py`.
  Route-level tests need the identical `_guard_test_database`/`_schema`/
  `_clean_tables` protection `tests/integration/conftest.py` already has -
  pytest's own import mode (neither directory has an `__init__.py`, so each
  conftest-owning directory gets prepended to `sys.path` individually, not
  the repo root) makes a shared `tests/_db_guard.py`-style import fragile
  enough that duplicating the ~30 lines directly into
  `tests/route/conftest.py` was the pragmatic choice at the time, made
  explicitly rather than silently.
- **Behaviour:** `_guard_test_database` (and `_schema`/`_clean_tables`
  alongside it) is now real, working code in two separate files
  (`tests/integration/conftest.py`, `tests/route/conftest.py`), not a
  single function two directories both call. Both copies are correct
  today - each was verified independently (refuses against `tenderos`,
  passes against `tenderos_test`) - but nothing keeps them in sync going
  forward.
- **Risk:** a future edit to the guard's logic (the database-name
  convention, the suffix check, anything about how it verifies the
  connection) that lands in one file and not the other silently restores
  the exact wipe risk this guard exists to close, but only for whichever
  test directory's copy was missed - the same failure shape as this
  file's own "a reader trusts a column its writer never keeps current"
  standing pattern, just applied to test infrastructure instead of product
  data. A third test directory added later (e.g. for B13's own component
  tests, or a future block's tests) would need a third copy, tripling the
  divergence surface.
- **Status:** accepted, not fixed - this pass created the duplication
  deliberately, for a real reason (import fragility), not as an oversight.
  **Owner: B13**, since this pass is what created the second copy.
  Mitigation until consolidated: whenever `_guard_test_database`'s logic
  changes, grep for `_guard_test_database` across the whole `tests/` tree
  and update every match, not just the one file being edited - if the two
  definitions are ever found to disagree, *that* is the failure, not a
  stylistic inconsistency to shrug off. A real fix (a shared fixture module
  both conftest.py files import cleanly) would need either a `tests/
  __init__.py` + explicit `pythonpath`/rootdir configuration in
  `pyproject.toml`, or a pytest plugin - a deliberate test-tooling decision
  for whoever next touches this, not a one-line fix bundled into an
  unrelated change.
- **Test:** each copy's own behavior is independently verified (see the
  entry above and B13's own route-test harness verification) - no test
  exists asserting the two copies stay identical, since that would just be
  a second place for the same drift to hide.

---

## [B13] CORS was never configured - the entire browser-facing surface was unverified by construction through B12

- **Discovered:** B13 §7 step 3's own browser verification (Playwright
  against a locally-run API), required by this project's own "start the
  dev server and use the feature in a browser" standard - the first time
  in this project's history anything has driven the API from an actual
  browser rather than curl, Swagger, or a direct service-function call.
- **Behaviour:** `tenderos_api.main.app` had no `CORSMiddleware` and no
  concept of the web app's origin anywhere. Every browser-originated
  `fetch()` call - register, login, every future screen - failed the
  browser's own CORS preflight check before authentication was even
  considered, with no server-side error at all (the request never
  completed; the browser blocked it client-side).
- **The real finding, not just the fix:** this is a gap in this project's
  *verification method*, not a missing feature nobody got to yet. Every
  prior block's own "live Docker smoke test" standard (B1 through B12)
  verified the running API via `curl`, Swagger's "Try it out", or a
  script's direct HTTP client - and none of those three enforce browser
  origin policy. CORS is specifically a *browser*-enforced mechanism;
  a tool that isn't a browser making the request never triggers a
  preflight check at all, so a stack that had genuinely never had CORS
  configured could pass every single one of B1-B12's own live-verification
  gates without that gap ever becoming visible. The entire browser-facing
  surface was unverified by construction, not by omission - B13 is the
  first block whose own verification method (an actual browser) is even
  *capable* of catching this class of bug, which is the more durable
  lesson than "add the middleware."
- **Fix:** `CORSMiddleware` added to `tenderos_api.main.app`, reading a new
  `web_base_url` setting (`tenderos_api/config.py`) from `.env`'s own
  `WEB_BASE_URL` - already present since B0, never read by any Settings
  class until now. Verified twice, deliberately not just once: first
  against a locally-run `uvicorn` + Vite dev server, then again from the
  real `tenderos-api-1`/`tenderos-web-1` Docker containers (rebuilt, not
  restarted) - the configuration the eventual Playwright E2E gate (§1)
  will actually run against, and not automatically the same thing a local
  dev server proves.
- **Status:** resolved-in-B13.
- **Test:** no automated test - CORS is a browser-enforced policy that a
  server-side `TestClient`/`curl`-based route test cannot exercise for the
  same structural reason this bug survived twelve blocks (neither is a
  browser). The only real coverage is the manual Playwright browser
  verification performed this pass, against both local dev and the real
  containers - worth remembering the next block that touches CORS
  configuration, since a green test suite would not catch a regression
  here.

---

## [B13] `updated_at` (`onupdate=func.now()`) not repopulated after an ORM-tracked UPDATE - `PATCH /tenders/{id}` 500'd on every call since introduction; a second, unfixed instance found by inspection

- **Discovered:** B13 §7 step 3's own route-level test for
  `PATCH /tenders/{tender_id}` - the first time this route was ever
  exercised through real HTTP response serialization; every prior
  integration test called `tender_service.update_tender` directly and
  never touched the Pydantic response layer.
- **The mechanism, named precisely since it can recur anywhere this shape
  repeats:** `TenderRepository.update()` (and several sibling repository
  methods elsewhere) fetch an ORM-mapped row, mutate its attributes with
  plain `setattr`, then `await self._session.flush()`. For an INSERT,
  SQLAlchemy's asyncpg dialect uses `RETURNING` to populate server-side
  defaults (`onupdate=func.now()`, `server_default`) back onto the
  in-memory object automatically. For an UPDATE via ORM attribute mutation,
  it does **not** do this automatically - the column stays unpopulated in
  memory unless the code explicitly calls `session.refresh()` afterward.
  The bug only became visible once something tried to *read* the
  unpopulated attribute *after* the owning `UnitOfWork`'s session had
  already closed (`routes/tenders.py` serializing `TenderResponse`,
  which includes `updated_at`, well outside `tender_service.update_tender`'s
  own `async with UnitOfWork()` block) - triggering
  `DetachedInstanceError` instead of a lazy DB round-trip, since there was
  no live session left to do the round-trip against. Every service-level
  test that called `update_tender` directly and only asserted on
  `name`/`client_name`/etc. never touched `updated_at` and never saw it.
- **Fix:** `TenderRepository.update()` now calls
  `await self._session.refresh(tender)` immediately after `flush()`,
  before returning - the object is fully populated while the session
  that can still do the round-trip is open.
- **Second instance found by inspection, not yet route-tested, not
  fixed - a named suspect, not assumed fine:**
  `ProposalRepository.update_proposal_section` (`db/repositories/
  proposal.py`) has the identical shape - `session.get()`, a `setattr`
  loop, `flush()`, return, no `refresh()` - against `ProposalSection`,
  whose own `updated_at` also carries `onupdate=func.now()`
  (`db/models/proposal.py`). `PATCH /proposal/sections/{section_id}`
  returns `ProposalSectionResponse`, which includes `updated_at` -
  the same failure is expected to reproduce there under the same
  conditions. Not fixed now, deliberately: Proposal workspace's own
  route-test module isn't due until §7 step 9 per this block's own
  sequencing, and confirming it live (rather than fixing on inspection
  alone) is exactly the "verify, don't assume" standard this project
  holds itself to elsewhere. Flagged here so it's fixed as a *confirmation*
  when that module comes up, not rediscovered as a surprise.
- **Audited, not yet exhaustively checked:** every repository method with
  `await self._session.flush()` was inventoried (grep, `db/repositories/
  *.py`) - `compliance.py`, `knowledge.py`, and `requirements.py` each
  already call `refresh()` in *some* of their own update-shaped methods
  (inconsistently - not every flush site in those same files has one),
  against models that also carry `onupdate=func.now()` columns
  (`compliance.py`, `knowledge.py`, `requirement.py`, `proposal.py`,
  `tender.py` models). Which specific methods are safe versus which share
  this bug has only been confirmed for `tenders.py` (fixed) and
  `proposal.py` (suspect, above) - the rest were not individually traced
  end to end. Practice going forward, not a one-time audit: check this
  specifically whenever a route-test module's own `PATCH`/update route is
  reached, the same way step 3 found it - a passing service-level test
  proves nothing about this shape, only a route-level test that actually
  serializes the response does.
- **Status:** resolved-in-B13 for `tenders.py`; **confirmed and resolved
  in §7 step 9 for `proposal.py`** - the prediction held exactly as
  named here: `test_patch_proposal_section_updates_content`'s first run
  raised the identical `DetachedInstanceError`, same mechanism, same
  fix (`session.refresh()` after `flush()`). See the dedicated step-9
  entry below for the fix details; recorded here too so this entry
  reads as closed, not as a stale open item. Unaudited elsewhere
  (`compliance.py`, `knowledge.py`, `requirement.py` models still carry
  the same risk in whichever of their own flush sites lack a
  `refresh()` - not individually re-checked since this entry was
  written).
- **Test:** `tests/route/test_organizations_and_tenders_routes.py::
  test_patch_tender_updates_the_named_fields` (tenders.py) and `tests/
  route/test_proposal_routes.py::test_patch_proposal_section_updates_
  content` (proposal.py) - the two confirmed instances of this shape.

---

## [B13] mypy baseline: 59 pre-existing errors, recorded so "no new errors" stays checkable

- **Discovered:** B13 §7 step 3's own report claiming "no new mypy errors"
  after the CORS/config changes - a claim that was only checkable by
  memory (comparing against a number carried in the reporting session's
  own context) until this entry existed.
- **Behaviour:** `uv run mypy` reports 59 errors across 12 files as of
  this pass, none introduced by B13's own work so far (confirmed by
  content match against the pre-B13 baseline, not just count match, for
  the errors re-observed in `apps/worker/src/tenderos_worker/jobs/
  knowledge_import.py`). Pre-existing, not enumerated individually here -
  this entry exists so the count itself is a durable, checkable fact
  rather than something each future report has to re-assert from memory.
- **Status:** accepted baseline, not fixed. Re-check this count (`uv run
  mypy`, compare to 59) at the end of B13 rather than trusting "none new"
  claims made along the way without a recorded number to check against.

  **Re-checked, §7 step 5:** `uv run mypy .` now reports **63 errors
  across 14 files** (up from 59/12). Verified directly, not assumed: zero
  of the 63 are in any file this step touched (`schemas/gates.py`,
  `schemas/readiness.py`, `rbac.py`, `orchestration/nodes/final_gate.py`
  - all clean) - the one `routes/gates.py` error this step's own work sits
  next to (`routes/gates.py:148`, the pre-existing `readiness.blockers if
  readiness is not None else []` union-attr issue) was already present
  before this step and is unchanged by it. The +4/+2-files delta is real
  and NOT independently explained here - the 59-error baseline was never
  enumerated file-by-file in this entry (only spot-confirmed by content
  match for `knowledge_import.py`), so there is no stored list to diff
  the current 63 against line-by-line. The new errors sit in
  `redteam_service.py`, `rerank.py`, `retrieval.py`, `extract.py`, and
  `routes/knowledge.py:234` (this last one in the exact module §7 step 6
  is about to touch) - none of which any B13 session work (this one
  included) has edited yet. Recorded honestly as an unexplained delta,
  not attributed to a guessed cause - do not assume "pre-existing and just
  never counted before" without checking, and do not assume "newly
  introduced by something in this session" either.

  **Re-checked again, §7 step 6:** still **63 errors across 14 files**
  (303 source files checked, up from 301 - the two new test files this
  step added are themselves clean). `routes/knowledge.py` was touched
  extensively this step (the `request_id` fix, the new `GET /employees/
  {id}` route) - its one pre-existing error (the `tuple[KnowledgeImportItem,
  UUID]`/`tuple[UUID, UUID]` mismatch, now at line 253) is unchanged by
  any of it. `document_service.py` and `knowledge_import.py` (both edited
  for the live-browser-check bugs) are clean. Count stable at 63 across
  two full steps of real editing in the exact files nearest the error
  list - the delta from 59 remains unexplained, not newly grown.

  **Re-checked again, §7 step 7:** still **63 errors across 14 files**
  (306 source files checked, up from 303 - the three new route test
  files this step added, `test_compliance_routes.py`/
  `test_talent_routes.py`/`test_commercial_routes.py`, are themselves
  clean). No product file touched this step (`schemas/compliance.py`
  wasn't edited - compliance.py's routes/schema needed no changes) added
  any new error either. Count stable at 63 across three full steps now.

  **Re-checked again, §7 step 8:** still **63 errors across 14 files**
  (307 source files checked, up from 306 - `client.ts`'s frontend
  additions and `GateDecisionPanel.test.tsx` aren't mypy's concern; the
  one new Python file this step's own scope touched directly,
  `routes/knowledge.py`'s Idempotency-Key addition to
  `set_knowledge_selection`, sits in the same file as the pre-existing
  `tuple[KnowledgeImportItem, UUID]` error and doesn't add to it). Count
  stable at 63 across four full steps now - the unexplained 59-to-63 delta
  from step 5 remains exactly that, unexplained but not growing.

  **Re-checked again, §7 step 9:** still **63 errors across 14 files**
  (308 source files checked, up from 307 - `tests/route/
  test_proposal_routes.py` is the only new Python file this step added,
  and it's clean). The three product files this step actually edited
  (`schemas/proposal.py`, `routes/proposal.py`, `domain/services/
  proposal_service.py`, `db/repositories/proposal.py`) added zero new
  errors - confirmed directly, not assumed, since this step also fixed a
  real bug in `db/repositories/proposal.py`
  (`update_proposal_section`'s `DetachedInstanceError`, see its own
  KNOWN_LIMITATIONS entry) and mypy's static checker doesn't catch that
  class of runtime error anyway, so a clean mypy pass on this file both
  before and after the fix is expected, not evidence the fix was
  unnecessary. Count stable at 63 across five full steps now.

  **Re-checked again, §7 step 10:** still **63 errors across 14 files**
  (310 source files checked, up from 308 - `tests/route/
  test_redteam_routes.py` and `tests/route/test_readiness_routes.py`
  are the two new Python files this step added, both clean). Every
  product file this step touched (`rbac.py`, `routes/proposal.py`,
  `routes/compliance.py`, `redteam_checks.py`, `graph_service.py`,
  `tests/route/conftest.py`) added zero new errors. Count stable at 63
  across six full steps now.

  **The 59-to-63 delta, actually explained (post-B13, CodeRabbit-fix
  pass):** never a real drift, and not still unexplained - the six
  "stable" re-checks above were carrying forward a step-5 invocation
  change nobody noticed. `pyproject.toml`'s own `[tool.mypy] files =
  ["apps/api/src", "apps/worker/src", "packages/tenderos_backend/src"]`
  is what CLAUDE.md's documented `uv run mypy` (bare, no path argument)
  actually scans - 234 source files, 59 errors, 12 files, reproduced
  exactly by running it fresh just now. Step 3's original 59/12 baseline
  used that bare command. Step 5's re-check used `uv run mypy .` instead
  - the literal `.` argument overrides `files` and scans the whole repo
  from cwd, including `tests/` and the top-level `scripts/` directory,
  neither of which the config's own `files` list names. Reproduced
  exactly too: `uv run mypy .` returns 63/14/310, matching every
  "still 63" re-check through step 10 to the digit. The extra 4 errors
  are `scripts/run_retrieval_eval.py`/`run_compliance_eval.py` (2 files,
  2 errors each, an `EmployeeMasterRow`/`CertificationRow` mix-up in
  both eval scripts) - real errors, just outside the scope
  `uv run mypy` was ever supposed to check, and never part of the
  59-error baseline to begin with. **Canonical baseline going forward:
  59 errors, 12 files, via bare `uv run mypy`** - the command CLAUDE.md
  actually documents. `uv run mypy .`'s wider 63/14 is a real, separate,
  also-stable number for the whole-repo scope (including the two eval
  scripts), not a second baseline to reconcile against this one -
  whichever command a future check runs, name it, so the count is
  checkable against the matching invocation rather than compared across
  two different scopes as if they were the same measurement.
- **Test:** none - this is a tooling-output count, not a behavior.

---

## [B13] `GateResponse.targets`/`.summary` and `ReadinessReportResponse.counts`/`.blockers` were untyped `dict`s - resolved

- **Discovered:** B13 §7 step 5 planning, before the shared gate-control
  component could be built against a real contract instead of a runtime
  shape someone would otherwise have to read `gate_target_service.py` to
  learn.
- **Behaviour:** `schemas/gates.py`'s `GateResponse.targets: list[dict]`/
  `.summary: dict` and `schemas/readiness.py`'s `ReadinessReportResponse.
  counts: dict[str, Any]`/`.blockers: list[dict[str, Any]]` were an
  interpretation of whatever the service happened to write, not a
  contract the database enforced (both are plain JSONB columns). Read
  every real producer directly rather than assuming uniformity from two
  (`orchestration/nodes/compliance.py`, `talent.py`, `commercial.py`,
  `requirement.py`, `redteam.py`, `final_gate.py`) - **six gate_types
  exist, not the five the B13 plan's own §8a draft assumed** (
  `finding_resolution`/`redteam.py:finding_gate_node` was missing from
  that first pass) - worth recording as a hit for "don't generalize a
  shape from two examples" rather than quietly absorbing. `targets` turned
  out to be one shape everywhere (`{target_type: str, target_id: UUID}`);
  `summary` is five plain all-int dicts (different key sets per gate_type)
  plus one structurally different snapshot (`final_readiness`, carrying
  `ready`/`blockers`/`counts`/`degraded_stages`). `ReadinessReportResponse.
  counts` is exactly 11 always-present int keys; `.blockers` entries are
  `{category, target_type, target_id, message}` with 8 fixed `category`
  values (`readiness_service.py:31-38`).
- **Why no live Docker/LangGraph run was used to pin this (a deviation
  from §8a's own original text, confirmed with the user before
  proceeding):** every one of these dicts is built by a single,
  unconditional, literal Python expression - never assembled across
  branches or filled from LLM-generated content - and passes through
  `build_gate_payload`/`_gate_to_response` unchanged. That's a different
  situation from the one this project's "suspect the tool, verify by
  observation" standard was built for (LLM output, multi-branch runtime
  state); here the source genuinely is the observation. Real DB-round-trip
  pinning tests were still added (below) rather than trusting the
  reasoning alone. **Caveat, on record rather than silently assumed:**
  those pinning tests call gate node functions directly
  (`interrupt()` monkeypatched), which is not the same as a compiled-graph
  run - if a gate's targets were ever populated differently inside a
  `Send()`-dispatched branch than under direct invocation, direct
  invocation wouldn't show it, and this project already has one history of
  exactly that divergence (B7's interrupt-replay bug, documented on
  `compliance_gate_node`'s own docstring). One gate route test
  (`tests/route/test_gates_routes.py`) does drive the real compiled graph
  via `run_workflow`, for `requirement_review` specifically - the fuller
  compiled-graph confirmation for the other five gate_types is what the
  full Playwright E2E scenario (§1) provides, not this pinning pass.
- **Status:** resolved-in-B13. `GateResponse` is now a discriminated union
  of six subclasses (`schemas/gates.py`, keyed on `gate_type`) instead of
  one flat model with `summary: dict`; `ReadinessReportResponse.counts`/
  `.blockers` are typed against `ReadinessCounts`/`ReadinessBlocker`
  (`schemas/readiness.py`). `GateDecisionRequest.decision` was narrowed
  from `str` to `Literal["approve", "reject", "request_changes"]` in the
  same pass, closing the pre-B12-audit minor finding "invalid gate
  decisions produce a bare 500, not 422."
- **Test:** `tests/integration/test_orchestration_nodes.py::
  test_gate_node_opens_exactly_one_gate_even_when_rerun` (extended with
  summary/targets shape assertions, now parametrized across all six
  gate_types including `finding_resolution`); `tests/integration/
  test_readiness_service.py::test_clean_tender_report_has_the_complete_
  counts_key_set`; `tests/route/test_gates_routes.py` (real compiled-graph
  confirmation for `requirement_review`).

---

## [B13] `list_gates`/`get_gate` were gated on `create_bid` - the fourth instance of a read action gated on a narrow authoring action

- **Discovered:** while typing `GateResponse` for the shared gate-control
  component (§7 step 5), not while looking for RBAC bugs - the same way
  `view_workflow`'s own gap (this file's `updated_at`/CORS-adjacent B13
  entries' sibling) was found while building screen 7, not through a
  dedicated RBAC audit.
- **Behaviour:** `GET /workflow-runs/{run_id}/gates` and `GET /gates/{gate_id}`
  (`routes/gates.py`) were both gated on `require_permission("create_bid")`
  - Bid manager/Admin only - hiding every gate's status from Architect,
  Talent reviewer, Commercial reviewer, Proposal writer, and Approver, the
  same five roles `view_workflow`'s own entry names. This is the fourth
  occurrence of the identical shape (`view_graph` B10, `view_readiness`
  B12, `view_workflow` B13, now `view_gates` B13) - see `rbac.py`'s own
  comment on `view_workflow`, corrected in this pass from "third instance"
  to "fourth." `POST /gates/{id}/decision` was never part of this bug - it
  already used `require_gate_decision_permission`, which resolves the
  correct per-gate-type action from `orchestration/gates.py`'s
  `GATE_TYPE_ACTIONS`, not a fixed route-level permission.
- **Status:** resolved-in-B13. New `view_gates` action added to
  `rbac.py` (all seven roles, matching `view_workflow`'s breadth) and
  `TenderOS-Build-Guide.md` Part H; both GET routes retrofitted.
  **Separately noted, not fixed here:** Part H's table never actually
  gained rows for `view_graph`/`view_readiness`/`view_workflow` when each
  was added (B10/B12/B13) despite `rbac.py`'s own comments citing them as
  precedent - only `view_gates` (this pass's own action) was added as a
  row. `rbac.py`'s comments are authoritative for the other three until
  someone does that backfill; not this entry's fix to make.
- **Test:** `tests/route/test_gates_routes.py::
  test_gate_routes_are_readable_by_every_role_that_is_a_real_member` (a
  Commercial reviewer succeeds) and `::test_gate_routes_reject_a_non_member`
  (a non-member is rejected - `view_gates`'s seven-role breadth leaves no
  narrower real role to test rejection with, same shape `view_workflow`'s
  own test file already documents).

---

## [B13] `resource_selection`'s gate targets advertise `role_requirement` ids that `target_updates` can never accept - accepted asymmetry, resolved at §7 step 7

- **Discovered:** while writing `GateDecisionPanel.tsx` (§7 step 5's
  TODO(human) 1), reading `gate_target_service.py` directly rather than
  assuming the gate-control component could treat all six gate types'
  `targets` lists the same way.
- **Behaviour:** `resource_selection_gate_node`
  (`orchestration/nodes/talent.py:142-152`) builds its gate's `targets`
  list as `[{"target_type": "role_requirement", "target_id": str(r.id)}
  for r in roles]` - the same shape `GateTargetRef` now types, and the
  same shape a client would naturally read off the gate response and echo
  back as `target_updates`. But `gate_target_service._KNOWN_TARGET_TYPES`
  (`gate_target_service.py:21`) does not contain `"role_requirement"` -
  only `"role_match"` is accepted, deliberately (that file's own comment:
  *"role_match, not role_requirement, because select_candidate needs the
  specific candidate row (a role_requirement can have several role_
  matches)"*). The consequence that comment doesn't state: submitting a
  `target_update` built from the gate's own `targets` list - exactly what
  a caller does correctly for `compliance_review` (`compliance_entry`) and
  `commercial_approval` (`pricing_finding`), the other two gate types
  `_KNOWN_TARGET_TYPES` covers - hits `apply_target_updates`'s permissive-
  on-unrecognized-type branch (`if target_type not in _KNOWN_TARGET_TYPES
  ... continue`) and is silently skipped. No error, no ownership check, no
  effect - the gate's own overall decision (approve/reject/etc.) still
  proceeds normally, so nothing in the response signals that the
  per-target update did nothing. This is worse than a clean rejection: a
  caller who reasonably generalizes from the two gate types where the
  gate's own targets ARE directly submittable has no signal they've hit
  the one gate type where that assumption is false.
  **What still works regardless:** the underlying capability isn't
  missing - `POST /roles/{role_id}/select-candidate` (G8) is a real,
  independent route that always works, needing only a role's real
  `role_match` id (from `GET /roles/{role_id}/matches`). The gap is
  narrower than "resource selection can't be approved per-item" - it's
  "the `targets`/`target_updates` contract is internally inconsistent for
  this one gate type," independent of any UI, since a UI can (and, per
  the `GateDecisionPanel.tsx` design note, must) route around it via the
  dedicated select-candidate route instead.
- **Resolved at §7 step 7 (screen 11, Team workspace), checked against
  the actual shipped frontend rather than decided in the abstract.**
  `GateDecisionPanel.tsx` (built §7 step 5) never sends `target_updates`
  at all - not for resource_selection, not for any gate type. Its
  `handleDecide` call to `decideGate` passes only `decision`/`notes`/
  `waive`; there is no `target_updates` field, and no prop through which
  a parent screen could supply one. This wasn't a deliberate design
  decision made in anticipation of this entry - it's a fact about the
  component as already built, checked directly (`GateDecisionPanel.tsx:
  48-60`) rather than assumed. Consequence: the two gate types where
  `target_updates` genuinely WOULD work correctly (`compliance_review`/
  `compliance_entry`, `commercial_approval`/`pricing_finding`) are
  *also* unreachable through this UI, not just `resource_selection`'s
  mismatched one - every per-item action in this frontend (compliance
  approve, candidate select, commercial resolve) goes through its own
  dedicated route (`POST /compliance/{id}/approve`, `POST /roles/{id}/
  select-candidate`, `POST /pricing-findings/{id}/resolve`), uniformly,
  with the gate's own decision always a separate, standalone call. That
  makes the `role_requirement`/`role_match` mismatch doubly moot for this
  codebase's real behavior, not just workarounded for one gate type -
  worth stating plainly rather than leaving implicit, since it means
  `target_updates` batch-approval is real, working backend capability
  with zero current frontend consumer.
  **Given that, treated as an accepted asymmetry, not fixed:** neither
  of the two fix options this entry originally named (rebuilding
  `resource_selection_gate_node`'s `targets` around `role_matches`, or
  adding a `submittable_target_type` field) earns its cost against a
  capability nothing calls. Both remain named here as the real options
  if a future direct API consumer (not this UI) ever needs
  `target_updates` to work uniformly across all three gate types it
  currently half-covers.
- **Status:** accepted asymmetry. Not fixed - the underlying
  `targets`/`target_updates` contract inconsistency for `resource_
  selection` still exists exactly as described above, for any caller
  that isn't this project's own frontend. Screen 11 is built entirely
  against `GET /roles/{tender_id}/roles`, `GET /roles/{id}/matches`, and
  `POST /roles/{id}/select-candidate` directly - the gate's own `targets`
  list is rendered for display only (via `GateDecisionPanel`'s typed
  `ResourceSelectionSummary`), never round-tripped as a `target_update`.
- **Test:** none dedicated to the mismatch itself (still a real, if
  unreachable-through-this-UI, inconsistency) - `tests/route/
  test_talent_routes.py` (§7 step 7) covers `select-candidate` through
  its own real route instead, which is the path that actually matters
  now.

---

## [B13] `target_updates` batch approval - revived by B12, given a real working backend path, and never called by anything - a decision, not a triage note

- **Discovered:** as a consequence noted inside the entry immediately
  above (`resource_selection`'s targets mismatch), while writing
  `GateDecisionPanel.tsx` - broken out into its own entry because it's a
  bigger finding than that one gate type's own inconsistency, and was at
  real risk of being read as a footnote to it rather than what it
  actually is.
- **The history, stated plainly:** `target_updates` was declared on
  `GateDecisionRequest` since B3, dead (never read by anything) through
  B9 - the "[B7/B8/B9] Gate approval does not apply GateDecisionRequest.
  target_updates" entry earlier in this file. B12 did real, deliberate
  engineering to revive it: `gate_target_service.apply_target_update(s)`,
  the cross-tenant ownership check, the whole-batch atomicity fix (no
  partial writes when one entry in a batch is foreign), all built and
  tested (`tests/integration/test_gate_target_service.py`) specifically
  so a reviewer could batch-approve individual targets in the same call
  that decides the gate. B13's frontend is the first UI this project has
  built that could actually call it. It doesn't. `GateDecisionPanel.tsx`
  (`:48-60`) sends `decision`/`notes`/`waive` only, for every gate type,
  with no `target_updates` field and no prop for a parent screen to
  supply one. **The feature B12 spent real work reviving is dead again,
  this time with a working backend path and zero callers** - a
  meaningfully different, and more pointed, fact than "resource_
  selection's targets happen to be the wrong id space."
- **The actual design question, answered rather than left open:** is
  per-target batch approval something this UI should do for the two gate
  types where it genuinely works (`compliance_review`/`compliance_entry`,
  `commercial_approval`/`pricing_finding`), or is per-item approval
  through each domain's own dedicated route the real, intended design,
  with `target_updates` understood as backend-only surface (a direct API
  consumer, a future bulk/admin tool - not this web UI)?
  **Decided here: per-item approval through the dedicated routes is the
  real design for this UI. `target_updates` is accepted as backend-only
  surface, not adopted.** Reasoning, not a default: of the six gate
  types, three are forced into per-item-only by construction, not
  choice - `requirement_review` and `finding_resolution` were never in
  `_KNOWN_TARGET_TYPES` at all (their own KNOWN_LIMITATIONS entry above),
  and `resource_selection` is the mismatched one this entry's sibling
  describes. `final_readiness` has no per-item targets to approve at all.
  That leaves exactly two of six gate types - `compliance_review`,
  `commercial_approval` - where batch approval is even possible. Building
  a second, different interaction pattern (select targets, submit one
  atomic batch-plus-gate-decision call) for only those two, while the
  other four necessarily use simple per-item dedicated-route clicks,
  would make the gate-control surface inconsistent across screens for a
  capability that can't extend uniformly to begin with - a worse UX
  tradeoff than the atomicity `target_updates` buys back for exactly two
  screens. The per-item routes already exist, are already real, already
  tested, and give simpler, immediate per-click feedback.
- **Owner:** this decision belongs to B13 - made here, in this pass, not
  deferred. No future block is currently scheduled to revisit it. If a
  genuine direct API consumer other than this project's own web UI ever
  needs atomic batch-approval-plus-gate-decision, `target_updates`
  already works for `compliance_entry`/`role_match`/`pricing_finding` and
  needs no further backend change - only then would adopting it in this
  UI (or building a second one) be worth reopening.
- **Status:** accepted, backend-only surface. Not a bug, not scheduled
  for frontend adoption.
- **Test:** `tests/integration/test_gate_target_service.py` (B12) is the
  real, standing proof the backend path itself still works - this entry
  is about the absence of a caller, not a defect to regress-test.

---

## [B13] Why `requirement`/`redteam_finding` aren't in `_KNOWN_TARGET_TYPES` - a plausible inference, not a documented decision

- **Discovered:** same pass as the entry above, prompted by the same
  question in reverse - `_KNOWN_TARGET_TYPES` accepts exactly three
  target types (`compliance_entry`, `role_match`, `pricing_finding`); the
  other two gate types with real per-target items
  (`requirement_review`/`requirement`, `finding_resolution`/
  `redteam_finding`) were never candidates for inclusion at all, and
  nothing in this codebase's comments says why.
- **Checked and found silent:** Part G's G4 spec (`:2489-2505`) - generic
  `target_updates: []` example, no gate-type/target-type scoping stated.
  Part E (Agent Contracts) - no mention of gate target types at all, not
  the right part of the spec for this question. The PRD - zero hits for
  `target_updates`/`role_match`/"batch approv[al]". The B7-B12
  `target_updates` history in this file (the "Resolved in B12" entry
  above) documents *what* B12 built (the three-type set) and *why*
  `role_match` was chosen over `role_requirement` for one of the three,
  but never states why only these three gate types were in scope to begin
  with - `_KNOWN_TARGET_TYPES`'s own comment calls it "Q1's chosen
  target_type set" without Q1 itself being recorded anywhere still
  readable in this repo.
- **The one relevant fact that IS on record, supporting a plausible but
  unconfirmed inference:** every gate type with real per-target items has
  its own independent, dedicated per-item route, regardless of
  `target_updates` coverage - `requirement_review` has had `POST
  /requirements/{id}/verify` since long before B12; `finding_resolution`
  has had `POST /redteam-findings/{id}/resolve` since B11. Both predate
  B12's `target_updates` work. By contrast, the B12 entry above states
  plainly that `compliance_review`/`resource_selection`/
  `commercial_approval` had **"zero existing write path"** for per-item
  approval before B12 - `human_approved`/`human_selected`/`resolved`
  could only ever be set through `target_updates` never actually being
  wired (the B7-B9 gap this file already tracks at length above). Reading
  those two facts together, the plausible story is: B12 scoped
  `target_updates` to the three gate types that had NO other way to
  record a per-item decision, not to all five - `requirement_review`/
  `finding_resolution` weren't omitted from something meant to be
  universal, they were simply never broken in the first place. **This is
  this entry's own inference, built from the same code+history evidence
  cited above, not a decision anyone wrote down at B12 time** - the B12
  entry itself never states this as its reasoning, and no other document
  in this repo does either.
- **Status:** open. Recorded as a plausible reading of the evidence, not
  a confirmed design decision - if `requirement_review`/`finding_
  resolution` were ever meant to gain `target_updates` coverage too, this
  entry is where that intention would have been recorded, and it wasn't
  found. Worth a direct answer next time someone who'd know is available
  to confirm or correct the inference above, rather than treating it as
  settled because it's plausible.
- **Test:** none - there's no behavior to test here, only a documentation
  gap.

---

## [B13] `make test`'s host-DSN override only patches Postgres - Redis/Neo4j/S3 still point at Docker-internal hostnames, hanging any test that touches them

- **Discovered:** running the full suite for §7 step 5's own numbers.
  `POSTGRES_DSN=...localhost.../tenderos_test uv run pytest` stalled for
  minutes with zero output on the very first document-upload test it hit,
  after 8 pure-auth tests (no storage/graph/cache dependency) had already
  passed in seconds. A fourth category of the "Standing pattern: local
  dev/test environment state can silently invalidate a real verification"
  entry above (`:721`) - related, not a duplicate: that entry's three
  categories are all "which server is actually being reached" (stale
  image, port-squatting, wrong-database-teardown); this one is "the
  configured address cannot be reached from where the test process runs,
  categorically, regardless of which server is listening where."
- **Behaviour:** `.env` sets `S3_ENDPOINT_URL=http://minio:9000`,
  `REDIS_URL=redis://redis:6379/0`, and `NEO4J_URI=bolt://neo4j:7687` -
  all three Docker-Compose-internal service names, resolvable only from
  inside the Compose network. The `Makefile`'s own `test` target
  overrides exactly one of the four service URLs for host execution
  (`POSTGRES_DSN=...localhost...`) - `S3_ENDPOINT_URL`/`REDIS_URL`/
  `NEO4J_URI` are left untouched, still pointing at hostnames that plain
  `getaddrinfo` confirms do not resolve from the host at all (`minio`:
  `gaierror [Errno 11001] getaddrinfo failed`, ~2.7s to fail). The
  consequence isn't a fast, loud error, though: `db/storage.py`'s
  `ObjectStorage` wraps a synchronous `boto3` client in
  `asyncio.to_thread`, and botocore's own default retry/backoff policy
  turns one fast DNS failure into several, compounding into a stall of a
  minute or more per call before anything raises - confirmed directly by
  probing `storage._ensure_bucket()` in isolation, which still hadn't
  completed after 10s even wrapped in `asyncio.wait_for` (the underlying
  blocking thread keeps running past the async timeout, since cancelling
  the `to_thread` wrapper doesn't stop the OS thread inside it). Any test
  that uploads a document (`document_service.create_document` ->
  `storage.put`) - which is most of `tests/integration/` and a growing
  share of `tests/route/` - hits this. Tests that reach Neo4j
  (`graph_service.project_tender`/`check_freshness`, several of
  `test_readiness_service.py`'s own blocker tests) or Redis (the LLM
  cache layer, though most node tests route around it via
  `FakeLLMClient`) would hit the identical shape for the same reason,
  unverified individually here but following from the same root cause.
  All three ports ARE published to the host by `docker-compose.yml`
  (confirmed directly: `localhost:9000`/`:7687`/`:6379` all accept a raw
  TCP connect) - this is purely a missing environment-variable override,
  not a networking or Compose-config problem.
- **Not attributable to Gemini rate limits, checked directly rather than
  assumed:** every LLM-touching orchestration node test in this suite
  (11 files) explicitly monkeypatches `_build_llm_client` to
  `FakeLLMClient` - grepped and confirmed, no test constructs a real
  `GeminiClient`. Worth recording because it was the first, reasonable
  hypothesis raised for the stall and is directly ruled out by this
  suite's own established no-real-Gemini-in-tests convention, not by
  guesswork.
- **A real process-management gap surfaced investigating this, kept
  separate from the root cause above because it's a different mechanism:**
  stopping the first hung run via the harness's own background-task-stop
  did not terminate the underlying Windows process - a second run was
  launched before noticing, briefly running two `pytest` processes
  against the same live `tenderos_test` database at once (each with its
  own autouse `_clean_tables` teardown, capable of wiping the other's
  in-progress test's rows). Resolved by verifying each process's actual
  service connections directly (`Get-NetTCPConnection`) rather than
  assuming the stop succeeded - no data corruption found, the two runs
  never actually collided on the same table at the same instant, but this
  was verified after the fact, not guaranteed by anything before it. Not
  reproduced deliberately, not root-caused (Windows process-tree signal
  delivery for a `uv`-wrapped subprocess is the likely mechanism, not
  confirmed) - recorded so a future stopped-and-relaunched background
  test run is verified the same way rather than trusted by default.
- **Status:** open, not fixed - `Makefile`'s `test` target needs
  `S3_ENDPOINT_URL`/`REDIS_URL`/`NEO4J_URI` overridden to `localhost`
  alongside its existing `POSTGRES_DSN` override, matching the ports
  `docker-compose.yml` already publishes. Worked around this pass by
  passing all four as inline env vars to `uv run pytest` directly rather
  than editing the Makefile - a real fix belongs to whichever block owns
  local dev tooling (not decided here), and should be a small, mechanical
  change once made.
- **Test:** none - this is a test-harness configuration gap, not
  application behavior. The full suite (361 tests) passes cleanly once
  all four URLs are host-reachable, confirmed this pass in 117.98s.

---

## [B13] `queue_knowledge_document_import` callers that omit `request_id` collide on re-upload/duplicate content - resolved, all four call sites

- **Discovered:** §7 step 6's own route-level pass on `knowledge.py` (18
  routes, previously zero HTTP coverage anywhere - the exact module
  `set_knowledge_selection`'s own dead-route bug lived in). First-attempt
  run of `tests/route/test_knowledge_routes.py::
  test_upload_resumes_creates_one_batch_for_multiple_files`, which
  uploads two files built from the same helper (so byte-identical
  content) - not a contrived edge case, a realistic one (a candidate's
  resume accidentally selected twice in a multi-file picker).
- **Behaviour:** `routes/knowledge.py`'s `_upload_batch_document` calls
  `document_service.create_document(...)` then unconditionally
  `knowledge_service.queue_knowledge_document_import(document.id,
  batch_id)` - the second call with no `request_id`, defaulting to
  `None`. `create_document`'s own org+hash dedup (`documents.
  uq_org_document_hash`) means a second byte-identical file in the same
  batch resolves to the SAME `document.id` as the first, not a new row.
  `queue_knowledge_document_import`'s idempotency key
  (`knowledge_document_uploaded_idempotency_key`) is built from
  `document_id` + `request_id` alone - with both calls sharing the same
  `document_id` and the same `request_id=None`, the second `outbox.
  create` collides with the first on `outbox_events`' own unique
  idempotency-key constraint and raises `IntegrityError`, uncaught,
  surfacing as a bare 500 for the entire `POST .../import-batches/
  resumes` request - not just skipping the duplicate file, the whole
  batch (including the first, legitimate file) fails to commit, per
  `UnitOfWork`'s own rollback-on-exception behavior.
- **The pattern, not just the one bug:** the correct fix already existed
  in this exact file - `replace_employee_resume` passes a fresh
  `request_id=uuid.uuid4()` specifically to avoid this collision, and its
  own docstring names the mechanism explicitly. `_upload_batch_document`
  was a sibling call site that never adopted it. Checked whether that was
  the only one, rather than assuming it: grepped every call site of
  `queue_knowledge_document_import` and found three more in this same
  file - `upload_employee_master`, `upload_certifications`, and
  `upload_knowledge_document` - all making the identical mistake, all
  with a MORE ordinary trigger than the resumes case: simply re-uploading
  the same employee-master/certifications/capability-statement file a
  second time (to start a fresh import batch - an entirely normal
  action, not a client bug) reuses the deduped `document.id` and collides
  with the FIRST upload's event, because the idempotency key never varied
  by `batch.id` at all. This is the same general shape `readiness_
  service.py`'s own comment names elsewhere in this codebase - "the same
  'guard that looks complete' shape this project has hit three times
  already" (a correct mechanism that exists, but doesn't cover every
  place it needs to) - here the mechanism isn't a guard but an
  idempotency convention, and the gap is between sibling call sites in
  one file rather than across the whole codebase, but the root shape is
  identical: a correct pattern that one call site follows and its
  neighbours silently don't.
- **Status:** resolved-in-B13, all four call sites. Every caller of
  `queue_knowledge_document_import` in `routes/knowledge.py` now passes a
  fresh `request_id=uuid.uuid4()` unconditionally - matching
  `replace_employee_resume`'s own "simpler than branching, harmless for
  the genuinely-new-document case" reasoning, not branching on
  `create_document`'s `_is_new` return value anywhere.
- **Test:** `tests/route/test_knowledge_routes.py::
  test_upload_resumes_creates_one_batch_for_multiple_files` (the
  duplicate-file-in-one-batch case, the one that surfaced this) and
  `::test_uploading_the_same_employee_master_file_twice_starts_a_second_batch`
  (the broader re-upload case, added once the other three call sites were
  found) - both failed against the unfixed routes, both pass now.

---

## [B13] Part G's G6 list never named a single-employee GET - resolved, and the third Part G/code drift this block has found

- **Discovered:** §7 step 6, building screen 4 (employee profile
  detail/verification) against Part G's own G6 route list (Build Guide
  `:2524-2548`). That list has exactly two employee-adjacent GETs: `GET
  /knowledge/{knowledge_item_id}` (generic - `title`/`status` only, no
  employee fields) and `GET /organizations/{org_id}/employees`
  (bid-eligible-only, by its own repository-level filter -
  `eligible_for_bid=true AND profile_review_status='verified'`). Neither
  can serve screen 4's actual job: a reviewer verifying a profile is, by
  definition, looking at one that is NOT yet eligible/verified - the one
  case `list_bid_eligible_employees` structurally excludes.
- **Behaviour:** confirmed by grep, not assumed - no route anywhere in
  `routes/knowledge.py` returned a single employee's full profile
  (email, `current_title`, `years_experience`, `availability_status`,
  `eligible_for_bid`, `profile_review_status`, `resume_document_id`) by
  id. The capability already existed internally, though -
  `knowledge_service.get_employee(knowledge_item_id)` and the
  `EmployeeResponse` schema are both used by `patch_employee_profile`/
  `verify_employee`/`replace_employee_resume` (each fetches through
  `get_employee` before mutating) - this was a genuinely missing route,
  not missing capability.
- **RBAC, checked rather than defaulted to the recent precedent:** the
  new `GET /employees/{knowledge_item_id}` route uses the same
  `manage_organization_knowledge` action every other route in this
  module already uses (4 roles: bid_manager, solution_architect,
  talent_reviewer, admin) - deliberately NOT a new `view_*` action, and
  deliberately NOT matching `view_workflow`/`view_graph`/`view_readiness`/
  `view_gates`'s 7-role breadth. Those four were all the same real bug
  (a workflow-transparency READ wrongly gated behind a narrow AUTHORING
  action, hiding process state from roles actively working the tender).
  This is a different shape: personnel data, not workflow status, and
  `list_bid_eligible_employees` already returns this exact
  `EmployeeResponse` shape under this exact 4-role action with no prior
  finding that the scope is wrong - reusing it is consistency with this
  module's own existing design, not an oversight to fix.
- **The third Part G/code drift this block has found, not an isolated
  miss:** `POST /tenders/{tender_id}/impact-preview` (G6) was documented
  but never built (the real capability lives at `POST .../graph/impact`
  instead - drift in the doc-ahead-of-code direction); `POST
  /tenders/{tender_id}/redteam/run` is real, load-bearing code with no
  line in G11 at all (drift in the code-ahead-of-doc direction); this
  route is the same code-ahead-of-doc shape as `redteam/run` - the
  capability (`get_employee`/`EmployeeResponse`) existed in code before
  this pass, Part G's own route list just never caught up to it. Three
  finds in one block, in both directions, is worth recording as evidence
  Part G's route lists need a real audit against `apps/api/src/
  tenderos_api/routes/*.py`'s actual decorators at some point - not
  attempted here, and not this entry's fix to make. The other two
  (`impact-preview`, `redteam/run`) get their own KNOWN_LIMITATIONS
  entries when their owning screens (10 and 14 respectively) are built,
  not written up in full here ahead of that work.
- **Status:** resolved-in-B13. Route added; Part G itself not edited (a
  living spec correction is a bigger, separate call than this one route
  addition, and is better made once alongside the other two drift finds
  above, not piecemeal here).
- **Test:** `tests/route/test_knowledge_routes.py::
  test_get_employee_returns_the_full_profile_even_when_unverified` (the
  case that motivated this - an unverified profile is fetchable) and
  `::test_get_employee_404s_for_an_unknown_id`.

---

## [B13] Every knowledge-document upload crashed or silently mis-ingested - two distinct bugs, found only by a live browser upload; zero test coverage existed for this code path at any level before this pass

- **Discovered:** the live-browser check on screen 3 (§7 step 6's own
  verification step, explicitly requested rather than trusting build/
  lint/route-tests alone) - uploading a real, valid `employees.csv`
  through the actual UI against the full rebuilt Docker stack. Every
  automated check this block had already run (`uv run pytest`, `tsc`,
  `eslint`, `mypy`) was clean; this bug lived entirely in a code path
  none of them exercise - the worker job layer, which
  `process_knowledge_document_import` had **zero test coverage at any
  level, anywhere, before this pass** (confirmed by grep - no unit,
  integration, or route test ever called it or drove a real upload far
  enough to trigger it). This is the same category of gap the B13 plan's
  own §3a inventory was built to close for HTTP routes - it just hadn't
  been extended to the worker job layer yet, and this pass is the first
  time anything reached that deep.
- **Bug 1 - a spurious generic-ingestion event fired for every
  organization-scoped upload.** `document_service.create_document` fired
  `DocumentUploaded` (outbox_dispatcher's own trigger for
  `parse_and_embed_document`, the generic tender-RAG pipeline)
  **unconditionally**, regardless of caller - including every
  `routes/knowledge.py` upload (employee-master, certifications, resumes,
  capability statements), which are organization-scoped (`tender_id=
  None`) and already fire their own, correct `KnowledgeDocumentUploaded`
  event separately. `reprocess_document`, in the same file, already
  refused to act when `tender_id is None` for exactly this reason - the
  same "correct pattern exists at one call site, the sibling never
  adopted it" shape as this pass's own `request_id`/duplicate-upload
  finding above, now found a second time in the same file in the same
  session. Fixed: `create_document` now only fires `DocumentUploaded`
  when `tender_id is not None`.
- **Bug 2 - real, independent of bug 1: `process_knowledge_document_import`
  itself calls `parse_and_embed_document` unconditionally, before
  branching on import type.** Its own docstring explains why: it's meant
  to be self-sufficient regardless of which of the two outbox events
  dispatches first (a real, reasonable defensive design for the two
  import types that genuinely need chunks - `bulk_resumes`/`manual`,
  whose `_process_resume`/`_process_capability_statement` both read
  `document_chunks` directly for LLM extraction). But it applied that
  same unconditional call to `employee_master_csv`/`employee_master_xlsx`/
  `certification_documents` too - all three parse deterministically off
  raw spreadsheet bytes and never touch `document_chunks` at all
  (`import_certification_row` builds its own single synthetic chunk
  internally, independent of this function entirely). Bug 1 alone would
  have stopped this from double-firing via the outbox, but this call site
  runs regardless of the outbox - it's inside the function bug 1's fix
  never touches. For `.csv` this crashed outright
  (`_extract_blocks` has no CSV handling - confirmed live:
  `ValueError: Unsupported document extension: '.csv'`); for `.xlsx`
  employee-master/certifications uploads it would not have crashed, but
  would have silently chunked and embedded the spreadsheet as if it were
  generic prose/pricing content - the worse of the two outcomes, since it
  fails quietly instead of loudly. Fixed: the `parse_and_embed_document`
  call moved into only the two branches that need it
  (`bulk_resumes`, `manual`).
- **Why both, not just one:** fixing bug 1 alone would have stopped the
  crash for a *first* upload of a given file (no more spurious
  `DocumentUploaded` firing means no ingestion job races the knowledge
  one) - but `process_knowledge_document_import` would still have called
  `parse_and_embed_document` on itself and still crashed the exact same
  way, just from a single instead of double trigger. Confirmed this
  directly by observing the crash recur, unchanged, against a rebuilt
  stack with only bug 1's fix applied, before finding and fixing bug 2 -
  not inferred from reading the code alone.
- **Status:** resolved-in-B13, both. Re-verified against a freshly
  rebuilt Docker stack after both fixes: a real `employees.csv` upload
  through the actual browser now reaches `completed` (2/2 items,
  `approved`, both real employee rows visible in the review queue and in
  the talent library), with a full round-trip through `PATCH .../profile`
  and `POST .../verify` also exercised live, no console errors.
- **Test:** `tests/integration/test_document_ingestion.py::
  test_organization_scoped_upload_fires_no_generic_document_uploaded_event`
  (bug 1) and the new `tests/integration/test_knowledge_import_worker_job.py`
  - `test_employee_master_csv_import_creates_real_items_without_ingestion`
  and `test_certification_documents_import_builds_its_own_chunk_without_
  generic_ingestion` (bug 2, and the first automated coverage
  `process_knowledge_document_import` has ever had for any batch type).

---

## [B13] The Neo4j driver singleton breaks under `tests/route/`'s per-call event loops - a fourth environment-trap category, test-infrastructure this time

- **Discovered:** §7 step 8, writing the first `tests/route/` file to
  touch Neo4j at all (`test_graph_routes.py`). First attempt, with no
  workaround, failed 6 of 8 tests with `RuntimeError: ... Future ...
  attached to a different loop` raised from inside the neo4j driver
  itself, during session close.
- **Behaviour:** `graph_projection/driver.py`'s `driver` is a module-level
  singleton, explicitly documented as "same 'module-level singleton,
  long-lived and pooled' shape as `db/session.py`'s own `engine`" - and
  it has the identical problem that `engine` had before
  `_no_pooling_for_the_app_engine` (this file's own earlier entry): its
  connection pool binds to whichever event loop is running the first
  time it's used. `tests/integration/` never surfaces this (one shared
  `httpx.AsyncClient`/event loop for the whole test). `tests/route/`'s
  `TestClient` does - and worse than the Postgres case: it's not just
  that each TEST gets its own loop, each individual `client.get()`/
  `.post()` call runs the ASGI app through its own `anyio` blocking
  portal, so two calls inside the SAME test function can each land on a
  DIFFERENT loop. A single fresh driver swapped in once per test (tried
  first, matching `_no_pooling_for_the_app_engine`'s own shape exactly)
  still failed all 8 tests the same way - the driver's own internal pool
  held a connection opened on one call's loop and tried to reset it from
  a later call's different loop.
- **The fix, and why the obvious one didn't work:** `NullPool` for
  SQLAlchemy has no direct neo4j-driver equivalent, so "swap the
  singleton for a fresh instance" (correct in shape for Postgres) isn't
  enough here - the fix has to match what `NullPool` actually *does*,
  not just look like it: open a brand-new driver, use it for exactly one
  session, close both, on **every call**, never holding anything across
  a loop boundary. `graph_service.py`'s own `get_neo4j_session` (a
  direct `from ...driver import get_session as get_neo4j_session`
  function-object import, bound into `graph_service`'s own namespace at
  import time) is what actually needed patching - patching
  `graph_projection.driver.get_session` itself does nothing, since
  `graph_service.py`'s already-bound name doesn't re-resolve through the
  original module at call time. Caught by checking, not assumed: the
  first (wrong) patch target left the exact same error unchanged.
- **Status:** resolved, test-side only, scoped locally to
  `test_graph_routes.py` (matching `_clean_neo4j`'s own established
  per-file scoping in `test_graph_projection.py`, not added to the
  shared `tests/route/conftest.py` - no other route test file touches
  Neo4j yet). Real product code (`graph_projection/driver.py`) is
  unchanged - a real uvicorn process has exactly one event loop for its
  whole life, so this was never a production bug, the same conclusion
  `_no_pooling_for_the_app_engine`'s own entry reached for the identical
  shape. The next `tests/route/` file that touches Neo4j will need the
  same fixture - worth lifting into `tests/route/conftest.py` at that
  point rather than a third copy-paste, not done now since exactly one
  file needs it today.
- **Test:** `tests/route/test_graph_routes.py` itself - all 8 tests fail
  immediately without this fixture, all 8 pass with it.

---

## [B13] Missing Idempotency-Key on the knowledge-selection PUT - resolved (§3c)

- **Discovered:** B13's own original planning pass (§3c) - Part I1 names
  "knowledge selection change" as one of exactly six operations requiring
  `Idempotency-Key`; `PUT /tenders/{tender_id}/knowledge-selections/
  {knowledge_item_id}` was the one of the six never wired up. Deferred
  from earlier steps to §7 step 8, since it's the route screen 10
  (Knowledge selections) is built directly against.
- **Status:** resolved-in-B13. Same `required=True` shape `select_
  candidate`/`decide_gate`/`create_workflow_run` already use - operation
  name `knowledge_selection_change`, matching Part I1's own phrase.
  Every existing caller updated to the new contract: three
  `tests/route/test_knowledge_routes.py` PUT calls and one in `tests/
  route/test_graph_routes.py`'s own staleness fixture now pass a real
  key; `tests/integration/test_knowledge_service.py`'s tests call the
  service function directly and were correctly unaffected.
- **Test:** `tests/route/test_knowledge_routes.py::
  test_set_knowledge_selection_requires_idempotency_key` (new) plus the
  four updated call sites above, all passing.

## [B13] No route ever exposed a proposal section's claim ids - resolved

- **Discovered:** §7 step 9, while building screen 13 (Proposal
  workspace). The plan's own §2 table marked this screen's row "None" for
  gaps, written without checking whether a human could actually discover
  which claims exist in a drafted section to click into a trace - `GET
  /proposal/claims/{claim_id}/trace` needs a `claim_id` handed to it, and
  nothing in G10 ever returns one. The repository method that would serve
  this, `list_proposal_claims_for_section`, has existed since B9
  (`db/repositories/proposal.py:128`) but was never called from
  `proposal_service.py`, let alone exposed as a route - a capability-
  exists-underneath, never-wired-to-the-surface gap, the same shape as
  the `GET /employees/{id}` and `view_gates` gaps found earlier in this
  block.
- **Considered and rejected:** parsing `ProposalSection.content` for
  claim-text substrings client-side, to avoid a new route. Checked
  directly against `agents/schemas/proposal.py::ProposalClaimDraft` and
  `ProposalSectionDraft` (`content` and `claims` are two independently
  LLM-generated fields from the same call, no documented or enforced
  invariant that `claim_text` is a verbatim substring of `content`) -
  building a highlight-matcher on that assumption would have been exactly
  the kind of unverified inference this project has repeatedly flagged
  elsewhere, not a shortcut worth taking.
- **Status:** resolved-in-B13. Added `GET /proposal/sections/{section_id}
  /claims` (`routes/proposal.py`), backed by a new thin service wrapper
  `list_claims_for_section` (`proposal_service.py`, mirrors `list_
  proposal_sections_for_tender`'s own shape) and a new list-item schema
  `ProposalClaimResponse` (`schemas/proposal.py`). Stays on the existing
  `edit_proposal` action, matching the other three G10 routes - **decided
  in §7 step 10, not left open:** now that `view_proposal_trace` exists
  (see its own entry below), it was checked rather than assumed that this
  route should widen with it. Screen 13 (Proposal workspace), this
  route's only caller, is itself gated to `edit_proposal` end to end, so
  a Talent/Commercial reviewer can't reach it regardless of this route's
  own RBAC - unlike the trace route, which is independently reachable (a
  claim_id can be opened directly from a red-team finding or the
  readiness gate), this one has no standalone entry point, so there's no
  real gap widening it would close. Not added to `TenderOS-Build-Guide.md`
  Part G, matching
  the precedent set by the `GET /employees/{id}` addition (code-only,
  recorded here rather than in the spec text).
- **Test:** `tests/route/test_proposal_routes.py::
  test_list_proposal_claims_returns_the_real_claim`.

## [B13] `PATCH /proposal/sections/{id}` raised `DetachedInstanceError` on every call - resolved (predicted, then confirmed)

- **Discovered:** not a fresh discovery - this was a **named suspect**,
  flagged by inspection in §7 step 3's own `updated_at`/`onupdate`
  entry above, specifically because that step's `tenders.py` fix made
  the failure shape recognizable and `update_proposal_section` was
  checked against it and found to share it, on inspection, before this
  route had ever been tested. That entry deliberately did not fix it
  early ("confirming it live... is exactly the 'verify, don't assume'
  standard this project holds itself to"), named §7 step 9 as the owner,
  and predicted it would reproduce under the same conditions once
  `test_proposal_routes.py` existed. §7 step 9's first run of
  `test_patch_proposal_section_updates_content` reproduced it exactly as
  predicted - the value here isn't that a bug was found, it's that the
  earlier by-inspection prediction was correct, which is itself the
  argument for naming suspects by inspection rather than only ever
  waiting for a test to surface them cold (matching this file's own
  `set_knowledge_selection` precedent for how much a route going
  untested can hide, just caught one step earlier this time).
- **Behaviour:** `ProposalRepository.update_proposal_section` (`db/
  repositories/proposal.py`) updated the row and called `flush()`, then
  returned the ORM object directly. `proposal_sections.updated_at` carries
  `onupdate=func.now()` - a server-computed value a plain `flush()` does
  not pull back into the Python object. By the time `patch_proposal_
  section` (`routes/proposal.py`) tried to read `.updated_at` to build the
  response, the owning `UnitOfWork`'s session had already closed (the
  route runs after the `async with UnitOfWork()` block that produced the
  object has exited), so SQLAlchemy raised `DetachedInstanceError`
  instead of lazily reloading it.
- **Root cause note, not just "fixed":** this is not a novel bug shape -
  `ComplianceRepository.update_compliance_entry` and `Requirement
  Repository.update_fields` both hit the identical problem earlier in
  this project and both already carry a `session.refresh()` call plus a
  comment naming exactly this concern. `ProposalRepository.
  update_proposal_section` is structurally the same kind of update (a
  `**fields` patch on a row with an `onupdate` timestamp, returned across
  a session boundary) but was written without the fix the other two
  already needed - a known pattern not applied consistently, not a new
  class of mistake.
- **Status:** resolved-in-B13. Added the same `await self._session.
  refresh(section)` call after `flush()`, with a comment cross-referencing
  the two prior instances so a fourth repository method doesn't repeat
  this.
- **Test:** `tests/route/test_proposal_routes.py::
  test_patch_proposal_section_updates_content` - failed with the real
  `DetachedInstanceError` traceback before the fix, passes after.

## [B13] Traceability drawer (screen 16) verified as an instrument - not yet pointed at real citation data

- **Discovered:** §7 step 9's own live-browser check, and the framing
  correction the user gave immediately after reading the first report of
  it - the original report risked being read as "step 9 made progress on
  the citation gap," which it did not.
- **What the live check actually established:** both rendering branches
  of `TraceabilityDrawer.tsx` are correct against the real API, real
  Postgres data, and a real Gemini-drafted proposal (not just
  `FakeLLMClient`'s scripted route test). The empty branch ("No
  additional in-scope evidence") rendered on real claims from a real
  drafting run. The non-empty branch (the `role="alert"` warning box)
  rendered correctly - real count, real evidence fields - after one
  `evidence_link` was inserted directly into Postgres by hand for this
  check and linked to the tender's existing compliance entry.
- **What it did NOT establish, stated plainly so this isn't
  over-claimed later:** neither observation is evidence about whether
  *real drafting* leaves *real* evidence uncited. The empty-branch
  claims were traced on a tender with **zero `evidence_links` at all**
  (confirmed directly: `SELECT count(*) FROM evidence_links WHERE
  tender_id = ...` returned 0 before the check) - "empty" there follows
  necessarily from the data having nothing to be in-scope in the first
  place, not from any drafting-time citation decision. The non-empty
  branch was produced by a hand-inserted row created specifically to
  exercise the rendering path, not evidence the drafting model actually
  had available and skipped during a real run. Both are checks of the
  drawer's own correctness, not samples of the citation-correctness
  question B9/B12 opened.
- **Why this matters going forward, not just as a correction:** §4 of
  this block's own plan already warned that an empty
  `currently_in_scope_evidence` result, seen for the first time during
  the eventual Playwright E2E run, "is not, by itself, a finding about
  the data" and told the runner to suspect the instrument before the
  data. That caution now cuts the other way too: the instrument (the
  drawer's rendering logic, for both branches) is now checked and
  confirmed correct, on real data shapes, independent of the Playwright
  scenario. So when the real sample eventually comes from the Playwright
  E2E run's own "inspect claim trace" step (§4's own rule for where the
  real sample has to come from - a tender that went through real
  extraction, real compliance/team/commercial approval, and real
  drafting, not hand-seeded fixture rows), an empty or non-empty result
  there will mean something real about that run's own citation behavior,
  not leave open the question of whether the drawer itself can be
  trusted to show it correctly.
- **Status:** instrument verified; citation-correctness question itself
  remains open, unchanged by this step. Owner: whichever pass runs the
  full Playwright E2E scenario (§7 step 11 of this block's own plan).
- **Test:** none automated - this was a manual live-browser check, not a
  regression test. The hand-inserted `evidence_link`/`compliance_
  evidence` rows were left in the dev database (`tenderos`, not
  `tenderos_test`) as accumulated fixture state, matching this session's
  established practice for other "*-check" org data, not cleaned up.

## Standing pattern: API surface lagging the service/repository layer - now four instances this block

- **Discovered:** named explicitly after §7 step 9's claims-list gap,
  once it was clear this was the same shape as two earlier B13 findings,
  not a one-off. Extended to a fourth instance during §7 step 11b's live
  E2E run - see the dedicated submission-item entry below for the full
  writeup; noted here only because it fits this exact shape, with one
  real difference from the first three: instances 1-3 were each fixed
  the same pass they were found, because a screen already under
  construction needed them. The fourth was found with no screen under
  construction to need it - nothing in Part J's own 17-screen list ever
  named one - so it's recorded as an open gap, not resolved-in-B13 like
  the others.
- **The pattern, stated precisely so a fourth instance is recognized
  faster:** a capability already exists correctly one or two layers down
  (a repository method, a domain-service function, sometimes both) but
  was never exposed - or was exposed with the wrong RBAC action - at the
  route layer a UI screen actually calls. This is a different shape from
  this file's other standing pattern ("a reader trusts a column its
  writer never keeps current," see the test-database-guard-duplication
  entry above) - that one is about state going stale after being wired
  up; this one is about the wiring never having been finished in the
  first place. Nothing was broken by drift here; nothing was ever
  connected.
- **The four instances, all found this block:**
  1. `GET /employees/{knowledge_item_id}` - Employee is a KnowledgeItem
     subtype; the generic item fetch existed, a dedicated one didn't,
     found building screen 4. Resolved-in-B13.
  2. `view_gates` - `list_gates`/`get_gate` existed and worked, gated on
     `create_bid` (a narrow authoring action) instead of a real read
     action, found building the shared gate-control component ahead of
     screen 8. Resolved-in-B13.
  3. `GET /proposal/sections/{section_id}/claims` - the repository
     method (`list_proposal_claims_for_section`) has existed since B9;
     nothing above it ever called it, found building screen 13.
     Resolved-in-B13.
  4. `SubmissionRepository.update_submission_item` - has existed since
     B5, zero callers anywhere in either app, found not by building a
     screen but by the live E2E run reaching final readiness and finding
     it permanently blocked. Open - see the dedicated entry below for
     why this one isn't B13's to close.
- **Why this is worth naming as a pattern:** the first three were each
  found by the same mechanism (a UI block trying to do something a
  screen genuinely needs and discovering the route doesn't exist or is
  mis-scoped) and each fixed the same pass. The fourth confirms the
  earlier prediction that this wasn't a closed list, but breaks the
  "found while building, fixed while building" shape - nothing in Part
  J's 17 screens ever needed it, so nothing ever went looking. Worth
  remembering for whichever block eventually builds a submission-item
  screen: check this pattern first, the repository method is very
  likely still there and still correct.
- **Status:** instances 1-3 resolved-in-B13. Instance 4 open, owner
  named in its own dedicated entry below.
- **Test:** none - this is a naming/recognition entry, not a behavior;
  see each instance's own entry for its test coverage.

## [B13] `POST /tenders/{id}/redteam/run` crashed with an unhandled `GraphStaleError` whenever the graph wasn't freshly projected - resolved

- **Discovered:** §7 step 10's own first route test for this endpoint
  (`tests/route/test_redteam_routes.py::
  test_trigger_red_team_runs_clean_on_a_fresh_tender`) - a real,
  reproducible crash, not a hypothetical, on the most ordinary possible
  input: a brand-new tender that has never had a graph projection run.
- **Behaviour:** `run_red_team`'s 5-step check list (`redteam_service.py`)
  includes `check_graph_contradictions` (`redteam_checks.py`), which calls
  `graph_service.find_contradictions_for_redteam` → `find_contradictions`
  → `check_freshness` unconditionally. `check_freshness` raises
  `GraphStaleError` whenever the latest completed projection doesn't match
  the tender's current `state_version` - "including when no successful
  projection has ever run" (its own docstring). Nothing between that call
  and `routes/redteam.py`'s `trigger_red_team` route caught it, so it
  propagated as an unhandled exception (an unstyled 500, not the
  documented 409 GRAPH_STALE contract `routes/graph.py`'s own read routes
  correctly return for the identical exception).
- **Why this wasn't caught earlier:** `run_red_team` is also invoked
  automatically by the LangGraph `red_team` node, and that path is
  genuinely safe - traced directly in `orchestration/graph.py`, not
  assumed: `red_team`'s only incoming edge is `project_graph → red_team`
  (`graph.py:104`, unconditional), and the bounded revision loop
  (`targeted_replan → ... → proposal_drafting → project_graph → red_team`)
  always re-enters through `project_graph` too - there is no path into
  `red_team` that skips a fresh projection. The manual trigger route
  (`POST .../redteam/run`, itself an interpretation beyond G11's locked
  3-route list, added specifically so a human can re-run red-team review
  on demand "independent of the full graph's automatic timing" - see its
  own routes/redteam.py comment) has no such guarantee, and nothing
  exercised it against an unprojected tender until this step's route test
  existed.
- **Fix:** `check_graph_contradictions` now catches `GraphStaleError`
  around the graph query specifically and returns `[]` - this one check
  contributing nothing this pass, not the whole run failing. Deliberately
  not fixed by making `trigger_red_team` return 409 GRAPH_STALE instead:
  the other 4 deterministic checks and the LLM critique have no graph
  dependency and can always produce useful findings, so aborting the
  entire red-team pass over one check's unmet precondition would throw
  away real, available information for a graph-unrelated reason. Contrast
  deliberately preserved: `routes/graph.py`'s own read routes are
  *answering a question about the graph itself*, where staleness is a
  real answer worth a 409; `run_red_team` is answering a broader "what can
  Red Team find right now," where a stale graph is just one input being
  temporarily unavailable.
- **Test:** `tests/route/test_redteam_routes.py::
  test_trigger_red_team_runs_clean_on_a_fresh_tender` - failed with the
  real `GraphStaleError` traceback before the fix, passes after.

## [B13] §1's own dependency analysis missed that step 7 ("select candidate"), not only the added 14th step, needs a pre-seeded company-knowledge item

- **Discovered:** the §7 step 11 pre-flight, checking fixture
  prerequisites before triggering any billed run - looking for the
  company-knowledge seed §1 already names, and finding it was scoped
  narrower than the scenario actually needs.
- **Behaviour:** §1's own text states the added 14th step (knowledge-
  selection impact preview) "needs at least one company-knowledge item
  pre-seeded in the test fixture's organization to select against...
  the fixture data has to carry this, not a live import step." True, but
  incomplete: step 7 of Part J's own 13 named steps ("select candidate")
  has the identical dependency - `check_role_qualification_gaps` and the
  whole talent-matching flow need a real, verified `KnowledgeItem`
  (employee) in the org before any role can be matched against one, and
  nothing about step 7 was ever flagged as needing this in §1's own
  per-step dependency notes, only the 14th step was.
- **Why it was missed, stated plainly rather than left as "just an
  oversight":** at the time §1 was written (early B13 planning, before
  §7 step 6 had built screens 2/3/4), the knowledge-import UI surface
  didn't exist yet to trace this dependency against - §1's own
  reasoning for the 14th step's fixture need was itself derived from
  reading `knowledge_service.set_knowledge_selection`'s docstring and
  `graph_projection/queries.py`, code that existed then; step 7's
  matching dependency on the same underlying data was equally real at
  that time but nothing in that planning pass traced talent-matching's
  own prerequisites with the same care. A real miss in the plan's own
  dependency analysis, not a new gap introduced later - worth recording
  as that, not softened to "an area that later became relevant."
- **Status:** resolved as part of planning §7 step 11a (the Playwright
  spec itself, not yet written at the time this entry was added) - the
  spec's own setup phase drives real import → approve → verify for at
  least one employee through the actual UI (screens 2/3/4), before the
  13 named steps begin, serving both step 7 and the 14th step's
  identical dependency from one real setup pass rather than two.
- **Test:** none yet - the spec this entry's fix lives in hadn't been
  written when this entry was added; see §7 step 11a's own plan.

## [B13, owner B16] `settings.llm_provider` is dead configuration - 8 sites construct `GeminiClient` directly, bypassing the provider-neutral interface it exists to select

- **Discovered:** §7 step 11's own pre-run pre-flight - checking
  whether `FakeLLMClient` (already every automated test's own LLM
  double) could be swapped in on the running `api`/`worker` containers
  via env var, to make a full selector/harness rehearsal of the E2E
  spec free before spending real Gemini calls on it.
- **Behaviour:** `config.py`'s `llm_provider: str = "gemini"` carries a
  comment stating it "selects which `agents/llm/*_adapter.py`
  implementation gets constructed." Grepped for every read of
  `settings.llm_provider` across both apps: zero matches, anywhere.
  The comment describes behaviour the field does not have. Every actual
  construction site hardcodes `GeminiClient(...)` directly, in 8
  separate places across two apps, each with its own local
  `_build_llm_client()`-shaped function: `orchestration/nodes/
  requirement_extraction.py`, `compliance.py`, `talent.py`,
  `commercial.py`, `proposal.py`, `redteam.py` (the 6 automatic
  LangGraph nodes), plus `apps/worker/.../jobs/knowledge_import.py` and
  `verification.py`. (A further 3 route-level `_build_llm_client()`
  functions in `apps/api/.../routes/proposal.py`/`redteam.py`/
  `commercial.py` exist too, for the manual on-demand triggers - not
  counted separately above since they're the same pattern, just a
  smaller fraction of a full run's total LLM cost than the 6 automatic
  nodes.) A2's own locked stack decision is "a provider-neutral
  `LLMClient` interface with model IDs supplied purely as
  configuration" - the interface exists and is honored (every
  construction site returns something satisfying `LLMClient`), but the
  "purely as configuration" half never got wired past `GeminiClient`
  itself. Confirmed directly this pass, not assumed: even if
  `llm_provider` *were* read somewhere, swapping it to `FakeLLMClient`
  bare (no scripted responses) would not produce thin/empty output as a
  naive reader might expect - `FakeLLMClient` is deliberately built to
  raise loudly on the first unscripted call ("fails loudly at the call
  site, not as a confusing downstream assertion failure," its own
  docstring), so real wiring would also need generously-sized generic
  scripted responses queued at container startup for every distinct
  agent name in the pipeline, not just a flag flip.
- **Consequence, stated plainly since it's the part future blocks need
  to know, not just this one:** there is no way to run the system
  end-to-end against the real stack without spending real, billed LLM
  calls. §7 step 11's own pre-run idea (exercise every selector/wait/
  navigation for free, save the real Gemini spend for one confirming
  run) was not possible for exactly this reason, and had to be dropped
  in favor of running the real spec once, live cost accepted. This is a
  real, structural constraint on every future block that wants to
  exercise the full path cheaply or repeatedly - not unique to B13's
  own E2E spec.
- **Status:** not fixed - real work (wiring 8 call sites plus sized
  fake-response scripts per agent name), correctly declined for this
  pass rather than built as a side effect of a pre-flight check.
  **Owner: B16 (Full Evaluation, Reliability, and Load Tests)** - this
  is exactly the kind of full-stack, repeated-run infrastructure a
  reliability/load-test block needs and would very likely have to build
  anyway to run its own suite affordably; B13's own E2E spec is a
  smaller, one-shot consumer of the same capability, not its natural
  owner. B15 was considered and set aside without a documented reason
  strong enough to prefer it here - nothing in this session traced B15's
  own scope closely enough to argue it fits better, so the choice is
  B16 on the strength of the evaluation/reliability match, not a
  comparison actually made between the two.
- **Test:** none - this is dead-configuration characterization, not a
  regression to guard yet.

## [B13] No screen manages submission-item status - final readiness is unreachable through the UI by construction, independent of every other blocker

- **Discovered:** §7 step 11b's own live E2E run - the first time
  anything drove this tender through requirement extraction to a real
  readiness check, and the first time this gap had a chance to become
  visible. `readiness_service.py`'s blocker 4
  (`missing_mandatory_submission_item`) fired for every mandatory
  submission item on the tender (14 of them, real ones: signed cover
  letter, tax clearance certificate, technical proposal, and others
  extracted from the real synthetic RFP package) - all still at their
  extraction-time default status.
- **Behaviour, checked precisely rather than assumed:**
  `submission_items.status` (Part C's own schema) has four values -
  `missing`, `present`, `needs_review`, `not_applicable` - and a
  `matched_document_id` column, suggesting the original design intent
  included automatic matching against uploaded documents. In practice,
  extraction creates every item at `status='missing'`
  (`db/models/requirement.py`'s own default) and nothing anywhere ever
  moves it: `SubmissionRepository.update_submission_item` exists (since
  B5) but has zero callers in either app - confirmed by grep, not
  assumed - and no API route exposes submission items at all, not even
  a read-only `GET`. This is the fourth instance of this block's own
  "API surface lagging the repository layer" standing pattern (see
  above), the one instance nothing was building toward when it was
  found.
- **Consequence, stated plainly:** with every mandatory submission item
  starting and staying at `missing`, blocker 4 fires on every real
  tender with at least one mandatory submission item extracted -
  independent of and in addition to whatever else is or isn't resolved.
  `final_readiness_approval`'s own gate can still be reached and waived
  (`GateDecisionPanel`'s waiver checkbox exists for exactly this), but
  the honest, unwaived `ready: true` state PRD 10.13 and Build Guide
  Part J both describe is unreachable through any of the 17 screens as
  they exist today, on any tender with a real submission checklist -
  not a corner case, the default extraction outcome.
- **Checked before naming an owner, per this project's own PRD-vs-Build-
  Guide conflict rule (CLAUDE.md):** the PRD names "Submission checklist"
  as an in-scope tracked capability three separate times (§5's own list
  of what a bid tracks, §6.2's document-type list, §10's capability
  list) and asks "which submission artefacts are missing" as a
  user-facing question (§4, §10.13) - but never once describes a human
  action for marking an item received or complete. The Build Guide's own
  B5 build list scopes "Submission-item extraction" only (read/create,
  not update), and Part J's own 17-screen enumeration never names a
  submission-checklist screen at all. Both documents are silent on the
  same point, not in conflict with each other - this is a genuine gap in
  what either spec asked for, not a Build-Guide omission of a PRD
  requirement. Flagged as such rather than resolved silently, per
  CLAUDE.md's own instruction for exactly this shape of ambiguity.
- **Status:** open, no owner named. Not B13's - Part J's own 17 screens
  never asked for this one, so building it now would be scope B13 was
  never given, not a bug B13 introduced. Whoever scopes the next block
  touching readiness or submission tracking should treat this as a real,
  load-bearing gap - final readiness cannot be honestly reached without
  it - not a nice-to-have.
- **Test:** none - `tests/route/` has no submission-item coverage
  because no route exists to cover.

## [B13] §1's own stale-graph reasoning was correct for the automatic path and silently applied to the manual one - a conflation, not a stale-data bug

- **Discovered:** §7 step 11b's real readiness result -
  `stale_graph_projection` fired as one of the blockers, which the
  plan's own §1 had explicitly reasoned should NOT happen at this point
  in the scenario.
- **The two facts, both independently true, wrongly treated as one:**
  §1 argued (correctly, and confirmed correct by tracing the actual
  graph edges in `orchestration/graph.py`) that `project_graph` runs
  unconditionally, automatically, immediately before
  `readiness_generation` in the LangGraph topology - so a readiness
  check reached by letting the automatic workflow run to completion is
  guaranteed a fresh projection. Separately, and also correctly: the
  E2E spec's own step 13 calls the *manual*
  `POST /tenders/{id}/readiness/generate` route (the same button screen
  15 exposes to a human), which calls `readiness_service.evaluate_
  readiness` directly - a standalone function with no graph-projection
  step of its own ahead of it, by design (it has to be independently
  callable, including from a tender with no live workflow run at all).
  §1's own guarantee is real and holds - for the automatic path only.
  It does not extend to the manual route, and nothing in §1, the
  pre-flight, or the spec's own review ever checked whether the
  scenario was actually going to exercise the automatic path or the
  manual one at that exact moment.
- **Why this is named as a shared miss, not a planning-only one:** this
  reasoning was written into §1, restated in the §7 step 11 pre-flight's
  own step-13 prediction, and approved at each point - by the plan's own
  author and by the reviewer, in the same conversation, without either
  catching that "the automatic path is safe" and "the spec uses the
  manual path" are two different claims. The 14th step's own
  knowledge-selection change bumps `state_version` after the last
  automatic projection (from whatever automatic pass, if any, already
  ran earlier in the workflow) with nothing re-projecting before the
  manual readiness check reads it - a real, mechanical staleness, not a
  false positive.
- **Status:** accepted, not fixed - the blocker firing here is `readiness_
  service.py` working exactly as designed (blocker 7 is supposed to
  catch a stale graph; it did). What's recorded here is the reasoning
  error, so it isn't repeated: a guarantee proven for the automatic
  workflow path is not a guarantee for a manual route that calls the
  same underlying function, even when both eventually reach the same
  code. Anyone re-deriving "X can't happen here" for a scenario mixing
  automatic workflow continuation with manual UI-button triggers should
  check which path is actually live at that point, not assume the
  topology's own guarantees travel with the function name.
- **Test:** none - this is a reasoning-error record, not a behavior to
  guard.

## [B13] E2E harness bug: premature navigation cancelled in-flight requests, twice

- **Discovered:** §7 step 11b, runs 1 and 2 of the E2E spec.
- **Behaviour:** two call sites in `full-flow.spec.ts` navigated
  (`page.reload()` or `page.goto()`) immediately after clicking
  something whose own handler was an in-flight async request - the
  setup phase's resume upload (`handleResumesUpload`'s
  `uploadResumes(...)` call) and, separately, every gate-approval click
  (`GateDecisionPanel`'s `decideGate(...)` call) immediately followed by
  navigating to the next screen. A browser cancels an in-flight
  fetch/XHR on navigation - `setInputFiles`/`.click()` only wait for the
  DOM event to fire, never for an async handler's own request to
  complete, so both call sites had a real chance of navigating away
  before the request ever reached the server. Confirmed directly, not
  assumed: run 1's own DB state showed zero `knowledge_import_batches`
  rows after repeated reloads, despite the file input showing the
  correct selected file moments earlier in the trace.
- **Fix:** wait for the real DOM signal that confirms the async handler
  completed, before navigating - the resumes input's own `disabled`
  attribute clearing, or (for gate decisions) the "Approve" button
  itself disappearing once `gate.status` flips off `pending`. Both
  fixes are the same shape: don't infer "the request is done" from an
  action's own return; wait for its actual, observable effect.
- **Test:** none automated - this is a spec-writing lesson, not a
  product regression. Anyone writing the next Playwright scenario
  against this app should assume every button click that triggers a
  `fetch` needs an explicit post-click wait before any navigation, not
  just the obviously-slow ones.

## [B13] E2E harness bug: `page.reload()` does not wait for React's own data-fetching effects - the one that will bite B14's SSE work

- **Discovered:** §7 step 11b, run 3 - the Commercial approval gate
  wait failed after the full 300s budget, even though the gate had
  opened server-side within 11 seconds of the previous gate's approval
  (confirmed via `approval_gates.requested_at`). Run 3's own trace
  showed why: every `queryCount` check landed ~170-200ms after its
  preceding `page.reload()`, reading the DOM before
  `listWorkflowRuns`/`listGates` (sequential awaited fetches inside a
  `useEffect`) had resolved - every single time, for the entire window.
- **Behaviour, the part worth naming precisely:**
  `page.reload({waitUntil: "load"})` resolves once the page's own
  HTML/JS/CSS finish loading and React has completed its first render -
  it does not and cannot wait for `useEffect`-triggered API calls fired
  *after* that first render, because those are asynchronous work with
  no correlation to the browser's own "load" event. Three of this
  block's four gate-waits happened to win that race often enough across
  ~250 retries (a new reload every ~1.2s) to succeed within budget
  anyway - not because the pattern was safe, but because polling
  frequently enough gave chance enough opportunities. The fourth did
  not. This was never a per-gate problem; it was a structural property
  of every `page.reload()` in the file immediately followed by a
  one-shot DOM read (`queryCount`, `textContent`), as opposed to an
  auto-retrying Playwright assertion (`expect(locator).toHaveCount(...)`,
  which does not have this problem, because it keeps re-querying until
  it passes or times out on its own).
- **Fix:** a shared `reloadAndSettle(page)` helper -
  `page.reload()` followed by `page.waitForLoadState("networkidle")` -
  used everywhere a polling loop reloads before reading state.
  `networkidle` (no in-flight requests for 500ms) is discouraged for
  general Playwright use, but is the correct, narrow tool for exactly
  this need: confirming a page's own initial data-fetching effects have
  actually settled, which nothing else in Playwright's public API
  observes directly.
- **Why this matters beyond this one spec:** any future Playwright
  scenario against this app that reloads a page and then reads state
  without either an auto-retrying assertion or an explicit settle-wait
  has this exact latent bug, whether or not a given run happens to win
  the race. B14 (SSE/real-time updates) is named specifically because
  it will change several of these screens' own fetch-then-render timing
  - a spec relying on lucky timing today could start failing, or
  worse, start silently under-testing, once that lands, for reasons
  that would look unrelated to the actual change.
- **Test:** none automated. `full-flow.spec.ts`'s own `reloadAndSettle`
  helper and its docstring are the durable record of this; any new
  polling loop added to that file (or a future E2E spec) should use it
  rather than a bare `page.reload()`.

## [B13] E2E harness bug: waiting for "at least one" instead of the real expected count silently truncated the candidate pool - and produced a wrong finding about why

- **Discovered:** CodeRabbit's review of this block's diff flagged the
  setup phase's own resume count (4 files imported, a comment claimed 5)
  as a documentation mismatch. Fixing that comment required explaining
  *why* importing every resume mattered, which cited readiness blocker 6
  (`role_with_no_selected_candidate`) firing at step 13 as the risk this
  guarded against - a claim nobody had actually checked against the real
  run's own data before writing it down. Checked, by query against the
  real post-run `pg_dump` (not by reading more code, per explicit
  instruction): blocker 6 *did* fire once, on Project Manager, and the
  first-pass explanation for it ("candidate headcount vs. role count")
  was wrong.
- **Behaviour, established by direct query, not assumption:**
  `role_matches` for Project Manager had exactly one row (Dana
  Whitfield, score 0.2), not zero - `talent_analysis_node`'s
  cross-product matching (`orchestration/nodes/talent.py`) worked
  correctly and did produce a real candidate. `human_selected` on that
  row was `false`. Cross-checked against `employees`: of the four
  imported people, only Dana had `eligible_for_bid=true`/
  `profile_review_status='verified'` - the other three were still at
  `needs_review`. Cross-checked against `knowledge_items.created_at`:
  the four employees appeared in the database at 04:11:06, 04:11:19,
  04:12:31, and 04:12:45 - real, sequential, per-resume LLM extraction,
  not a batch that lands atomically. Cross-checked against
  `audit_events`: exactly one `employee_verify` event exists for this
  org, at 04:11:09 - three seconds after Dana's own row was created, and
  well before any of the other three existed. The setup phase's own
  employee-discovery poll (`full-flow.spec.ts`, right after the resume
  import) waited only for `count() > 0` before snapshotting
  `employeeHrefs` and verifying each one - it caught Dana, the first to
  appear, verified only her, and never went back for the other three,
  who arrived over the next ~95 seconds. Every downstream role's
  candidate pool was silently capped at one person, not four, for the
  entire rest of the run - not because matching has a threshold (it
  doesn't), but because the pool it drew from was truncated upstream.
- **Why blocker 6 fired on exactly one role and not all five:** with
  Dana as the only ever-eligible candidate, every role's real candidate
  list was `[Dana]` by the time step 7 (select candidate) ran - so the
  pre-fix version of that loop's own async-race bug (a separate,
  already-recorded finding: `TeamWorkspace.tsx` doesn't clear the
  previous role's matches before its own fetch resolves) was almost
  always harmless here, because a stale read and a fresh read named the
  same person. The one role processed *first* in that loop had no stale
  data to fall back on - its read landed against the component's true
  initial empty state, which renders identically to "this role really
  has no candidates." Four roles, four rolls of the same race; one came
  up wrong.
- **The finding this replaces:** `full-flow.spec.ts`'s own comments
  (both at the resume-import step and at step 7) previously reasoned
  from code alone that candidate headcount couldn't be the risk given
  `talent_analysis_node`'s cross-product shape, and treated blocker 6 as
  a `ready: true`-compatible "residual risk... not a bug in this test."
  Both statements needed correcting once weighed against what the run
  actually produced, not reconciled by picking one: the cross-product
  claim was correct as a description of the matching algorithm; the
  "not a bug in this test" label was not - a live-run finding blocker 6
  legitimately caught was misattributed to the fixture instead of the
  harness that fed it.
- **Fix:** the employee-discovery poll now waits for the real expected
  count (`resumeFiles.length`, matching the document-upload loop's own
  `toHaveCount(expectedDocCount, ...)` discipline a few steps above)
  instead of `toBeGreaterThan(0)`. With all four employees verified
  before step 7 runs, every role gets its real four-candidate pool, and
  `ready: true` is the correct expectation again - no fixture change
  needed, and no reason to assert the blocker as expected behavior
  instead.
- **Test:** none automated - not re-run against the live stack as part
  of this pass (that would be a new billed E2E run, not a review-fix
  pass). The corrected poll condition and this entry are the record;
  the fix should be confirmed the next time `full-flow.spec.ts` runs for
  real.

## [B13, owner B14] Every screen in this UI is a snapshot - the system keeps running after a human looks away, and nothing tells them

- **Discovered:** §7 step 11b's real E2E run, diagnosing why 3 red-team
  findings were still unresolved at step 13 despite step 12's own
  resolve-loop clicking every `Resolve` button visible when it ran. The
  diagnosis (see the run's own record) confirmed the loop missed
  nothing on screen - `agent_traces` shows four separate
  `redteam_critique` calls for one tender, not one, meaning `run_red_
  team` executed at least four times: once from this spec's own single
  manual trigger, and at least once more afterward with no further
  human action - almost certainly B11's own bounded revision loop
  (`route_after_red_team` → `targeted_replan` → back through drafting →
  `project_graph` → `red_team` again) continuing to run automatically in
  the background.
- **The finding, stated at the right level - not "the test's loop
  missed one":** the scenario navigated away from the red-team screen
  once it looked clear, exactly as a real reviewer would. The workflow
  kept working after that. Ten seconds later it produced new, real
  findings that nothing - not the scenario, not a human doing the same
  thing - would ever see without either staying on that screen
  indefinitely or happening to reload it again at the right moment. A
  human who approved the commercial gate, watched red-team run once,
  saw the list empty out, and moved on to generate the proposal would
  have done everything asked of them and still acted on a screen that
  had already gone stale behind their back. This is not a test
  artifact - the scenario is a faithful stand-in for that human, and it
  was fooled the same way.
- **Cross-referenced against `usePolling`'s own design note
  (`apps/web/src/hooks/usePolling.ts`), not assumed adjacent to it:**
  polling already exists in this codebase, and does not cover this case
  - `usePolling` answers "is the specific thing I'm already watching
  done yet" (functional requirement 5, `UploadAndProcessingStatus.tsx`'s
  own task list, an open resource on a screen currently being viewed).
  It has no mechanism for "something changed on a resource I'm not
  currently polling, or that didn't exist when this screen last
  fetched." More frequent polling everywhere would not fix this either
  - the actual gap is a screen having no way to know work is continuing
  *elsewhere*, not that it's polling too slowly wherever it does poll.
- **Status:** open. **Owner: B14** - not by default assignment, but
  because B14's own scope (SSE progress streaming, replacing
  `usePolling`'s interval with a push subscription per `usePolling`'s
  own docstring: "Progress updates through polling initially; SSE by
  B14") is specifically the mechanism that answers "work is still in
  flight," which is exactly what this gap needs and nothing already
  built provides. Recorded as the first concrete demonstration of why
  B14 exists, not a theoretical nicety it happens to also cover - this
  block found a real, reproducible case of a human (or a faithful
  stand-in for one) being shown a false "done" state by a live system,
  not a hypothetical one.
- **Test:** none - the E2E run's own timestamped DB state
  (`redteam_findings.created_at`, `agent_traces` for `redteam_critique`)
  is the evidence trail; no automated test exists for "did the UI miss
  background work" because nothing in the current architecture could
  make one pass without B14's own mechanism first.

---

## [B14] SSE transport decision: outbox + direct API-side poll, not Redis pub/sub or LISTEN/NOTIFY - a design decision, not a triage note

- **Discovered:** B14 planning, while resolving what `GET /workflow-runs/
  {run_id}/events/stream`'s underlying data source should be. Recorded
  here as the decision itself, with its rejected alternatives and why -
  not a limitation to fix later, the same shape as this file's own
  "`target_updates` batch approval... a decision, not a triage note"
  entry above.
- **The real starting facts, checked rather than assumed:** the write
  volume behind this decision is small - `create_task`/
  `mark_task_completed`/`mark_task_failed` are bounded by task *type*
  (~4-6 per run; `requirement_extraction_node`'s own docstring says
  "runs once per workflow run" - one task, an internal loop over every
  requirement, not one task per requirement), and `sync_run_status`'s
  ~19 real per-run calls on a happy path with 2 revision passes
  cross-validated exactly against live B13 data pulled from Postgres
  this session (3 `graph_projection_runs` rows, matching 3
  `projecting_graph` status writes for that tender). ~25-30 total writes
  across a 20-40 minute run - not a hot path under any of the three
  options priced below. The worker's `outbox_dispatcher.py` polls every
  `POLL_INTERVAL_SECONDS = 5` (`:33,205`); `apps/api/main.py` has no
  `lifespan`/startup hook (grepped, zero matches) - no long-lived
  connection exists anywhere in the API process today.
- **Redis pub/sub - rejected.** `PUBLISH` has no transactional coupling
  to Postgres. A publish issued before a commit that then fails delivers
  a notification for a state change that never happened; the only fix
  (publish strictly after a successful commit) is a scattered,
  convention-only discipline across the ~11 real call sites
  (`db/repositories/workflow.py`'s 4 methods, called from ~11 places
  across `orchestration/nodes/`), with no structural enforcement. This
  project's own history - `set_candidate_selection`'s vacuous
  self-comparison check, the stale-column patterns already logged above
  - is specifically about conventions that hold everywhere except the
  one place nobody checked; no reason to expect this one holds
  indefinitely across 11 sites either.
- **Postgres `LISTEN`/`NOTIFY` - real, viable, deliberately deferred, not
  dropped.** asyncpg (`pyproject.toml:7`) has first-class support, and a
  `NOTIFY` issued inside a transaction is deferred by Postgres itself
  until commit and dropped on rollback - atomically correct by
  construction, the same guarantee the outbox gives, with no durable row
  required. Not adopted now because it needs genuinely new
  infrastructure this codebase doesn't have (a raw `asyncpg` connection
  held per open SSE stream, bypassing the SQLAlchemy engine/session
  layer entirely - nothing to reroute, has to be built from scratch),
  and even built, it has zero reconnect-replay on its own (a `NOTIFY`
  fired while nobody is listening is simply gone) - a complete design
  would still want the outbox underneath it for that, so `LISTEN`/
  `NOTIFY` alone was never a self-sufficient answer even if the new
  connection-handling code were written.
- **Decision:** the outbox, written in the same transaction as each of
  the 4 `WorkflowRepository` choke points (`update_run_status`,
  `create_task`, `mark_task_completed`, `mark_task_failed` -
  `db/repositories/workflow.py:57-131`), matching the existing
  `DocumentUploaded`/`KnowledgeDocumentUploaded` pattern exactly - safe
  on rollback by construction, free reconnect-replay for an SSE client
  that drops and reconnects. Delivery is the SSE route polling the
  outbox table directly at ~1s, deliberately bypassing the dispatcher's
  own, separate 5s tick (routing through the dispatcher would add a
  second, unnecessary cross-process hop on top of its own 5s floor -
  the dispatcher and the API run as separate containers). Treated as a
  wake-up signal only, not the payload - G13's own payload is
  metadata-only text and says not to stream real content
  (`TenderOS-Build-Guide.md:2640-2652`), so on seeing a new row the
  route re-queries `workflow_tasks`/`workflow_runs` (the same tables
  `listWorkflowTasks`/`getWorkflowRun` already read) for current state
  rather than serializing task/run state into the outbox payload itself
  - avoids a second copy of the same fact with its own chance to drift,
  matching `evaluate_readiness`'s own "recompute at read time"
  precedent.
- **Honest characterization, on record so the next person doesn't have
  to re-derive it:** this is server-side polling of the outbox,
  re-emitted as a client-facing push stream - not database-level push.
  It solves the actual requirement (no human reload) but isn't "SSE" in
  the strictest sense underneath. If ~1s ever proves inadequate in
  practice, `LISTEN`/`NOTIFY` is the named path to genuine push, not a
  redesign from scratch - the outbox-write half of this decision stays
  exactly as built either way.
- **Status:** accepted, decided, and now built and verified live - not
  revisited unless ~1s latency is measured as inadequate against a real
  stop-gate run. The 4 choke-point outbox writes, the SSE route's own
  wake-up-then-re-query design, and the honest polling-not-push docstring
  are all in place (`routes/workflow.py::_workflow_progress_stream`,
  `db/repositories/workflow.py`). Live-verified in a real browser (local
  dev server, not yet the Docker rebuild) - a `WorkflowTask` row written
  directly against Postgres appeared on an already-open Upload and
  Processing screen with zero reload or navigation, confirmed via two new
  `GET /workflow-runs/{id}` / `.../tasks` requests firing automatically in
  the browser's own network log seconds after the write, no console
  errors.
- **Test:** `tests/integration/test_workflow_repository_outbox.py` (the 4
  choke points each write the correct outbox row, and a repeat status
  change doesn't collide on `idempotency_key`); `tests/integration/
  test_workflow_events_stream.py` (the SSE generator itself - a heartbeat
  when nothing's changed, a real `task.created` message once a task
  exists, and cross-run isolation - a second run's event never leaks into
  the first's stream); `tests/integration/test_workflow_api.py::
  test_stream_workflow_events_rejects_a_caller_outside_the_organization`
  (real HTTP, real RBAC). See the ASGITransport testing-limitation entry
  below for why the full HTTP *delivery* path (not just the auth
  rejection) has no automated test and is instead covered by the live
  browser check above.

---

## [B14] SSE auth: existing JWT as a query param, not a header - a decision, not a workaround

- **Discovered:** B14 build, writing `GET /workflow-runs/{run_id}/events/
  stream`. `EventSource` has no way to send a custom `Authorization`
  header, and this app authenticates purely via a `localStorage`-held JWT
  bearer token (`apps/web/src/api/client.ts:11,89,108,129` - no cookies
  anywhere), confirmed by reading the client directly before assuming
  either cookie-based auth (which would make this a non-issue) or header
  auth (which would make EventSource unusable outright).
- **Three options priced, not defaulted into one:**
  1. **The existing access token as a query parameter** (`?access_token=
     ...`) - zero new dependencies, zero new backend token-minting
     infrastructure. Real, accepted exposure: the token appears in server
     access logs and browser history for its `jwt_access_minutes` (15)
     life.
  2. **A fetch-based SSE library** (e.g. `@microsoft/fetch-event-source`)
     - avoids the token-in-URL exposure entirely (the existing
     `Authorization`-header auth just works over a normal `fetch()`), at
     the cost of this project's first SSE dependency and losing native
     `EventSource`'s own reconnect behavior, which would then need
     reimplementing by hand.
  3. **A short-lived, single-use SSE-only ticket**, minted by its own new
     endpoint - the actually-correct production pattern, deliberately not
     built here: it's real token-minting infrastructure, and B15
     (Security and Prompt-Injection Hardening) owns auth hardening: this
     block would be making a decision that block should make itself.
  Decided: **option 1.** `apps/api/src/tenderos_api/dependencies.py`'s
  `get_current_user_for_sse`/`require_permission_for_sse` accept the
  token from either the header (curl/Swagger/non-browser callers keep
  working) or the query param (what a real `EventSource` connection
  actually uses) - same decode-and-lookup as `get_current_user`, via a
  shared `_user_from_token` helper, not a separate auth policy.
- **The real, accepted exposure, named precisely:** the access token
  sits in `GET` request URLs for up to 15 minutes per connection -
  visible to anything that logs server access logs, browser history, or
  a `Referer` header from that page. Not fixed here; option 3 above is
  the named correct fix and is B15's, not this block's.
- **The UX consequence of choosing this over option 3, stated plainly
  since it's a real, user-facing gap, not just a security one:** once
  the 15-minute token expires, `EventSource`'s own automatic reconnect
  reuses the exact same (now-expired) URL - the browser's own retry gets
  a fatal, non-retryable HTTP error from the server and `EventSource`
  stops retrying on its own, `readyState` becomes `CLOSED`. `apps/web/
  src/hooks/useEventStream.ts` detects this and sets its own `error`
  state - but nothing currently reads it: `UploadAndProcessingStatus.tsx`
  (this hook's only consumer) destructures only `{ data: tasks }`/
  `{ data: activeRun }`, the same way it already ignored `usePolling`'s
  own `error` return before this swap. So the concrete failure mode is
  **silent staleness, not a visible error**: a workflow run open in a
  browser tab for more than 15 minutes (plausible - B13's own full E2E
  run took ~40 minutes) stops updating with no indication anything is
  wrong. This is not a new gap `useEventStream` introduces - it's the
  exact same pre-existing `usePolling`-consumer gap (a throwing fetcher
  was equally invisible), now reachable by an ordinary session length
  instead of requiring a fetch to literally throw. Fixing the *consumer*
  to render `error` is a real, separate, small fix with no dependency on
  B15's own ticket-minting work - not done here, since it's a UI-polish
  concern outside this block's own scope, but flagged so it isn't
  mistaken for something B15's fix would also close (it wouldn't -
  option 3 removes the *token-expiry* trigger, not the *ignored-error*
  gap underneath it).
- **Status:** accepted, decided. Revisit if a real reviewer session is
  observed hitting this in practice, or when B15/a UI-polish pass wires
  `error` into this component.
- **Test:** `get_current_user_for_sse`'s dual-path behavior is exercised
  live (header path: every other route's own tests; query-param path:
  `tests/integration/test_workflow_api.py::
  test_stream_workflow_events_rejects_a_caller_outside_the_organization`
  and the live browser check above, both authenticate via the query
  param). No test exists for the token-expiry-mid-stream behavior itself
  - would need a token minted with a sub-second expiry to observe
  live, not attempted here.

---

## [B14] httpx.ASGITransport cannot test an unbounded StreamingResponse at all - a structural test-tooling limit, not a route bug

- **Discovered:** B14 build, writing the first automated test for `GET
  /workflow-runs/{run_id}/events/stream`. A route-level test using this
  project's own established `httpx.AsyncClient`/`ASGITransport` pattern
  (`test_workflow_api.py`'s pre-existing convention) hung indefinitely -
  first tried against `fastapi.testclient.TestClient` (60s+, no
  response), then again against `AsyncClient` directly after switching
  to avoid `TestClient`'s own sync/thread-hop concerns (still hung,
  properly bounded by `asyncio.wait_for` this time rather than left to
  hang unbounded a second time).
- **Root-caused directly, not assumed:** a minimal, isolated repro (a
  bare FastAPI app, one route returning `StreamingResponse(gen())` with
  an infinite `while True` generator, no other code involved) reproduced
  the identical hang - `AsyncClient.stream()`'s own `__aenter__` never
  returns. The traceback shows why: `ASGITransport.handle_async_request`
  awaits the ASGI `app()` call to completion before it hands anything
  back to the httpx client; Starlette's own `StreamingResponse.__call__`
  runs the body generator and a concurrent `listen_for_disconnect` task
  (awaiting `receive()`) side by side, and `ASGITransport`'s own mock
  `receive()` only resolves on a real client disconnect signal - which
  never arrives in-process, because there is no real client to send one.
  A *finite* generator (the same repro with `for i in range(3)` instead
  of `while True`) completes and returns normally - the hang is
  specifically about *unbounded* generators, which is exactly this
  route's own shape by design (an SSE stream is meant to stay open
  until the client disconnects).
- **Risk:** this is not new to B14 - any future route returning an
  unbounded `StreamingResponse` will hit the identical wall the moment
  someone writes an `AsyncClient`/`ASGITransport` test for its actual
  content delivery, not just its opening handshake. The failure mode is
  a hang, not a clean error, which is a worse failure to debug cold than
  a clear "unsupported" message would be - worth recognizing on sight
  the next time, rather than re-discovering it the same way this entry
  did.
- **What this project's test suite actually covers instead, and why
  that's a real, not partial, substitute:** the SSE *generator itself*
  is tested directly, bypassing HTTP entirely (`tests/integration/
  test_workflow_events_stream.py`, calling `_workflow_progress_stream`
  as a plain async generator against real Postgres) - genuine coverage
  of the actual logic, not weaker than an HTTP-level test for that
  purpose. The HTTP *layer* (auth, RBAC, that the route is wired
  correctly) is tested for real, over real HTTP, at the one point that
  doesn't need incremental reads - a *rejected* connection returns
  immediately and never touches the generator at all
  (`test_workflow_api.py::
  test_stream_workflow_events_rejects_a_caller_outside_the_organization`,
  `test_workflow_routes.py::test_stream_workflow_events_rejects_a_missing_token`).
  What remains genuinely untested by any automated test is the full,
  successful HTTP delivery path end to end (a real 200, a real client
  reading real bytes as the server writes them) - closed instead by a
  live browser check (Playwright, local dev server) confirmed as part of
  this same block: a `WorkflowTask` row written directly against
  Postgres appeared on an already-open screen with zero reload, network
  log showing the two refetch requests firing automatically.
- **Status:** accepted as a structural test-tooling boundary, not
  something to work around with a differently-shaped test. A future
  block wanting real automated coverage of the full delivery path would
  need either a real running server (actual TCP, e.g. `uvicorn` in a
  background thread/process, not `ASGITransport`) or a bounded test-only
  variant of the generator (e.g. an injectable max-iteration count) -
  neither attempted here, since the live browser check already closes
  the actual risk (does delivery work) more directly than either would.
- **Test:** the isolated repro itself is the standing evidence (not
  committed - a throwaway script, reproducible from this entry's own
  description if ever needed again). No automated regression test exists
  for the ASGITransport behavior itself, since it's third-party library
  behavior, not this project's own code.

---

## [B14] `preview_impact` can only ever show something for a knowledge item that already has a `USED_IN` edge - and nothing before the 14th step ever creates one

- **Discovered:** live E2E runs 2 and 3 of the extended 14th step
  (`full-flow.spec.ts`), both failing at the step's own discovery loop
  with zero qualifying rows - the second run even after relaxing the
  loop's own criterion from "names both a Requirement and a
  ProposalClaim" down to "names a Requirement alone." Traced to the real
  cause by reading the projector directly, not by guessing at a third
  relaxation: `preview_impact`'s own Cypher (`graph_projection/
  queries.py:63-65`) hard-requires `MATCH (fact)-[u:USED_IN]->(:Tender
  {id: $tenderId})` as its literal first match clause, before any further
  traversal happens - and `used_in_rows` (`graph_projection/
  projector.py:400-412`) is built *only* from `tender_knowledge_
  selections` rows (`for sel in selections if sel.knowledge_item_id in
  resolved`). Nothing in `full-flow.spec.ts` calls the knowledge-
  selection PUT before the 14th step - every row on that screen has no
  selection row at all yet, hence no `USED_IN` edge, hence an empty
  `preview_impact` result regardless of what real compliance evidence
  that knowledge item backs in Postgres.
- **Why this is structural, not a fixture gap:** B7's own compliance
  evidence retrieval is deliberately *not* gated by
  `tender_knowledge_selections` (`compliance_service.py`'s own comment:
  "`selected_only` is deliberately not used here... retrieves evidence
  straight from the org's knowledge base with no prior 'select knowledge
  for this tender' step"). This means the set of knowledge items that
  actually back real compliance decisions and the set of knowledge items
  with a `USED_IN` edge in the graph are two genuinely independent sets -
  overlapping only for whatever a human (or this spec) has explicitly
  selected at least once. An item can be exactly what a compliance
  decision cites and still show "No downstream facts would be affected"
  in impact preview forever, until someone selects it.
- **The fix, in the spec:** the 14th step now selects a candidate first
  (creating its `USED_IN` edge and triggering one real reanalysis pass),
  previews it again, and only then decides whether to use it as the
  deselection target - not a discovery loop over passive preview calls,
  which can never show real impact for an item nobody has ever selected.
- **Correction to this file's own prior record: "Tax Clearance
  Certificate" is not a valid target for this spec, at all - not merely
  "not guaranteed twice."** Queried directly against the live B13 tender's
  own organization (`dedf113a-74ce-4c8b-a20e-5c8b9da9c543`): exactly 4
  knowledge items exist, all `item_type='employee'` (Dana Whitfield,
  Marcus Ilori, Priya Ostrowski, Tomás Reyes) - zero certifications.
  `full-flow.spec.ts`'s own setup phase imports resumes only, never
  `certifications.xlsx` - the B9/B12 runs that observed "Tax Clearance
  Certificate"/"PMP" grounded used a different import path this spec's
  own setup never exercises. An earlier version of this plan named that
  certification as the empirically-best target for this exact spec; that
  was wrong, not merely optimistic - the row cannot exist here, so it
  would have failed the same way run 2 did, only after spending the
  selection/reanalysis cost first. Fixture gap, not a code gap: this
  spec's own setup phase has no certification-import step at all, and
  no live run of it has ever exercised the certification path B9/B12
  both did.
- **Status:** the `USED_IN` mechanism finding is accepted as correct,
  structural behavior (not a bug - selections genuinely are what scopes
  a tender's own graph, by design). The spec fix (select-then-preview) is
  implemented and confirmed working as designed - live run 4 completed
  the discovery loop over all 4 employees with no errors and attached
  real findings for each. **Result: none of the 4 showed any impact at
  all (`labels: []` for all four)** - not a fixture-targeting miss, but
  a downstream consequence of two separate, real, pre-existing bugs this
  run's own data exposed (see the two entries immediately below). The
  select-then-preview mechanism itself is verified correct; there was
  simply nothing real for it to find in this run, for reasons unrelated
  to knowledge selection.
- **Test:** none dedicated - this is a mechanism finding about existing
  code (`projector.py`, `queries.py`), not new behavior to pin. The
  spec's own live run is the standing verification.

---

## [product bug, golden path - found during B14 live verification] Candidate selection (`role_select`) intermittently never persists, despite the UI click, the button-hide, and every test assertion reporting success

- **Discovered:** B14 live run 4, investigating why all 4 employees
  showed zero graph impact after being selected (the entry above).
  `role_matches` for that run's tender (`415b2da2-037c-47bb-a438-
  10c60e9827ed`) has 16 real rows (4 roles × 4 candidates, real scores
  from `talent_analysis_node`) - every one `human_selected = false`, and
  `audit_events` for that tender has zero `role_select` actions at all,
  across all 4 roles - despite `full-flow.spec.ts`'s own step 7 loop
  clicking "Select" and asserting the button hides
  (`:562-577`) once per role, every time, without that assertion ever
  failing.
- **Not a permanent break - confirmed intermittent, not assumed:** the
  B13 tender's own audit trail (`dedf113a-74ce-4c8b-a20e-5c8b9da9c543`,
  queried the same way) shows 3 real `role_select` actions - the
  mechanism worked, partially, in that run. Run 4 shows 0 of 4. This is
  a real, live product bug in the golden path, not an artifact of the
  E2E harness or something B14 introduced - it happens with or without
  B14's own changes anywhere near this code, and by these two data
  points alone, intermittently rather than always.
- **The same shape as two other findings already in this file:** the UI
  reports success (button flips to "Deselect"), the test's own
  `.toBeHidden()` assertion passes, and nothing actually persisted
  server-side - the identical "looks like it works, doesn't" pattern as
  `PATCH /tenders/{id}` (silently 500ing on every call until B13's own
  route-level test finally exercised real HTTP serialization) and
  `knowledge.py`'s dead `set_knowledge_selection` route (a stale
  6-argument call, invisible because nothing tested the route layer).
  **This is also the third distinct real issue found in this exact
  function's own history**, not a first occurrence: `set_candidate_
  selection` already had a genuinely vacuous cross-tenant authorization
  check found and fixed once (this file's own "Standing pattern: a
  two-resource guard..." entry, instance 2), found NOT actually fixed
  and fixed again during B12 (same entry, corrected). This is a third,
  different failure mode in the same area - not the same bug recurring,
  but a third reason not to trust this call path without direct,
  server-confirmed verification.
- **Not yet root-caused - which layer is unknown:** the click event, the
  PATCH request, the route, or `set_candidate_selection` itself.
  Deliberately not traced further here, per this project's own "verify
  before fixing" standard, and because this is not B14's own bug to fix
  under this project's build-order discipline. Also not the already-
  documented `TeamWorkspace.tsx` stale-candidates race (this file's own
  earlier note, `full-flow.spec.ts:544-550`) - that race would select
  the WRONG candidate for a role, still leaving SOME row `human_selected
  = true`; this run left all 16 false, pointing at the request never
  landing, not landing on the wrong target.
- **Risk:** a real bid run through this exact golden path can reach
  "resource selection gate: approved" with zero roles genuinely staffed,
  and nothing in the current UI or test suite would show it - the same
  "confidently wrong, not obviously broken" shape this project has
  repeatedly named. `full-flow.spec.ts`'s own `.toBeHidden()` assertion
  is provably insufficient proof of persistence (it passed both times,
  once when the mutation worked and once when it didn't) - it should
  check server-confirmed state (a reload showing "Deselect," or the
  audit trail), not just the clicked button's own re-render.
- **Status:** open. Owner named honestly, not force-fit: no block in
  Part J's current B12-B17 list has a stop-gate that touches
  `select_candidate`/`TeamWorkspace.tsx` specifically, so this is left
  unassigned rather than attached to a block whose own gate doesn't
  actually depend on it - matching this file's own "a home that isn't
  real is worse than an honest 'unassigned'" standard (the no-clean-
  owning-block triage entry, pre-B12). Real, reproducible, confirmed via
  direct Postgres/audit-log inspection on two independent runs, not
  inferred.
- **Test:** none - the bug itself has none (that's the finding). A real
  fix should also tighten `full-flow.spec.ts`'s own step 7 assertion to
  check server-confirmed state, so a future regression of this exact
  shape fails loudly instead of passing silently a third time.

---

## [joins the "[B9]/[B12] Drafting mostly produces ungrounded text" cluster] Two independent live runs both show ~0/94-98 compliance entries reaching an LLM-affirmed decision - evidence essentially never reaches compliance_entries at all, not just claims

- **Discovered:** B14 live run 4's own tender
  (`415b2da2-037c-47bb-a438-10c60e9827ed`): 98 `compliance_entries`, 98
  `human_approved = true`, but `audit_events` shows all 98 arrived via
  `compliance_override` (step 6's "not already affirmative" branch,
  `full-flow.spec.ts:486-498`) - zero via the "already affirmative,
  click Approve" branch. `evidence_links` for the whole tender: 1 row.
  `compliance_evidence` (the junction actually attaching evidence to a
  decision): 0 rows - that one link isn't attached to anything.
  **Independently confirmed against the B13 tender's own surviving data**
  (`dedf113a-74ce-4c8b-a20e-5c8b9da9c543`, requested specifically as a
  second, zero-cost data point before filing this): 94 `compliance_
  entries`, 94 via `compliance_override`, 0 via affirmative approval,
  1 `evidence_link` for the whole tender, 0 attached. Same shape, same
  near-total absence, two runs of the same fixture, at two different
  points in this project's history.
- **Why this joins the existing citation-gap cluster rather than
  standing alone, and the hypothesis it raises - stated plainly as a
  hypothesis, not asserted as confirmed:** the B9/B12 entries measured
  citation correctness on DRAFTED PROPOSAL CLAIMS (1/19, 2/19 grounded
  across two runs) - a claim citing no evidence, but the underlying
  COMPLIANCE DECISION it might draw on could still have had real
  evidence behind it, in principle. These two runs show the earlier
  stage is *also* affected, at a comparable or worse rate: the LLM's own
  compliance PROPOSAL (E4's `ComplianceProposal.status`) came back
  something other than `fully_compliant`/`partially_compliant` for
  essentially every requirement, in both runs, and cited real evidence
  in essentially none of them. **If evidence largely isn't reaching
  compliance entries in the first place, then a claim later having
  nothing to cite may be upstream of drafting entirely - the ~1-in-10
  claims-level citation rate could be a symptom of this earlier gap
  rather than a separate, independent problem at the drafting stage.**
  Not established here which - genuinely unknown, same "cause is
  elsewhere and unknown" posture the B9/B12 entry already holds for its
  own layer - but two independent runs landing on the same near-zero
  number is enough to name the hypothesis explicitly rather than leave
  it implicit for whoever investigates next.
- **Risk:** if `override_compliance_entry` - a path E4/E7 describe as the
  exception for when automated evaluation gets it wrong - is functioning
  as the ONLY real path compliance decisions take in practice, almost
  none of them are evidence-backed regardless of which knowledge item
  backs what. This makes B14's own reanalysis cascade (compliance
  reopens when evidence disappears) structurally unable to demonstrate
  itself on a fresh live run of this fixture: there is no real evidence
  for any knowledge-item deselection to invalidate, independent of
  whether knowledge-selection or role-selection mechanics work.
- **Status:** open, no owner named - not B14's own bug, and two runs
  showing the same number is a real, replicated pattern, not something
  to chase inside this block per this project's own build-order
  discipline. Two data points, not yet a proven cause - worth a
  deliberate, preserved-database measurement (the `scripts/verify_
  req073_074_divergence.py` precedent: a live run, results left
  untouched afterward, not run through `_clean_tables`) rather than a
  third opportunistic observation from whatever live run happens to
  produce one next.
- **Test:** none - a live-run observation, not a code path with a
  missing test.
