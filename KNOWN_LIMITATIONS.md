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
- **Status:** unverified / accepted, not fixed in B6 or B9 - noted rather
  than patched under time pressure at the end of B6, re-confirmed rather
  than fixed in B9 since it's B6 code and CLAUDE.md's build-order
  discipline says fix the block that's actually broken, not patch it as
  a side effect of an unrelated block's eval script. B9's own
  citation-correctness script was changed to poll `.../items` instead,
  working around the bug rather than depending on it. A real fix belongs
  in B6/B13 (the screen list names "Talent import batches and review
  queue" - exactly the UI this bug would visibly break) and needs one of:
  batch-column updates added directly inside
  `process_knowledge_document_import`, or the GET route computing
  status/counts from the items table live instead of trusting stored
  columns that nothing maintains.
- **Test:** none dedicated in either block. Observed directly via B6's
  live smoke test and, independently, via B9's `docker exec ... psql`
  reproduction against a real batch row plus its `knowledge_import_items`
  rows. Worth a regression test in whichever block fixes it: upload real
  rows, assert the batch's own `status`/`completed_items` reflect them
  without needing a second query against `.../items`.

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
- **Status:** resolved-in-B8, extended in B9 and B10. B8 fixed
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
  measurement:
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
- **Why this belongs next to the measurement-tooling standing pattern
  above, not folded into it:** same "confidently wrong, not obviously
  broken" shape, but a different root-cause category - that pattern is
  about *code* (eval scripts, cache scoping) being wrong; this one is
  about *environment/process state* (which image is running, which
  process owns a port, which fixture just ran against the database you
  were about to inspect) silently substituting itself for, or destroying,
  the thing actually being verified. All three are invisible to a result
  that merely "looks fine" - the first two produce a wrong answer that
  looks like a right one; the third produces no answer at all where a
  real one used to be, without saying so.
- **Risk - why this matters going forward, not just as a postmortem:** B10
  through B17 are all worker/graph-heavy blocks (graph projection,
  red-team, readiness, event-driven reanalysis) that will keep needing
  live Docker verification the same way B7-B9 did, and will keep producing
  real live-run data worth inspecting *after* the run, not just during it.
  All three traps recur on every future block that (a) changes
  orchestration/domain code and verifies via a rebuilt-assumed container,
  (b) runs host-side scripts/tests against `localhost` and trusts the
  port, or (c) runs the automated test suite against the same database a
  live run's evidence is still sitting in. None of the three produces a
  code-level exception in the product itself - all three are purely
  which-process-or-fixture-am-I-actually-touching failures, exactly the
  class of bug a "the tests passed" report does not surface on its own.
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
  to tell scratch data from evidence still in use.
- **Test:** none automated (all three are host-machine/Docker-daemon/test-
  fixture state, not application code) - the incident write-up above and
  the discipline it now dictates are the mitigation.

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
- **Test:** none automated yet. Worth a dedicated regression test in
  whichever block actually resolves it: approve a gate without any
  per-target action, assert the targets remain unapproved.

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
- **Test:** `tests/unit/test_claim_grounding.py` (7 cases, synthetic,
  pins the invariant's logic) + this live run (1 real grounded case, 18
  real unsupported cases, all correctly classified) - together they cover
  "the code never wrongly grounds an unsupported claim" thoroughly, but
  neither is a statistically adequate measurement of citation semantic
  correctness at scale, and the evidence-availability question above has
  no test at all yet.

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
- **Status:** accepted interpretation. Both decisions are internally
  consistent with locked invariants (PRD 27.13's accessibility invariant
  for RBAC breadth; D5's own hop-bounding caution applied to the trace
  query's lack of a caller-controlled depth) but neither is dictated by
  either document, and both were caught and named as such during planning
  rather than assumed silently.
- **Test:** `tests/unit/test_graph_trace_labels.py` pins the trace
  endpoint's label-allowlist behavior (a security fix, not a semantics
  test) but nothing yet asserts the neighborhood-shape interpretation
  itself is what a consuming UI actually needs - that can only be judged
  once B13 has a real trace-view screen to build against.
