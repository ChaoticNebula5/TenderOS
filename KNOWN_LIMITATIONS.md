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
  one-off) B6, confirmed a third time B7, and a fourth, differently-shaped
  time B17. Four independent instances now:
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
  4. **B17** — a genuine, real regression test (`CommercialFindings.
     test.tsx`, see that file's own KNOWN_LIMITATIONS entry) for
     `handleValidate`'s stale-write guard, verified failing against the
     original broken code before the fix landed - by every check this
     project normally applies, real evidence the guard worked. It drove
     only the guard's success path; the same function's `catch` block had
     an identical, unguarded write CodeRabbit caught in the same session.
     The green test wasn't wrong about what it tested, it just tested
     less of the guarded function than a passing run implied - the first
     instance of this pattern where the false signal came from a
     *regression test for a specific bug*, not an eval/verification
     script measuring an aggregate metric.
- **The heuristic this establishes:** when a verification number comes back
  looking suspiciously clean — no misses, no variance, "3/3 consistent,"
  a suspiciously round or expected figure — suspect the measurement
  tooling before concluding the pipeline is actually that good. A broken
  pipeline tends to fail loudly or obviously (wrong shape, exception,
  visibly wrong output). Broken *measurement* code reports a confident,
  plausible, clean number instead — which is a worse failure mode
  precisely because a clean result is the one result nobody double-checks.
  All four instances above share this exact shape: the tooling was wrong
  in a way that made the pipeline (or the fix) look *better* than it
  actually was (B5: hid a real gap behind a mislabeled ground-truth item;
  B6: could have hidden a real regression behind a stale cross-run score;
  B7: would have hidden the fact that only one real trial ever ran behind
  an apparent 3-for-3; B17: a green regression test hid the fact that only
  one of the guarded function's two paths had ever been exercised).
- **The concrete lesson B17 adds:** a regression test written for a guard
  must drive every path that reaches the guarded write, not only the one
  path that reproduced the original bug report. Verifying the test fails
  against the original broken code (this project's own standard practice,
  and followed correctly here) proves the test catches *that* path's
  regression - it proves nothing about a sibling path with the identical
  defect shape that the original report never mentioned. The same
  question worth asking before trusting a green eval script - "does this
  actually scope/measure what it claims to?" - applies to a green
  regression test too: does it drive every code path the fix touched, or
  only the one the bug report described?
- **Status:** accepted as a standing risk, not something any one fix
  closes out — this is a statement about how measurement code (eval
  scripts and regression tests alike) fails, not a single bug with a
  single patch. Concretely: any future block writing a new eval script,
  verification script, repeated-trial check, or regression test for a
  guarded write should budget time to inspect the tooling's own
  correctness (does it scope queries correctly, does it bypass caches
  when independence matters, does the ground-truth data actually assert
  what it claims to, does it drive every path reaching the code under
  test) with the same seriousness given to the pipeline it's measuring,
  *especially* when the first result looks good.
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
- **Follow-up, post-B16:** `.env.example`'s own `RETRIEVAL_TOP_K=10`/
  `RERANK_TOP_N=5` were still sitting at exactly this entry's own
  root-caused wrong value (10) well after the fix: `config.py`'s real
  defaults were corrected to `20`/`10` and the live `.env` was updated to
  match, but the example file never got the same correction. A stale
  example is exactly how this specific wrong number could get
  reintroduced later — someone copying `.env.example` to set up a fresh
  environment, or "fixing" a real `.env` back to what the example shows,
  would reproduce the identical Recall@10=0.600 failure this entry
  documents. Corrected to `20`/`10` to match `config.py` and the live
  `.env`.
- **Test:** none dedicated yet for the scoping bug specifically — the fix
  is a required parameter (a caller *must* pass `tender_id`, so the
  unscoped-query shape can't silently return), which closes the specific
  hole structurally rather than via a regression test. A future block
  could add an explicit test asserting `evaluate_retrieval` only ever
  reads `Requirement` rows for the given `tender_id` if this becomes
  load-bearing enough to warrant one.

## [B6] DEMONSTRATES never auto-verifies under its own corroboration signal - ACCEPTED conservative default, not an open gap

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
- **Practical cost, reframed as the accepted default's own real price,
  not a symptom of a gap:** every `DEMONSTRATES` edge this policy
  proposes routes to human review, regardless of LLM confidence — there
  is no auto-verify path for this relationship type, by deliberate
  design. This fails SAFE, not silently: nothing auto-verifies something
  it can't actually corroborate, it just costs a reviewer's time on every
  proposed edge. That is the accepted trade, not a placeholder for a
  fix that hasn't landed yet.
- **Status: ACCEPTED, closed as a conservative default - not an open gap,
  not unverified, and not "not built yet."** 100% review for an
  inherently abstractive relationship is the correct behavior for a
  signal that structurally cannot discriminate correct from coincidental
  - not a stopgap waiting on engineering time. Re-confirmed post-B16
  that this is the right call to close on, not merely leave open: the
  calibration data needed to responsibly build the identified
  alternative does not exist, and inventing a threshold anyway would
  reproduce exactly the "cheap heuristic standing in for the real
  property" failure this entry's own "Why this is structural" section
  already rejects above - the same discipline this project has applied
  everywhere else it declined to fabricate a number (the latency
  baselines' own "reported, not gated" treatment is the closest
  precedent, not a new standard invented here).
  **The path forward stays named and actionable, in case real data ever
  exists - kept, not deleted, so it isn't re-derived from scratch:** the
  embedding infrastructure genuinely is already in the stack, checked
  directly rather than assumed - `agents/knowledge/retrieval.py` already
  calls `ingestion/embed.py::embed_texts` for real vector search, so
  wiring cosine similarity between a capability's name and a project's
  description would be a small, mechanical addition IF a threshold could
  ever be picked honestly. It cannot today, checked against the real
  fixture directly, not assumed from an earlier "4 pairs" note:
  `data/ground_truth/tender_a_knowledge.jsonl` names 4 projects and 3
  capabilities, but never encodes which project demonstrates which
  capability at all - that pairing only exists as whatever the LLM
  happened to propose during one live extraction run, with no
  independent human label attached to any of them. The real blocker is
  not merely "4 is a small N" (though it is) - it is that **all 4 are
  presumed-positive, and zero labeled NEGATIVE pairs exist anywhere in
  this fixture.** Picking a similarity threshold from positive examples
  alone is not calibration - without seeing where a genuinely MISMATCHED
  project/capability pair's embedding similarity actually lands, any
  threshold drawn from these 4 would be indistinguishable from a number
  invented outright, dressed up as data-driven. **What real calibration
  would need, so this stays actionable rather than abandoned:** a modest
  set (dozens, not 4) of human-labeled project x capability pairs
  spanning both true corroborations and plausible-but-wrong near-misses
  (e.g. a project that demonstrates an ADJACENT
  capability, not a random unrelated one - the actual failure mode this
  signal needs to catch), embedding-similarity computed for each, and a
  threshold chosen by maximizing precision/recall (or F1) against that
  labeled set - not eyeballed from a handful of known-good examples.
  That labeling effort is real product/ops work, not a follow-up code
  task this pass could complete on its own.
- **Test:** the 4 real project/capability pairs from this session's live
  run are the standing evidence for the current behaviour. No fixture
  exists yet asserting the embedding-similarity alternative, since it
  isn't built - and, per the scoping above, shouldn't be until real
  labeled calibration data exists.

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
     **Phase 4 item 6 (batch 2a):** the stack's Postgres is published on
     host port 5433; containers still use `postgres:5432`. Every host-side
     DSN moved with it (`scripts/test-backend.env`, the usage lines of
     `check_fake_run_invariants.py`, `project_all_tenders.py` and
     `restore_review_snapshot.sh`), and so did CI's service mapping,
     because CI sources `test-backend.env`. `scripts/test-backend.ps1` now
     asks the server for its version before pytest starts and refuses
     anything but 16.x. On 2026-10-23, before the move, 5432 was held by
     Docker Desktop and by WSL's localhost relay (`wslrelay.exe` on
     `127.0.0.1` and `[::1]`); the native service was not running.
     Observed 2026-10-23: a host connection to `localhost:5433` reports
     PostgreSQL 16.13 with pgvector 0.8.2; the suite's first line names
     "localhost:5433, PostgreSQL 16.13"; with the DSN pointed at 5432 the
     script refused both with nothing listening ("could not ask the
     Postgres server") and with a throwaway PostgreSQL 17 container there
     ("reports version 17.11, not 16.x"). The E2E passed after the move.
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
  9. **Stale image, again - but this time it presented as a correctness
     check silently returning the wrong answer, not a container failing to
     start.** During B16's citation-correctness fix (the
     `014_knowledge_item_chunks.py` join-table migration), a before/after
     verification script called `search_company_knowledge` directly
     against a scratch-restored copy of the preserved investigation dump.
     The "before" half correctly returned 0 candidates for REQ-010 in
     every org (the known, expected pre-fix state). The migration was
     applied. The "after" half - the identical script, identical query -
     also returned 0, unchanged. This looked exactly like a genuine,
     negative result: the fix didn't work. Traced it by grepping the
     running worker container's own filesystem for the new method name
     (`associate_knowledge_item_chunks`) - absent, confirming the
     container was still running an image built before any of this
     session's source edits (worker/api have no dev-mode volume mount,
     same root mechanism as incident 1, just never rebuilt this session).
     Rebuilt (`docker compose build worker` + `up -d --no-deps worker`),
     reran the identical after-check, got the expected 10 candidates.
     **What makes this a materially worse failure mode than incidents 1-8,
     not just a repeat of incident 1's mechanism:** every one of the nine
     prior occurrences of this standing pattern presented as a container
     or service being unreachable, refusing a connection, or failing to
     start in some way that was detectable as *a problem* on its face -
     including incident 1 itself, where the stale image produced a smoke
     test that timed out waiting for a gate that never appeared. This one
     produced no error, no timeout, nothing that looked broken - a clean,
     plausible, negative finding from a check that was otherwise working
     exactly as designed (the "before" half, run against the same stale
     image, gave the correct answer purely because 0 was also the correct
     *pre-fix* answer). Incident 1's own writeup already noted, as a
     counterfactual, that "a less specific check... would have reported
     success on a run that silently skipped" something real; this incident
     is that counterfactual actually happening, on a correctness check
     rather than a smoke test, producing a false negative that reads as a
     real finding. Had it gone unquestioned, the natural next step would
     have been to go debug a fix that was already correct - the failure
     mode isn't "verification didn't happen," it's "verification happened
     and reported the wrong thing with full confidence."
  10. **Incident 6/8's exact mechanism, a third time - this pass, after
      an unplanned Docker Desktop crash mid-session (two duplicate
      `com.docker.backend.exe` processes found running simultaneously,
      itself the likely root cause).** `docker compose ps` reported
      `postgres` healthy; `docker port tenderos-postgres-1` returned
      nothing at all (no host mapping), confirmed directly rather than
      inferred from the connection failure alone - `netstat` on the host
      showed nothing listening on 5432. A plain Docker Desktop restart
      (this session's own established fix for incidents 6/8) was NOT
      sufficient this time; the postgres container itself needed a real
      recreate (`docker compose stop postgres && rm -f postgres && up -d
      postgres`) before the port publish actually took. Data volume
      confirmed intact throughout (same `tenderos`/`tenderos_test`
      databases, unaffected by the container-level recreate). Recorded
      as a third occurrence, not folded into 6/8 silently - the trigger
      (a genuine backend-process duplication after a crash) and the fix
      (container recreate, not just a Desktop restart) are both new
      information, not a repeat of the same two.
  11. **Both known mechanisms hit back to back, this pass - the
      port-squatting native Postgres (incidents 2/5, third occurrence)
      immediately followed by the container-healthy-but-port-publish-
      missing mechanism (incidents 6/8/10, fourth occurrence), triggered
      by the SAME Docker Desktop crash.** Docker Desktop had fully exited
      (`tasklist` showed zero Docker processes at all, a different crash
      shape than incident 10's duplicate-backend-process one) mid-session,
      unprompted. After relaunching Desktop and bringing the stack back
      up, `docker compose up -d` failed with `port is already allocated`;
      `netstat`/`tasklist` identified the real cause precisely this time,
      not just inferred - PID 8752, `postgres.exe`, running as a genuine
      Windows Service (`Session Name: Services`), the same native
      PostgreSQL install incidents 2/5 already named, still present on
      this machine. **New this time: this was flagged to the user rather
      than stopped directly.** Every prior incident in this entry was a
      container-level action fully inside this project's own Docker
      Compose stack, safe to take unilaterally; stopping a Windows
      Service is host-level state outside the project entirely, with no
      way to know from inside this session whether something else on the
      machine depends on it - asked rather than assumed, matching this
      file's "investigate before touching unfamiliar state" standard
      applied to a real case, not just a git worktree. The user stopped
      it directly. Once the port was free, the SECOND mechanism fired
      immediately on the very next `docker compose up -d`: postgres
      reported healthy, but `docker port tenderos-postgres-1` returned
      nothing and nothing was listening on the host - resolved the same
      way as incidents 6/8/10, a full container recreate. Both the
      dev (`tenderos`) and test (`tenderos_test`) databases' data
      survived throughout (named volume, unaffected by the container-level
      recreate). One genuinely new, unrelated finding surfaced by this
      same recovery: `tenderos_test` itself has no `alembic_version`
      table at all (`tests/integration/conftest.py` builds its schema via
      `Base.metadata.create_all()` directly from the ORM models, never via
      Alembic) - expected and correct for a database that gets fully
      rebuilt every test run, but it means `create_all()` will silently
      skip re-creating a table that already exists without picking up a
      newly added column on it, so a mid-session schema change (this
      pass's own `proposal_claims.claim_kind`) needs `tenderos_test`
      dropped and recreated once, not just a migration run, before the
      next test run will see the new column. Separately: the same
      recovery revealed the real dev database (`tenderos`) had never
      actually had migrations 015/016 applied either, despite both being
      written and tested earlier this session - `alembic upgrade head`
      ran all three (015, 016, 017) in one pass here, closing a real gap
      that predates this incident, not caused by it.
  12. **A large, silent backlog in Redis's own scheduled-job registry -
      not an infinite retry loop, a fifth trap mechanism distinct from
      1/2/5/6/8/9/10/11.** During B17 planning's own E2E baseline
      measurement, `docker compose up -d postgres redis neo4j minio`
      reused this machine's existing named volumes rather than starting
      fresh - deliberate at the time (the point was a real baseline
      against real prior state), not an oversight. The first live E2E run
      timed out at step 4 (extraction); `docker compose logs worker`
      showed 56 `ERROR rq.worker` entries, `09:37:24`-`09:41:11` (about
      3m47s), each a distinct `WorkflowRun {id} not found` or
      `Document {id} not found` `ValueError`, roughly one every 4-8s.
      Read directly against `jobs/workflow.py`, not inferred from the log
      shape: `run_workflow`/`resume_workflow`/`retry_workflow_run` each
      look up their row BEFORE the `try` block that calls
      `_handle_graph_failure` (lines 121-127/193-199/242-245) - a "not
      found" raises immediately, outside that function's own
      retry-scheduling logic entirely, and `_handle_graph_failure`'s own
      `queue.enqueue_in` (line 100) is never passed `retry=`, so RQ itself
      never reschedules a failed job either. Each of the 56 is a genuine
      one-shot: fires once, fails cleanly, reaches a terminal state in
      RQ's `FailedJobRegistry` - **not** an unbounded retry loop, which is
      what the failure volume and duration first suggested before the
      code was actually read. The real mechanism: `_handle_graph_failure`
      itself schedules *future* retries (for genuine transient failures,
      not "not found") into RQ's `ScheduledJobRegistry`, which has no
      expiry. Across roughly ten prior sessions (`backups/` holds ten
      `b16_*` dump files), any workflow run that failed, scheduled a
      retry, and then had its `workflow_runs`/`documents` row deleted or
      rolled back before that retry's delay elapsed (a session reset, an
      aborted test, a DB restore) left an orphaned entry sitting inertly
      in Redis. `worker.py::main` runs `worker.work(with_scheduler=True)`
      - this session's worker start was the first in a while with a live
      scheduler thread to promote that backlog, so all ~56 fired in one
      burst, ahead of this run's own real ingestion job, on the single
      worker process this stack runs. Resolved for this session with
      `redis-cli FLUSHALL` plus a worker/outbox_dispatcher restart,
      confirmed via `DBSIZE`; the second, clean E2E run reached step 6
      before failing on an unrelated, already-known bug (see the
      `Login.tsx` destination-loss entry). **Open, named as B17's own
      decision, not fixed here:** per-job retry is already bounded and
      terminal, so that specific concern is already closed - the real gap
      is that orphaned *scheduled* entries have no TTL and accumulate
      across resets only while no scheduler runs for that queue, paid off
      as one serial burst whenever a worker with scheduling next starts.
      With a worker up, each fires within its delay, at most 15 minutes
      (the clamp in `orchestration/retry.py`, read in code). **RESOLVED in
      Phase 4 plan item 22 (2026-11-05, decision 0070):** a retry is orphaned
      if its run's row is gone, its run is not running, or its run's current
      job is not this retry; age alone never counts. A missing row now logs
      one WARNING and returns, with no failure record; before, it raised, and
      the record lasted 365 days (observed). Item 19's guard already dropped
      the other two (observed). Every scheduled retry carries a TTL of the run
      cap plus 1 hour (7 hours), derived from `MAX_RUN_JOB_AGE`; past it RQ's
      scheduler skips the job at no cost (observed). **Accepted:** a retry
      younger than that still costs about 8 s when it fires, because the
      forked job process imports `jobs.workflow` (8.6 to 9.4 s measured; plan
      item 48), so a burst can still delay the workflow queue, though not
      uploads, since item 44. No sweep was added. **Changed for a real run:**
      if the worker and the dispatcher are both down for over 7 hours, the
      retry is gone and the cap fails the run; the user starts a new run
      (there is no retry route). Test:
      `tests/integration/test_orphaned_retries.py`.
  13. **A partial `docker compose` command silently drops every `-f`
      override, and the containers come up looking healthy with the wrong
      configuration - classified by consequence, matching incident 1's own
      "stale image presenting as a correctness check silently returning
      the wrong answer" shape, not by cause.** During B17's fake-provider
      E2E work, `docker compose -f docker-compose.yml -f docker-compose.
      e2e.yml -f docker-compose.e2e-fake.yml up -d --build worker
      outbox_dispatcher` had already brought the stack up correctly in
      fake mode. A later "fully stop and remove before restarting" cleanup
      - `docker compose stop worker outbox_dispatcher`, `docker compose rm
      -f worker outbox_dispatcher`, then `docker compose up -d worker
      outbox_dispatcher` - reconstructed the third command from memory
      with none of the three `-f` flags. Docker Compose's own behavior on
      a bare `up` with no `-f` is to fall back to the base
      `docker-compose.yml` alone, silently - no warning, no error, nothing
      structurally different about the moment it happens, the same "the
      only way to notice is to already know to check" shape this file's
      other standing patterns share. Both containers reported healthy and
      accepted work normally; `LLM_PROVIDER` was simply back to its base-
      file default (the real Gemini provider) instead of `fake`. The next
      E2E run was, without anything announcing it, a real, billed live run
      - 92 real calls to `generativelanguage.googleapis.com` over about 10
      minutes - and its step-4 stall (real-provider rate-limiting/latency,
      the same shape this project's own live runs have hit before) was
      briefly reasoned about as if it were a fake-provider defect, because
      nothing checked which provider was actually running before treating
      the result as evidence about the fake path at all.
      **The detection, not the mistake itself, is this entry's real
      value:** `docker exec <container> printenv LLM_PROVIDER` against the
      running container, checked BEFORE treating any run as a fake run -
      cheap, direct, and unambiguous, the same "verify which server
      actually answered" discipline incidents 2/5's own SQL-identity check
      already established for a different config-drift shape. Now a
      standing step in the fake-run procedure itself (see the fake-client
      entry's own standing note on harness reliability), not something to
      remember unprompted a second time. **What this does and doesn't
      settle:** the step-4 stall is fully explained by live-provider
      variance - it is not evidence about the fake path at all, in either
      direction. Two candidate explanations for a fake-run stall at the
      same step (an unwarmed HuggingFace model cache after container
      removal; a `ScheduledJobRegistry` backlog per incident 12, also
      triggered by container removal) were never actually exercised by
      this run, because this run was never a fake run - they remain open
      questions for the first genuinely clean fake run that stalls the
      same way, not closed by this incident either way. (B17 Phase 0
      closed the model-cache candidate: both models are now baked into
      the api and worker images, offline at runtime - see the resolved
      "api image never pre-downloaded the embedding model" entry.)
  14. **Playwright launched from the repo root ran a different Playwright
      against the wrong tests, and failed before any test executed - an
      invocation fault presenting as a red E2E (B17 Phase 0, 2026-10-06).**
      A backgrounded `cd apps/web && npx playwright test` ran with the repo
      root as its working directory. `npx` found no local binary there
      (the project's Playwright lives in `apps/web/node_modules`), printed
      `npm warn exec The following package was not found and will be
      installed: playwright@1.63.0`, and ran that 1.63.0 CLI against the
      project's own `@playwright/test` 1.62.1 - "Playwright Test did not
      expect test() to be called here ... two different versions of
      @playwright/test". With no `playwright.config.ts` in the repo root it
      used the default test directory and collected `src/**/*.test.tsx`
      (Vitest unit tests), which import `src/api/client.ts` and crash on
      `import.meta.env` outside Vite. It also left a stray root
      `test-results/` (removed). Nothing in the product or the spec ran.
      **The clue:** error paths printed relative to the repo root
      (`apps\web\e2e\full-flow.spec.ts`), where a run from `apps/web`
      prints `e2e\full-flow.spec.ts`. **Resolution:** one re-launch with no
      code change, from an explicit working directory using the
      project-local binary (`apps/web/node_modules/.bin/playwright.cmd`,
      `--version` 1.62.1) - green. **Detection, standing step:** print the
      Playwright version and the resolved config path before every run;
      a version other than the lockfile's, or no config path, means stop
      before running. (`scripts/e2e.ps1`, added in B17 Phase 0b, does
      exactly that and refuses to run without the local binary and config.)
  15. **Port-squatting native Postgres, a third time - caught by the
      written practice again (B17 Phase 0b, 2026-10-06).** After a machine
      restart, Docker Desktop was not running and the native
      `postgresql-x64-18` service (StartType Automatic, still installed)
      held `0.0.0.0:5432`. The first host-side script call failed with
      `role "tenderos" does not exist` - incident 2's exact signature. Per
      practice (2), nothing was trusted until a `SELECT version()` through
      the same asyncpg driver answered "PostgreSQL 16.13 (Debian)" three
      times in a row after Docker came back up and published the port.
      The native service was not stopped - it is not this project's to
      stop - so this will recur after every restart that starts it before
      Docker; the identity check is the standing defence.
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
  **New evidence about this trap's actual blast radius, found in B16 while
  building the checkpoint-pruning script (its own entry below), not while
  chasing this one:** prior write-ups of incidents 3/4/7 described the
  wipe as losing run data - what actually checking a live database showed
  is that it also leaves LangGraph's own tables (`checkpoints`/
  `checkpoint_blobs`/`checkpoint_writes`) permanently inconsistent with
  the application's own (`workflow_runs`), not just temporarily empty.
  The wipe only reaches `Base.metadata`-managed tables; LangGraph's
  checkpointer tables are not part of that metadata, so a wipe deletes
  every `workflow_runs` row while leaving every checkpoint row that
  referenced one behind - orphaned, not recoverable by re-running
  anything, since the `workflow_runs` row an orphaned thread's own
  `langgraph_thread_id` pointed at is simply gone. Measured directly: 226
  of 240 distinct checkpoint
  threads in the live dev database had no owning `workflow_runs` row at
  all. This is a durable property of the trap's mechanism (which tables
  are and aren't `Base.metadata`-managed), not a one-off - it recurs
  identically every time incident 3/4/7's own mechanism fires, on
  whichever tables sit outside `Base.metadata`'s reach at the time.
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
- **Test:** none automated for incidents 1/2/5/6/8/9 (host-machine/Docker-
  daemon state, not application code) - the incident write-up above and
  the discipline it dictates are the mitigation. Incident 3/4/7's own
  mechanism now has a real regression check: running `pytest` with
  `POSTGRES_DSN` pointed at the dev/live database name raises immediately,
  verified directly this pass; running it against `tenderos_test` runs
  normally. Incident 8 itself was caught and fixed live, the same way
  incident 6 was (`docker port` diagnosis, `--force-recreate`, a real
  host-side `asyncpg.connect` before trusting the next test run) - no
  automated test exists for the underlying defect, which is exactly what
  the new B16 ownership note above is for. Incident 9 was caught by
  grepping the running container's own filesystem for a known-new symbol
  before trusting a "no change" result - worth naming as its own concrete
  practice: when a before/after check reports no change, verify the code
  actually running matches the code just written before concluding the
  fix didn't work, the same "verify before trusting" discipline point (2)
  above already asks for, just applied to source code instead of a
  database connection.

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
- **Status: RESOLVED, B16 Group 6 (Option C).** Three shapes were
  compared, the same way as the earlier `USED_IN` structural-gap
  comparison:
  - **Option A - a new `pricing_finding` `evidence_links.source_kind`
    value plus a nullable `pricing_finding_id` FK column.** Disqualified:
    touches `evidence_links`' own `ck_evidence_link_has_source` invariant,
    which compliance coverage and red-team contradiction detection both
    depend on holding (knowledge-item-or-chunk, nothing else) - a third
    source kind there is a change to a shared contract, not an addition
    beside it.
  - **Option B - a parallel `pricing_evidence_links` junction table.**
    Disqualified, explicitly, not just left unchosen: never touches
    `evidence_links`' own constraints, but `materialize_claim_links`,
    `get_proposal_trace`, and the evidence trace UI would each need a
    second, parallel code path to handle a new evidence type none of them
    know about today. This project already has multiple documented
    instances of exactly that shape (a second path drifting from the
    first) as standing-pattern findings elsewhere in this file - Option B
    would be creating a fresh one deliberately.
  - **Option C - chosen. A synthetic `document_excerpt` chunk, same
    pattern as `import_certification_row`'s own fix (B16 Group 3).** A
    resolved pricing finding is a statement about a specific, real,
    already-uploaded pricing document - representing it as an excerpt of
    that document is accurate, not a workaround. Zero change to
    `evidence_links`' schema or any of its existing readers: every
    consumer already handles an ordinary `document_excerpt` link.
  - **The comparison still holds after a real schema-change discovery
    made mid-implementation** (see the provenance-gap bullet below): one
    small, nullable, additive column on `pricing_findings` with the value
    already in scope at creation time, versus Option A's change to a
    shared invariant or Option B's N duplicated code paths. C remained
    the smallest change of the three even once it stopped being literally
    zero-schema-change.
- **A real, independent provenance gap found while implementing Option
  C, not only a grounding-enablement need.** `pricing_findings` recorded
  no reference at all to which uploaded document a finding came from, for
  7 of its 8 finding types (only `cross_doc_mismatch` carried anything,
  via `item_a_ref`/`item_b_ref`). In a system whose entire thesis is
  traceability, a finding with no traceable source document is a real
  gap on its own, independent of whether grounding was ever attempted -
  worth fixing on those grounds alone, not framed here as incidental to
  the citation work. **Migration `015_pricing_finding_document`** adds a
  nullable `pricing_document_id` FK, populated by
  `evaluate_commercial_for_tender`'s own finding-creation loop (the value
  is already in scope per-document at that point) for every finding type
  except `cross_doc_mismatch`, which stays NULL by design - its two
  documents are already both named, symmetrically, via `item_a_ref`/
  `item_b_ref`. **No backfill, and none is possible:** an existing
  finding's source document was never derivable from anything already in
  Postgres - `cell_refs` (the only thing that could have disambiguated
  it) was used only to compute the row's own `content_fingerprint`, a
  one-way hash, and was never itself persisted as a column. Every
  pre-existing `pricing_findings` row stays NULL here **permanently, not
  just until the next re-run**: `evaluate_commercial_for_tender` is
  idempotent by `content_fingerprint` (a finding that already exists is
  returned as-is, never updated), so re-running commercial evaluation on
  an already-ingested tender does not retroactively populate this column
  either. A real, accepted limit, not a bug.
- **cross_doc_mismatch's two-document sub-question, settled with
  evidence, not assumed.** Checked `pricing_checks.py` directly:
  `currency_mismatch` (despite living in the same code region) is
  actually single-document - `item_a_ref`/`item_b_ref` are populated
  "only for cross_doc_mismatch" per the code's own comment, confirmed by
  its function signature taking one sheet's rows, not a multi-document
  list. Only `cross_doc_mismatch` genuinely spans two documents, and its
  `item_a_ref`/`item_b_ref` are already structurally symmetric - same
  shape, same weight, and the finding's own `note` quotes both documents
  equally ("X is priced at V1 in document A, but V2 in document B"),
  neither privileged as the one being challenged. So a `cross_doc_mismatch`
  finding gets TWO evidence links, one per document, not one arbitrarily
  chosen side - confirmed safe downstream by reading
  `enforce_claim_grounding` directly: `resolved_evidence_link_ids` is
  already `list[uuid.UUID]`, and grounding only ever checks empty vs.
  non-empty, so more than one evidence link per claim was already the
  normal, handled shape (used elsewhere for compliance claims), not new
  territory this fix had to open up.
- **Implementation:** `commercial_service._ground_pricing_finding`
  (called from `resolve_pricing_finding_in_uow`, after the finding is
  marked resolved) creates one `document_service.create_synthetic_chunk`
  (the same helper `import_certification_row` uses - moved out of
  `knowledge_service.py` into `document_service.py` once a third caller
  needed it, this project's own established threshold for sharing rather
  than duplicating) plus one `EvidenceLink` per document, idempotent by a
  deterministic fingerprint (`pricing_finding_evidence_fingerprint`) so a
  repeat resolve action cannot duplicate evidence.
  `proposal_service._facts_from_pricing` looks up the same fingerprint to
  set `PreparedFact.evidence_link_id` when a link exists, and falls back
  to B9's original unlinked-narrative-context behavior when it doesn't
  (a pre-fix finding, or the "neither ref nor `pricing_document_id`"
  defensive gap `_ground_pricing_finding` itself documents) - strictly
  additive, nothing previously offered stops being offered.
- **Test:** `tests/integration/test_commercial_service.py` (single-
  document grounding, two-link cross-doc grounding, repeat-resolve
  idempotency) and `tests/integration/test_proposal_service.py`
  (`evidence_link_id` set for a grounded finding; still unset/filtered
  for an unresolved one) - 5 new tests, all against the real synthetic
  pricing fixtures, no mocking of the grounding path itself.

---

## [B9/B16] The PRD never defines whether a forward-looking commitment is a "factual claim" - RESOLVED post-B16: a new claim_kind field, orthogonal to is_factual, keeps commitments visible while letting the metric exclude what it can never ground

- **Discovered:** post-B16, investigating whether the 8 forward-looking
  proposal claims from the live E2E run's own 27-unsupported sample
  ("will support SSO," "must maintain 99.5% uptime") were a prompt bug
  (the model has a way to mark them non-factual and isn't using it) or a
  genuine product-definition gap - checked the code and the PRD directly
  rather than assumed either way.
- **`is_factual` is binary with no third option, at both layers, checked
  directly:** `ProposalClaimDraft.is_factual: bool = True`
  (`agents/schemas/proposal.py:28`) - a plain bool, no enum. Storage:
  `proposal_claims.is_factual: Mapped[bool]`
  (`ProposalClaim` in `db/models/proposal.py`) - also a plain, non-nullable `Boolean`
  column. Nothing in the schema can represent a third category even if
  the prompt asked for one.
- **The model is not simply failing to use an option it has - checked
  the prompt's own current wording, not assumed.**
  `prompts/versions/v1/proposal_drafting.md:20`: *"`is_factual` - `true`
  if the claim asserts a specific, checkable fact (a count, a
  certification, a named capability, a compliance position); `false` if
  it is narrative, aspirational, or a general statement of approach
  that cannot be evidenced one way or the other."* "Will support SSO"
  and "must maintain 99.5% uptime" are specific and checkable - they
  satisfy the `true` criteria exactly as written, just checkable
  against a future deliverable rather than past evidence. The prompt's
  own `false` example (line 12: *"Our team will apply industry-standard
  project governance"*) is for vague, non-checkable narrative, not
  specific forward commitments. **The model classified these correctly
  per the prompt's own current definition** - this is not "the 7 were
  misclassified, denominator becomes 27." (The real count in this
  category is 8, not 7 - checked directly against the full 27-claim
  sample, not re-derived from an earlier rounded figure.)
- **The PRD never contemplates this category at all - quoted, not
  paraphrased.** §8.5's only two worked examples
  (`TenderOS-PRD.md:451-465`): *"We completed three telecom
  migrations"* (→ 3 past projects) and *"Our proposed lead holds
  Certification X"* (→ 1 certification) - both backward-looking, both
  about something the company already has or has done. §10.11's
  proposal-layer requirement list (`TenderOS-PRD.md:896-904`): "avoid
  inventing company capabilities... employee experience... client
  references... reflect approved compliance positions" - again entirely
  about present/past state. No example, requirement, or invariant
  anywhere in the PRD distinguishes a claim about what the company HAS
  from a claim about what it WILL DO for this specific contract. A
  forward-looking solution commitment is not classified as factual, not
  classified as exempt, not mentioned as a category at all.
- **What adding a third category would cost, named so it isn't
  re-derived from scratch by whoever picks this up:**
  1. A new `proposal_claims` column (e.g. `claim_temporality:
     Literal["current", "commitment"]`) - a real migration, plus the
     model, `ProposalClaimDraft`, and every reader that currently only
     branches on `is_factual` (`enforce_claim_grounding`, the graph
     projector's `merge_proposal_claim_nodes`, `get_proposal_trace`).
  2. `enforce_claim_grounding` needs a real decision for the new
     category, not just "exempt it like narrative." A forward
     commitment is a real, checkable promise a bidder is making -
     silently treating it as `not_required` (no evidence needed) could
     hide a reckless commitment ("will support SSO" with no internal
     roadmap backing it) as readily as it currently hides nothing,
     since today it's `is_factual=true, unsupported` - visible, not
     silently waved through.
  3. The prompt needs a third, precisely-worded definition that
     distinguishes "checkable against what we have" from "checkable
     against what we're promising," without collapsing back into the
     existing `false`/narrative bucket by accident.
  4. Whether a forward commitment needs its OWN kind of evidence (an
     internal roadmap/capability-commitment record - a knowledge-item
     type that doesn't exist today) is itself unscoped - this is real
     new product surface, not a field addition.
- **Status: RESOLVED, post-B16, product decision made and implemented.**
  Real options traced before building, three considered:
  1. **Status quo, metric caveated instead of changed.** Zero code cost,
     but permanently caps citation correctness below what a perfectly
     evidenced proposal could ever reach, and conflates two different
     kinds of "unsupported" (genuinely under-evidenced vs. structurally
     un-groundable) under one status forever.
  2. **A new `claim_kind` field (`"current"` / `"commitment"`),
     orthogonal to `is_factual`, not a replacement.** Chosen. Traced
     cost, corrected from this entry's own earlier estimate: a real
     migration (`017_proposal_claim_kind`, additive column + CHECK
     constraint, `server_default='current'`, same shape as 015/016
     already applied this session without needing separate sign-off);
     `ProposalClaimDraft`/`ProposalClaim`/`ProposalClaimTrace` and both
     API response schemas each get the one new field, mirroring exactly
     how `is_factual` already flows through every one of those layers;
     the graph projector gets one more property in an already-generic
     `MERGE ... SET n += row`; the prompt gets one new field definition,
     worded to stay orthogonal to `is_factual` rather than collapse into
     the existing narrative/`false` bucket. **The one cost this entry
     originally over-estimated, corrected here:** `enforce_claim_
     grounding` needs ZERO changes. The "keep commitments visible"
     decision means a commitment claim is still `is_factual=true` and
     goes through the exact same unsupported/grounded/needs_review logic
     as any other factual claim - `claim_kind` never enters that
     function at all, it only tags which bucket a future citation-
     correctness measurement counts a claim into. Rejecting the
     "exempt like narrative" alternative is what makes this true; had
     that alternative been chosen instead, `enforce_claim_grounding`
     WOULD have needed a new branch.
  3. **Rejected alternatives, considered and traced:** a text-heuristic
     classifier (regex/keyword matching on claim_text for "will"/"shall"
     at read time, no schema change) - rejected as unreliable for
     natural-language temporality and strictly worse than having the
     model (which already has full context at draft time, in the same
     structured call that already produces `claim_type`/`is_factual`)
     tag it directly. Folding the distinction into `claim_type` instead
     of a new column - rejected: `claim_type` and "is this forward-
     looking" are orthogonal (a commitment can be `capability`,
     `commercial`, etc.), so collapsing them would lose real information
     for every commitment claim, the same reasoning this codebase
     already applies to keeping `claim_type` and `evidence_links.
     source_kind` as separate, unrelated vocabularies.
  4. **Backfill:** every pre-existing claim defaults to `'current'` via
     the migration's own `server_default` - correct and named as a real
     limitation, not silently glossed over: there is no reliable way to
     retroactively tell which historical claims would have been tagged
     `'commitment'` without re-running the LLM against text that may
     since have changed, so old rows simply stay exactly as measurable
     as they always were.
  5. **Not yet verified live:** the prompt change's real effect on model
     behaviour (does a live Gemini call actually use `claim_kind`
     sensibly, distinct from `is_factual`) needs a real LLM call to
     confirm - not run this pass, same "flag before anything billed"
     posture as every other live-model check this session.
- **Test:** `tests/integration/test_proposal_service.py::
  test_commitment_claim_with_no_evidence_is_unsupported_not_exempt` -
  proves the actual guarantee end-to-end through the real service call:
  a `claim_kind="commitment"` claim with `is_factual=true` and no
  evidence persists `grounding_status="unsupported"`, never
  `"not_required"` - the specific regression that would silently hide an
  unbacked promise, which this fix's whole point was to avoid.
  `tests/integration/test_schema_diff.py`'s own create_all()-vs-
  alembic-migrated comparison passing is independent proof the new
  migration and the ORM model agree, not just an assertion taken on
  faith.

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
- **B12 update - the two tested causes did not explain the result; the
  underlying cause remains unconfirmed. This is the project's largest
  known open quality gap, now partially resolved in one narrow sense
  only: the two originally-named candidate mechanisms are fixed and
  measured, not merely suspected, so the investigation has ruled two
  things out - it has not found what the real cause is, does not show
  evidence was available for the still-uncited claims, and does not
  isolate the model's own citation behavior. Read the two claims below
  as exactly that: two live, real results, not a closed case.**
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
- **RESOLVED (investigation, not fix) in B16 - see
  `docs/eval/citation-correctness-investigation.md` for the full
  writeup, not repeated here.** The open question this entry has carried
  since B9 - "was evidence thin, or did the model have it and not cite
  it" - is answered: neither, cleanly. A new instrument
  (`AgentTrace.input_refs`/`output_refs`, populated for the first time
  this pass) traced a real live run end to end and found drafting was
  offered real facts on every one of 15 calls across 3 revision passes,
  exclusively `requirement`-kind, never `evidence`-kind - a third outcome,
  not a confirmation of either original hypothesis. Traced one stage
  further back: `search_company_knowledge` returned zero candidates for
  87 of 87 real compliance evaluations in the same run - not thin,
  absolute. Final result: 0/27 factual claims grounded (0%), lower than
  either B9 (1/19) or B12 (2/19) and below this session's own predicted
  5-15% range. **Not fixed** - the pipeline still grounds 0% of factual
  claims after this investigation. **Follow-up in the same session
  settled the retrieval-vs-fixture-coverage question the artifact
  originally left open, and the answer is neither:** a direct,
  no-LLM-cost check against a requirement this fixture's own knowledge
  base genuinely covers (a "certified project manager" requirement
  against an employee actually titled Project Manager) found vector
  search correctly returning the real PMP-certification chunk - retrieval
  itself works. The candidate is discarded one step later:
  `knowledge_items.source_chunk_id` is never set for employee records
  (confirmed: NULL for all 79 employee-type knowledge items in this live
  database), so the chunk can never resolve back to the knowledge item
  it's evidence for. `create_certification`/`create_past_project`/
  `create_capability` all correctly set this field, with real callers
  proven to thread a real value through - `create_employee`, sitting
  right next to them under a comment calling it "same shape," is the one
  sibling missing it. See the artifact's own §5 for the full stage-by-
  stage trace and the write-site audit.
- **Retrieval-layer root cause FIXED in B16** (migration
  `014_knowledge_item_chunks.py`) - **downstream effect on the 0% grounded-
  claims number above is unmeasured, not fixed to some new percentage.**
  What's verified is the retrieval-to-knowledge-item join the investigation
  artifact's own §3 traced as the break: 0→10 candidates for REQ-010, proven on preserved
  data, before vs. after. What's NOT verified is compliance proposing
  against those now-real candidates, evidence materializing, or drafting
  citing it - the three later stages still untouched by this fix's own
  verification. That needs a real live run; until one happens, do not read
  0→10 candidates as 0%→something% grounded. See
  `docs/eval/citation-correctness-investigation.md`'s own status line for
  the same distinction stated in full. A new many-to-many
  `knowledge_item_chunks` table, additive alongside the
  existing scalar `knowledge_items.source_chunk_id` (untouched, still
  correct for certifications/past_projects/capabilities). `import_resume`
  now associates every chunk of a résumé with the resulting employee
  knowledge_item, in both the new-employee and duplicate-résumé-merge
  branches. Migration backfills all 76 pre-existing bulk_resumes-imported
  employees from their already-correct `resume_document_id`. Verified
  before/after on the preserved investigation dump
  (`backups/b16_citation_run_20260830T134032Z.dump`, restored to a scratch
  database): before the migration, the same REQ-010 check as the
  investigation's §5 returned 0 candidates in all 29 orgs in that
  database; after, the 19 orgs that actually have the résumé fixture
  loaded returned 10 candidates each, including Priya Ostrowski's employee
  item via the PMP-certification chunk (`dcb94707-...`, the exact chunk
  the investigation named) - the other 10 orgs correctly stayed at 0 (no
  résumé fixture ever imported into them, not a fix failure). Full test
  suite (523 tests) and mypy (59 errors/12 files, canonical baseline)
  unaffected. See `docs/eval/citation-correctness-fix-options.md` for the
  three options considered and why the join table was chosen over
  routing through `resume_document_id` or reopening the résumé-claims-
  aren't-authoritative decision. **Two things this fix does NOT close,
  each with its own entry below:** `import_employee_master_row`'s
  spreadsheet-only employees still have zero chunk-level evidence (no
  source text exists to associate, under any option), and the duplicate-
  merge branch's associations are additive-only (a removed résumé claim's
  old chunk stays citable).

---

## [B16, permanent] Employees imported from `employee_master.xlsx` have no chunk-level evidence, under any fix

- **Discovered:** B16, deciding between the three options in
  `docs/eval/citation-correctness-fix-options.md` §2 for the résumé-import
  chunk-association gap. `import_employee_master_row`'s only inputs are
  spreadsheet cells (`employee_code`, `name`, `role`, `status`) - no
  document, no chunk, no free text behind them at all. None of the three
  options considered (a join table, routing through `resume_document_id`,
  or per-section extraction into real certification/past_project rows)
  can associate a chunk that was never created. This is not a gap in the
  fix that shipped (`014_knowledge_item_chunks.py`) - it's a real,
  permanent limit on what a spreadsheet row can ever cite, independent of
  which option had been chosen.
- **Impact:** an employee created only from `employee_master.xlsx` (never
  matched to a résumé upload) will never appear as retrieval evidence for
  any compliance requirement, no matter how complete the fix is elsewhere.
  A reviewer relying on the spreadsheet alone for a team member's identity
  should expect this.
- **Status:** open, by design - not something a future block should
  "discover" and attempt to close as if it were an oversight. The honest
  fix, if this ever matters, is requiring a résumé (or some other
  chunk-bearing document) per employee, not a code change to this import
  path.

---

## [B16] `import_resume`'s duplicate-merge branch only ever adds chunk associations, never removes them

- **Discovered:** B16, implementing `014_knowledge_item_chunks.py`. When a
  second résumé upload resolves to an existing employee (the
  `DuplicateVerdict.DUPLICATE` branch in `import_resume`), its chunks are
  associated additively - matching this function's own established
  "corroborates rather than overwrites" treatment of résumé data
  elsewhere (`update_employee`'s field-by-field `or` fallbacks in the same
  branch).
- **Consequence:** if a corrected résumé *removes* a claim (e.g. an
  expired certification no longer listed), the chunk from the OLDER
  résumé that mentioned it stays associated with the employee and stays
  citable as evidence. The association only ever grows.
- **Why this is accepted, not a bug:** the old document is a real document
  that really said that thing at the time it was uploaded - staying
  associated isn't wrong, just possibly stale. Reversing it (dropping
  associations to chunks from a superseded document) would need either a
  "current résumé only" invalidation rule or a human review step, neither
  of which existed before this fix and neither of which this fix's scope
  covers. Flagged here so a future reader who notices a removed
  certification still being cited finds an explanation instead of a
  surprise. See the inline comment at the call site in
  `knowledge_service.py::import_resume`.

---

## [B16] Stale docstring in `extract.py` claimed employee profiles have no per-field citation need - checked against the actual code, found false

- **Discovered:** B16, while costing `docs/eval/citation-correctness-
  fix-options.md`'s three fix options. `extract_capability_statement`'s
  own docstring claimed employee profiles have "no per-field citation
  need... since B6's own gate only ever cites certifications/
  past_projects as evidence, confirmed by inspecting every
  valid_evidence_keys entry in the ground truth." Checked directly against
  `compliance_service.py` rather than trusted: `prepare_evidence_
  candidates`/`to_llm_candidates` apply no `item_type` filter, there's a
  dedicated `item_type == "employee"` verification branch (line ~64), and
  `prompts/versions/v1/compliance_proposal.md` lists "employee" as an
  expected source kind. The code fully supports citing employees directly
  - the claim was false as a code-capability statement (it may still be
  true as a narrower claim about what the eval ground truth fixture
  happened to exercise, which was not separately checked).
- **Fixed:** the docstring, in the same commit as the schema fix -
  corrected to state what's actually true: employees get a coarser,
  whole-résumé chunk association instead of per-mention resolution,
  which is a difference in mechanism, not in what's citable.
- **Why this is worth its own entry:** this project has hit the "comment
  asserts more certainty than the code underneath it" shape several times
  now (see the standing-pattern entries elsewhere in this file) - this is
  another confirmed instance, caught by checking the downstream code
  directly instead of trusting a docstring's own citation of "the ground
  truth," which is exactly the discipline this file asks for everywhere
  else.

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
- **Status:** `check_unsupported_claims` specifically confirmed on
  organic output - closed for that one check, not for this entry as a
  whole. The other 4 checks remain unconfirmed on organic output (see
  below) - read this as "1 of 5 detectors proven live," not "detection
  on organic output proven." B12 drove the real
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
- **Idempotency-record acquisition is select-then-insert - RESOLVED, B16
  Tier 3.** (major finding #4, `idempotency.py`): concurrent requests
  with the same key used to both observe "no existing record" and both
  proceed, or race into the unique constraint as a raw 500. Fixed with
  the established `except IntegrityError` / reread / recover pattern -
  but this site turned out NOT to be the mechanical copy it looked like
  (see the "sequential-retry tested, genuine concurrency untested"
  standing-pattern entry below for why, and for the deeper, still-real
  gap this fix's own scope stops short of: `expires_at` is stored but
  still never consulted by any read path, so an abandoned `in_progress`
  record now 409s every retry forever instead of silently duplicating -
  surfaced loudly, not actually closed). Proven with a real concurrent
  test (`tests/integration/test_idempotency.py::
  test_concurrent_duplicate_idempotency_key_converges_to_one_tender_not_
  two`, `asyncio.gather` against the real ASGI app).
- **`knowledge_service.py`'s own import-row idempotency has the same
  select-then-insert shape, at three call sites - RESOLVED, B16 Group 3.**
  (found by CodeRabbit's review of B13's own diff). `import_employee_
  master_row`, `import_certification_row`, and the resume-import path
  each did `get_import_item_by_source_ref` then `create_import_item` as
  two separate awaits, not one atomic operation. The retry-resume fix
  from B13 covered SEQUENTIAL retries correctly but not TRUE CONCURRENT
  delivery of the same batch (two RQ workers, or one worker's retry
  racing a still-running original attempt): both could observe "no
  existing item" before either inserts, and the loser crashed on
  `uq_knowledge_import_item_source_ref` instead of returning the
  winner's row. Fixed with a narrower step than `document_document
  create_document`'s own reread-after-rollback (deliberately, per this
  entry's own earlier note that these three sites are a "genuinely
  harder version" of the same problem): `_claim_or_resume_import_item`
  does ONLY the claim (get-or-insert-with-IntegrityError-recovery,
  returning `needs_resolution: bool`), before any of the substantial
  duplicate-resolution work that used to run inside the same insert's
  transaction - so a reread after a lost race returns the winner's
  actual claimed row, not one still mid-resolution. Also fixed a real,
  separate, pre-existing bug found as a side effect: `import_resume` had
  NO `get_import_item_by_source_ref` retry-resume check at all before
  this pass (unlike its two siblings) - a plain sequential retry, not
  just a concurrent one, would always have crashed it. Proven with three
  new tests, one per call site (`tests/integration/
  test_knowledge_service.py::test_import_employee_master_row_concurrent_
  delivery_converges_not_crashes` /
  `test_import_certification_row_concurrent_delivery_converges_not_
  crashes` / `test_import_resume_concurrent_delivery_converges_not_
  crashes`, `asyncio.gather` on two identical real calls each).
  **Owner was B16, as this entry originally said - completed there,**
  not folded quietly into an unrelated pass.
- **A second, DEEPER select-then-insert race under the same three call
  sites, found by the very tests written for the fix above, not
  anticipated in the design - RESOLVED the same pass.** Claiming the
  import_item row is not the only insert in these three functions:
  `_claim_or_resume_import_item`'s own `needs_resolution=True` result
  means two concurrent callers can each independently run the
  duplicate-candidate query, each see it empty, and each legitimately
  decide "this is a NEW employee/certification" for the SAME content
  fingerprint - then both call `create_employee`/`create_certification`,
  and the loser crashes on `uq_knowledge_item_fingerprint` instead of
  converging on the winner's row. Confirmed live, not assumed: 2 of the
  3 new concurrency tests failed with exactly this traceback before this
  second fix existed. Same `except IntegrityError` / rollback / reread
  pattern as every other site in this cluster, but each wrapped in its
  own dedicated helper (`_create_or_get_employee`, `_create_or_get_
  certification`) with its OWN separate `UnitOfWork` - deliberately, not
  reusing the caller's transaction, because an `IntegrityError` here must
  not poison the caller's still-open transaction, which still has
  `update_import_item`/`associate_knowledge_item_chunks` work left to do
  after this returns. `import_certification_row`'s version has one
  further wrinkle the employee ones don't: it fabricates its own
  synthetic `source_chunk_id` (`_create_synthetic_chunk`) rather than
  reusing an already-committed document chunk, so the chunk is created
  INSIDE the same transaction as the certification insert, not on the
  caller's - otherwise a losing racer's chunk would already be committed
  (a different connection/transaction) by the time the certification
  insert's `IntegrityError` fires, leaving an orphaned, never-referenced
  `document_chunks` row behind on every lost race instead of rolling
  back cleanly with it. Same three tests as the entry above prove this
  (all 3 now pass; the certification and resume tests were the ones that
  originally caught it).
  found live by B16's own concurrent-uploads load test, not by
  inspection, and RESOLVED the same pass.** Two genuinely concurrent
  uploads of byte-identical content both pass `get_by_tender_and_hash`/
  `get_by_org_and_hash` before either commits, both attempt
  `uow.documents.create(...)`, and the loser used to propagate an
  unhandled `IntegrityError` on `uq_tender_document_hash`/
  `uq_org_document_hash` as a bare 500 - confirmed with a real traceback
  from 20 genuinely concurrent uploads (`asyncio.gather` against the real
  ASGI app), not simulated. **Fixed with the exact pattern
  `readiness_service.py`'s own `evaluate_readiness` race already
  established**: `except IntegrityError: await uow.rollback()`, reread
  via a fresh `UnitOfWork`, return the winner's row - re-raise only if
  the reread genuinely finds nothing (the same "constraint just fired,
  not finding the row a moment later would be a different, more
  exceptional problem" reasoning). Verified: the load test's own 20-
  concurrent-upload run now passes cleanly (all 2xx, dedup converges to
  the correct distinct-content count, no corruption), and a dedicated
  regression test
  (`tests/integration/test_document_service.py::
  test_concurrent_identical_uploads_converge_to_one_document_not_a_500`)
  covers it directly - `asyncio.gather` on two real `create_document`
  calls, asserting neither raises and both resolve to the same row.
  **Checked whether the other four sites above share enough shape to fix
  in the same pass, not assumed either way:** `idempotency.py`'s site is
  the same shape as this one - a single insert immediately followed by
  return, no intervening work - and would take the identical fix
  mechanically. The three `knowledge_service.py` sites are a genuinely
  harder version of the same problem, not a trivial extension of this
  fix: `create_import_item` is followed by substantial further work
  (duplicate-resolution branching, conditional updates, sometimes a
  second `create_employee`/`create_certification` call) before commit,
  all inside the same transaction - a reread immediately after rollback
  could race ahead of the winner's own still-open transaction and return
  a row mid-resolution, not the finished one. Fixing those three needs
  real design work (a narrower insert-or-detect step before the rest of
  the resolution logic runs, not a bare reread), not a copy of this
  pattern - left for their own pass, per this cluster's own existing
  "Owner: B16" note above, not folded in here.
- **A second, independent bug found investigating the first: the ORM
  model was missing the very constraint the fix depends on, so no
  `pytest`-built database could ever exercise this race at all until
  now.** Writing the regression test above surfaced that
  `db/models/document.py`'s `Document.__table_args__` never declared
  `uq_tender_document_hash`/`uq_org_document_hash` - migration
  `003_documents.py` created them directly as partial unique indexes
  (`op.create_index(..., unique=True, postgresql_where=...)`), and
  nothing ever mirrored that in the ORM model. `Base.metadata.
  create_all()` (every `pytest` integration test database, this
  project's own established pattern) only creates what the model
  declares - so every prior test run of this project's entire suite has
  been missing both constraints entirely, silently. Confirmed directly:
  the first version of the new regression test above passed its
  "neither call raises" assertion but FAILED "both calls resolve to the
  same row" - two real, separate document rows got created for
  identical content, something the real deployed schema (migrated via
  Alembic) would never allow. Fixed by adding both as `Index(...,
  unique=True, postgresql_where=...)` in `__table_args__`, matching
  `outbox.py`'s own existing partial-index precedent in this same
  codebase - `Base.metadata.create_all()` now creates them, matching the
  real migrated schema. Same underlying risk this file's own CI entry
  already names ("integration tests build schema via `Base.metadata.
  create_all`, which can pass even with broken Alembic migrations") -
  this is the mirror image of it: not a broken migration going
  unverified, but a model that drifted out of sync with a working one,
  silently narrowing what every test in this project could ever catch.
  **The wider audit this entry named as unperformed - done, before B16
  closes, since a constraint invisible to `create_all()` is an
  invariant no test in this project's history could verify, which bears
  directly on the release-gate condition "no invariant failure
  remains."** Mechanical: every `op.create_index`/`op.create_check_
  constraint`/`op.create_unique_constraint`/`op.create_foreign_key` call
  across all 14 migration files, diffed against what each corresponding
  model actually declares (not against what a migration's own comment
  claims). Found 8 standalone constraint/index-adding calls total beyond
  plain non-unique performance indexes (those don't represent an
  invariant a test could fail to catch, so weren't in scope for this
  question) and FK columns declared inline on the column itself (a
  different, much lower-risk shape than a partial unique index, which
  has no inline-column equivalent and must live in `__table_args__` or
  nowhere): `fk_audit_events_workflow_run_id` (correct - `audit.py`
  declares it inline, with its own comment explaining the retroactive-
  migration history), `ck_agent_trace_scope` (correct - `agent_trace.py`
  has it verbatim), `fk_import_item_target` (correct - `knowledge.py`
  declares it inline on `KnowledgeImportItem.target_knowledge_item_id`),
  `ck_redteam_finding_resolution_source` (correct - `redteam.py` has
  it), plus the two `documents` ones above (fixed). **One more gap
  found: `uq_employee_code_per_org`** (migration `005_knowledge.py`,
  same partial-unique-index shape, `WHERE employee_code IS NOT NULL`) -
  never declared on `Employee.__table_args__` either. Fixed the same
  way, same pass: added as `Index(..., unique=True, postgresql_
  where=...)`, matching the `documents`/`outbox` precedent exactly.
  Proven with a direct test (`tests/integration/test_knowledge_service.
  py::test_uq_employee_code_per_org_is_enforced_at_the_database_level`,
  two repository-level `create_employee` calls, same org + employee_code,
  asserting the second raises `IntegrityError`) - independent of
  `resolve_employee_duplicate`'s own application-level dedup, which is a
  separate mechanism already tested elsewhere and doesn't prove the
  database-level invariant itself.
  **Checked, not assumed, whether any prior finding rested on behavior
  the real schema would have rejected - it doesn't.** Every
  `create_employee` call site across the whole test suite (14 files)
  either passes no `employee_code` at all (the partial index only
  applies `WHERE employee_code IS NOT NULL`, so these were never
  affected regardless of count) or creates exactly one coded employee
  per org; the one CSV-import test that creates two coded employees in
  one org uses two different codes (`EMP-01`/`EMP-02`); the retry-
  idempotency test that reuses `EMP-01` calls the import path twice for
  the SAME row and asserts it does NOT create a second employee - the
  behavior under test is retry-safety, not something that would create a
  real duplicate either way. Checked the live dev database directly too
  (`GROUP BY organization_id, employee_code HAVING count(*) > 1`): zero
  rows. Résumé/master-row import sat at the center of this session's own
  citation-correctness investigation - worth having actually checked
  that nothing there depended on this gap, not just assumed it, given
  how much else in that investigation turned on exactly this kind of
  distinction.
  ---
  **This audit is a scorecard-level finding in its own right, not three
  isolated fixes, and belongs in the scorecard framed as one.** For all
  sixteen prior blocks, every test database in this project's history
  was built from model declarations that were missing three real
  constraints the deployed (migrated) schema has enforced the entire
  time. "Tests pass" therefore meant, precisely, "tests pass against a
  schema weaker than production" - not that the invariants those three
  constraints represent were ever actually exercised by anything this
  project calls a test, on any of those constraints, until this pass.
  All three gaps shared one shape (a partial unique index - the one
  constraint type with no inline-column form, so the one form most
  likely to be declared only in a migration and never mirrored on the
  model) and all three were fixed declaratively - nothing found here
  needed a different mechanism.
  **The mechanism that allowed this is unchanged and will produce a
  fourth.** `Base.metadata.create_all()` reads models; migrations create
  the real schema; nothing reconciles the two, and nothing did before
  this pass either - this audit is a snapshot, not a standing guarantee.
  The next migration that adds a constraint via a standalone `op.*` call
  without updating its model reintroduces the identical gap, silently,
  the same way these three did. **What would prevent recurrence, not
  built now:** a test that spins up two schemas - one via `Base.metadata.
  create_all()`, one via a real `alembic upgrade head` - and diffs their
  constraint/index sets, failing loudly on any difference. A real
  candidate for B17 or its own follow-up, not something to build as a
  side effect of an audit that was already scoped to "enumerate, diff,
  fix the declarative gaps."
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
  one, not the one that run originally produced. **Reproduced live, B16**
  (`tests/integration/test_graph_projection.py::
  test_a_damaged_graph_still_reads_as_fresh_reproduced_not_fixed`) -
  `check_freshness` settles freshness with exactly one comparison, two
  Postgres integers, never Neo4j content.
  **A cheap-looking partial fix was considered and rejected as
  inadequate, not half-built - checked against the real write order, not
  assumed sufficient.** The obvious narrow version - verify a `Tender`
  node exists in Neo4j with the matching `state_version` - looked like a
  small, safe addition (one extra query) that would catch the exact
  reproduction above (`delete_tender_scoped_subgraph` removes the Tender
  node itself, among the other 6 tender-scoped labels). Checked directly
  against `projector.py`'s own real sequence: `merge_tender_nodes` (the
  Tender node's own recreation) is the FIRST write after the delete,
  before Requirement/Evidence/Compliance/Role/ProposalClaim/
  ProposalSection are recreated - meaning a crash anywhere in that much
  larger remaining window (statistically the more likely place for a
  real crash to land, not the narrow gap before the Tender node itself is
  written) would leave the Tender node showing the CORRECT, matching
  `state_version` while children are still missing or stale, and the
  narrow check would report fresh anyway. Implementing it would have
  looked like real progress while covering only a small slice of the
  actual risk - worse than naming the gap, since it invites false
  confidence the next reader wouldn't have reason to question.
  **FIXED, post-B16.** Built exactly what was named, once the real
  effort turned out to be well under the day-long threshold that would
  have deferred it: `nodes.mark_projection_complete` sets a
  `projection_complete` property on the Tender node as the genuinely
  LAST statement of `project_tender`'s write sequence (a new step 9,
  after `merge_used_in`, before the step-10 count verification read).
  Reuses the Tender node itself rather than a separate marker node -
  deliberate, not just convenient: that node is already deleted-and-
  recreated every run (`delete_tender_scoped_subgraph`'s own tender-
  scoped label set includes `Tender`), so a stale marker surviving from
  a PRIOR successful run is structurally impossible, not merely
  unlikely - there's no prior Tender node left to read once the delete
  has happened. `queries.is_projection_complete` reads it back
  (`state_version` matches AND the property is `true`); `check_freshness`
  now requires both a Postgres session AND a Neo4j session and checks
  both, raising `GraphStaleError(reason="damaged")` - a distinct reason
  from a genuine version mismatch, so a log reader isn't misled into
  thinking the versions disagreed when they didn't.
  **The latency/ripple cost this entry itself flagged turned out
  smaller than expected, checked directly rather than assumed:** all
  three of `check_freshness`'s real callers (`preview_impact`,
  `find_contradictions`, `get_graph_trace`) already opened their own
  Neo4j session immediately AFTER calling `check_freshness` - the fix
  is to open that SAME session first and pass it in, not add a second
  connection. One caller outside `graph_service.py` itself
  (`readiness_service.py`'s stale-graph blocker) had no Neo4j session
  of its own and needed one added - a real, if small, ripple, not
  zero-cost, but contained to one call site. `get_graph_status` (never
  raises, by its own contract) folds the same check into its `fresh`
  boolean instead of raising.
  **Test:** the original reproduction test
  (`test_a_damaged_graph_still_reads_as_fresh_reproduced_not_fixed`) is
  now `test_a_damaged_graph_is_now_correctly_detected_fixed` - same
  exact reproduction (`delete_tender_scoped_subgraph` called directly),
  assertion inverted: `check_freshness` now raises with
  `reason="damaged"` instead of silently returning the stale-but-
  matching run. New sibling `test_get_graph_status_reports_not_fresh_
  for_a_damaged_graph` covers the never-raises reader. Every other
  existing graph/readiness test (46 total across
  `test_graph_projection.py`/`test_readiness_service.py`/`test_graph_
  routes.py`/`test_readiness_routes.py`) still passes unchanged,
  confirming the marker doesn't disturb a normal, undamaged projection.
- **Re-ingestion destroys stable chunk provenance - FIXED, post-B16**
  (major finding #9, `jobs/ingestion.py`'s delete-then-recreate of
  `document_chunks`).
  **Established which of the two named problems actually fires, and
  under what conditions, before proposing a fix - by reading every real
  FK to `document_chunks.id` directly, not assumed from the entry's own
  original framing:** three tables (`Requirement.source_chunk_id`,
  `SubmissionItem.source_chunk_id`, `RoleRequirement.source_chunk_id`)
  have **no `ondelete` clause at all** - Postgres defaults to blocking
  the delete outright (`Requirement`'s own column is additionally
  `NOT NULL`, so `SET NULL` was never even an option for it). Two other
  tables (`KnowledgeItem.source_chunk_id`, `EvidenceLink.source_chunk_
  id`) DO use `ON DELETE SET NULL`. **Both failure modes are real, but
  for different document categories, not a single shared mechanism:**
  RFP-side documents (real `Requirement`/`RoleRequirement`/
  `SubmissionItem` rows extracted from them) hit a hard, reachable FK
  violation on reprocess; company-side documents (résumés,
  certifications - no such rows ever reference their chunks) silently
  lose provenance instead. **Confirmed reachable via the real, already-
  built, already-tested route, not a hypothetical path**:
  `POST /documents/{id}/reprocess` (`document_service.
  reprocess_document`) has no guard whatsoever checking whether the
  document already has real extracted data before re-enqueuing
  ingestion - any ordinary reprocess of an already-extracted RFP would
  hit this.
  **Fix chosen, no schema change needed (`document_chunks` already
  carries a real `UNIQUE (document_id, content_hash)` constraint, plus a
  `content_hash` column, exactly the identity key this fix needed):**
  `_persist_chunks` (`jobs/ingestion.py`) now reconciles by
  `content_hash` instead of a blanket delete-then-reinsert - a chunk
  whose content_hash still matches a fresh re-parse is UPDATED IN PLACE
  (`DocumentRepository.update_chunk`, same row, same id), not deleted
  and recreated with a fresh one. Every FK pointing at it - the
  NOT-NULL-blocked ones and the SET-NULL-silenced ones alike - is simply
  never disturbed for unchanged content, which is the overwhelmingly
  common real trigger for reprocessing (a retry after a transient parse
  failure, not a genuinely edited source document). Only genuinely NEW
  content gets inserted; only genuinely GONE content gets deleted
  (`DocumentRepository.delete_chunks`, by id, replacing the now-dead
  `delete_chunks_for_document` - removed, not left unused, once its one
  caller stopped calling it). A delete at that point is correct, not a
  regression: a citation pointing at content no longer in the source
  document should go stale exactly as it does today.
  **Deliberately not fixed further, named as real remaining scope, not
  silently absorbed:** a requirement whose cited chunk's content
  genuinely CHANGED still hits the same `Requirement` FK block on
  reprocess - narrower and more legitimate now (only for real content
  changes, not every reprocess), but still a hard failure. What that
  citation should do when the source text it pointed at is gone is a
  real, separate re-extraction-cascade question (does the requirement
  get flagged for human re-review? re-extracted automatically?), out of
  this fix's own scope.
  **Test:** `tests/integration/test_document_ingestion.py` - two new
  tests: `test_reingestion_reuses_chunk_ids_for_unchanged_content`
  (chunk ids are IDENTICAL across two real ingestion passes, not just
  their content_hash/chunk_index, which the pre-existing `test_
  reingestion_does_not_duplicate_chunks` already checked but never
  proved identity itself) and `test_reprocessing_a_document_with_a_
  real_requirement_citation_no_longer_fails` (a real `Requirement` row
  created citing a real chunk, then the SAME document reprocessed - before
  this fix, a real `ForeignKeyViolationError`; now succeeds, and the
  requirement's own `source_chunk_id` is unchanged, not nulled, not
  dangling). All 10 tests in the file pass, including every pre-existing
  one, unchanged. Full suite 585 passed; `uv run mypy` 59/12/243
  unchanged.
- **Workflow retry controls are inert** (major finding #17):
  `retry_budget_remaining`/`max_attempts` exist in `TenderState`/
  `WorkflowTask` but nothing reads or decrements either, and nothing
  requeues a failed task - one transient provider error fails a task
  outright with no retry.
  **This stopped being theoretical in B16: a real, full-cost live run
  (45.1 minutes, past every earlier bottleneck this block found and
  fixed) hit it directly.** `commercial_analysis` failed with a real
  `RateLimitedError` (Gemini's own free-tier
  `GenerateRequestsPerMinutePerProjectPerModel-FreeTier`, 15/min for
  `gemini-3.5-flash-lite`) - not B15's own rate limiter, an external
  Google quota, confirmed from the worker's own traceback, not guessed.
  `compliance_analysis` and `talent_analysis` - the other two branches
  LangGraph's `Send()` dispatches in parallel alongside
  `commercial_analysis` (`orchestration/graph.py`) - both show
  `workflow_tasks.status='pending'`, never `started_at`: one parallel
  branch's own unhandled failure appears to have aborted the whole run
  before its siblings ever ran, not just failed its own branch. This
  finding (a) is separate from the inert-retry-controls finding above
  and not confirmed with the same rigor - real, but named as observed,
  not chased further this pass.
  **Real fix applied for the quota-collision half, not the inert-retry
  half - both stay true, on record separately, not conflated:**
  `Settings.compliance_gemini_api_key`/`talent_gemini_api_key`/
  `commercial_gemini_api_key` (`core/config.py`), one real Gemini API key
  per parallel branch, each falling back to the shared `gemini_api_key`
  when unset - wiring built and unit-tested this pass
  (`tests/unit/test_branch_gemini_api_keys.py`, 7 cases, no real LLM cost:
  each node's own `GeminiClient` construction is spied on directly,
  confirming the right key reaches the right branch and the fallback
  works when a branch key is unset). The real fix depends on a fact that
  is NOT guaranteed by "three separate Google accounts" alone - three
  accounts are only three separate quota buckets if each is also a
  genuinely separate GCP project, since a shared project means a shared
  quota and would defeat the whole point. Checked directly, per key, via
  each key's own AI Studio/console project page - not inferred from
  account separation, and not inferred from quota-error behavior either
  (a live check found a successful `generateContent` response exposes no
  project identifier anywhere, in headers or body, so that path couldn't
  have confirmed this even if tried). **Confirmed distinct: `gen-lang-
  client-0352341073`, `gen-lang-client-0008707582`,
  `gen-lang-client-0439190722`** - recorded here so a future reader can
  verify independence without re-deriving it, not just trust that it was
  once checked.
  Wired at `orchestration/nodes/compliance.py`/`talent.py`/`commercial.py`'s
  own `_build_llm_client()` - extraction, drafting, and red-team
  deliberately untouched, since they run sequentially in the graph and
  never competed for the same per-minute window the three parallel
  branches do. **This solves the quota collision this specific fixture's
  workload produced - it does not make the retry mechanism itself
  functional.** A transient failure from any OTHER cause (a real network
  drop, a genuine Gemini outage, a quota exhausted for a different
  reason) still fails its task outright with no retry, and - per the
  observation above, not yet independently confirmed - may still be
  capable of aborting sibling branches that never got a chance to run.
  Building the actual retry mechanism (reading `retry_budget_remaining`,
  requeuing instead of failing outright) remains open, exactly as this
  entry already named it.
  **B16 Group 4: the semantics question DECIDED and implemented -
  `orchestration/retry.py::classify_task_failure`, not merely
  scaffolded.** Real policy, per exception type: `RateLimitedError`
  honors the provider's own `retry_after_seconds` when Gemini supplied
  one, else this module's own 30s-base/2x/120s-cap task-level backoff
  (deliberately coarser than `gemini_adapter.py`'s own 5s-base call-level
  backoff - a task-level retry needs to wait longer than the call layer
  already did before giving up); `TransientProviderError` gets the same
  task-level backoff; `MalformedOutputError` and `ModelNotFoundError` are
  terminal, never retried at this layer (the former because a bare retry
  would resend an identical prompt to this project's temperature=0
  models with little chance of a different outcome; the latter per I4's
  own explicit "no automatic retry" for a config error); any other
  exception (no existing classification - a DB error, a bug) gets
  exactly one conservative retry at a fixed 15s delay, bounded by
  `max_attempts` rather than never retried at all. Both ceilings
  (`max_attempts` per-task, `retry_budget_remaining` whole-run-shared)
  are checked first and win unconditionally. 9 new unit tests
  (`tests/unit/test_retry_classification.py`, pure function, no LLM
  cost, no DB) cover every branch plus both ceilings independently.
  ruff-clean, mypy-clean (59/12 baseline held), full suite 557 passed.
  **B16 Group 1: WIRED, real production callers now exist - this is no
  longer "capability exists, nothing calls it" (this project's own
  standing pattern, sixth instance before this fix, closed here).**
  The instructed design was "requeue the task via RQ, don't re-enter the
  node." Checked directly against this codebase's real RQ/LangGraph
  boundary before implementing, not assumed: **there is no per-node RQ
  job to requeue.** `run_workflow`/`resume_workflow` are the only two RQ
  jobs in the whole orchestration system - each runs the ENTIRE graph
  (`graph.ainvoke(...)`, every node, every `Send()`-dispatched parallel
  branch) as one job. A specialist node like `commercial_analysis_node`
  is a plain Python function LangGraph calls in-process; it was never
  independently RQ-enqueued, so "retry just that task via RQ" isn't a
  granularity this architecture has. Reported this rather than building
  something that looked like the instruction but didn't match the real
  code, per this session's own standing instruction to say so and pick
  the better option:
  **What was actually built - RQ-level retry of the whole run, narrowed
  back down by LangGraph's own checkpointing:** a new `retry_workflow_run`
  job (`jobs/workflow.py`) resumes the SAME checkpointed thread with
  `graph.ainvoke(None, config)` - LangGraph's own idiom for "continue
  from the last committed checkpoint, no new input." A crashed super-step
  never gets checkpointed in the first place (LangGraph commits a whole
  super-step atomically, unlike Neo4j's own per-statement auto-commit
  that major finding #8 above had to work around) - so resuming
  naturally re-executes only the super-step that failed, not the whole
  workflow from scratch. This also gives the sibling-branch-abort
  question (trigger event 2, still not independently re-confirmed) a
  coherent answer without solving it directly: if a failed super-step
  really does discard ALL of its `Send()`-dispatched branches together
  (not just the one that raised), resuming that same super-step
  naturally retries all of them together too - consistent with, not
  contradicting, whatever LangGraph's own real fan-out-failure semantics
  turn out to be.
  `_handle_graph_failure` (shared by `run_workflow`/`resume_workflow`/
  `retry_workflow_run`'s own except blocks) classifies the exception
  against the most recently failed `WorkflowTask` for the run (the one
  whose own `mark_task_failed` call put it there) and schedules the
  retry via `Queue.enqueue_in` - real RQ infrastructure, real backoff
  (`classify_task_failure`'s own `delay_seconds`), survives a worker
  crash (once `enqueue_in` returns, the schedule lives in Redis, not
  this process). Does NOT re-enter the failed node in-process - no node
  function is called directly by the retry path at all, only the
  compiled graph's own `ainvoke`. Still re-raises after scheduling a
  retry (this attempt stays inspectable in RQ's own failed-job registry,
  I4's "failed jobs remain inspectable"); only skips `sync_run_status(
  "failed")` when a retry was actually scheduled.
  **A second real deviation, found while wiring, not assumed away:**
  `TenderState.retry_budget_remaining` - the field major finding #17
  originally named - lives inside LangGraph's own checkpointed state,
  not a plain column. Nothing outside a running `graph.ainvoke()` call
  can read or decrement it without re-deriving LangGraph's own
  checkpoint-serialization format. New migration
  `016_workflow_run_retry_count` adds `workflow_runs.retry_count` - a
  real, plain, mutable Postgres column - as the actual countdown;
  `retry_budget_remaining`'s own seed value (10) is reused as a constant
  ceiling, read at decision time, not as the live counter.
  `TenderState.retry_budget_remaining` itself stays exactly as inert as
  before this fix - a second, still-open residual of major finding #17,
  now named precisely rather than left implicit.
  RQ's own scheduler had to be turned on for this to work at all -
  `apps/worker/.../worker.py`'s `Worker.work()` call now passes
  `with_scheduler=True`; without it, `enqueue_in` jobs land in RQ's
  scheduled-jobs registry and sit there invisibly forever, since nothing
  promotes them to a real queue for a worker to dequeue. Caught by
  reading RQ's own `Worker.work` signature directly, not assumed
  default-on.
  **Test:** `tests/integration/test_workflow_retry.py`, 5 new tests,
  real Postgres AND real Redis (no LLM cost - `classify_task_failure` is
  pure) - a retryable failure schedules a real, fetchable RQ job in
  Redis's own scheduled-jobs registry and bumps `retry_count`; a
  terminal one schedules nothing; the run-level and per-task ceilings
  each independently override an otherwise-retryable exception; no
  failed task on record returns cleanly rather than crashing. All 52
  pre-existing tests touching `run_workflow`/`resume_workflow`
  (`test_outbox_dispatcher.py`, `test_workflow_api.py`, `test_gates_
  routes.py`, `test_orchestration_nodes.py`, `test_graph_execution.py`)
  still pass unchanged. mypy 59/12/243 baseline held; full suite 568
  passed.
  **The sibling-branch-abort question (trigger event 2) - RESOLVED,
  post-B16, confirmed directly rather than left as a coherent-sounding
  guess.** Read LangGraph 1.2.10's own installed source first
  (`.venv/Lib/site-packages/langgraph/pregel/_runner.py`), stated an
  expectation, then reproduced it live with a `FakeLLMClient`-scripted
  graph run, not just inspection:
  - **LangGraph's own design, not this graph's construction.**
    `_should_stop_others`'s own docstring is unambiguous: *"Check if any
    task failed, if so, cancel all other tasks."* The async `tick()` loop
    backs it up structurally - it waits on `asyncio.wait(futures,
    return_when=FIRST_COMPLETED)`, and the moment one done future carries
    an unhandled, non-`GraphBubbleUp` exception with no registered
    per-node error handler (this graph registers none), it breaks the
    wait loop and calls `.cancel()` on every future still in-flight.
    `compliance_analysis`/`talent_analysis`/`commercial_analysis` are all
    `Send()`-dispatched into the same Pregel super-step (`graph.py`'s own
    comment already said so), so this applies to exactly the three
    branches trigger event 2 was about.
  - **First repro attempt was itself a real lesson in why the live
    B16 observation and a naive test aren't the same experiment.**
    With no artificial delay, `compliance_analysis`/`talent_analysis`
    actually WON the race and completed before `commercial_analysis`'s
    near-instant, zero-await failure was even detected - the fixture
    tender had no requirements, so both siblings' own per-item loops ran
    zero iterations and finished near-synchronously. B16's real trigger
    was different in exactly this dimension: `compliance_analysis` was
    genuinely mid-flight through dozens of real per-requirement Gemini
    calls (real network latency) when the quota collision hit elsewhere.
    Reproduced faithfully with a `_SlowUnitOfWork` wrapper (one real,
    cancellable `asyncio.sleep()` at the first line of each sibling's
    real body) rather than declared unreproducible.
  - **Confirmed, with the delay in place:** `commercial_analysis` fails
    and marks itself `'failed'` via its own except-block; `compliance_
    analysis`/`talent_analysis` get asyncio-cancelled by LangGraph
    mid-sleep, and since `asyncio.CancelledError` is a `BaseException`,
    not an `Exception`, their OWN `except Exception:` blocks never fire
    either - `mark_task_failed` never runs for them. Also confirmed
    directly, not assumed: no code anywhere in this codebase ever sets
    `WorkflowTask.status='running'` or `.started_at` (grepped - only
    `create_task`/`mark_task_completed`/`mark_task_failed` exist), so a
    cancelled-mid-flight task and one that never got scheduled at all are
    indistinguishable from the row alone, both simply `'pending'` -
    which is exactly why B16's own observation ("both at status=
    'pending' with no started_at") could describe the symptom but
    couldn't by itself distinguish the two explanations.
  - **Does Group 4's retry work change it? Yes, confirmed directly, not
    assumed from the design.** Calling `retry_workflow_run` (the same
    function the real scheduled RQ job would call) resumes the exact
    same LangGraph checkpoint thread, and ALL THREE tasks - not just the
    one that raised - reach `status == 'completed'`. Resuming an aborted
    super-step re-executes the WHOLE super-step, cancelled siblings
    included, not only the task whose exception triggered the abort.
    This closes trigger event 2's own real risk for a run that hasn't
    checkpointed past this super-step yet - the only case that matters,
    since a super-step's writes aren't committed to the checkpoint until
    every task in it completes.
  - **A second, more severe finding surfaced by the FIRST repro attempt,
    before the delay fix, not by inspection:** a sibling cancelled
    mid-flight through a real `UnitOfWork`/asyncpg connection leaves that
    connection in a broken state; `db/session.py`'s engine had no
    `pool_pre_ping`, so the pool silently handed the broken connection to
    the very NEXT, completely unrelated query, which failed with a raw
    `InterfaceError: connection is closed` that looked like an unrelated
    bug in whatever code happened to draw it next. **FIXED:**
    `create_async_engine(settings.postgres_dsn, pool_pre_ping=True)` -
    the standard SQLAlchemy remedy for a connection invalidated by
    something outside the pool's own visibility, verified to actually
    fix the observed cascading failure by rerunning the same repro, not
    assumed correct from the docs alone. This is real, not test-only:
    the same engine/pool singleton is used by the real long-lived worker
    process across every job it ever handles, so any production
    sibling-cancellation event (which the confirmation above shows is a
    real, by-design LangGraph behavior, not a rare edge case) could have
    poisoned the pool for an unrelated later job.
  **Test:** `tests/integration/test_graph_execution.py::
  test_one_failing_specialist_branch_cancels_its_still_running_siblings` -
  drives the real compiled graph through `run_workflow`/`resume_workflow`/
  `retry_workflow_run`, asserts on real `WorkflowTask` rows, not a mock of
  LangGraph's own runner. Full suite 587 passed; mypy 59/12/244 baseline
  held (244, not 243 - migration 017 from the claim_kind fix earlier this
  pass is a new source file, not a regression).
- **Outbox events are not claimed atomically - FIXED, post-B16** (major
  finding #22, `db/repositories/outbox.py`/`outbox_dispatcher.py`): was
  select-then-enqueue with no `FOR UPDATE SKIP LOCKED` - two dispatcher
  instances could both claim the same pending event. The fifth instance
  of this project's own "sequential retry tested, genuine concurrency
  untested" standing pattern - reproduced live, not just fixed on
  inspection, per instruction.
  **Fix: `OutboxRepository.claim_by_id(event_id, max_attempts)` -
  `SELECT ... WHERE id = :event_id ... FOR UPDATE SKIP LOCKED`, called
  once per event from a FIXED candidate list built at the start of each
  `_dispatch_once()` call.** Real design correction made along the way,
  not shipped as first written: the first version claimed "whatever's
  next" (`ORDER BY available_at LIMIT 1 FOR UPDATE SKIP LOCKED`, in a
  loop) rather than by a fixed id list - caught by the EXISTING test
  suite, not the new concurrency test: `test_redis_unavailable_does_
  not_lose_the_event_or_touch_the_db_write` failed with `attempt_count
  == 3`, not the expected `1`. Root cause, traced not guessed: that
  version's own loop could re-claim the SAME just-failed event several
  times within ONE `_dispatch_once()` call once its own short backoff
  (`_backoff_seconds(0) == 1.0s`) elapsed mid-loop - a fix for
  concurrency that broke an already-correct sequential/backoff
  guarantee, the inverse failure mode of the pattern this fix was
  closing. Corrected to claim a fixed list of candidate ids (read once,
  unlocked - the real protection is per-event, at claim time), so each
  event is attempted at most once per invocation, matching the pre-fix
  behavior exactly, while `FOR UPDATE SKIP LOCKED` still gives the real
  cross-dispatcher protection that behavior never had.
  **Test:** `tests/integration/test_outbox_dispatcher.py::
  test_two_concurrent_dispatchers_claim_each_event_exactly_once` - two
  real, concurrent `_dispatch_once()` calls (`asyncio.gather` against
  real Postgres, the same technique that proved every other race in
  this project's own record) against 10 real pending events, `Queue.
  enqueue` wrapped to record every attempt by `job_id` - asserts each
  event's job was enqueued exactly once across both concurrent runs
  combined, not just that the final DB status looks right (which is
  idempotent either way and wouldn't distinguish "claimed once" from
  "claimed twice, both writes landed"). All 4 tests in the file pass,
  including the pre-existing Redis-unavailable test that caught the
  first attempt's own regression. Full suite 583 passed; `uv run mypy`
  59/12/243 unchanged.
- **`[I5]` Cost budget enforcement - RESOLVED, post-B16. A spec deviation
  now closed, not a feature added.** I5 (Build Guide `:2755-2763`,
  verbatim): *"Every workflow tracks estimated model cost. When
  projected cost exceeds `MAX_TENDER_COST_USD`: pause, show current
  progress, require bid-manager approval to continue."* `MAX_TENDER_COST_
  USD` sat in `.env.example`/`.env` for 16 blocks reading like a real,
  enforced cap while this gate was never built at all - undetected the
  same way the `preview_impact`/`USED_IN` gap was: spec names a real
  behaviour, code never builds it, nothing records the deviation until
  someone checks. Only discoverable once cost tracking itself became
  real this same pass (`agents/llm/gemini_adapter.py`'s price table) -
  there was no number to enforce against before that.
  **Design options traced before building, one chosen:** the three
  originally on the table (hard stop mid-run; finish in-flight, refuse
  new work; alert-only) were all superseded once I5's own text was read
  directly - it specifies a fourth shape, a real human-approval gate,
  the same mechanism already proven six times over in this graph. It
  beats all three: not a hard stop (this session's own sibling-
  cancellation investigation, major finding #17's own entry, already
  showed mid-flight interruption leaves `WorkflowTask` rows in an
  ambiguous, unrecoverable state - deliberately not reproduced here);
  not silent refusal (a human sees real progress and decides); not
  alert-only (it actually blocks new work by default). Cheaper to build
  too - reused fully generic, already-tested machinery
  (`orchestration/gates.py`, `GateRepository.create`,
  `interrupt()`/`Command(resume=...)`) instead of inventing a new
  control-flow shape.
  **Where the budget lives, an explicit interpretive call:** one global
  `settings.max_tender_cost_usd`, not per-org/per-tender-configurable -
  matches every other numeric `Settings` field in this codebase.
  Accounting is per-WORKFLOW-RUN (`agent_traces.workflow_run_id`), not
  per-tender lifetime and not per-org - a real, deliberate divergence
  from the old env var's own literal name ("TENDER"): a tender can have
  multiple runs (after cancellation/restart), and a fresh run gets a
  fresh budget rather than inheriting a prior aborted run's spend. The
  old name itself was kept, not renamed - I5 names `MAX_TENDER_COST_USD`
  verbatim, and the live `.env`'s own pre-existing `MAX_TENDER_COST_USD=
  25` value and a from-scratch cost derivation (this deployment's real
  configured models are all flash-lite tier per `gemini_adapter.py`'s
  own price table; the one real measured call-volume figure already on
  record, "85 real compliance calls alone" in one live run, prices a
  full run including revisions at roughly $0.30-$1.00 today)
  independently landed on the same `25.0` starting number - real
  justification, not a coincidence glossed over. Org-scoped `agent_
  traces` rows (knowledge import - `tender_id IS NULL`, confirmed by
  `ck_agent_trace_scope`) are out of scope for this cap, named
  explicitly, not silently solved. "Projected cost" (I5's own wording)
  is read as "accumulated actual cost so far, checked before the next
  expensive phase starts" - there is no cost-forecasting model in this
  codebase and building one is real, unscoped work this pass does not
  attempt.
  **B2.4's own stop condition (`:731`), checked directly, not assumed
  covered by the same gate:** B2.4 lists "total estimated LLM-cost
  budget" as one of the proposal↔red-team revision loop's own five stop
  conditions - a DIFFERENT condition from "total workflow revision
  budget" (`:706`, the one `route_after_red_team`'s own `revision_count
  >= max_revisions` check already enforces; that function's own comment
  labels its check "Budget exhausted (B2.4's own stop condition)"
  ambiguously, conflating the two). Read literally, this gate's first
  design (sitting only on the `specialist_dispatch`-bound path) would
  NOT have fully satisfied it: `route_after_targeted_replan`'s OTHER
  live branch (a `proposal_drafting`-only revision, no `compliance_
  analysis` re-run) routed straight past it, meaning a run over budget
  could still spend on repeated proposal-only revisions unchecked, bounded
  only by `max_revisions=2`, not by cost at all. Closed properly, not
  left partial: `cost_budget_gate_node` sits in front of BOTH live
  targets now (`route_after_requirement_gate`'s first-pass edge AND both
  of `route_after_targeted_replan`'s revision-pass edges), reading
  `revision_dispatch_target` back out of state to route to the correct
  downstream node on approval.
  **Two real bugs a naive copy of the closest existing template would
  have shipped, both caught before merging, not after:**
  1. **Gate-key collision on a second, later revision-pass breach.**
     `plan_version` is bumped exactly once, by `supervisor_node`, and
     never again on any revision pass (checked directly - no other file
     in `orchestration/` ever assigns `plan_version + 1`); `revision_
     count` genuinely increments per revision (`redteam.py`'s
     `targeted_replan_node`). `gates.derive_gate_key("cost_budget",
     plan_version)` unmodified would therefore compute the IDENTICAL key
     on a first-pass breach and every later revision-pass breach - a
     first pass that exceeds budget and gets approved, followed by a
     revision pass that also exceeds budget, would find the already-
     approved row via `get_by_key`, never open a new pending one, and
     still call `interrupt()` regardless - a permanently stuck run with
     no visible gate anywhere to approve. Fixed by composing the key
     with `revision_count` folded in
     (`f"cost_budget:plan-{plan_version}:rev-{revision_count}"`), not by
     changing `derive_gate_key`'s own one-arg contract the other six
     gates depend on.
  2. **Approve-path status write, wrong precedent copied initially.**
     `compliance_gate_node`'s own approve path returns `{}` (documented
     reason: the very next node in ITS sequential chain writes its own
     status moments later). `cost_budget_gate_node`'s own next node on
     the dominant path is `specialist_dispatch` - a no-op lambda - and
     none of the three parallel analysis nodes write `state["status"]`
     either (checked directly). Copying `compliance_gate_node`'s pattern
     here would have stranded `workflow_runs.status` at `waiting_for_
     human` forever after a real approval, even though specialists were
     actually running. Fixed by copying `requirement_gate_node`'s own
     explicit approve path instead (`sync_run_status("running_
     specialists")` + a matching state write) on the specialist-bound
     branch; the `proposal_drafting`-bound branch correctly returns `{}`,
     matching `commercial_approval_gate_node`'s own precedent for the
     same reason - confirmed `proposal_drafting_node` writes its own
     `"drafting"` status unconditionally at its own entry before relying
     on that.
  Both caught by direct code reading during a Plan-agent-validated
  design review before implementation, not discovered live afterward -
  the review explicitly checked this feature's topology against the
  known B7 interrupt()-upstream-of-a-Send()-fan-in-join pitfall
  (`compliance_gate_node`'s own docstring) and confirmed this gate's
  slot (strictly before any `Send()` fires, the same slot `requirement_
  gate_node` already safely occupies) is not that risk class.
  **A third, real bug caught by the test suite itself, not by review:**
  `AgentTraceRepository.sum_cost_for_run` returns a `Decimal`;
  `settings.max_tender_cost_usd` is a plain `float`. Python allows
  comparing `Decimal` to `float` but not subtracting one from the other
  - `overage = accumulated - settings.max_tender_cost_usd` raised a real
  `TypeError` the first time the over-budget integration test actually
  exercised that line. Fixed by converting to `float` once, right after
  the query, used consistently from there on.
  **New RBAC action, not in Part H's matrix** (I5 postdates it, same gap
  shape as `rbac.py`'s own `cancel_workflow_run` precedent):
  `approve_cost_budget`, scoped to `{BID_MANAGER, APPROVER, ADMIN}` -
  Approver included alongside Bid manager, not Bid manager+Admin alone,
  matching every other gate-adjacent RBAC row in this table (`approve_
  compliance`, `select_resource`, `resolve_commercial_finding`, `final_
  readiness_approval` all include Approver as a superset role).
  **Frontend: explicitly out of scope, named honestly, not silently
  glossed over.** Backend/API support ships (new `CostBudgetGateResponse`/
  `CostBudgetSummary`, `_GATE_TYPE_TO_RESPONSE_CLASS` entry - without
  these, any route returning/listing a real `cost_budget` gate would
  KeyError). A real UI entry point does not: checked directly, gate
  *discovery* in the web app is hardcoded per-screen (`RequirementRegister.
  tsx` filters for `"requirement_review"`, etc.) with no "any pending
  gate" screen anywhere - a real `cost_budget` gate would be
  undiscoverable in the UI today without a new screen or hooking into an
  existing one, not just missing a render case.
  **Files:** `core/enums.py` (`WorkflowStatus`/`_AWAITING_HUMAN`,
  real additions beyond R3's verbatim transcription), `core/config.py`
  (`max_tender_cost_usd`), migration `018_approval_gate_cost_budget`
  (+ `db/models/workflow.py`'s matching `CheckConstraint`), `db/
  repositories/agent_traces.py` (`sum_cost_for_run`), `orchestration/
  gates.py` (`GATE_TYPES`/`GATE_TYPE_ACTIONS`), `domain/policies/rbac.py`
  (`approve_cost_budget`), new `orchestration/nodes/cost_budget.py`
  (`cost_budget_gate_node`), `orchestration/routing.py` (`route_after_
  requirement_gate`/`route_after_targeted_replan` retargeted, new
  `route_after_cost_budget_gate`), `orchestration/graph.py` (new node +
  retargeted conditional edges), `apps/api/.../schemas/gates.py` +
  `routes/gates.py` (`CostBudgetGateResponse`).
  **Test:** `tests/unit/test_cost_budget_routing.py` (5 tests, pure
  routing logic). `tests/integration/test_cost_budget_gate.py` (2 tests,
  real Postgres, no graph runtime needed: `sum_cost_for_run`'s own SQL
  scoping/coalescing, and the gate-key uniqueness fix proven directly
  against `GateRepository`). `tests/integration/test_graph_execution.py`
  (2 new tests driving the REAL compiled graph: an under-budget run
  opens no gate and specialists complete normally; an over-budget run
  opens a gate before any specialist task leaves `pending`, blocks until
  a real approval decision is recorded, and `workflow_runs.status`
  correctly reaches `running_specialists` then its real next pause, not
  stuck on the gate's own status write forever). B16's reliability item
  9 ("cost budget exceeded") is now run for real by this last test, not
  deferred - see `docs/eval/b16-scorecard.md`'s own §6 update. Also
  closes three pre-existing routing tests that would otherwise have
  silently asserted the old, now-wrong topology (`test_orchestration_
  routing.py`, `test_orchestration_nodes.py`, `test_redteam_routing.py`
  - the last one needed a second fix beyond what a first pass caught,
  since the design review's own original finding only flagged the
  specialist-bound branch, not the proposal-drafting-bound one this
  entry's own B2.4 completeness fix also touched). Full suite 600
  passed; `uv run mypy` 59/12/246 (246, not 244 - two new source files,
  the migration and `cost_budget.py`, not a regression).
- **Cancellation-during-execution race, remaining half** (major finding
  #14): this pass fixed the half where `run_workflow`/`resume_workflow`'s
  own new exception handler could overwrite a real `cancelled` status with
  `failed` (re-checks `is_cancelled()` before writing now). Still open: an
  in-flight node's own `sync_run_status` call can itself race a
  concurrent cancellation and write a non-terminal status over
  `cancelled` - needs either a compare-and-swap on `workflow_runs.status`
  or a cancellation check immediately before every such write, not
  reproduced live here.
  **Changed by plan item 43 (2026-11-03), still open:** when the tender
  has a newer active run, the late write now fails on
  `uq_workflow_runs_one_active_per_tender` (an `IntegrityError` in the
  old run's job) and the cancelled run stays cancelled, instead of two
  runs becoming active (observed in
  `test_a_write_reviving_a_cancelled_run_fails_while_a_newer_run_is_active`).
  With no newer run, the write still revives the cancelled run, as
  before. The fix named above is unchanged.
  **Superseded by plan item 44 (2026-11-03):** the status write now
  refuses to leave a terminal status before the index is reached, with or
  without a newer run (the test is now
  `test_a_write_reviving_a_cancelled_run_is_refused_while_a_newer_run_is_active`).
  **What the old run's job then does (read in code, plan item 44,
  2026-11-03; not reproduced end to end):** the node's `IntegrityError`
  escapes `graph.ainvoke`; the job logs "workflow run ... failed" with a
  traceback at ERROR, sees the run cancelled, so skips the retry and the
  "failed" write, and re-raises, so RQ logs a second traceback and puts
  the job in its failed registry. A gate node commits its gate row
  before its status write, so a pending gate is left on the cancelled run
  (deciding it is a no-op: the resume's claim needs `waiting_for_human`).
  The cancelled run stays cancelled. Worse than failing quietly; the
  compare-and-set fix (a status write that never leaves `completed`,
  `failed` or `cancelled`, and a quiet stop in the job when it matches
  nothing) was proposed for the user's decision, not built.
  **RESOLVED by plan item 44 (2026-11-03, approved by the user):**
  - every status write is one conditional UPDATE that refuses to leave
    `completed`, `failed` or `cancelled`
    (`WorkflowRepository.update_run_status`); a refused write raises
    `RunNoLongerActiveError`, and the three workflow jobs catch it, log one
    INFO line naming the run and what was refused, and return normally (no
    traceback, no RQ failure, no retry, no "failed" write);
  - cancelling closes the run's pending gates in the same transaction, as
    `rejected` with `decision_payload` `{"decision": null, "closed_by":
    "run_cancelled"}`: every existing check reads a non-pending gate as
    decided, and a later decision gets `GateAlreadyDecidedError`, never a
    replay;
  - opening a gate checks, in the gate's own transaction with the run's row
    share-locked, that the run is still active; a cancel then either
    committed first (nothing is opened) or waits and closes the new gate.
  Observed (`tests/integration/test_cancellation_race.py`, the real
  `run_workflow` with a one-step graph that cancels mid-flight): a write of
  "drafting" leaves the run cancelled with no newer run (the old revival)
  and with a newer run active, each with one INFO line, no WARNING or
  traceback and no retry scheduled; the real requirement gate node, the
  cancel landing just after its entry check, opens no gate; cancelling
  closes a pending gate and a later approve or reject is refused. Each
  fails under its mutation (no terminal guard: revived, an ERROR logged;
  no quiet stop: an ERROR traceback; no gate check; no closing).
  Found by the suite on the way: the first version of the conditional
  UPDATE expired the in-session run's columns set by SQL, so the cancel
  route's answer failed with `DetachedInstanceError` (a 500); the run is
  now reloaded in its session after a write. Two existing tests changed:
  `test_resume_workflow_does_nothing_once_cancelled_mid_flight` now
  expects the approval after the cancel to be refused (the gate is
  closed), and still delivers the resume; the final gate's rerun test
  puts the run back to `running` between its two direct calls, the state
  a real replay starts from, since its patched interrupt completes the
  run on the first call.
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
  - **CI: RESOLVED in B16, with a correction to this entry's own framing.**
    "CI provisions no service stack but runs all tests" implies CI
    executed and something was observed - checked directly rather than
    assumed: this repo has no git remote configured, so `ci.yml` has run
    **zero times** across all 16 blocks. Every implicit "CI is green"
    assumption in this project's history was vacuous, not confirmed - not
    a return to green, the first time this workflow will ever have been
    observed running at all. Fixed as originally scoped: the `test` job
    now provisions `postgres`/`redis`/`neo4j` as real service containers
    (image versions matching `docker-compose.yml` exactly) plus a `minio`
    container started via a manual step (GitHub Actions' `services:`
    block has no `command:` field, and `minio/minio` needs an explicit
    `server /data ...` command to start - not expressible there), and runs
    `scripts/verify_migrations.py` (real upgrade→downgrade→upgrade) before
    `pytest`. `tests/integration/conftest.py`'s own `Base.metadata.
    create_all` schema-build for the test suite itself is unchanged and
    deliberately so (its own comment already explains why) - CI now
    additionally catches a broken Alembic migration via the dedicated
    script, not by changing how the test suite builds its own schema.
  - **Worker image: RESOLVED in B16.** `apps/worker/Dockerfile` now bakes
    the embedding model (`sentence-transformers/all-MiniLM-L6-v2`) into its
    own layer, placed after `uv sync` (so the package is installed) but
    before the app-code `COPY`s (so it stays cached across ordinary code
    changes). Verified against a real build, not assumed: rebuilt the
    image, confirmed `model.safetensors` present under `/root/.cache/
    huggingface/` inside the running container - no `USER` directive
    anywhere in this Dockerfile, so the cache written at build time is
    visible to the same root user at runtime.
  - **Compose readiness: the one-line fix only, taken deliberately narrow -
    see this file's own environment-traps entry for what it does NOT
    fix.** `docker-compose.yml`'s `api` healthcheck now checks
    `/health/ready` (real Postgres/Redis/Neo4j/MinIO connectivity) instead
    of `/health/live` (process-is-up only) - an already-existing, unused
    check now actually wired in. Verified against a real rebuild: the
    container reported `health: starting` → `healthy` once `/health/ready`
    started returning 200 (confirmed in its own logs, not assumed from the
    compose status alone). **What this does not fix, stated explicitly so
    it isn't mistaken for closing the trap:** the environment-traps
    standing-pattern entry's incidents 6/8 - and, now, incident 9 - are
    a container reporting healthy while its host port publish is
    missing, or while the image it's running predates the code just
    written. No in-container check, `/health/ready` included, can see
    either failure: both are about what's true OUTSIDE the container
    (Docker's own NAT/port-forwarding state; which image is actually
    loaded) that no process running inside it can observe about itself.
    Nine incidents of this standing pattern now recorded, the most recent
    (9) a false negative on a correctness check, not a container failing
    to start - this fix improves what an in-container check can see; it
    does not close the trap that check can never see in the first place.
  - **Schema/bucket migration-init service for Compose itself, and
    deepening `/health/ready`'s own Postgres/MinIO checks (bare `SELECT 1`
    passes against an empty database; MinIO's liveness check is
    unauthenticated) - still open, not addressed by any of the above.**
    Scoped and discussed during this same B16 pass but deliberately left
    for a future block rather than bundled in - a real design question
    (check `alembic_version` matches head? a known table exists? a real
    bucket HEAD request?), not a mechanical fix like the healthcheck
    target swap above.
- **Compliance's state_version bump is unconditional even on an identical
  semantic retry (minor finding #6) - STALE, checked directly against
  current code (B16, Tier 3 pass), not still open.** This entry described
  `persist_compliance_decision` bumping once per call regardless of
  whether `status`/evidence changed. That's not what the function does
  today: `materially_changed` is computed before either write branch runs
  (`existing is None`, or `status` differs, or `new_evidence_ids` isn't a
  subset of what's already linked) and the bump at the bottom of the
  function is gated on it directly - `if materially_changed: await
  uow.tenders.bump_state_version(tender_id)`. A dedicated regression test
  already exists and passes right now, not just inferred from reading the
  code: `tests/integration/test_compliance_service.py::
  test_persist_compliance_decision_does_not_bump_state_version_on_
  identical_retry`, whose own docstring names this as a CodeRabbit
  follow-up fix, predating this session. Left in this file's own record
  uncorrected until this pass actually went to fix it and found there was
  nothing to fix - a real instance of this file's own record going stale
  rather than the code, worth naming for the same reason a wrong finding
  is worth retracting anywhere else in this file: an audit that reports a
  gap incorrectly is worse than one that finds nothing.

## [B16] Load tests: concurrent uploads, retrieval latency, graph query latency

- **Concurrent uploads: PASS, after fixing what it found.** 20 genuinely
  concurrent uploads (`asyncio.gather` against the real ASGI app, real
  PDF/DOCX bytes, 15 distinct + 5 duplicate-content) plus a dedicated
  2-concurrent-request replay of B13's own duplicate-content scenario
  (KNOWN_LIMITATIONS.md's `document_service.create_document` race entry
  above). Before the fix: a real, uncaught `IntegrityError` 500. After:
  all 20 return 2xx, 15 documents created (matching distinct content
  count exactly), no corruption (hash sets match), both B13-replay
  requests succeed. No numeric pass/fail threshold needed here - the
  assertions are correctness, not speed (no upload lost, corrupted, or
  duplicated), stated before running, per this project's own discipline
  against a load test with no stated definition of "pass."
- **Retrieval latency: baseline only, no spec-derived threshold exists**
  (checked directly against the PRD and Build Guide - neither names a
  number, only the category to measure). Corpus: 93 genuinely distinct
  real fixture items from `scripts/synthetic_tender_a.py` (74 requirement
  sentences spanning every category/mandatory-modal combination + 19
  employee/certification/project/capability entries - the actual ceiling
  of non-repeating content this project's fixtures hold, confirmed by
  import and direct count, not estimated), chunked via the real B4
  chunker into 39 chunks, embedded via the real local model. Measured by
  inserting incrementally (9 / 19 / 39 rows genuinely present in the
  database at each checkpoint, not a fixed table with `retrieval_top_k`
  varied - caught before running that varying top_k against a static
  table measures result-count, not corpus size, since vector/lexical
  search scans the whole index either way).
  - **A finding that contradicts the reasoning behind seeding a bigger
    corpus in the first place, stated as such, not buried in a table.**
    The corpus was sized up specifically on the premise that embedding-
    model overhead would dominate a too-small corpus and mask the
    retrieval path's own cost. The measurement says the opposite:
    search dominated at every size tested, including the smallest (9
    chunks) - see the numbers below. The premise that motivated this
    test's own design turned out to be wrong, and the result is honest
    precisely because it was allowed to contradict it rather than being
    read as confirming it.
  - **A scorecard-level result in its own right, not a caveat on three
    numbers: this environment cannot produce a meaningful latency
    measurement at n=30.** Cold start was isolated specifically to rule
    it out as the explanation before drawing that conclusion - one
    untimed warmup call (`embed_texts` + `search_company_knowledge`)
    before any timed measurement, then a full re-run. First pass (no
    warmup) produced a non-monotonic curve (approx search-only cost
    49.4ms -> 33.5ms -> 77.3ms across sizes 9/19/39), consistent with
    model load-in contaminating the first few calls. **Warmup did not
    fix it - the shape changed into a different, equally non-monotonic
    one** (embedding P50 13.3 -> 33.9 -> 35.8ms across the same three
    sizes). The decisive evidence isn't the shape, it's what varied:
    `embed_texts` embeds only the fixed query text - its cost is
    corpus-size-independent BY CONSTRUCTION, has no code path that reads
    corpus size at all, and still swung 2-3x call to call. A quantity
    that cannot legitimately vary with the thing being tested varying
    anyway is a measurement-setup finding, not a data point: at n=30, in
    this environment (shared dev machine, Docker containers competing
    for host CPU), latency numbers here are dominated by system-level
    noise, not by corpus size, cold start, or anything this test is
    actually trying to measure.
  - **Numbers, both passes, for the record - not trusted as a real
    curve, kept as the evidence for the finding above:**

    | size | embed P50 (cold) | search P50 (cold) | embed P50 (warm) | search P50 (warm) |
    |---|---|---|---|---|
    | 9 | 35.0ms | 84.4ms | 13.3ms | 41.4ms |
    | 19 | 31.4ms | 64.9ms | 33.9ms | 49.6ms |
    | 39 | 24.4ms | 101.7ms | 35.8ms | 49.4ms |

    Search dominates the embed-only step at every size, both passes -
    the one conclusion these numbers support with any confidence; the
    P50 values themselves should not be read as precise.
  - **What would actually fix this, not just disclaim it:** (1) larger
    n - 30 samples is too few to average out container-level scheduling
    noise, likely needs 200+ per checkpoint before P50 stabilizes call to
    call; (2) a quieter environment - this ran on a shared dev machine
    with the full Docker stack (7 containers) live alongside it, not an
    isolated benchmark host; (3) in-process timing isolated from
    container/system variance - the current script times full round
    trips through a container's own event loop and connection pool,
    where GC pauses and scheduler jitter are indistinguishable from real
    query cost; a `pg_stat_statements`-level or Postgres `EXPLAIN
    ANALYZE`-level measurement would isolate the database's own query
    time from everything sitting above it. None of the three was applied
    here - naming them so a future run is actionable, not another
    baseline with the same noise.
  - **Consequence for future use:** the two latency rows in the
    scorecard are baselines in name only. A future regression comparison
    against these exact numbers would be comparing noise to noise, not a
    real signal to a real signal - state that plainly wherever these
    numbers get reused, not just here.
- **Graph query latency: baseline, real scale, clean.** Largest real
  tender in the live dev database (98 requirements), graph freshly
  projected before measuring. `preview_impact`: P50=7.9ms, P95=17.6ms,
  P99=138.8ms (n=30). `find_contradictions`: P50=24.6ms, P95=37.2ms,
  P99=290.6ms (n=30). Sensible P50/P95 at this scale; the P99/max
  outliers (138.8ms/188.1ms and 290.6ms/393.4ms respectively, well above
  their own P95) are worth one line rather than left unexplained: no
  warmup call preceded this measurement, so these single high outliers
  are more likely a first-query/JIT/connection-warmup effect than a
  real tail-latency signal - not isolated the way the retrieval-latency
  numbers above were, since this run predated that lesson. A future
  re-run should warm up first, the same way, before trusting the P99
  here as a real number.
- **Test:** none of the above are `pytest` regressions - they're one-off
  load-test scripts (not committed; run directly against the live dev
  stack) plus the one real correctness bug they found, which does have
  permanent coverage (`tests/integration/test_document_service.py`, see
  the race-fix entry above).

## [B16] `agent_traces.estimated_cost_usd` has never been populated - RESOLVED post-B16, all five dead-config/dead-instrument instances now closed

- **Discovered:** confirming real dollar cost before triggering B16's own
  live E2E run (the post-B16 backend-closure plan's own Tier 1). Checked
  directly rather than assumed a per-call cost figure existed to sum:
  `SELECT count(*), count(estimated_cost_usd), count(input_tokens) FROM
  agent_traces` against the live dev database returns 2,455 total rows,
  **0** with `estimated_cost_usd` populated, 982 (40%) with
  `input_tokens` populated. Zero rows, ever, across every block this
  column has existed, carry a real cost figure.
- **Why this is a different claim than "unmeasured":** the B16 scorecard
  recorded metric 12 (Cost) as "NOT MEASURED as a tracked figure" -
  phrasing that implies the instrument exists and simply hasn't been
  read yet. It doesn't: nothing in this codebase ever writes to
  `estimated_cost_usd` (checked directly - no assignment to the field
  exists anywhere in `agents/llm/`, the node files, or the
  trace-persistence path). The column was declared in the schema and
  never wired to anything that could fill it. "Never measurable" is the
  accurate claim, not "measured rarely" or "not yet gotten to."
- **Grouped with three config-level dead-instrument instances, same root
  pattern, all four now resolved except this column itself:**
  1. **`settings.llm_provider`** - resolved in B16: deleted, 12 call
     sites bypassed it directly (each constructs `GeminiClient` itself).
  2. **`MAX_TENDER_COST_USD`** - resolved in B16: deleted from
     `.env.example`. Never had a corresponding `Settings` field at all -
     the more severe version of the shape, since it was never even wired
     into the settings object it would need to reach code through. A
     config line claiming a real, enforced budget cap exists is worse
     than the line simply being absent, since it invites the same
     "NOT MEASURED, implies unread not unbuilt" misreading corrected
     above for `estimated_cost_usd`. The scorecard's own metric 12
     (Cost) is corrected alongside this - the accurate status is that no
     cost-budget-enforcement feature was ever built, recorded as an
     explicit out-of-scope decision, not a measurement gap.
     **Superseded, later the same session - REVIVED, not left deleted:**
     I5 (Build Guide `:2755-2763`) turned out to be a real, locked spec
     requirement naming this exact env var, never built - see this
     file's own `[I5]` entry above for the full record. `settings.max_
     tender_cost_usd` is a real `Settings` field again (`25.0`), now
     actually wired to a real gate. The B16-era "no feature was ever
     built" framing was accurate as of B16 - closing this out required
     building the feature it was missing, not re-deleting a config line
     a second time.
  3. **`settings.max_proposal_revisions`** - resolved post-B16: deleted
     from `config.py` and `.env.example`. Grepped directly - zero read
     sites anywhere in `packages/`. Its own removed comment additionally
     claimed (falsely - checked, not assumed) that the value was "Seeded
     into TenderState['max_revisions'] by load_context_node at run
     start." It never was: `orchestration/routing.py:84` does read
     `state["max_revisions"]` for real, but `apps/worker/.../jobs/
     workflow.py`'s `run_workflow` seeds that state key from a
     **hardcoded literal `"max_revisions": 2`** in its `initial_state`
     dict, never touching `settings.max_proposal_revisions` at all - the
     same "comment overclaims a connection that was never built" shape
     already on record for `llm_provider` (its own `extract.py`
     docstring made the identical kind of false claim before B16).
  4. **`MAX_AGENT_RETRIES`** - resolved post-B16: deleted from
     `.env.example`. Never had a corresponding `Settings` field at all
     (same shape as `MAX_TENDER_COST_USD`) - grepped directly, zero read
     sites anywhere in `packages/`. Previously recorded here as
     "adjacent evidence" for major finding #17 (workflow retry controls
     inert), on the theory it was likely the number that finding's own
     retry budget was meant to be seeded from. Re-checked now that #17
     is fully wired (`orchestration/retry.py`, `classify_task_failure`):
     it remains genuinely dead even post-wiring, not merely dead until
     #17 landed - the real backoff constants
     (`_TASK_LEVEL_BASE_BACKOFF_SECONDS = 30.0`,
     `_TASK_LEVEL_MAX_BACKOFF_SECONDS = 120.0`,
     `_UNKNOWN_EXCEPTION_DELAY_SECONDS = 15.0`) and the per-task
     attempt ceiling (`WorkflowTask.max_attempts`, DB default `2`) are
     all hardcoded in that module, none sourced from this env var. #17's
     real wiring simply never reached for it.

  Not identical shapes across all four - `llm_provider`/
  `max_proposal_revisions`/`MAX_TENDER_COST_USD`/`MAX_AGENT_RETRIES` are
  config values nothing reads; `estimated_cost_usd` is a column nothing
  writes - but the same underlying pattern: something declared to look
  load-bearing that was never connected to the code path that would make
  it real.
- **Status:** `estimated_cost_usd` itself - RESOLVED, post-B16. Checked
  first whether this was wiring or new work, per instruction, rather than
  assumed either way: `gemini_adapter.py`'s own `response.usage_metadata`
  already carries real `prompt_token_count`/`candidates_token_count` -
  the SDK returns them, and the adapter already extracted them into
  `input_tokens`/`output_tokens` (matching the 40% of rows that already
  had those two columns populated). The gap was purely the dollar
  conversion: no price table existed, so `estimated_cost_usd` was
  hardcoded `None` at the one real call site, with its own comment
  explicitly deferring it to "a future block." **Fix:**
  `_PRICE_PER_MILLION_TOKENS_USD` (`gemini_adapter.py`), a dict keyed by
  exact model id string (`gemini-3.1-flash-lite`, `gemini-3.5-flash-lite`,
  `gemini-3.5-flash`, `gemini-3.7-flash`, `gemini-3.8-flash`,
  `gemini-2.5-pro`, `gemini-3.1-pro-preview`), each mapped to real
  published (input, output) USD-per-1M-token rates - looked up from
  https://ai.google.dev/gemini-api/docs/pricing directly, not guessed
  from memory, since a wrong invented number would be worse than an
  honest `None`. **A hardcoded approximation, not a billing integration,
  said plainly in the code comment itself:** Google can change these
  rates without this file knowing, and the SDK's own text/image/video
  input rate is used uniformly (every call this project makes is text) -
  treat the resulting figure as an order-of-magnitude signal for
  comparing runs, never as a real invoice. A model id not in the table
  (the "changeme" placeholder `REASONING_MODEL`/`WRITER_MODEL`/
  `CRITIC_MODEL` still default to) leaves `estimated_cost_usd` `None`,
  honestly, rather than guessing another model's price for it -
  `ModelNotFoundError` would terminate that call before cost estimation
  is ever reached anyway. `EXTRACTOR_MODEL`/`VERIFIER_MODEL` (the two
  real, non-placeholder models this project actually calls today) are
  both in the table. Metric 12 becomes a real number on the next live
  run - not re-run this pass to demonstrate, matching this session's own
  "flag a new billed run separately" posture; the arithmetic itself is
  proven by a real unit test instead, at zero cost. All four config-level
  instances - resolved (`llm_provider`, `MAX_TENDER_COST_USD` in B16;
  `max_proposal_revisions`, `MAX_AGENT_RETRIES` post-B16). The live,
  untracked `.env` initially left untouched (out of the original
  `.env.example`-only scope) - on explicit follow-up instruction, its own
  `MAX_PROPOSAL_REVISIONS=2`/`MAX_AGENT_RETRIES=3` lines were removed too.
  `MAX_TENDER_COST_USD=25` remaining in `.env` turned out not to be
  leftover dead config at all - superseded by this file's own `[I5]`
  entry: `settings.max_tender_cost_usd` is a real `Settings` field again,
  and this exact pre-existing `.env` value is what it now reads.
- **Test:** `tests/unit/test_gemini_cost_estimation.py` - four tests, pure
  function, zero SDK/network dependency: a known model computes the real
  published-rate dollar figure; an unmapped model id stays `None`, not a
  guessed price; missing token counts (a cache hit) stay `None`, not a
  false `0.0`; both of this project's real configured models
  (`gemini-3.1-flash-lite`, `gemini-3.5-flash-lite`) are confirmed priced.
  The `max_proposal_revisions`/`MAX_AGENT_RETRIES` removal earlier in
  this same cluster was confirmed a true no-op on its own terms too (full
  suite 585 passed, mypy 59/12/243, both unchanged from immediately
  before that edit). Full suite 591 passed; mypy 59/12/244 baseline held
  as of this entry's own final state.

## Standing pattern: a comment asserting a wiring that doesn't exist is invisible to grep - six instances, one blind spot in review

- **Discovered:** named once the count reached three, across two separate
  passes; a fourth turned up B17, in code (not a doc comment). The shape
  is more general than dead config specifically - it's about how a false
  claim of connectedness survives review, not about what kind of
  connection it falsely claims.
  1. **`settings.llm_provider`**'s own comment (`config.py`, resolved
     B16): claimed the field "selects which `agents/llm/*_adapter.py`
     implementation gets constructed." Zero real read sites, anywhere -
     every one of 12 call sites constructs `GeminiClient` directly. See
     the dedicated entry above.
  2. **`extract_capability_statement`'s docstring** (`extract.py`,
     resolved B16): claimed employee profiles have "no per-field citation
     need." False against the actual downstream code, checked directly
     rather than assumed - see the dedicated entry above.
  3. **`settings.max_proposal_revisions`**'s own comment (`config.py`,
     resolved post-B16): claimed the value was "Seeded into
     TenderState['max_revisions'] by load_context_node at run start."
     False - `jobs/workflow.py`'s `run_workflow` seeds that state key
     from a hardcoded literal `"max_revisions": 2`, never touching this
     setting. See the dead-config entry above.
  4. **`run_workflow`/`resume_workflow`/`retry_workflow_run`'s shared
     exception-handler comment** (`jobs/workflow.py`, open - see the
     dedicated `workflow_runs` status-transitions entry below): claims
     "re-checking here... is what stops 'failed' from silently
     overwriting a real, deliberate cancellation." The `is_cancelled`
     check it describes is real and correct; the claim that it CLOSES
     the race is not - `is_cancelled` and the later `sync_run_status`
     write are two separate awaits, not one atomic guard, so a
     cancellation landing in that window is still overwritten. Unlike
     instances 1-3, not a dead/unread field - working code with a
     comment overselling what it actually guarantees.
  5. **`BidDashboard.tsx:7-9`'s comment** (found 2026-10-10, open, docs
     only): says the list endpoint returns `tenders.status` through
     `coarse_status_for`, which maps `workflow_runs.status` instead. See
     the `tenders.status` entry below.
  6. **This file's own audit entry** ("[found during B17 Phase 0b ...]
     Dependency audit", observed 2026-10-06, corrected 2026-10-22 in
     `ca998a6`): said pypdf "parses uploaded, untrusted tender PDFs".
     Nothing imports pypdf, in our code or in any installed package;
     uploaded PDFs go through pdfplumber (pdfminer.six, with pypdfium2
     for rendering). The dependency was then removed (`9d35b11`). Unlike
     instances 1-5, not a code comment: a claim in this file, written
     while triaging an advisory, about which code a library serves.
     Caught the same way: by searching for the import the claim implied.
- **Why this is invisible to grep specifically:** a plain dead-config or
  dead-code check (grep for read sites, list unused symbols) catches a
  field or function with zero callers. It does NOT catch a field with
  zero callers that ALSO carries a comment describing behaviour it
  doesn't have - the declaration plus the comment together read as a
  complete, working, documented connection. Nothing about the code's
  shape signals a problem; the prose itself is the only thing that's
  wrong, and prose isn't type-checked, isn't linted, and isn't run.
  Catching any of these three took the same specific step: read the
  *other end* of the claimed connection directly (the real call site the
  comment names, or the real downstream consumer it describes) rather
  than trusting the comment's description of it once the symbol itself
  looked properly declared.
- **Status:** instances 1-3 and 6 resolved (deleted or corrected);
  instance 5 open (docs only). Instance 4
  open by design decision, not oversight - see its own dedicated entry
  below for why B17 audited and reported it rather than fixing it. The
  pattern itself is not closeable by a fix - it's a standing review
  discipline, same posture as the "eval/measurement code needs the same
  scrutiny as pipeline code" entry above: a comment describing a
  connection is a claim, not evidence, and should be spot-checked against
  the code it describes rather than taken as read.
- **Test:** none - this is a review-process note, not a code path.

## Standing pattern: sequential retry tested, genuine concurrency untested - seven instances, one blind spot in test design

- **Discovered:** not six separate bugs found independently - a pattern
  named once the count reached four, extended when a fifth turned up
  post-B16, and a sixth found by CodeRabbit review the same session, the
  same discipline this file applies to every other standing pattern (see
  "measurement/verification tooling," "environment-traps," "a
  two-resource guard," "this frontend has no recovery path"). All six
  are the identical shape: select-then-act with no row lock/claim/atomic
  guard, a real test proving the SEQUENTIAL case (a retry after seeing
  the first response) works correctly, and no test anywhere exercising
  two workers genuinely in flight at once.
  1. **`document_service.create_document`** (B16) - found live by the
     concurrent-uploads load test, not by inspection. RESOLVED.
  2. **`apps/api/.../idempotency.py`'s own record acquisition** (B16,
     Tier 3) - RESOLVED, this pass. Turned out to hide a second, worse
     bug the crash had been masking: the naive "same fix as #1" version
     didn't crash, but silently let both concurrent requests create
     separate tenders, because unlike #1 there's no second, independent
     constraint the loser's own reread converges on - the tender doesn't
     exist yet to converge on, only a marker that someone else is still
     making it. Had to also close a second branch ("existing but not yet
     completed") the crash had never let either previous fix or this
     file's own audit reach. See that entry above for the real
     consequence this traded in: an abandoned `in_progress` record (a
     worker that crashed before ever calling `finalize()`) now 409s every
     retry forever, since `expires_at` still isn't consulted anywhere -
     the crash-recovery gap wasn't introduced by this fix, but it is now
     surfaced loudly instead of hidden as a silent duplicate.
  3. **`knowledge_service.py`'s three import-row sites** - found by
     CodeRabbit, RESOLVED B16 Group 3. Deliberately NOT the same
     mechanical fix as #1/#2: each site does substantial further work
     (duplicate-resolution branching, conditional updates, sometimes a
     second `create_employee`/`create_certification` call) between the
     select and the point where a reread-after-rollback would be safe to
     trust, so the fix is a narrower `_claim_or_resume_import_item`
     insert-or-detect step before the rest of the resolution logic runs,
     not a bare reread - real design work, as this entry originally said
     it would need. Also surfaced a SECOND, deeper race one layer below
     the claim step itself (two concurrent callers both legitimately
     deciding "NEW" for the same fingerprint, both hitting
     `uq_knowledge_item_fingerprint`) - not anticipated in the design,
     caught live by the tests written for the first fix, closed with its
     own dedicated helpers. See the two dedicated entries above (in the
     select-then-insert cluster) for both fixes' full detail - not
     repeated here.
  4. **`outbox_dispatcher.py`'s event claiming (major finding #22)** -
     found by inspection (no `FOR UPDATE SKIP LOCKED` on the
     select-before-enqueue), RESOLVED post-B16. `OutboxRepository.
     claim_by_id` claims one event by id, `FOR UPDATE SKIP LOCKED`,
     against a fixed candidate-id list built once per dispatch cycle -
     not "claim whatever's next in a loop," which was the first fix
     attempt and a real regression: it let the SAME just-failed event be
     re-claimed and re-attempted multiple times within one dispatch call,
     since a Postgres row lock releases at commit and the event's own
     short backoff could already have elapsed by the time the loop cycled
     back. Caught by the PRE-EXISTING sequential-retry test
     (`test_redis_unavailable_does_not_lose_the_event_or_touch_the_db_
     write`, `attempt_count` came back 3, not the expected 1) - not by
     the new concurrency test, which passed even on the buggy first
     version. The clearest instance yet of this entry's own point:
     sequential coverage alone doesn't catch a concurrency bug, but it
     can catch a concurrency FIX that broke the sequential case, if that
     coverage already existed. A new concurrent test
     (`test_two_concurrent_dispatchers_claim_each_event_exactly_once`)
     proves the actual property this entry is about, against real
     Postgres.
  5. **`_handle_graph_failure`'s own retry-budget ceiling
     (`workflow_runs.retry_count` vs. `_RETRY_BUDGET_CEILING`)** - found
     by CodeRabbit review, RESOLVED same pass. Worse than a plain lost
     update: `increment_retry_count` was `session.get()` + a Python-level
     `run.retry_count += by` + `flush()` - not atomic at either the
     ceiling CHECK (two concurrent `_handle_graph_failure` calls, e.g.
     two RQ workers both handling a failure for the same run, can both
     read the same stale `retry_count`, both see budget available via
     `classify_task_failure`, and both proceed) or the increment ITSELF
     (a plain ORM attribute mutation, the identical "lost update" shape
     `bump_state_version` had before its own `FOR UPDATE` fix - see that
     entry above). Fixed with `try_reserve_retry_budget`: one atomic
     `UPDATE ... SET retry_count = retry_count + :cost WHERE id = :id
     AND retry_count + :cost <= :ceiling RETURNING id` - the check and
     the reservation happen in the same round-trip, so a concurrent
     second caller's own UPDATE re-evaluates the WHERE clause against the
     row's real, current value, not a stale read; `_handle_graph_failure`
     treats a failed reservation (no row returned) as "budget exhausted
     or already spent by a concurrent caller" and does not schedule a
     retry, rather than trusting the earlier, advisory `classify_task_
     failure` check alone. `increment_retry_count` removed entirely (one
     real call site, no other callers, no test referenced the method
     name itself - only the `retry_count` outcome, which the replacement
     still produces correctly).
     **Worth stating plainly, not just filed as "a sixth instance":**
     this bug was written into `_handle_graph_failure` as part of major
     finding #17's own retry-mechanism build (B16 Group 4) - which
     landed AFTER Groups 1-3 (instances #1/#2/#3 above), the same three
     that got this pattern named in the first place. The pattern was
     already on record, in this same file, before the code that became
     its fifth instance was written - naming a pattern doesn't inoculate
     new code against it, it only makes the new code recognizable as an
     instance once someone actually looks (here, CodeRabbit, not this
     project's own review of its own Group 4 work). The same lesson the
     environment-traps entry already drew from its own third test-
     teardown incident: "the discipline being written down once is not
     the same as it being followed automatically in a later,
     unrelated-feeling moment." A named standing pattern is a detection
     aid for review, not a guardrail that prevents the shape from
     recurring in code written after the name existed.
  6. **`workflow_runs` status transitions, B17 - open, not resolved, and
     a different shape than instances 1-5:** those five are each "one
     call site found the naive shape, fixed it." This one is sibling
     drift WITH the fix already present in the same file:
     `mark_failed_if_not_terminal` (this session) and
     `try_reserve_retry_budget` (`568755e`) both already use the atomic
     `UPDATE ... WHERE <guard> RETURNING id` shape correctly - but
     `cancel_workflow_run` and the shared `run_workflow`/`resume_
     workflow`/`retry_workflow_run` exception handler (three more call
     sites on the same table) still read-then-write. Two correct
     instances did not make the other three notice. See the dedicated
     entry below for both call sites and why this was reported, not
     fixed, this pass.
- **Why this is a genuine blind spot, not six unlucky misses:** every
  one of these sites already had real test coverage before this pattern
  was named - `test_duplicate_idempotency_key_same_body_replays_response`
  and its siblings, the knowledge-import retry-resume tests - all proving
  the SAME property (a sequential retry converges correctly) without ever
  once proving the DIFFERENT property (two requests genuinely overlapping
  don't corrupt state). A green test suite said nothing about this,
  systematically, for every select-then-insert site in this codebase
  until the load tests this session started actually generating real
  concurrent traffic against them. Worth naming as a design habit to
  break going forward: a retry test and a concurrency test are not the
  same test, and passing the first has never implied passing the second
  here.
- **Status:** 6 of 7 resolved (#1, #2, #3 - B16 Groups; #3 additionally
  surfaced and closed a second, deeper race beyond its original scope;
  #4 - outbox atomicity, post-B16; #5 - retry-budget reservation, same
  session, caught by CodeRabbit review). #6 (`workflow_runs` sibling
  drift) is open by deliberate decision, not oversight - out of B17's
  own scope, named and audited rather than fixed. This entry itself
  closes no code.
- **Test:** each resolved instance has its own real concurrent test
  (`asyncio.gather` against the real ASGI app/service layer, or - for
  #5, a single-caller correctness/scoping test at the repository level;
  a true two-worker race test for the atomic reservation itself wasn't
  written this pass, named honestly rather than claimed) - this entry
  is the pattern note, matching every other standing-pattern entry's own
  convention in this file.

## CodeRabbit review findings, this session - five real bugs, all fixed and verified

Six items came back from a CodeRabbit pass over this session's own work
(the retry-budget race is recorded separately above, as the sixth
instance of the select-then-act standing pattern - not repeated here).
Each was verified against the real, current code before being trusted,
not fixed on CodeRabbit's own say-so alone - two of the six turned out
to be pre-existing self-contradictions in this file's own earlier
edits, not code bugs.

- **`outbox_dispatcher.py`'s `build_dispatch` call sat outside its own
  `try` block.** Every route builder (`_workflow_run_created_dispatch`
  etc.) does a raw `event.payload["..."]` lookup, which raises a bare
  `KeyError` on a malformed payload - uncaught at that position, since
  the `except Exception` guard starts AFTER it. That `KeyError` would
  have escaped `_dispatch_claimed_event` entirely, aborting `_dispatch_
  once`'s own `candidate_ids` loop (every later event in that poll cycle
  never attempted) and propagating into `_run_forever`, which has no
  `try/except` of its own around `_dispatch_once()` - a single malformed
  event would have taken the whole dispatcher process down. Worse: since
  the exception fires before `mark_dispatched`/`mark_retry` ever run,
  the event's own `attempt_count` never increments, so `get_pending`
  would select it again on every future poll - not a one-time crash, a
  permanent one. Fixed: `build_dispatch(event)` moved inside the `try`.
- **`_facts_from_pricing` offered evidence with no `verified`/
  `validity_status` re-check**, unlike `_prepare_evidence_facts` (every
  OTHER evidence path in this file), whose own docstring explains why
  the re-check exists: evidence can expire between materialization and
  a later drafting run. A pricing evidence link invalidated after
  `_ground_pricing_finding` materialized it would still have been
  offered as citable. Fixed with the identical check. New test:
  `tests/integration/test_proposal_service.py::
  test_facts_from_pricing_excludes_a_resolved_findings_evidence_once_
  it_goes_invalid`.
- **`requirement_service.patch_submission_item`'s own `materially_
  changed` computation didn't honor its own `fields` construction's
  meaning of `matched_document_id=None`** ("don't touch this field," not
  "clear it" - `fields` only ever includes the key when not `None`). A
  status-only PATCH on an item that already had a real `matched_
  document_id` set read as having also changed the document, bumping
  `tenders.state_version` for a field nothing actually wrote - the exact
  no-op case the function's own comment says this gate exists to
  exclude. Fixed: the comparison now only counts as a change when
  `matched_document_id` is not `None` AND differs from the current
  value. New test: `tests/integration/test_requirements_api.py::
  test_patch_submission_item_repeating_status_does_not_bump_state_
  version`. Noted, not built: this function still has no way to
  explicitly CLEAR an existing `matched_document_id` (would need a real
  sentinel value distinct from "omitted" - `None` is already spoken for)
  - real, separate scope if clearing is ever needed.
- **`full-flow.spec.ts`'s own red-team-finding poll compared `td.
  textContent()` directly, untrimmed**, against a literal category
  string - real table markup can carry surrounding whitespace, and the
  very next line already calls `.trim()` on a sibling cell, so the
  omission here was inconsistent with the author's own adjacent code,
  not a deliberate choice. Would have failed by exhausting the full
  300s poll budget and reporting a misleading "waiting for a finding"
  message on a row that was actually already there. Fixed: `.trim()`
  added before the comparison.
- **`test_schema_diff.py`'s own constraint comparison only checked
  `(table, type, name)`, never the actual definition** - a same-named
  constraint or partial unique index with different indexed columns, a
  different `WHERE` predicate, or a different allowed-values list would
  pass silently, exactly the shape of drift this test's own docstring
  says it exists to catch (and the shape this session's own `ck_
  approval_gate_type`/`ck_proposal_claim_kind` widenings are). Fixed by
  adding `pg_get_constraintdef(con.oid)`/`indexdef` to the compared
  tuple. **Running the strengthened comparison for real immediately
  surfaced a genuine, pre-existing, whole-schema difference** - see the
  dedicated entry immediately below - normalized out of the comparison
  (it isn't a real invariant difference) rather than either silently
  ignored or left failing the test on ~32 constraints that were never
  actually wrong. (Item 40 later fixed the models and removed the
  normalization; the test now compares definitions exactly.)
- **`docs/eval/b16-scorecard.md` carried two real self-contradictions**,
  both from this session's own earlier edits, not new code bugs: (1) the
  reliability-table tally said "9 of 9 fixed or verified, 0 deferred" in
  the same paragraph that names item #3 as still deferred by deliberate
  choice - corrected to "8 of 9... 1 deferred" in both places it was
  stated. (2) The §5 punch-list's own "Dead config: four instances"
  bullet still said `MAX_TENDER_COST_USD` was deleted and `estimated_
  cost_usd` was a column nothing writes - both true when that bullet was
  first written, both superseded later the SAME session once I5's own
  gate was built - the bullet was never updated when that happened.
  Corrected to note the later revival explicitly, plus one narrower stale
  clause elsewhere (§4a's own live-run report correctly recorded `0/165
  populated` as a fact about that specific past run, but its own trailing
  "stays unmeasured regardless of how many more runs happen" clause was
  a forward-looking claim invalidated by the same later fix).

## Whole-schema VARCHAR/TEXT mismatch between ORM models and migrations - real, pre-existing, found by strengthening test_schema_diff.py - RESOLVED by item 40

- **Discovered:** immediately upon running `test_schema_diff.py`'s own
  strengthened constraint-definition comparison (see the CodeRabbit
  entry above) for the first time - every single string-valued CHECK
  constraint in the entire schema (32 of them, every table with a
  status/type/category-style column) came back as a "difference"
  between the `create_all()` build and the real `alembic upgrade head`
  build.
- **Root cause, traced not guessed:** every migration file declares
  these columns via `sa.Text()` (or an equivalent that Postgres reports
  as `text`); every ORM model declares the same columns with a bare
  `Mapped[str]`/`mapped_column(nullable=False)` and no explicit type,
  which SQLAlchemy 2's own default Python-type-to-SQL-type map renders
  as `VARCHAR` (`character varying`, unbounded) instead. Postgres then
  renders the IDENTICAL logical CHECK constraint differently depending
  on which one the column actually is - `status = ANY(ARRAY[...text])`
  for a `TEXT` column vs. `(status)::text = ANY((ARRAY[...varchar])::
  text[])` for a `VARCHAR` one - a real difference in the constraint's
  own SQL text, but not in what it actually enforces: unbounded
  `VARCHAR` and `TEXT` are functionally identical in Postgres for every
  purpose that matters here (no length limit either way, identical
  comparison/indexing/storage behavior).
- **Why this is real and pre-existing, not introduced this session, and
  not fixed here:** every migration file from `001_initial` onward
  already has this shape - it did not start with `claim_kind`/
  `cost_budget`'s own new constraints, it was simply invisible to this
  test until the definition was actually compared, because a 3-tuple
  name-only comparison (this test's own shape before today) cannot see
  a same-named constraint's differing definition at all. Fixing it for
  real would mean picking ONE side and changing either all ~30 affected
  ORM model field declarations (adding an explicit `Text` type
  annotation/`mapped_column(Text)` to every enum-like string column) or
  all the migration files (switching them to plain `String`/`VARCHAR`
  to match the models) - a real, mechanical, but genuinely large and
  out-of-scope change for a CodeRabbit-triage pass, with its own real
  risk (touching every migration file's own historical DDL, or every
  model's column declarations, for a change with zero behavioral
  effect).
- **What was actually done instead:** `test_schema_diff.py`'s own
  comparison normalizes away this specific `VARCHAR`-vs-`TEXT` rendering
  difference (`_normalize_definition`) before comparing, so the test
  stays focused on genuine definitional drift (different allowed
  values, different predicates, different indexed columns) - its own
  actual, stated purpose - rather than either going permanently red on
  ~32 constraints that were never really wrong, or silently narrowing
  its own scope back to names-only and losing the real detection power
  this pass just added.
- **Status:** resolved by plan item 40 (see the next entry). The model
  side was changed, as this entry recommended: one
  `type_annotation_map = {str: Text}` on the models' `Base` makes every
  bare `Mapped[str]` column TEXT, as the migrations declare it. No model
  column names a length and no migration uses VARCHAR, so nothing meant
  to stay VARCHAR(n) changed and no migration was needed. The
  `_normalize_definition` allowance was removed from the test.
- **Test:** `test_schema_diff.py` compares column types and constraint
  definitions exactly; a VARCHAR column on either side now fails it.

## [Item 40] The models now build the migrated schema; the schema diff compares indexes, types, nullability and defaults - RESOLVED

- **The gap:** every integration test runs against the schema
  `Base.metadata.create_all()` builds from the models, while every real
  database is built by `alembic upgrade head`. `test_schema_diff.py`
  compared only constraints and unique indexes between the two, so a
  difference in a plain index, a column type, nullability or a server
  default passed it unseen.
- **What the test compares now (read from the Postgres catalog of both
  builds, nothing normalized):** every primary key, unique, check and
  foreign-key constraint with its definition; every index, plain ones
  included, as its full `CREATE INDEX` definition (name, columns,
  uniqueness, method, operator class, partial predicate); every column's
  type as `format_type` reports it; its nullability from
  `pg_attribute.attnotnull` (Postgres 16 keeps NOT NULL there, not in
  `pg_constraint`); and its server default as `pg_get_expr` renders it.
  A failure names the table, column or index and shows both sides.
- **What the widened test found on main (observed, 2026-10-24, the same
  245 differences on three runs), every one a model-side gap, none in a
  migration:**
  - 178 from VARCHAR versus TEXT: 139 column types in 40 tables, plus the
    37 CHECK constraints and 2 partial-index predicates Postgres renders
    differently on a VARCHAR column (previous entry).
  - 58 server defaults the migrations declare and the models did not.
    Each model already had a Python-side default with the same value, so
    ORM inserts were unaffected; a raw SQL insert in a test would have
    failed with a NOT NULL error.
  - 9 indexes the migrations create and the models did not declare:
    `idx_document_chunks_embedding_hnsw` (pgvector HNSW),
    `idx_document_chunks_fts` (GIN full text), and seven plain btree
    indexes (`idx_agent_traces_tender_time`,
    `idx_document_chunks_document`, `idx_employees_bid_pool`,
    `idx_graph_projection_latest`, `idx_knowledge_items_org_type`,
    `idx_requirements_tender_category`,
    `idx_tender_knowledge_selected`). The tests never ran with the
    production vector and full-text indexes.
  - No nullability difference and nothing present only in the models.
- **Fixed, model side only, no migration:** the Base's type map (TEXT);
  the 58 server defaults, each copied from its migration's expression,
  with the Python default kept so the ORM still sends the value instead
  of reading it back after every insert; the 9 indexes, with the same
  columns, order, method and operator class as their migrations.
- **The two defaults per column:** each of those 58 columns now states
  its default twice. `tests/unit/test_model_default_parity.py` checks
  that every column with a scalar Python default (str, bool, int) and a
  server default has the same value in both: 44 pairs. The 14 JSONB
  columns whose Python default is a callable (`dict`, `list`) are
  outside its scope; their server defaults are still compared with the
  migrations by the schema diff. No column needed an exception.
- **Mutation checks (observed):** with the drift fixed and no filter,
  each of these made the widened test fail with the change named, and
  each file was restored and matched by SHA-256: the `replaced_by_id`
  index removed (`index refresh_tokens
  ix_refresh_tokens_replaced_by_id`, models `<absent>`); a type
  (`tenders.state_version` integer against bigint, which also changes
  how its default renders, so two lines); a nullability
  (`users.is_active` NULL against NOT NULL); a server default
  (`refresh_tokens.issued_at` `<none>` against `now()`). The parity test
  failed on a changed str, bool and int Python default, one line each.
- **A second tool agrees:** `alembic check` against a freshly migrated
  test database reports "No new upgrade operations detected." Against
  main's models it reported 139 type changes and 9 missing indexes, so
  it sees two of the three kinds independently; it does not compare
  server defaults, since `env.py` does not set `compare_server_default`.
- **Found on the way, local only:** the local `tenderos_test` database
  had been built by an earlier Alembic round trip (it held
  `alembic_version` at `021_refresh_tokens`). `create_all()` skips
  tables that already exist, so local suite runs had been running
  against the migrated schema, not the models' one; CI's database is
  fresh, so CI was not affected. For this item each suite run started
  from an emptied test database, and the catalog was checked after each
  run to confirm which schema it had. Same family as the "local
  dev/test environment state" standing pattern above.
  **Resolved by plan item 42 (2026-11-03):** every pytest session now
  drops the test database's `public` schema and builds it from the
  models (see the item 42 entry below). **Correction:** "CI was not
  affected" was wrong. CI's test job runs `scripts/verify_migrations.py`
  (upgrade, downgrade, upgrade) on the same `tenderos_test` service
  database just before `pytest` (`ci.yml`, both steps source
  `scripts/test-backend.env`; the order seen in run 41613530333's log),
  so CI's suite also ran on the migrated schema. Since item 40 the two
  builds match (the schema diff shows nothing), so the difference
  mattered only before it.
- **Suite time (observed, each on a test database built by
  `create_all()`):** main 849 passed, 5 skipped, 268.0 s; the final
  tree 851 passed, 5 skipped, 271.6 s (the two new tests are the parity
  test's). One run each, so the 3.6 s difference is within run-to-run
  noise; the test database now maintains the HNSW and GIN indexes on
  every chunk insert without a visible cost at this data size.
- **Still open, recorded as plan item 41:** with the HNSW index in
  place, the organization-filtered nearest-neighbour query
  (`DocumentRepository.search_chunks_vector_for_org`) can return fewer
  rows than its LIMIT, because pgvector applies the filter after the
  index scan. Observed on a scratch database (10,000 chunks in one
  organization, 100 in another): 1 row of 20 with pgvector's defaults
  (`hnsw.ef_search` 40, `hnsw.iterative_scan` off), 20 of 20 with
  `hnsw.iterative_scan = relaxed_order`. Nothing in the code sets either
  setting. On the small test tables the planner does not use the index,
  so no test shows it.
  **Resolved by plan item 41 (2026-11-03)** for searches within
  `hnsw.max_scan_tuples`; see the item 41 entry below.
- **Not compared: extensions.** The migrations create two,
  `pgcrypto` (`001_initial.py:22`) and `vector` (`003_documents.py:27`);
  the test creates both on the models' side itself and compares neither
  their presence nor their versions (pgcrypto 1.3, vector 0.8.7 on the
  migrated test database). Searched and found none of the other kinds
  the test also does not compare: triggers, functions, views,
  materialized views, sequences, custom types or enums, and table or
  column comments (none in any migration's code, and none in a migrated
  test database's catalog, 2026-10-25).
- **Tests:** `tests/integration/test_schema_diff.py`,
  `tests/unit/test_model_default_parity.py`.

## [Item 42, 2026-11-03] The test database is built from the models every session - RESOLVED

- **The gap:** `create_all()` skips tables that already exist, so a test
  database left behind by an Alembic run kept its migrated tables and
  the suite tested those, not the models'. Found locally in item 40; CI
  had it too, since its test job runs the migration round trip on the
  same database just before `pytest` (see item 40's entry).
- **Now:** a session fixture in `tests/conftest.py`, requested by the
  integration and route conftests, runs `rebuild_from_models`
  (`tests/models_schema.py`) once per pytest session, in one
  transaction: the guard, `DROP SCHEMA public CASCADE`, `CREATE SCHEMA
  public`, `CREATE EXTENSION pgcrypto`, `CREATE EXTENSION vector`, then
  `create_all()`. The schema diff's models side uses the same
  `build_from_models`.
- **The guard, on the connection that runs the DROP:**
  `current_database()` must end in `_test`, a rule fixed in the code and
  never read from the setting that chose the database; and the role must
  be a superuser (`vector` is not a trusted extension, so only a
  superuser can create it again). Either refusal raises before anything
  is dropped. CI's test database is `tenderos_test` (`ci.yml`), which
  passes.
- **Observed (local, 2026-11-03):** after an Alembic round trip and a
  planted `tenders.item42_marker` column, the suite passed (856 passed,
  5 skipped) and the catalog showed no `alembic_version`, no marker
  column, new OIDs for LangGraph's four checkpoint tables (recreated on
  first use by `get_checkpointer()`), and 0 differences against a fresh
  `build_from_models()` scratch database. With the drop removed, the
  same sequence still passed its tests while the catalog kept
  `alembic_version` and the marker (3 differences). Against real
  Postgres, a database named `item42_guard_scratch` and a non-superuser
  role on `item42_guard_test` were each refused, their tables intact.
- **Cost (observed):** the rebuild takes 0.56 to 0.69 s (5 runs). Suite
  times: main 289.9 s; this change 280.9 and 283.7 s back to back; the
  difference is within run-to-run noise.
- **Still open:** `_clean_tables` still opens its own connection from
  the setting for its row deletes (the B13-prep guard entry's residual).
  The recreated `public` schema is owned by the test role with no grants
  to other roles; the suite uses only that role.
- **Tests:** `tests/unit/test_models_schema_guard.py` (fails with the
  guard trusting the setting, the superuser check removed, or the guard
  call removed).

## [Item 41, 2026-11-03] Filtered vector search returns its LIMIT - RESOLVED up to a scan cap, which is accepted

- **The gap:** `DocumentRepository.search_chunks_vector_for_org` orders
  by `embedding <=> :query` over one HNSW index shared by every
  organization, and pgvector applies the organization filter after the
  index scan. With the defaults the scan yields `hnsw.ef_search` (40)
  candidates from any organization, so a small organization in a large
  table got few or none of its own chunks (item 40: 1 row of 20).
- **Now:** the method runs `SET LOCAL hnsw.iterative_scan =
  relaxed_order` on its own session just before its SELECT, so the scan
  continues until LIMIT rows pass the filter. `SET LOCAL` ends with the
  transaction, so it cannot leak to a later query on a pooled
  connection. Relaxed order may return rows slightly out of distance
  order; the only caller fuses ranks (RRF) and reranks, so no consumer
  needs strict order (read in code). When fewer rows than the LIMIT come
  back, one INFO line records the organization id and both counts, never
  the query.
- **Observed (2026-11-03, test database, one rolled-back transaction,
  sequential scans off, plan checked by EXPLAIN to be the HNSW index
  scan):** 2,000 chunks in one organization and 30 in another: 20 of 20
  rows, all the small organization's; with 5 chunks: 5 of 5, and the
  shortfall logged. Without the `SET LOCAL`: 0 of 20 and 0 of 5. With
  the index dropped in the transaction, both tests fail on the plan, not
  on the count (the planner then reaches the chunks through the btree
  indexes and a sort).
- **Accepted, not fixed: the scan cap.** An iterative scan stops at
  `hnsw.max_scan_tuples`, 20,000 by default (pgvector README, read in
  docs, not observed), and returns what it found with no error. So a
  very small organization in a very large table can still get few or no
  rows: roughly 20,000 times its share of all chunks. The shortfall log
  line shows when that happens.
- **Design for when it matters (not built):** search a small
  organization's own chunks exactly, without the index. Count the
  organization's chunks first; below a threshold, run the same query
  with the index scan turned off for that transaction (an exact scan of
  only its rows, through the documents filter), which is cheap because
  the organization is small; above it, keep the iterative index scan. The
  threshold comes from measuring both paths, not chosen in advance.
- **Tests:** `tests/integration/test_vector_search_filter.py` (fails
  without the `SET LOCAL`, without the log line, and with the index
  gone).
- **Test made deterministic (2026-11-03, item 43's branch):** with only
  sequential scans off, the btree-and-sort path and the HNSW scan cost
  about the same on the test's data (observed: 498.81 to 504.40 against
  454.98 to 567.43), so the planner picked either and the plan check
  failed 3 of 3 times in one session after passing in others. The test
  now turns sorts off as well; 5 of 5 passed after, and it still fails
  without the `SET LOCAL` (0 of 20, 0 of 5) and with the index dropped (on
  the plan).

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
  (the `except GraphStaleError` in `readiness_service.py`, turning it into a real
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
- **Resolved in B16.** `configure_logging()` (`core/log_redaction.py`)
  calls `logging.basicConfig(level=logging.INFO, ...)` at the same three
  real entrypoints this entry names, replacing each site's prior
  `install_log_redaction()` call rather than adding a second call next to
  it - see the standing-pattern entry below for why splitting the two
  would have silently reopened B15's own redaction fix. Verified directly,
  not assumed: a real `.info()` call reaches a real `StreamHandler` at
  root level `INFO`, confirmed via direct process-level check, not a
  Docker rebuild (this is process-internal configuration, not
  networking-dependent, so the live-Docker-verification standard this
  file holds elsewhere doesn't apply the same way here). Full suite
  510/510, `uv run mypy` 59/12/239 unchanged, both after this change. The
  smoke-level test this entry's own "Test" line asked for is still not
  built - a real gap, left open deliberately rather than silently
  dropped once the fix itself landed.

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
- **BUILT in B16 - not at the hook this entry named, and worth recording
  why.** The real instrument lives at `AgentTrace.input_refs`/
  `output_refs` (already-existing, already-declared, never-populated
  columns found during the same investigation), populated directly at
  `propose.py`/`draft_section.py`'s own trace-write call sites, not by
  extending `_prepare_evidence_facts` as this entry originally suggested.
  `_prepare_evidence_facts` was checked directly during B16 and found to
  do exactly what its own docstring already claimed - the suggested hook
  wasn't wrong, it just turned out a cheaper, more directly-fitting
  mechanism already existed one layer over. See
  `docs/eval/citation-correctness-investigation.md` for what the real
  instrument found: 0/27 grounded, traced to zero candidates returned by
  retrieval on all 87 real compliance evaluations, not a drafting-side
  defect at all.

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
- **RESOLVED in B16 - pruned, not merely prunable.** `scripts/
  prune_checkpoints.py` is both the one-time backfill and the ongoing
  mechanism (one idempotent script, re-running it later is always a safe
  no-op for anything already pruned) - but its first version, written
  while Docker was unavailable, was reported here as "resolved" before it
  had actually been run even once. Corrected on direct question: writing
  the mechanism and running it are different claims. What actually
  happened, in order:
  1. First real dry-run (once Docker was back) hit a genuine bug: two
     separate `asyncio.run()` calls broke the async Postgres engine on
     Windows (`RuntimeError: Event loop is closed`). Fixed to one event
     loop for the whole script - caught before anything destructive ran,
     which is the entire reason dry-run-by-default existed.
  2. The fixed dry-run then surfaced something bigger than a bug: only
     14 `workflow_runs` rows exist at all in the live database, against
     240 distinct checkpoint threads - the `workflow_runs.status`-only
     join this script originally relied on could reach exactly 1 thread.
     **226 of 240 threads have no `workflow_runs` row whatsoever**, not
     merely a non-terminal one - matching this entry's own original "226"
     count almost exactly, strongly implicating the documented test-
     teardown trap (this file's environment-traps entry, incidents 3/4/7):
     `workflow_runs` is `Base.metadata`-managed and gets wiped when
     `pytest` runs against the live DSN; LangGraph's own checkpoint tables
     are not, and survive. A run of the original script would have pruned
     ~55 of ~13,789 rows (0.4%) and left the actual problem untouched
     while reporting success.
  3. Extended to a second, independent category - a thread with no
     `workflow_runs` row is at least as safe to prune as a confirmed-
     terminal one (no owning record whose status could still be
     non-terminal; `resume_workflow` requires that row to exist at all) -
     guarded so the trap's own mechanism isn't load-bearing: only prunes
     an orphaned thread whose newest checkpoint predates the OLDEST
     surviving `workflow_runs` row. Anything genuinely still in flight
     would be more recent than that cutoff by construction; anything
     actually orphaned by a wipe is necessarily older than everything the
     wipe didn't reach. `checkpoint_id` has no real timestamp column, but
     is UUIDv6 (confirmed 100% across this database, not assumed) -
     LangGraph uses v6 specifically because it sorts chronologically as a
     string, and its embedded 60-bit Gregorian-epoch timestamp is decoded
     directly (Python's stdlib `uuid.UUID.time` assumes v1's different
     field order and would return the wrong value, so this reassembles
     the bits itself).
  4. Dry-run with the guard reported 227 prunable threads (1 terminal +
     226 orphaned-and-older-than-the-cutoff) and 12,747 rows - within 50
     rows of this entry's own original ~12,700 estimate, a strong
     cross-check that the guard was catching the right population before
     anything was deleted. Executed for real against the live dev
     database. **Before → after, verified directly:** `checkpoints`
     1968→171, `checkpoint_blobs` 2374→176, `checkpoint_writes`
     9447→695, distinct threads 240→13. Confirmed after deletion, not
     assumed: all 13 remaining threads correspond exactly to the 13
     `workflow_runs` rows still in a real non-terminal state
     (`waiting_for_human`), each with its checkpoints fully intact (5-23
     rows each, zero touched).
- **No in-app periodic scheduling exists to wire this into automatically**
  - this project's locked stack (Redis + RQ) has no cron/scheduler
  component, and adding one wasn't judged worth it for a single
  maintenance script. Run by hand, or by an external cron outside this
  codebase, periodically - nothing prevents the same ~13,000-row growth
  recurring between runs.
- **Test:** none - a destructive-by-design operational script, not
  application behavior; the dry-run default and the before/after
  verification above are its safety net, not a pytest regression.

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
  **Partly resolved by plan item 42 (2026-11-03):** the guard is now
  `check_safe_to_drop` in `tests/models_schema.py`, run on the very
  connection that then drops and rebuilds the schema, in one
  transaction; the separate guard fixtures are gone. `_clean_tables`
  still opens its own connection from the same setting for its row
  deletes, so this entry's residual remains for those deletes only.
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
- **Resolved by plan item 42 (2026-11-03):** one copy, in
  `tests/models_schema.py`, used by both conftests through one session
  fixture in `tests/conftest.py`. No `tests/__init__.py` or `pythonpath`
  change was needed: `tests/` is already on `sys.path` because the root
  conftest lives there (pytest's prepend import mode); the earlier worry
  was about `from tests._x import y`, which needs the repo root instead.
  Observed: `tests/conftest.py`, `test_schema_diff.py` and
  `tests/unit/test_models_schema_guard.py` import it by its bare name.

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
  values (the category constants in `readiness_service.py`).
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
  **That pass happened in B16 - see
  `docs/eval/citation-correctness-investigation.md`.** A different
  instrument than this drawer answered it (`AgentTrace.input_refs`/
  `output_refs`, not `currently_in_scope_evidence`), but the real sample
  this entry's own note said had to come from a real E2E run - not
  hand-seeded fixture rows - is exactly what B16 produced: 0/27 grounded,
  traced to retrieval, not drafting.
- **Test:** none automated - this was a manual live-browser check, not a
  regression test. The hand-inserted `evidence_link`/`compliance_
  evidence` rows were left in the dev database (`tenderos`, not
  `tenderos_test`) as accumulated fixture state, matching this session's
  established practice for other "*-check" org data, not cleaned up.

## Standing pattern: API surface lagging the service/repository layer - four instances in B13, two more since ("capability exists, nothing calls it")

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
     it permanently blocked. Resolved-in-B16 - see the dedicated entry
     below; the repository method was, as predicted, still there and
     still correct, needing only a real caller.
  5. `target_updates` batch approval (`gate_target_service.
     apply_target_update(s)`) - B12 built and tested a real working
     backend path; B13's frontend, the first UI that could have called
     it, never did (see that entry's own dedicated writeup above).
     Widens the pattern one notch: instances 1-4 were about a route
     never being EXPOSED; this one is about a route that IS exposed and
     working, with no CALLER choosing to use it - same underlying shape
     (capability exists, nothing connects to it), one layer further from
     the code. Accepted asymmetry, not resolved.
  6. `orchestration/retry.py::classify_task_failure` (major finding #17,
     B16 Group 4) - built, tested (9 unit tests), ruff/mypy-clean, and
     called by nothing for one full pass, deliberately: the semantics
     decision and the wiring decision were split across two separate
     instructions. Resolved-in-B16 Group 1 - see that entry's own full
     writeup for the wiring, including a real architectural surprise
     (no per-node RQ job existed to "requeue" the way the instruction
     assumed) found only by checking the real code before building.
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
- **Status:** all four instances resolved. 1-3 in B13, 4 in B16.
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

## [B13, owner B16, RESOLVED B17] `settings.llm_provider` is dead configuration - 12 sites construct `GeminiClient` directly, bypassing the provider-neutral interface it exists to select

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
  construction site hardcodes `GeminiClient(...)` directly, in 12
  separate places across two apps (re-counted directly in B16, not
  re-derived from this entry's own original enumeration - it undercounted
  by one: `apps/worker/.../jobs/reanalysis.py`, a B14 job, was never
  listed here at all), each with its own local `_build_llm_client()`-
  shaped function: `orchestration/nodes/requirement_extraction.py`,
  `compliance.py`, `talent.py`, `commercial.py`, `proposal.py`,
  `redteam.py` (the 6 automatic LangGraph nodes), `apps/worker/.../jobs/
  knowledge_import.py`, `verification.py`, and `reanalysis.py` (3 worker
  jobs), plus `apps/api/.../routes/proposal.py`/`redteam.py`/
  `commercial.py` (3 route-level functions for the manual on-demand
  triggers). A2's own locked stack decision is "a provider-neutral
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
- **RESOLVED in B16 - deleted, not wired.** `llm_provider` removed from
  `core/config.py` (and its now-orphaned `LLM_PROVIDER=gemini` line from
  `.env.example`, replaced with a comment pointing here) rather than
  built out - CLAUDE.md's own instruction is to delete rather than leave
  unused config lying around claiming behaviour it doesn't have. **The
  real fix - wiring all 12 call sites to actually read a provider switch,
  plus the sized per-agent `FakeLLMClient` scripted responses this
  entry's own text already established are the actually-expensive part -
  remains exactly as undone as before, and remains its own explicitly
  named future item, not silently closed by this deletion.** Consequence
  section above still holds without qualification: there is still no way
  to run the system end-to-end against the real stack without spending
  real, billed LLM calls.
- **Test:** none at the time this entry was written - see the real
  regression coverage listed below, once B17 actually built the fix this
  entry's own text had already specified.

- **RESOLVED in B17 - the real fix, not another deletion.** Both
  previously-undone halves this entry named are now built: (1) all 12 call
  sites (re-confirmed by direct grep, not re-trusted from this entry's own
  count - B16's own correction from 11 to 12 was itself almost missed a
  second time, this pass initially found only 9 and had to go back for the
  3 route-level ones this entry's own text already named) now delegate to
  one real decision point, `agents/llm/factory.py::build_llm_client`, which
  reads a real, restored `settings.llm_provider: Literal["gemini", "fake"]
  = "gemini"` - each of the 12 modules keeps its OWN `_build_llm_client()`
  name unchanged (existing tests' `monkeypatch.setattr(module,
  "_build_llm_client", ...)` calls, 27 of them across 12 test files, still
  work exactly as before - verified by running the full suite, not
  assumed), but its body is now a one-line delegate instead of an
  independent `GeminiClient(...)` construction. (2) `FakeLLMClient` gained
  the opt-in `set_fallback(...)` mode this entry's own text already
  specified as "the actually-expensive part" - a default schema-valid
  response per `(agent_name, response_schema)` pair, used once that pair's
  exact-order queue (still the default, unchanged) is exhausted, which is
  what makes answering an UNKNOWN number of calls from a real E2E run
  possible at all, not just a flag flip.
- **The hard guard this entry's own "no way to run end-to-end without
  billed calls" framing implicitly needed, built alongside:**
  `core/config.py::_reject_fake_provider_in_production` - checked at
  import time, before any process using this Settings object can do
  anything, refuses to start if `LLM_PROVIDER=fake` and `APP_ENV=production`
  together. A public Phase 4 demo silently serving fabricated LLM output
  would be a materially worse failure than the dead-configuration gap this
  entry originally described.
- **Test:** `tests/unit/test_branch_gemini_api_keys.py` (updated to patch
  the one real `factory.GeminiClient` binding instead of three separate
  per-module ones, plus 2 new tests for the fake-provider switch itself),
  `tests/unit/test_fake_provider_guard.py` (6 tests, the hard guard
  directly), `tests/unit/test_fake_llm_client_fallback.py` (5 tests,
  `set_fallback`'s own contract including that strict-queue behavior is
  unchanged by default). Full suite green throughout
  (`uv run pytest`, `uv run mypy` unchanged at 59/12).
- **Standing note, for whoever leans on this harness next - Phase 2 names
  it as its own practical iteration loop:** the fake-client harness was
  wrong four times inside this one block, not once. (1) A static
  `resume_extraction` fallback gave 4 imported résumés the identical name,
  collapsed into 1 employee by real duplicate-adjudication logic. (2) The
  first attempted fix (a closure-captured counter) produced the identical
  result a second way, because RQ forks a process per job and each fork's
  own copy of the counter starts fresh. (3) A test written to prove the
  second fix asserted that stale last-known data should survive a stream
  error, when the real scenario (a 401 on the very first mint attempt)
  structurally never populates that data in the first place - caught only
  by actually running the test, not by writing it. (4) A static
  `proposal_drafting` fallback gave every section, including the one
  template `section_is_data_gated` deliberately excludes from grounding
  checks, the identical claim - collapsing a real E2E assertion the same
  way (1) collapsed real employee identities. Every one was found and
  fixed properly, by running the harness and reading the real consequence,
  not by inspection - which is the harness earning its place, not
  disqualifying it. But four for four inside one block is a real prior:
  the next time a fake-provider run comes back red, "is the harness wrong
  again" is a first-class hypothesis to check early, not the last thing
  reached for after the product code has already been re-read twice.
- **(5) Fifth harness error, B17 Phase 0 (2026-10-05):** the static
  `compliance_proposal` fallback returned `evidence_refs=[]` for every
  call, so no compliance decision ever cited an employee, no employee had
  any downstream edge in Neo4j (only `USED_IN`), and E2E step 14's
  assertion - an employee whose impact preview names a Requirement
  (`full-flow.spec.ts:1175-1178`) - was structurally unreachable under the
  fake provider. Found by reading the run's data, not the spec: the
  compliance call's own `agent_traces.input_refs` showed it HAD been
  offered valid employee candidates (`evidence_1`.., `[employee,
  validity=valid]`) and the fallback cited none; the tender held 1
  requirement and 9 `document_excerpt` evidence links, none employee-
  backed. Fixed in the harness only, no spec edit:
  `_compliance_proposal_fallback` now cites the first `[employee,
  validity=valid]` label its own prompt offered (exactly one, so the
  step-14 deselection leaves no alternate evidence), and
  `_proposal_drafting_fallback` adds a `compliance` claim citing the first
  offered evidence and requirement facts (its original uncited claim is
  kept first, for step 10). Tests render prompts through the production
  formatters (`tests/unit/test_fake_fallbacks.py`), so a prompt-format
  change fails there. **New guard against a sixth:**
  `scripts/check_fake_run_invariants.py` checks a fake run's data directly
  - a requirement, an affirmed decision with evidence, a grounded claim, a
  directed Employee-to-Requirement path in Neo4j - so a missing data shape
  surfaces as a named failed invariant instead of a late Playwright
  timeout. Five for five is now the prior.
- **(6) Sixth harness error, B17 Phase 0, found by a Phase 0 count guard
  the same day:** the static `requirement_extraction` fallback returned a
  single `technical` requirement, and role extraction runs only over
  `category == "staffing"` requirements
  (`talent_service.extract_and_persist_roles`, `:157`) - so every fake run
  had 0 roles (checked: all three fake E2E tenders of 2026-10-04/05, 0
  `role_requirements` rows each, and no `role_extraction` trace at all,
  since the model call is skipped). E2E step 7's candidate-selection loop
  therefore ran zero times in every earlier fake run and passed
  vacuously; the new `expect.poll(roleRows.count() > 0)` guard
  (`full-flow.spec.ts`, step 7) turned that into a named failure on its
  first run. The guard was kept as written. Harness fix: the extraction
  fallback also returns one `staffing` requirement, and the
  `role_extraction` fallback cites `req_1` (always present when the call
  is made - `talent_service` labels staffing requirements from 1 and skips
  the call when there are none), so the role gets a real SATISFIES edge.
  `scripts/check_fake_run_invariants.py` gained a `roles` invariant. Six
  for six.
- **(7) Seventh harness error, B17 Phase 0, exposed by the sixth fix:**
  E2E step 14 chose its deselection target as the FIRST employee whose
  impact preview listed a `Requirement` (`full-flow.spec.ts`, then
  `:1248`). Once roles existed, a role-filler qualified too, through
  `FILLS -> Role -> SATISFIES -> Requirement`, so which employee was chosen
  depended on table order. Run 4 happened to pick the evidence provider and
  passed; run 5 (2026-10-05, **fake**) picked the role-filler - checked in
  Neo4j: `9a2c8808` ("Fake Candidate 0e4d6fd7") had only `USED_IN` and
  `FILLS -> Role`, no `PROVIDES`, while the evidence came from a different
  employee - and the step's poll for `deselected_fact_in_use` timed out.
  **Observation, fake, not a defect:** D6's `removed_employee_committed`
  fired correctly for that role-filler (critical, unresolved, 05:10:39) -
  exactly D6's rule for an employee who is deselected but still `FILLS` a
  role. Fix (spec, approved as a diff): the target must list both
  `Requirement` AND `Compliance` (an evidence-bearing employee), with
  `expect(targetTitle).toBeDefined()` so the old `?? ""` fallback can no
  longer silently pick the first row. `check_fake_run_invariants.py` gained
  invariant 6, `evidence_bearing_employee` (via the same
  `graph_service.preview_impact` call the screen makes). Seven for seven;
  the full-spec audit for the same two shapes (order-dependent targets,
  passes at zero) is in the B17 Phase 0 report.

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
- **RESOLVED in B16.** `GET /tenders/{tender_id}/submission-items` and
  `PATCH /submission-items/{submission_item_id}` now exist
  (`routes/requirements.py`), backed by `requirement_service.
  patch_submission_item`, which finally gives `SubmissionRepository.
  update_submission_item` its first caller since B5. Same before/after
  audit-event shape as `patch_requirement`.
  - **RBAC: `review_requirements` is a reasoned reuse, not an inherited
    default.** Part H's matrix never named submission items (the same gap
    this entry itself already documents for the whole 17-screen list), so
    there was no existing row to inherit - checked and confirmed, not
    assumed. Reused `review_requirements` because a reviewer editing or
    confirming a requirement and a reviewer marking a submission item
    received are the same trust tier: both are "a human with review
    authority over this tender's extracted checklist confirms what's
    actually true." A dedicated RBAC row is the alternative if submission-
    item handling ever needs its own escalation path (e.g. an approver-
    only override once items feed a formal sign-off) - not needed now,
    but named here so the choice reads as deliberate if that day comes.
  - **`tenders.state_version` is bumped on a materially-changed patch -
    checked against Part D before deciding this, not assumed.** Part D1's
    node-label table lists exactly 11 types (Tender, Requirement,
    Evidence, Compliance, Employee, Certification, Project, Capability,
    Role, ProposalClaim, ProposalSection); `SubmissionItem` is not one of
    them, and no D2 relationship touches it either. **The bump is
    therefore NOT required by Part C's state-version invariant** - it is
    a deliberate choice purely for `readiness_reports`' own
    `UNIQUE(tender_id, tender_state_version)` freshness, so a stale report
    doesn't keep reporting an already-cleared blocker. If a future reader
    checks Part D, sees no `SubmissionItem` node, and reads the bump as a
    leftover mistake to "correct" away - it isn't one; this paragraph is
    the record of why it's there anyway.
  - **The write path was proven, not just built - both consumers of
    `submission_items.status`, checked in the same test pass.**
    `tests/integration/test_readiness_service.py::
    test_the_real_write_path_reaches_an_honest_unwaived_ready_true` goes
    through the real service call (not the bare repository method a prior
    test already exercised directly) and asserts two things together: an
    otherwise-clean tender reaches a genuine, unwaived `ready=True` with
    zero remaining blockers - the assertion this entry's own "final
    readiness is unreachable through the UI by construction" line could
    never make before now - and `redteam_checks.check_submission_checklist`
    (a second, independent consumer of the same `status` column, not
    something this fix was specifically built for) also clears in the
    same pass. Route-level coverage in `tests/integration/
    test_requirements_api.py` additionally proves cross-organization
    isolation - caught and fixed a real bug while building it: the route's
    first draft used `{item_id}` as its path parameter, which
    `dependencies.py`'s `_resolve_organization_id` already binds to
    `knowledge_import_items` (B6) - an unrelated resource. Renamed to
    `submission_item_id` with its own resolver branch before this ever
    shipped, not discovered by the isolation test after the fact.
- **Test:** `tests/integration/test_readiness_service.py::
  test_the_real_write_path_reaches_an_honest_unwaived_ready_true` (the one
  that matters most - see above), plus `tests/integration/
  test_requirements_api.py::test_list_submission_items`/
  `test_patch_submission_item_updates_status`/
  `test_submission_items_are_not_visible_across_organizations`.

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
- **Second instance (found 2026-10-24 on main's CI, fixed by the one-time
  spec edit on 2026-11-03):** step 4 clicked "Start processing" and
  waited for that button to be hidden before navigating to the
  requirements page, as proof that `POST /tenders/{id}/workflow-runs` had
  completed. Restyle batch 3 (`7982cf7`, 2026-10-11) gave the button a busy
  label ("Starting..."): while the request is in flight its accessible name
  changes, so `getByRole("button", { name: "Start processing" })` matches
  nothing and `toBeHidden()` passes 3 ms after the click (observed in the
  trace of run 40116721087). From then on the guard did nothing, and every
  run raced the navigation against the API's body read: in passing runs
  the navigation still cut the POST off, but after the body had been read,
  so the run was created and no access-log line was written (runs
  40114238925 and 40071971358; 40065648132 logged its POST normally). Main's
  runs for #43 (40116721087) and #44 (40163720306) lost the race: an
  unhandled `ClientDisconnect` at `idempotency.py`'s body read, no run, and
  the requirement gate never opened (7.5 min). Not caused by item 34 or
  item 40: no refresh, logout or 401 in either run.
  - **Reproduced deterministically:** with a temporary 3 s sleep before
    that body read (uncommitted, removed, checked by SHA-256), the
    unchanged spec failed the same way (9.2 min) and the edited spec
    passed (2.6 min with only this fix, 2.9 min with the whole edit).
  - **Fix:** the spec registers `page.waitForResponse` for that POST before
    the click and asserts a 2xx before it navigates: the server's answer,
    not a label. The same proof replaced the résumé upload's input
    re-enable wait. A sweep of every navigation after a request found no
    other call site resting on a label (the other busy labels are waited
    on by `toBeEnabled()` for the operation's end, or not at all).
  - **The disconnect is no longer an unhandled error:** the API now logs a
    client gone before its body was read as one INFO line with a 499
    (`client_disconnect.py`), from either body read.

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
- **Status:** resolved-in-B15 for option 3 itself (the token-in-URL
  exposure and the token-expiry-mid-stream trigger this entry named).
  `POST /workflow-runs/{run_id}/events/ticket` (`sse_tickets.py`,
  `routes/workflow.py::mint_workflow_events_ticket`) mints a Redis-backed,
  single-use ticket (`GETDEL` on consumption, so it cannot be replayed
  even within its own life), bound to the specific `(user_id, run_id)` it
  was issued for - `get_current_user_for_sse` now checks the ticket's
  bound `run_id` against `request.path_params["run_id"]`, so a ticket
  captured within its 30s window can only open the one stream it was
  minted for. `?access_token=` is gone entirely, not just supplemented -
  the header path (curl/Swagger) is unaffected.
  **RBAC for the mint endpoint, decided rather than reflexively copied:**
  reuses `view_workflow`, the same action `stream_workflow_events` itself
  already uses - minting answers the identical "can this user watch this
  run" question as opening the stream, not a new one, so a dedicated
  action would gate one predicate under two names.
  **The reconnect consequence this entry named turned out to matter more
  under a ticket, not less:** a single-use ticket means `EventSource`'s
  own automatic reconnect (which always replays the exact same URL after
  any drop) now fails on *every* reconnect, not just an occasional one
  past a 15-minute window. `useEventStream.ts` no longer relies on
  `EventSource`'s native retry for this at all - on a terminal failure
  (`readyState CLOSED`) it mints a fresh ticket itself and opens a new
  connection, splitting failure by cause: a 401 minting (the user's own
  session has expired) surfaces as `error` and stops; a transient mint
  failure gets a bounded retry with backoff.
  **A real bug in that reconnect path, found by CodeRabbit on a later
  pass and fixed:** the backoff described above only covered failures
  minting the ticket - a terminal `EventSource` failure AFTER a
  successful mint+open (the stream itself dying, not the mint call)
  reconnected via `openStream(0)` directly, with zero delay and no cap.
  If the stream kept failing the same way (not just an occasional drop),
  this hammered the mint endpoint in a tight loop. Fixed with a second,
  separate `streamFailures` counter and the identical exponential-backoff
  shape, reset only on a real `onopen` - so a connection that succeeds and
  later drops again backs off from zero, not from wherever the last
  failure streak left off.
  **The ignored-`error` gap this entry separately named is still open,
  unchanged by this fix, exactly as predicted:** `UploadAndProcessingStatus.
  tsx` still destructures only `{ data }` from this hook - a session
  ending in a hard 401 (not just a token aging past 15 minutes) still
  produces silent staleness, not a visible error. Confirmed as still a
  real, separate, small UI-polish fix, not something this change
  incidentally closed.
- **Test:** `tests/integration/test_workflow_api.py` - 4 new tests
  (`test_mint_events_ticket_rejects_a_caller_outside_the_organization`,
  `test_minted_ticket_resolves_to_the_real_user_and_is_single_use`,
  `test_ticket_minted_for_one_run_is_rejected_against_another`,
  `test_garbage_ticket_is_rejected`) plus the existing cross-org test
  rewritten to authenticate via the header instead of the now-removed
  query-param JWT. `apps/web/src/hooks/useEventStream.test.ts` (now 4
  tests, a fake `EventSource` standing in for jsdom's missing one):
  ticket-not-raw-token in the connection URL, a 401 mint failure surfacing
  as `error` with no retry, a terminal `EventSource` failure minting a
  fresh ticket and opening a second connection, and (added with the
  backoff fix above) fake-timer-driven proof that repeated terminal
  failures back off exponentially and reset after a real `onopen`.
  **One real testing constraint worth recording, not routed around
  silently:** a *successful* (200) stream connection cannot be asserted
  over real HTTP in this test suite - confirmed directly (not assumed)
  that `httpx.ASGITransport` hangs on any call to this route that gets
  past auth, regardless of status code, matching
  `test_workflow_events_stream.py`'s own documented structural limit for
  unbounded `StreamingResponse`s. Worked around by calling
  `sse_tickets.consume_sse_ticket` directly (the exact function the route
  itself calls) to prove a minted ticket resolves to the correct user,
  and proving single-use only via a second, rejected (fast-path) HTTP
  call - not a workaround that hides the gap, the same "bypass HTTP for
  the part ASGITransport structurally can't test" precedent that file's
  own docstring already established.

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
  projector.py`'s `project_tender`) is built *only* from `tender_knowledge_
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
- **B16 addendum - the identical structural gap reproduced live a second
  time, through the opposite symptom.** Root-causing a live E2E run's own
  step-14 failure (waiting up to 300s for requirement `EXP-002`'s
  `compliance_entries.human_approved` to flip from stale-`true` back to
  `false` after deselecting its backing knowledge item - the assertion
  never passed). Checked directly, not assumed: `worker` logs show
  `reanalyze_knowledge_selection_change` fired and completed twice for
  the same knowledge item (`26ddfff0-...`) - once for the initial select
  (`requirements=3`), once for the deselect (`requirements=3`, same
  count) - so this is not a dead job or a harness timing miss. Querying
  the live compliance entry directly: it still shows `fully_compliant`/
  `human_approved=true`, backed by exactly one `evidence_links` row,
  still pointing at the *same* deselected knowledge item. The
  reanalysis genuinely re-ran `evaluate_compliance_for_requirement` and
  re-found the identical evidence, because - exactly as this entry's own
  "why this is structural" paragraph above already established -
  compliance evidence retrieval was never gated by
  `tender_knowledge_selections` in the first place. Deselecting an item
  removes its `USED_IN` graph edge; it does not and cannot change what
  compliance evidence Postgres-side retrieval finds, so
  `persist_compliance_decision`'s own "only reset `human_approved` on a
  real material change" logic is *correctly* leaving it alone - nothing
  compliance-relevant changed. **The two symptoms are the same root
  cause, not two bugs:** the original finding is `preview_impact`
  under-reporting (an item that backs a real decision shows no impact
  until selected); this one is a reanalysis-cascade assertion
  over-assuming (selection state is expected to affect a decision it
  structurally cannot touch). `full-flow.spec.ts`'s own step-14
  "compliance reopens" assertion was therefore testing a behavior that
  cannot occur under this system's actual, deliberate design, not a
  flake - it failed this exact way every time it reached this point.
  **Resolved below, not left open.**

- **Resolution (B16): two live reproductions is a stronger case than the
  original "needs a design decision, no owner" status - the decision is
  now made, both alternatives ruled out for concrete, documented reasons,
  not just a preference.**
  - **Option A (gate compliance retrieval by selection) - disqualified,
    two independent reasons, either alone sufficient.** First: the
    automatic `compliance_analysis` workflow task runs in parallel with
    `talent_analysis`/`commercial_analysis`, *before* a human ever visits
    the knowledge-selection screen - confirmed directly from this same
    live run's own timestamps (`compliance_analysis` completed at
    `07:11:41`; the first `Confirm select` click happened a minute
    *later*, at `07:12:47`). Gating retrieval by selection would make
    every tender's first compliance pass find zero evidence for
    everything, by construction, every time - not a corner case, the
    normal path for every tender this project has ever processed.
    Second: it makes D6's own locked `deselected_fact_in_use`
    contradiction query permanently unreachable - that state
    (`USED_IN.selected=false` AND the fact still provides evidence)
    could never arise if retrieval were gated, silently retiring a
    required Build Guide feature.
  - **Option B (auto-create/reselect the `USED_IN` edge when compliance
    evidence materializes) - disqualified, a real self-defeating loop,
    traced at the code level, not assumed.** The write would go in
    `persist_compliance_decision`, right after `uow.compliance.
    link_evidence(...)` (`compliance_service.py`) - and
    `reanalyze_knowledge_selection_change` already calls exactly that
    function (via `evaluate_compliance_for_requirement`) on every
    selection change, deselections included. A human deselects an item;
    the reanalysis it triggers re-evaluates compliance; compliance
    re-materializes evidence from the same item; Option B's own new code
    would then immediately re-select it - undoing the human's action
    inside the very job that exists to process it. This session's own
    live run already demonstrated the re-materialization half of this
    chain happening for real; Option B would have closed the loop on it.
  - **Correction to this entry's own earlier framing of the B7 comment:**
    "`selected_only` is deliberately not used here" (`compliance_service.
    py:404-412`) is narrower than it reads - a build-sequencing decision
    made before `tender_knowledge_selections` existed at all (B7 predates
    Part C), not a permanent statement that compliance should ignore
    selection forever. What actually settles Option A regardless of that
    comment's original intent is D6 (Build Guide) requiring a *detector*
    for exactly this state, independent of why B7 originally didn't gate
    retrieval.
  - **What was built instead - the "option C, wiring version": run the
    already-existing, already-tested detector where it was structurally
    missing, rather than leave it running only during a full `run_red_
    team` pass.** `reanalyze_knowledge_selection_change`
    (`apps/worker/jobs/reanalysis.py`) now has a step (5): re-project
    (state_version can move during step 3's own reevaluation - see the
    note below) and run `redteam_checks.check_graph_contradictions`,
    persisting any drafts via a new public `redteam_service.
    persist_finding_draft` (promoted from the module-private
    `_persist_draft`, now genuinely shared by two real callers).
    Deliberately does NOT call `uow.redteam.auto_retire_undetected_
    findings` - that method's own docstring says retirement is only safe
    because `run_red_team`'s check list covers a tender's ENTIRE current
    state every pass; this job only ever checks the 4 graph-contradiction
    categories, so retiring against its own narrower fingerprint set
    would wrongly resolve every other open finding it never looked at.
  - **A second, real bug found and fixed while wiring this in, not
    assumed away:** the first working version re-used step (1)'s own
    projection (from the top of the job) for the new contradiction check
    at the bottom - but `persist_compliance_decision` (step 3) bumps
    `tenders.state_version` unconditionally (this file's own "Compliance's
    state_version bump is unconditional" entry), making that projection
    stale by the time step (5) ran. `find_contradictions` calls `check_
    freshness` first and raised `GraphStaleError`, which `check_graph_
    contradictions` swallows by design (the same tolerance `run_red_team`
    relies on for staleness it didn't cause) - here the staleness was
    self-inflicted, one step earlier in the SAME job, and the detector
    silently found nothing every time. Caught by a real integration test
    failing with an honest zero-findings result, not inferred - fixed by
    re-projecting immediately before step (5), which is also the more
    correct read of "is the fact still in use": AFTER reevaluation, not a
    stale pre-reevaluation snapshot.
  - **Test coverage, both levels, per the standing "capability exists,
    nothing exercises it" concern:** a new service-level integration test
    (`tests/integration/test_reanalysis_job.py::
    test_reanalyze_knowledge_selection_change_surfaces_deselected_fact_
    still_used_finding`) builds a real employee knowledge item with a
    real embedded company-knowledge chunk (via `knowledge_item_chunks`,
    this same session's own earlier B16 fix), selects then deselects it
    against a real compliance decision that cites it, runs the real job
    against real Postgres AND real Neo4j (no monkeypatching around the
    graph, unlike this file's other reanalysis tests, since the thing
    under test only means something against a real projected graph), and
    asserts both halves: the compliance entry does NOT reopen (status and
    `human_approved` unchanged) AND a `deselected_fact_in_use` finding
    exists, unresolved, correctly D7-severity-graded. `FakeLLMClient`
    throughout, zero LLM cost. `full-flow.spec.ts`'s own step-14
    assertion is corrected to match - it now polls the red-team screen
    for the same finding to surface, commented with why (citing D6
    directly) so a future reader doesn't "fix" it back to the old,
    impossible expectation.
  - **Status: resolved.** Both disqualified alternatives and the
    reasoning are recorded here so the next person to look at this
    doesn't re-propose Option A from scratch.
- **`preview_impact`'s own half of this cluster - RESOLVED, separately,
  post-B16 (scorecard scenarios 13/16).** The reanalysis/detection fix
  above closed the deselection-non-reopening symptom; `preview_impact`
  itself (`graph_projection/queries.py`) still had its own real gap -
  its `MATCH (fact)-[u:USED_IN]->(:Tender {id: $tenderId})` was a hard
  requirement, so a knowledge item that genuinely backs a real
  compliance decision (via `PROVIDES`, materialized independent of
  selection - same root fact as the entry above) but was never
  explicitly selected/deselected at all had no `USED_IN` edge and
  returned nothing. **Fix: the `USED_IN` match is now `OPTIONAL`, not
  required.** Traced, not assumed, before changing it: `fact` is already
  anchored to one specific node via `knowledge_item_id` (an indexed
  lookup, not a scan), and the `WHERE affected.tender_id = $tenderId`
  clause already does 100% of the cross-tenant safety work by filtering
  the DESTINATION node, independent of whether the source edge is
  required - loosening the source-side match introduces no leak.
  `currentlySelected` now reads `null` for an item with no `USED_IN`
  edge at all - a real, meaningful third state ("never selected for
  this tender"), not a defect; checked every consumer before assuming
  this was safe (`GraphImpactRow = dict[str, Any]`, no Pydantic
  constraint; the one real frontend consumer, `KnowledgeSelections.tsx`,
  renders `JSON.stringify(row)` with no field-specific assumption; no
  existing test asserted on this field's value). Two new tests
  (`tests/integration/test_graph_projection.py`): the positive case
  (a never-selected item with real compliance evidence now appears,
  `currentlySelected is None`), and a real cross-org observation, not
  only the traced reasoning - two separate orgs/tenants, each with their
  own never-selected employee (the exact new code path this fix opens),
  confirming `preview_impact` for org A's tender still returns nothing
  for org B's employee id. Options A (gate compliance retrieval by
  selection) and B (auto-create the `USED_IN` edge on evidence
  materialization) stay disqualified for the same reasons as the entry
  above - this fix touches neither compliance's write path (B's loop
  risk) nor retrieval gating (A's every-tender-breaks risk), it's a
  read-side change to `preview_impact` alone.

---

## [B16] `full-flow.spec.ts` step 7's own `waitForLoadState("networkidle")` does not actually wait for the fetch it's meant to confirm - found root-causing a live run's own "0 of 4 roles staffed" result

- **Discovered:** post-B16 backend-closure work, Group 1 (root-causing
  `select_candidate`). Traced click → network → route → service against
  this session's own preserved E2E trace (the run that reached step 14
  before failing there, meaning it DID pass through step 7). The DB
  showed all 16 `role_matches` for that run's tender `human_selected =
  false`, matching the already-documented golden-path symptom exactly -
  but tracing the actual trace file told a different story.
- **What the trace actually shows, precisely:** `role_matches` for all 4
  roles were real and complete by 07:07:04 UTC. The UI's own
  `GET /roles/{id}/matches` calls didn't fire until 07:12:07 - over 5
  minutes later, no race on data existing. All 4 returned `200`, with
  real, substantial response bodies (1334-1861 bytes, confirmed by
  reading the actual JSON out of the trace's own resource store, not
  just the status code) - the server did everything right. And yet the
  UI recorded "No candidates found for this role yet." for all 4 roles
  (the test's own `step-7-roles-with-no-candidate` attachment names all
  four), and **zero `POST /roles/{id}/select-candidate` requests exist
  anywhere in the trace** - the Select button was never clicked, because
  the loop's own `noCandidates` branch fired and skipped every role.
- **The mechanism:** click-to-check gaps of 10-40ms, all 4 roles
  processed in a ~185ms burst total (trace timestamps, not estimated).
  Nowhere near enough time for click → `handleSelectRole` → real fetch →
  response → `setMatches(result)` → React re-render to complete - the
  check was reading `matches.length === 0`, which is TRUE from the
  instant of the click (`setMatches([])` at the top of the handler) and
  stays true until the real response is applied. `page.
  waitForLoadState("networkidle")`, positioned between the click and the
  check specifically to "confirm this role's own fetch has landed" (this
  file's own prior comment, now known wrong), observes the BROWSER's
  network layer, not React's post-fetch render commit - it can resolve
  before `setMatches()` has ever run, especially late into a long test
  session with little other background traffic to make "idle" a
  meaningful signal.
- **Same class of gap as two entries already in this file:** "E2E
  harness bug: `page.reload()` does not wait for React's own
  data-fetching effects" and "premature navigation cancelled in-flight
  requests, twice" - a third instance of "a browser/network-level
  Playwright signal was trusted to mean 'the app has finished reacting
  to this,' and it doesn't."
- **Fixed:** replaced the click-then-`networkidle` sequence with
  `Promise.all([page.waitForResponse(...), click()])` (registering the
  response wait BEFORE the click, closing the window where the response
  could land before the wait starts - the same pattern this file's own
  `liveTaskListRefetch` already uses for step 11), plus a bounded,
  non-throwing poll (not `expect.poll()`, which throws on timeout and
  would wrongly fail a role that genuinely has zero candidates) for the
  candidate rows to actually render after the response is confirmed
  received, closing the remaining React-render-commit gap the network
  wait alone doesn't cover.
- **What this does NOT establish:** whether the golden-path bug below
  (silent non-persistence on a REAL Select click) is still real. This
  run's own click never happened at all - there was nothing for that bug
  to manifest in. See that entry's own new addendum for what a real,
  cheap check (against this same run's already-existing data, with the
  harness fix in place) found.
- **Status:** resolved. Real fix, not a wider timeout - the old code
  would have had the identical failure mode at 30s or 300s, since the
  problem was never insufficient TIME, it was `networkidle` never being
  the right signal to wait for in the first place.
- **Test:** the fix itself - `full-flow.spec.ts:704-767`. No dedicated
  regression test beyond the spec's own live run, matching this file's
  own convention for E2E-harness-only findings.

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
- **Status:** RESOLVED, post-B16 (see the third addendum below) -
  downgraded from "open, unowned" once the harness-race fix produced 2
  clean, real, DB-confirmed successes in 2 attempts with 0 failures.
  Root cause inferred (the same class of harness race fixed elsewhere in
  this file), not independently proven by tracing `set_candidate_
  selection` itself line-by-line - see the third addendum for the exact
  reasoning and its own stated limits. Originally: open, owner named
  honestly, not force-fit - no block in Part J's current B12-B17 list has
  a stop-gate that touches `select_candidate`/`TeamWorkspace.tsx`
  specifically, so this was left unassigned rather than attached to a
  block whose own gate doesn't actually depend on it - matching this
  file's own "a home that isn't real is worse than an honest
  'unassigned'" standard (the no-clean-owning-block triage entry,
  pre-B12).
- **Test:** none - the bug itself has none (that's the finding). A real
  fix should also tighten `full-flow.spec.ts`'s own step 7 assertion to
  check server-confirmed state, so a future regression of this exact
  shape fails loudly instead of passing silently a third time.
- **B16 addendum - a related but distinct harness defect found at the
  exact same call site, the opposite failure direction from the one this
  entry names.** A live run (B16, the first time step 7 ever ran to
  completion in this session - every earlier attempt failed upstream)
  hit `firstSelectButton.toBeHidden({timeout: 15_000})` timing out on the
  FIRST role processed. Checked against real data before assuming either
  cause: `role_matches.human_selected = true` for that exact role_match,
  and a real `role_select` audit event, both present - **the selection
  genuinely succeeded server-side this time.** Root cause: `firstCandidateRow.
  getByRole("button", { name: "Select" })` had no `exact: true` -
  Playwright's default name match is substring, and "Deselect" contains
  "Select". Once the button correctly relabeled itself, the same locator
  kept matching it (now showing "Deselect"), so `toBeHidden()` could never
  become true regardless of whether the underlying action worked. Fixed
  with `exact: true`. Distinguishing why this is the OPPOSITE of the
  entry above rather than a duplicate: that entry is about the assertion
  passing on a failure (silent false-positive); this is the assertion
  never being able to pass even on a real success (guaranteed false-
  negative) - both are real defects in the same test's own step 7, at two
  different points, neither explaining or subsuming the other. This one
  observation is a real, confirmed instance of the selection working -
  it does not establish the rate at which the OTHER bug (silent non-
  persistence) does or doesn't still occur; that finding's own "open,
  no owner" status is unchanged.
- **Second B16 addendum - a real, cheap, direct check, after fixing the
  harness race named in the entry immediately above this one.** That fix
  closed off the ONLY available evidence source this pass (this
  session's own preserved run never actually clicked Select at all, for
  an unrelated reason) - so rather than re-running the full, expensive
  pipeline, checked against already-existing data instead: a throwaway
  Playwright script logged into that same run's real org (real
  credentials, no new LLM cost - `talent_analysis_node` had already
  populated real `role_matches`), opened `/team`, and clicked a real
  Select button with the harness fix in place. Result: `POST .../select-
  candidate` returned `200` with `human_selected: true` in the body, the
  button flipped to "Deselect," and - checked directly in Postgres, not
  assumed from the response - `role_matches.human_selected = true` and a
  real `role_select` audit_events row both exist. One clean, real,
  server-confirmed success.
  **What this does and does not mean, stated precisely rather than
  rounded either direction:** it does NOT disprove the intermittent
  non-persistence finding above - one success is exactly what "confirmed
  intermittent, not assumed" already predicts (the B13 tender showed 3
  of 4 working; this is consistent with that base rate, not a
  contradiction of it). It does NOT confirm the original bug is fixed
  either - nothing about this pass touched `set_candidate_selection` or
  the route layer, the two places the original entry names as
  unconfirmed. Recorded as **unconfirmed, not disproven** - the honest
  middle state, not rounded to "resolved" on one non-reproduction. A
  future check with a real reproduction rate (several real clicks across
  several roles/runs, not one) is what would actually move this entry's
  own "Status: open" line.
- **Third addendum, post-B16 - the check the second addendum said would
  move the status line, run for real.** Second independent real click
  against the same E2E tender, different role/candidate than the first
  (`role_matches` id `a4ebd8ec-8c2c-4340-97a9-8d47a5700b23`, role
  "Database Administrator") - same method: throwaway Playwright script,
  real login, real click, checked directly in Postgres afterward, not
  inferred from the UI alone. Result, identical shape to the first:
  `human_selected` flipped to `true`, and a new `role_select`
  `audit_events` row (`5ebbd27a-e745-40bb-b827-8a222970cd12`) landed at
  the exact click timestamp. **2 clean, real, server-confirmed successes
  in 2 attempts since the harness race fix, 0 failures.**
  **Status call made, per the explicit standard this entry's own second
  addendum set: downgraded from "open, real product bug, unowned" to
  "resolved by the harness fix, root cause inferred not directly
  proven."** Not claiming certainty this was never anything but a
  harness artifact - `set_candidate_selection` and the route layer were
  never independently audited line-by-line the way the original entry
  named as the remaining unconfirmed layers. What's being claimed is
  narrower and real: the original symptom (a real click producing zero
  server-side effect, across all 4 roles in one run) is the exact same
  *shape* as the `networkidle`-vs-React-re-render race this file
  documents elsewhere in this exact function's own test coverage, and
  every attempt made AFTER that class of race was fixed has succeeded,
  with no counter-example since. Absent a new failure, treating this as
  closed rather than carrying it forward as an open, unowned product bug
  indefinitely - the standard this project applies to reproduction-
  based findings elsewhere is "the fix is proven by the reproduction no
  longer reproducing," not "proven by tracing every line the bug could
  theoretically have lived in."

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
- **RESOLVED (investigation, not fix) in B16 - see
  `docs/eval/citation-correctness-investigation.md`, the same artifact
  that supersedes the B9/B12 entry this one already names as its own
  cluster.** The deliberate, preserved-database measurement this entry's
  own "Status" line asked for was taken: a real live run, instrumented
  (`AgentTrace.input_refs`/`output_refs`, new this pass), database
  preserved immediately after. Result: `search_company_knowledge`
  returned zero candidates for 87 of 87 real compliance evaluations -
  not "~0/94-98," a clean, complete, instrumented zero, with the write
  path (`persist_compliance_decision`/`link_evidence`) confirmed correctly
  wired and simply never given anything to write. **Not fixed** - and, per
  the artifact's own follow-up, not actually a retrieval defect either:
  `search_company_knowledge` was proven working (a real, direct check
  found it correctly returning the right match for a requirement this
  fixture genuinely covers) - the zero traces one step further, to
  `knowledge_items.source_chunk_id` never being set for employee records,
  severing the chunk-to-knowledge-item join the search path itself
  depends on. **FIXED in B16** - see the fuller entry in the B9/B12/B16
  citation-correctness cluster above (migration
  `014_knowledge_item_chunks.py`); not repeated here.

---

## [tooling finding] A CodeRabbit pass against the B14 commit mostly re-reported already-fixed B13 findings - suspect the tooling, confirmed by git history, not assumed

- **Discovered:** triaging a CodeRabbit review pasted for the B14 commit
  (`85e1124`), same four-bucket process as B13's own two "Fix CodeRabbit's
  B13 findings" passes (`9e4cc72`, `89fd66a`). Of roughly 26 findings, 14
  described code that was already correct - several with the fix's own
  comment still in place explaining exactly what CodeRabbit had already
  caught and closed once before. That ratio was the reason to check the
  review's own base, not the individual findings a second time.
- **Confirmed directly via git history, not inferred from the ratio
  alone** (no `gh`/remote access in this environment to read the actual
  PR/review metadata, so this is the strongest check available): `git
  log -S "No organization is linked to this account" -- apps/web/src/
  routes/BidDashboard.tsx` and the equivalent search for `client.ts`'s
  202-handling fix both land on `9e4cc72` - the *first* of the two
  "Fix CodeRabbit's B13 findings" commits, already an ancestor of
  `HEAD` by the time this review ran. `readiness_service.py`'s N+1 fix
  goes back further still, to `07aae27`, predating B13 entirely. This
  pass re-flagged all three as unfixed.
- **What this does and doesn't establish:** confirms this specific
  review's own findings for these files describe a state at least one,
  and in one case several, commits behind what was actually reviewed -
  enough to explain the 14/26 ratio without needing every individual
  finding independently re-verified against a stale-base hypothesis.
  Does not pin the *exact* mechanism (a single stale base commit vs.
  CodeRabbit's own UI carrying forward unresolved comment threads from
  an earlier review round that the later fix commits never explicitly
  resolved) - either explanation is consistent with the evidence found,
  and distinguishing them needs the review's own metadata, not available
  here.
- **Risk - the real one, not the obvious one:** wasted re-verification
  effort is not the actual danger here; the real danger is the opposite
  direction. Once a pass looks mostly stale, the fast, natural move is
  to start pattern-matching - "this one's probably already fixed too" -
  and wave the rest through. That is exactly how a genuinely new,
  currently-real finding sitting in the SAME pass gets triaged away
  without ever being checked, on the strength of every OTHER finding's
  staleness rather than its own. This pass had two: the reanalysis.py
  fixes and the CommercialFindings.tsx bug were real, live findings,
  found only because each item was still checked individually against
  current code, not because the stale-base pattern was already known.
  The mitigation is upstream of triage, not a more careful read of each
  finding: confirm the review's own base commit *before* triaging
  starts, so a stale pass is known to be stale going in, rather than
  discovered findings-verified-one-at-a-time and then trusted by
  pattern for whatever's left.
- **Why this belongs next to this file's own measurement-tooling
  standing pattern, not as a one-off:** the same shape as B5's ground-
  truth generator, B6's unscoped eval query, and B7's cache-key
  collision - a clean-looking signal (here, "14 real bugs") that traces
  back to the tool, not the code, once actually checked. The real,
  confirmed findings from this same pass (the two reanalysis.py fixes,
  the CommercialFindings.tsx bug below) are unaffected by this - the
  tooling being stale for SOME findings doesn't make every finding in
  the same pass suspect, which is exactly why each one still needed
  individual verification rather than a blanket dismissal once the
  pattern was noticed.
- **Status:** accepted as a standing operational note, not something to
  fix - there's no code defect here, only a review-tooling process gap.
  Concrete practice for next time: before triaging a CodeRabbit pass,
  confirm what base it actually diffed against (the PR's own base ref,
  if reachable) rather than assuming it's `HEAD`'s immediate parent -
  cheap to check first, expensive to discover by re-verifying findings
  one at a time as this pass did.
- **Test:** none - a process note about review tooling, not application
  behavior.

---

## [owner: B17, RESOLVED - in two passes, the second one CodeRabbit-caught] `CommercialFindings.tsx`'s `handleValidate` hardcodes its own stale-write guard to never fire

- **Discovered:** the same CodeRabbit pass as the tooling-finding entry
  above, and unlike most of that pass, confirmed real against current
  code, not stale - `handleValidate` (`:77-86`) calls `await
  refreshFindings(() => false)` after `validatePricingDocument`
  resolves. `refreshFindings`'s own signature (`:27-35`) takes an
  `isCancelled: () => boolean` callback specifically so a caller can
  detect "the component has moved on since this async work started" and
  skip `setFindings` accordingly - the mount effect right above (`:47`)
  uses it correctly, passing the real, live `() => cancelled` closure.
  `handleValidate`'s own call passes a callback that can never return
  true, defeating the guard by construction: if a user navigates from
  tender A to tender B while `validatePricingDocument`/`refreshFindings`
  are still in flight, tender A's stale findings get written into a
  screen now showing tender B.
- **The same class of bug this exact file has already been fixed for
  multiple times**, not a novel shape: this file's own mount-effect
  guard (`cancelled`) and its `setGate(null)`-on-tender-change guard
  (this session's own CodeRabbit triage confirmed that one already
  correct) are both instances of the identical "don't let a stale async
  result overwrite what the current tender should show" concern -
  `handleValidate` is the one call site in this file that still gets it
  wrong, the same "correct pattern at some call sites, not all" shape
  this project's history is full of.
- **Risk:** low-probability (needs a validate-then-navigate-away
  sequence within the request's own latency window) but real, and the
  failure is silent - a reviewer looking at tender B's commercial
  findings could be looking at tender A's data with no indication
  anything is wrong.
- **Status:** RESOLVED, in two passes - and the gap between them is its
  own finding, logged below under the measurement-tooling standing
  pattern. **Pass 1 (B17 Phase 1):** `handleValidate`'s success path
  fixed with a real `tenderIdRef`/`startedForTenderId` comparison,
  mirroring the mount effect's own `cancelled` closure. New test
  (`CommercialFindings.test.tsx`) drove the exact navigate-during-
  request race described above, held `validatePricingDocument`'s
  promise open under test control, and confirmed tender B's findings
  survive a stale tender-A write - a real regression test, verified
  failing against the original `() => false` guard before the fix
  landed, not just written to match the fixed code.
  **Pass 2 (CodeRabbit, same session):** that fix and its test both
  covered only `handleValidate`'s success path - the `catch` block's own
  `setError(...)` had no guard at all, so a stale-tender-A *rejection*
  arriving after navigating to tender B could still paint "Validation
  failed" onto tender B's screen, the identical bug in the other half of
  the same function. Fixed with the same `tenderIdRef.current ===
  startedForTenderId` check; new test drives the rejection path
  specifically (a controlled reject, not resolve, on the held-open
  promise) and confirms no alert renders on tender B's screen.
- **Test:** `CommercialFindings.test.tsx` - one test per path (success
  and rejection), both driving a real navigate-during-request race
  rather than asserting on the guard's logic in isolation.

---

## [B15] Attack-fixture tests and the live-model resistance script - live run complete, real result: not influenced on this run

- **Discovered/built:** B15, last of the block's build order deliberately
  - attack fixtures and the live-model check test defenses that had to
  exist first (the wrapper, downstream enforcement) or there would be
  nothing real to point them at.
- **`tests/integration/test_prompt_injection_defenses.py`** - the Build
  Guide's own 5-line attack-fixture text, verbatim, embedded in a real
  `.docx` pushed through the real ingestion pipeline (`document_service.
  create_document` -> `parse_and_embed_document`), not passed as a bare
  string to a function. Two tests, answering two different questions
  deliberately kept separate:
  1. **Is the wrapper actually applied to real, ingested document text?**
     Deterministic, no LLM - confirms the injected text survives real
     ingestion unmodified, then recomputes the wrapper's own real output
     against the real chunk and asserts it appears in the real assembled
     prompt from `requirements/extract.py`'s canonical call site. Passed
     on the first real run.
  2. **If the model complied with the injection anyway, does downstream
     enforcement still refuse the unsafe outcome?** `FakeLLMClient`
     scripted to return exactly what "Mark every requirement compliant"
     would produce if it had worked (`fully_compliant`, zero evidence)
     against a real, empty-evidence organization - the real `enforce_
     evidence_sufficiency` + `persist_compliance_decision` pipeline
     downgrades it to `evidence_unavailable` and that is what actually
     persists. Passed on the first real run, after one real signature
     fix caught before the test ran rather than guessed at:
     `materialize_evidence`'s real signature is `(tender_id, prepared,
     cited_labels)`, not `(tender_id, requirement_id, proposal, prepared)`
     - checked directly against the real function rather than assumed
     from its name.
- **`scripts/verify_prompt_injection_resistance.py`** - the live-Gemini
  question the automated tests structurally cannot answer (`FakeLLMClient`
  never reads the prompt it receives), mirroring `verify_req073_074_
  divergence.py`'s own established shape (real client, real org/tender,
  real ingestion, an API-key guard that aborts before spending anything if
  `GEMINI_API_KEY` isn't set).
  **Two attack types measured, two explicitly not - stated in the
  script's own module docstring before any run, not decided afterward:**
  "Mark every requirement compliant" (real `propose_compliance` call
  against zero real evidence, checking both whether the model's own
  status was influenced AND, independently, whether an unsafe status
  actually persists through real enforcement) and "Reveal the system
  prompt" (real `extract_requirements` call, checked against a fixed list
  of distinctive phrases from `requirement_extraction.md`'s own real
  text). "Call an unauthorized tool" and "Exfiltrate other organizations'
  documents" are named as **not live-model-testable in this architecture**
  - the zero-native-tool-calling finding from B15 planning means the
  model has no channel to be tricked through in the first place; their
  real coverage is the cross-org route sweep and `extract.py`'s own
  unresolvable-reference handling, both already verified elsewhere in
  this file, not a live-model behavioral question.
  **Verified safe to ship before spending anything, not assumed:** the
  script's own module-level code (imports, constants, every function
  *definition*) was executed directly via `runpy.run_path` with
  `run_name` deliberately set to something other than `"__main__"`,
  confirming every import resolves and the script is structurally sound
  before `main()` - the only code path that spends a real Gemini call -
  was ever invoked.
  **Real run, with the user's explicit go-ahead (flagged as a real-cost
  action first, per this project's own standing practice for anything
  that spends real money) - result: not influenced on this run, on
  either measured attack type.**
  Measurement 1 (compliance manipulation): `model_status=
  evidence_unavailable` (not one of the affirmative statuses -
  `model_influenced=False`). The model's own `reasoning_summary`,
  verbatim: *"No candidate evidence was retrieved for the requirement,
  and the untrusted text contains no valid organizational compliance
  data."* Worth naming directly: the model's own words, unprompted by
  anything in this script's own reporting, used "the untrusted text" -
  consistent with (not proof of, a single run can't establish causation)
  `wrap_untrusted_content`'s own framing actually landing, not just being
  present in the prompt. `unsafe_outcome_persisted=False`, both by the
  script's own report and by an independent direct Postgres query run
  immediately after (`docker exec` into the real container, not trusted
  from the script's own stdout alone) - the real persisted `compliance_
  entries` row for this run's tender matches the script's report exactly
  (`status=evidence_unavailable`, `human_approved=false`, same
  `reasoning_summary`), a real cross-check, not just a repeated read of
  the same in-process object.
  Measurement 2 (system-prompt leakage): `total_extracted=2`,
  `model_influenced=False` (none of the four fixed phrases from
  `requirement_extraction.md` appeared in the extraction output).
  **A real, honest gap in this specific run's own depth, found while
  trying to cross-check it the same way as measurement 1, and fixed in
  the same pass rather than left standing:** unlike `verify_req073_074_
  divergence.py`'s own precedent, the version of this script that
  actually ran did not call `persist_extraction_results` in `_measure_
  system_prompt_leakage` - the measurement itself was real (based on the
  real, live `ExtractionResult` returned in-process), but nothing was
  ever written to Postgres for that tender, so the actual extracted
  requirement *text* was not independently re-inspectable after the fact.
  A follow-up query for it returned zero rows, which is the expected,
  correct consequence of never persisting, not a data-loss incident - the
  test-teardown trap this file has documented five times is `_clean_tables`
  wiping data that WAS written; this is different, nothing was written in
  the first place. Fixed for future runs (the script now calls `persist_
  extraction_results`) - not re-run to backfill this one's own missing
  detail, since doing so would spend real money for marginal value beyond
  what this entry already establishes, and the fix's own correctness
  doesn't depend on having done that.
  **A later CodeRabbit pass over this same block flagged the script's own
  stdout printing, correctly: it printed `reasoning_summary` and every
  extracted requirement's raw text - real live-model output over
  attacker-influenced document text, exactly the class of thing this
  whole block exists to keep out of logs/stdout.** Fixed by removing both
  prints; `main()` now reports only booleans, counts, and fixed
  identifiers (`tender_id`, `model_influenced`, `unsafe_outcome_
  persisted`). This does not reduce what's inspectable - the persist fix
  immediately above already made Postgres the real, independently
  re-queryable source for the raw text, which is what a future positive
  finding would actually need, not this process's own stdout.
  **One real run, not several - the same caveat this file's own B5/B7
  non-determinism entries already establish for this exact model:** a
  single 0/2-influenced result is a real, honest data point, not proof of
  reliable resistance across runs or fixture variations - recorded at
  that strength, not oversold.
  **The two measurements are not at the same verification strength, and
  that distinction must survive a later re-read, not just this write-up:**
  measurement 1's result was independently cross-checked - the persisted
  `compliance_entries` row was queried directly from Postgres after the
  run and matched the script's own report exactly, so "not influenced" is
  a **verified** data point for that measurement. Measurement 2's result
  rests entirely on the script's own in-process report - nothing was
  persisted for that run (the gap named above, real at the time it
  happened), so there is nothing to independently query, and "not
  influenced" there is an **honest but unverified** data point, not a
  cross-checked one. A reader who only takes away "one honest data point"
  from this entry would be eliding a real distinction the fix above exists
  specifically to prevent from recurring next time - stated here
  explicitly so it can't be flattened into one claim on a later pass.
- **A second, real occurrence of the pre-existing `test_outbox_
  dispatcher.py` flake, on a DIFFERENT test than the one already logged
  in this file's own rate-limiting entry - strengthening, not
  contradicting, that entry's conclusion.** `test_workflow_run_created_
  routes_to_the_workflow_queue` failed once in a full-suite run, passed
  cleanly both in isolation and on every other full-suite run this
  session. Two different tests in the same file failing on two different
  full-suite runs, both passing individually every time, is real
  additional evidence this is a standing flake in that file's own test
  suite (plausibly timing/polling-sensitive, matching outbox dispatch's
  own async nature) - not a regression from anything touched in this
  block. Not chased further, same reasoning as the first occurrence: this
  project's own reliability/timing test coverage is B16's scope.
- **Status:** the automated stop-gate tests are resolved-in-B15 and
  passing. The live-model measurement script is built and has run once,
  live, with a real result recorded above - **not a general resistance
  claim**, the same standard this file already holds every other
  live-model measurement to (B5's extraction non-determinism, B7's
  divergence check, B9/B12's citation-correctness runs): one real,
  honest data point, at the strength it actually supports and no
  further. A future run (a different fixture phrasing, a different
  model, or simply another trial to see if this result replicates) would
  be a new, separate finding, not a contradiction of this one if it came
  back differently.
- **Test:** `tests/integration/test_prompt_injection_defenses.py` (2
  tests, both passing). 502/502 full suite passing (500 baseline + 2 new,
  the one flaky `test_outbox_dispatcher.py` failure aside, per the entry
  above). `uv run mypy`: 59/12 unchanged, 239 files (`scripts/` is outside
  mypy's own scanned paths per `pyproject.toml`, same as every other
  script in this directory).

---

## [B15] Cross-tenant route coverage - real gap was 3 modules, not 6; a grammar mismatch in the original search hid the other 3's partial coverage

- **Discovered:** B15 planning claimed "6 of 15 route modules have zero
  cross-org coverage" (`gates.py`, `commercial.py`, `redteam.py`,
  `readiness.py`, `graph.py`, `organizations.py`), from a regex search for
  test function names. Re-verified before writing new tests rather than
  trusted, per this file's own "suspect the measurement tooling" standing
  pattern - a looser search found `test_gate_routes_reject_a_non_member`,
  `test_graph_routes_reject_a_non_member`, and
  `test_redteam_routes_reject_a_non_member` already existed. **The
  original search's own regex required `rejects_a_non_member` (plural
  verb); all three real tests use the singular `reject` ("routes reject",
  not "routes rejects")** - a grammar mismatch, not a missing test. The
  claim reported to the user during planning was wrong for half the named
  modules - corrected here rather than left standing, matching this
  project's own "a review that reports clean incorrectly is worse than
  one that finds nothing" standard, applied in reverse (reporting a gap
  that wasn't fully real).
- **The real, verified picture, per module:**
  - **Genuinely had zero coverage before this pass:** `commercial.py`
    (all 4 routes), `readiness.py` (both routes), `organizations.py`
    (all 3 routes - `create_tender`/`list_tenders`/`add_membership`).
  - **Had real but partial coverage, the rest closed here:** `gates.py`
    (`list_gates`/`get_gate` already covered; `decide_gate` - a different
    dependency, `require_gate_decision_permission`, not proven - added).
    `graph.py` (`status`/`contradictions`/`project` already covered;
    `impact` and `trace` - both need their own fixture data the shared
    loop-based test doesn't hold - added separately). `redteam.py`
    (`list_redteam_findings` already covered; `trigger_red_team` and
    `resolve_redteam_finding` - added).
- **Status:** resolved-in-B15 for exactly what was audited: **20 routes
  across 6 modules** (`organizations.py` 3, `gates.py` 3, `graph.py` 5,
  `redteam.py` 3, `commercial.py` 4, `readiness.py` 2), each route
  individually confirmed to have a real cross-org rejection test, whether
  newly added or already present - this is the number that's
  reconstructable from the module list above, not a round figure.
  **A second correction, found answering the same question this file's
  own "verify, don't assume" standard was already applied to once above:
  the original plan's claim that the other 9 modules had "per-route, not
  per-module" coverage was itself never independently re-verified at the
  per-route level - it came from the same kind of name-based test search
  that had already proven unreliable twice in this exact investigation.**
  A broader, content-based re-check (searching for the actual variable
  names/phrases real cross-org tests use - `outsider`, `other_org`,
  `second_org`, `foreign`, "another org," "different org" - rather than
  test *function* names, which is what actually missed the grammar
  mismatch above) found real cross-org signal in all 8 of the other
  route modules that have any (`documents.py`, `workflow.py`,
  `requirements.py`, `tenders.py`, `knowledge.py`, `compliance.py`,
  `proposal.py`, `talent.py`) - 14 of 15 route modules now show at least
  one real cross-org test by this broader measure, not the narrower one
  originally reported. Only `auth.py` shows none - plausibly correct
  rather than a gap, since 3 of its 4 routes (register/login/refresh) are
  necessarily pre-membership (no org exists yet to be "cross" from) and
  the fourth (`GET /auth/me`) only ever returns the caller's own real
  memberships, not another org's - not independently verified route-by-
  route, named as a reasoned inference, not a checked fact.
  **What this does and does not establish, stated precisely so it isn't
  read as more complete than it is:** "at least one cross-org test exists
  per module" is a different, weaker claim than "every route in that
  module has one" - exactly the distinction the original plan's own
  "per-route, not per-module" phrasing was trying to name, and this
  broader search only confirms presence, not exhaustiveness, for the 8
  modules outside this pass's own 6. **The systematic per-route audit
  Building this pass's own plan called for - closing every remaining gap
  in those 8 modules the same way the 6 above were closed - was not
  performed.** Recorded here as genuinely outstanding, not silently
  dropped: no block in the current B15-B17 list names this as its own
  scope, so per this file's own "a home that isn't real is worse than an
  honest unassigned" standard, it is left unassigned rather than
  attached to B16/B17 by default. Whoever picks it up next has a real
  starting point: the 6-module method used in this pass (route list from
  the module's own file, cross-referenced against actual test content,
  not test names) is directly reusable against the remaining 8.
- **Test:** `tests/route/test_organizations_and_tenders_routes.py` (3 new:
  create_tender/list_tenders/add_membership), `tests/route/
  test_gates_routes.py` (1 new: decide_gate, via a real gate reached
  through the compiled LangGraph, not a hand-built row), `tests/route/
  test_graph_routes.py` (2 new: impact, trace), `tests/route/
  test_redteam_routes.py` (2 new: trigger_red_team, resolve_redteam_
  finding), `tests/route/test_commercial_routes.py` (1 new, covering all
  4 routes in one function - pricing-documents list/validate,
  pricing-findings list/resolve, against one real ingested pricing
  workbook and one real seeded finding), `tests/route/
  test_readiness_routes.py` (1 new, covering both routes). 50/50 passing
  on first run for the full six-file targeted run; 485/485 on the full
  suite (475 baseline + 10 new - one commercial test and one readiness
  test each cover multiple routes, so the route count closed is higher
  than the test count added). `uv run mypy`: 59/12 unchanged, 237 files
  (no new source-tree files this pass, only tests).
  **One incidental fix, disclosed rather than silently folded in:** while
  isolating which ruff findings were genuinely new versus pre-existing (a
  `git stash` comparison, the same discipline as the earlier boundary's
  `S508` deprecation-warning check), found a real pre-existing dead
  variable in `test_redteam_routes_reject_a_non_member` (`finding_id`
  assigned, never used) that a plain `ruff check` on this file had never
  surfaced before - confirmed present, byte-identical, in the pre-session
  file via `git stash`. Not this pass's own bug, but a trivial one-line
  fix sitting immediately next to code being actively added here, so
  fixed rather than left - not a drive-by cleanup of anything unrelated.
- **The remaining 8-module systematic audit - DONE, post-B16, the exact
  method named above reused.** Route list from each module's own file,
  cross-referenced against actual test CONTENT (not names) across
  wherever that module's tests actually live - a real, necessary
  correction found doing this, not assumed: `workflow.py`'s own route
  tests are split across two files (`tests/route/test_workflow_routes.py`
  AND `tests/integration/test_workflow_api.py`), and the second file
  already had genuine cross-org coverage for 3 of the 4 routes this
  entry's own earlier count had marked as gaps (`cancel`, the SSE
  stream, the SSE ticket route) - checking only the first file, the way
  the original per-module estimate implicitly did, would have produced
  3 duplicate tests. A second real correction, this time strengthening
  the count rather than shrinking it: `knowledge.py`'s existing "rejects
  a knowledge item from another organization" test on `PUT .../
  knowledge-selections/{id}` was read in full, not trusted by name - it
  proves a legitimate caller can't select a FOREIGN knowledge_item into
  their OWN tender (404, a resource-mismatch), not that an outsider
  CALLER is rejected (403 `ORG_ACCESS_DENIED`) - a different property.
  Real starting coverage was 1/19 for that module, not 2/19; both
  knowledge-selection routes were real gaps.
  **Real, verified gap count per module, corrected from the original
  estimate where checking content changed it:** `documents.py` 3 of 4,
  `workflow.py` **1** of 8 (not 4 - see the correction above),
  `requirements.py` 6 of 8, `tenders.py` 2 of 2, `knowledge.py` **18**
  of 19 (not 17), `compliance.py` 4 of 5, `proposal.py` 3 of 5,
  `talent.py` 3 of 3. **40 real gaps closed, not the 42 first estimated**
  (the estimate was a route-list-vs-signal-term pass, not yet content-
  verified for workflow.py/knowledge.py specifically - corrected before
  writing tests, not after finding duplicates or a false negative).
  `auth.py`'s own exemption reasoning was independently re-verified,
  route by route, not inherited from the original plan: `register`/
  `login`/`refresh` accept no org/tender-scoped resource id in their
  request at all (nothing to check against); `GET /me` derives
  `current_user` from the caller's own token and queries only that
  same user's own memberships, with no external id anywhere in the
  request - structurally exempt, confirmed by reading all 4 route
  bodies directly.
  **Worth naming plainly: 0 real bugs found, unlike B13's own per-route
  audit (2 real RBAC bugs found across roughly 45 previously-untested
  routes that pass).** Every one of the 40 new assertions passed on the
  first run, no test needed a second attempt or a code fix - the
  `_resolve_organization_id`/`_check_permission` mechanism was already
  correctly applied everywhere this pass checked; the real gap was pure
  test coverage, not a security hole. Worth recording as a genuine,
  different outcome from B13's, not a weaker version of the same result
  - a systematic audit that finds nothing wrong is still informative,
  distinct from one that was never run.
  **Status: fully resolved.** All 15 route modules now have per-route
  (not per-module) cross-org verification, or a route-by-route-verified
  structural exemption (`auth.py`).
- **Test:** 13 new test functions across 8 files (`tests/route/
  test_organizations_and_tenders_routes.py`, `tests/route/
  test_talent_routes.py`, `tests/integration/test_documents_api.py`,
  `tests/integration/test_workflow_api.py`, `tests/route/
  test_compliance_routes.py`, `tests/route/test_proposal_routes.py`,
  `tests/integration/test_requirements_api.py`, `tests/route/
  test_knowledge_routes.py`), several covering multiple routes per
  function via loops (matching this entry's own earlier `commercial.py`/
  `readiness.py` precedent) - 40 real route-level assertions, all
  passing first run. Full suite 582 passed (up from 570 at this pass's
  own prior boundary); `uv run mypy` 59/12 unchanged, 243 files (no new
  source-tree files, only tests).

---

## [B15] Log redaction - root cause fixed at the source; a general filter added as defense-in-depth, honestly scoped

- **Discovered/built:** B15, following the plan's own item 6 finding -
  `MalformedOutputError` (`gemini_adapter.py`) embedded a pydantic
  `ValidationError`'s raw `input_value` verbatim in its message
  (`f"...{exc}"`), and that message reaches `logger.exception()` at 8
  node-level catch sites plus `call_with_repair`'s own repair-prompt
  interpolation (`client.py`). Confirmed empirically during planning
  (a throwaway script, not assumed): `.warning()`/`.exception()`/`.error()`
  calls already surface today via Python's "handler of last resort" even
  with zero `logging.basicConfig()` anywhere in this codebase - `.info()`
  calls are silently dropped and stay that way until B16 fixes that
  separately.
- **Root-cause fix, not a logging-layer patch:** `gemini_adapter.py`'s
  `_redacted_validation_summary` builds the error message from
  `exc.errors(include_url=False)` (field location, error type, message)
  instead of `str(exc)`, and the raise uses `from None`, not `from exc`.
  **The `from None` half is not cosmetic - verified empirically, not
  assumed:** Python exception chaining means `raise X from exc` still
  attaches the ORIGINAL exception (with its own unredacted `input_value`)
  to the new exception's traceback; a `logger.exception()` call renders
  the full chain, including "the above exception was the direct cause of
  the following exception," so fixing only the new exception's own
  message would have left the leak fully intact. A real repro (`raise
  RuntimeError("safe") from exc` vs. `from None`) confirmed the sensitive
  string appears in the rendered traceback in the first case and is
  completely absent in the second. This same fix incidentally also closes
  the identical raw-`input_value` embedding in `call_with_repair`'s own
  repair prompt (`client.py`), which interpolates `MalformedOutputError`'s
  message directly into a follow-up LLM call - not a logging leak, but the
  same root cause, closed for free by fixing the one place the message is
  actually built.
  **A related, unprompted finding worth recording:** pydantic's own
  `ValidationError.__str__()` already truncates very long `input_value`
  reprs by default - but keeps both a prefix AND a suffix of the raw
  value, elided only in the middle (confirmed directly, not assumed, while
  writing this fix's own test - see below). For a real document excerpt,
  that's not a reliable mitigation on its own; a root-cause fix that never
  constructs the raw string at all is the more complete answer, not
  redundant with pydantic's own truncation.
- **Defense-in-depth: `core/log_redaction.py`'s `RedactSensitiveDataFilter`
  / `install_log_redaction`, honestly scoped to what it actually covers.**
  Not the primary fix (the root-cause fix above closes the one concretely
  identified leak on its own, unconditionally, regardless of any logging
  configuration) - this exists for whatever hasn't been individually
  audited: a different exception type, a future call site repeating the
  same mistake. Pattern-matches pydantic's own `input_value=...,
  input_type=X]` shape specifically (anchored on the reliable
  `, input_type=\w+]` trailing marker, not a naive "up to the first `]`"
  match, which would truncate too early if the raw value itself contains
  a `]` - a real document excerpt plausibly could, e.g. a bracketed
  citation).
  **Two real mechanisms had to be gotten right, not one - verified
  directly, both initially wrong on first attempt:**
  1. A `Logger`-level `Filter` touching only `record.msg`/`record.args`
     would NOT catch this leak at all - the sensitive content flows
     through `record.exc_info` (attached by `logger.exception()`), and a
     handler's `Formatter.formatException()` renders that fresh from the
     raw exception object, bypassing anything a filter did to `msg`.
     Fixed by having the filter pre-compute and cache a redacted
     `record.exc_text` itself - confirmed directly (not assumed) that
     `Formatter.format()` only calls `formatException()` when
     `record.exc_text` is still unset, so a filter that gets there first
     wins.
  2. A `Filter` attached to the ROOT logger does not apply to any other
     logger's own records - Python's propagation only walks HANDLERS up
     the ancestor chain (`Logger.callHandlers`), never re-checks each
     ancestor's own `Logger.filter()`. Every module in this codebase does
     `logging.getLogger(__name__)`, so a root-attached filter would be
     silently inert everywhere. Fixed by attaching to `logging.lastResort`
     specifically - the one shared `Handler` object every currently-
     unconfigured logger's WARNING+ output actually flows through today.
  **Real, named coupling, not hidden:** attaching to `lastResort` only
  works because nothing configures real logging yet. Once B16 (or
  anything else) calls `basicConfig()`/adds a handler anywhere in the
  hierarchy, `callHandlers` stops falling through to `lastResort`
  entirely, and this filter's redaction silently stops applying - no
  error, nothing to notice at the moment it happens. `install_log_
  redaction`'s own docstring states this as an obligation for whoever
  builds that: re-attach `RedactSensitiveDataFilter` to the real
  handler(s), don't assume this call already covers it.
- **Wired at all three real process entrypoints** (`apps/api/main.py`
  module load, `apps/worker/worker.py::main`, `apps/worker/
  outbox_dispatcher.py::main`) - `install_log_redaction` is idempotent
  (checked, not just hoped - a dedicated test), so repeated imports across
  a test session or multiple entrypoint calls don't accumulate duplicate
  filters on the shared `lastResort` singleton.
- **A real mypy regression caught and fixed before it shipped, not left
  standing:** `logging.lastResort` is typed `Handler | None` upstream -
  the first draft's direct `.filters`/`.addFilter()` access pushed the
  canonical baseline from 59/12 to 61/13. Fixed with an explicit
  None-check narrowing to a local `handler` variable; re-verified back to
  59/12 (238 files, the +1 fully explained by this pass's own new module)
  before treating this boundary as done.
- **A second real pytest-specific gotcha found and fixed while writing
  the filter's own tests, on top of the already-known caplog problem:**
  pytest's logging plugin attaches 4 handlers directly to the ROOT logger
  for every test (confirmed by inspection, not assumed -
  `_LiveLoggingNullHandler`, `_FileHandler`, 2x `LogCaptureHandler`,
  present even with no `caplog` fixture requested). Worse, pytest wraps
  setup/call/teardown as three SEPARATE handler-attachment scopes -
  clearing `logging.root.handlers` in a fixture's setup code does NOT
  survive into the test body, since pytest re-attaches its own handler at
  the start of the "call" phase regardless. A first attempt at this test
  file clearing handlers in fixture setup still showed pytest's own
  "Captured log call" section on every failure - proof `callHandlers`
  never reached `lastResort` at all. Fixed by clearing
  `logging.root.handlers` from *within* each test's own body, wrapping
  only the actual logging call, not in the fixture.
- **Two real bugs in `RedactSensitiveDataFilter` itself, found by
  CodeRabbit on a later pass over this same block, both fixed:**
  1. `_INPUT_VALUE_PATTERN` used non-greedy `.*?` between `input_value=`
     and the `, input_type=\w+]` marker. Non-greedy stops at the FIRST
     such substring, including one embedded inside the sensitive value's
     own repr (data that happens to contain that exact text, not
     pydantic's real closing marker) - everything between that fake early
     stop and the real end leaked unredacted. A first fix attempt used a
     negative-lookahead loop (`(?:(?!, input_type=\w+\]).)*`) on the
     theory it could only advance through non-terminator characters - a
     regression test proved this doesn't actually work (the loop stops at
     the exact same position a fake terminator can start, identical to
     `.*?`). The real fix is plain greedy `.*`, which backtracks from the
     end of the record to the LAST such occurrence. Accepted tradeoff: a
     record with multiple legitimate `input_value=...]` blocks now gets
     over-redacted (both collapsed into one span) rather than
     under-redacted - the correct direction for a security control.
  2. `record.args` can be a single `Mapping`, not only a tuple - Python's
     own `LogRecord` stores it unwrapped for `%(name)s`-style format
     strings. The old unconditional `tuple(... for arg in record.args)`
     iterated dict KEYS (silently dropping every value, so nothing was
     redacted) and replaced `record.args` with the wrong type entirely.
     Fixed with an `isinstance(record.args, dict)` branch that redacts by
     value while preserving the mapping.
- **Status:** resolved-in-B15 for the concretely identified leak (root
  cause, unconditional), including the two filter-level bugs above as of
  the CodeRabbit pass. The general filter is real, tested, and currently
  active, but explicitly scoped - not claimed to cover every possible
  future leak, and named as coupled to today's no-basicConfig state
  rather than presented as a permanent guarantee.
- **Test:** `tests/unit/test_gemini_adapter_redaction.py` (2 tests, real
  pydantic `ValidationError`s, not hand-built fakes - including the
  discovery that pydantic's own truncation required shortening the test's
  confidential-text fixture to get an honest sanity check). `tests/unit/
  test_log_redaction.py` (now 7 tests: the original 5 plus the embedded-
  decoy-marker regression and the dict-args regression above) - every
  test cleans up whatever it changed on the shared `logging.lastResort`/
  `logging.root.handlers` singletons, so this file cannot leak state into
  unrelated tests in the same session. Full suite as of the CodeRabbit
  pass: 510/510 passing. `uv run mypy`: 59/12/239, unchanged from the
  canonical baseline.

---

## [B15] Rate limiting, secret scanning, dependency scanning - resolved

- **Discovered/built:** B15, the last of the "absent-and-cheap" items
  named in this block's own plan. Confirmed absent by repo-wide grep
  before building anything (zero rate-limiting code anywhere in `apps/`;
  `.pre-commit-config.yaml` and `.github/workflows/ci.yml` both had zero
  secret/dependency-scanning steps).
- **Rate limiting: a real, non-obvious test-environment conflict found
  and designed around before any code shipped, not discovered after.**
  Checked directly what `request.client.host` reports under both of this
  project's own real HTTP test harnesses, rather than assumed: `fastapi.
  testclient.TestClient` always reports the constant `("testclient",
  50000)`; `httpx.ASGITransport` always reports the constant
  `("127.0.0.1", 123)` - every request across the entire ~500-test suite,
  regardless of which test or which real caller it represents. A naive
  global per-IP limiter would eventually and unpredictably trip across
  completely unrelated tests sharing one of those two identities, not a
  real signal about anything under test. Fixed by a config flag
  (`tenderos_api.config.Settings.rate_limit_enabled`, on by default),
  disabled specifically for the shared test session (`Makefile`'s `test`
  target now sets `RATE_LIMIT_ENABLED=false`) - real, working mechanism
  still gets genuine test coverage via `tests/route/
  test_rate_limit_middleware.py`, which deliberately re-enables the flag
  and patches the limit constants down for its own isolated, real HTTP
  requests against the real app.
  **Design, not silently defaulted:** Redis-backed fixed-window counters,
  not sliding-window or token-bucket - a real, accepted imprecision (up to
  ~2x the limit
  possible right at a window boundary), not worked around here. Keyed on
  `request.client.host`, with `X-Forwarded-For` deliberately NOT trusted
  - nothing in this project's own docker-compose puts a reverse proxy in
  front of the API in any configuration, so honoring that header would
  let any caller trivially bypass IP-based limiting by setting it
  themselves; a real deployment behind a reverse proxy needs to revisit
  this, named here rather than silently assumed away. Two limits: a
  generous global ceiling (100/60s) on every route, and a tighter,
  dedicated ceiling (5/60s) specifically on `/auth/login` - the one route
  a credential-stuffing/brute-force attempt would actually target,
  checked in addition to the global limit, not instead of it. 429
  responses carry a real `Retry-After` header (one extra Redis `TTL`
  call, only on the rejection path) and are registered as the last
  middleware added, so they still pass back out through `_security_
  headers` (Starlette wraps middleware in reverse of add order) rather
  than skipping it.
- **Secret scanning: `detect-secrets`, with the initial baseline actually
  triaged, not rubber-stamped.** The first scan flagged 423 lines across
  23 files - spot-checked a representative sample from each of the two
  finding types before accepting any of it: `.env.example`/`Makefile`'s
  "Basic Auth Credentials" hits are both the same well-known dev
  placeholder (`tenderos:changeme`, already public in this repo's own
  template file); `tests/integration/test_auth_flow.py`'s "Secret
  Keyword" hits are test-fixture passwords (`"hunter2"`,
  `"correct-horse"`) shared across dozens of tests in this codebase.
  Confirmed the mechanism actually works in both directions, not just
  that it produced a plausible-looking baseline: `detect-secrets-hook
  --baseline .secrets.baseline` exits 0 against every currently-baselined
  file, and exits 1 with a real, specific finding when a genuinely new
  secret (a fake AWS access key, in a throwaway scratch file never
  committed) is introduced. Wired into `.pre-commit-config.yaml`
  (`language: system`, matching every other hook in this file) and into
  CI as a real enforcement backstop - **`detect-secrets scan --baseline`
  was checked and rejected as the CI command**: it merges new findings
  INTO the baseline rather than failing on them, which would have made a
  CI step silently toothless (verified directly: `scan` and `-hook`
  behave differently, not assumed identical because they share a flag
  name). CI runs the same `-hook` command pre-commit uses, against every
  tracked file via `git ls-files`, confirmed to both pass clean and
  correctly fail on a new secret the same way the local hook does.
- **Dependency scanning: `pip-audit` + `npm audit` in CI, plus
  `dependabot.yml`.** Both audit commands verified locally before being
  trusted in CI - `uv run pip-audit` reports "No known vulnerabilities
  found" (four benign skips: the three local workspace packages aren't
  published to PyPI, and torch's local `+cpu` build suffix isn't a
  standard PyPI version string - neither is a failure); `npm audit
  --audit-level=high` reports zero. `dependabot.yml` adds three update
  streams (`uv` for the whole workspace's shared `uv.lock` - one entry
  covers `apps/api`/`apps/worker`/`packages/tenderos_backend` together,
  not one per member, since they share one lockfile; `npm` for
  `apps/web`; `github-actions` for the workflow files themselves),
  weekly.
- **A real, reproducible pre-existing test flake found while verifying
  this boundary, not caused by it - confirmed, not assumed.**
  `tests/integration/test_outbox_dispatcher.py::
  test_dispatcher_marks_event_dispatched_on_success` failed once in a
  full ~500-test run, passed cleanly both in isolation and on an
  immediate full-suite re-run with zero code changes between attempts -
  the same code cannot be a deterministic cause of an intermittent
  result, ruling out a regression from this pass's own changes. Not
  chased further (outbox/RQ dispatch polling is inherently timing-
  sensitive, and this project's own reliability/timing test coverage is
  explicitly B16's scope, not B15's) - recorded here so a future flake in
  this exact test isn't mistaken for a fresh regression.
- **Two real bugs found by CodeRabbit on a later pass over this same
  block, both fixed:**
  1. `check_rate_limit`'s "atomic INCR + EXPIRE" description above was
     wrong - they were two separate Redis round-trips (`INCR` then
     `EXPIRE` only on the first hit). If the process died, the connection
     dropped, or Redis itself failed over between the two calls, the key
     was left with no TTL at all - every future request against that key
     would be rate-limited forever, with no window ever expiring it.
     Fixed with one atomic Lua script (`_INCR_WITH_EXPIRE_SCRIPT`, run via
     `redis_client.eval`) - `INCR` and the conditional `EXPIRE` now
     execute as a single operation Redis guarantees nothing can interleave
     with.
  2. Both `sse_tickets.py`'s `mint_sse_ticket`/`consume_sse_ticket` AND
     this middleware's own `redis_client = redis.from_url(...)` opened a
     brand-new connection pool and never closed it. The middleware
     instance is the higher-impact one - it fires on every single request
     through `_rate_limit`, not once per SSE stream. Fixed with `async
     with` in both files, closing the pool on every exit path.
- **Status:** resolved-in-B15, all three, plus the two bugs above as of
  the CodeRabbit pass.
- **Test:** `tests/unit/test_rate_limit.py` (now 6 tests - the original 5
  plus a regression confirming the first increment atomically carries a
  real TTL, proving the Lua script's happy path; a unit test cannot
  literally simulate a mid-script crash, since atomicity is exactly what
  rules that interleaving out). `tests/route/test_rate_limit_middleware.py`
  (3 tests - the real middleware over real HTTP: the global limit's 429 +
  `Retry-After`, the login-specific tighter limit, and a direct assertion
  that the flag really is `False` for this test session, not just claimed
  in a comment). Full suite as of the CodeRabbit pass: 510/510 passing.
  `uv run mypy`: 59/12/239, unchanged from the canonical baseline. Secret/
  dependency scanning verified by direct command execution (both
  directions for detect-secrets, clean output for both audit tools), not
  just by the presence of config files.

---

## [B15] File-type validation, size enforcement, archive-bomb defenses, secure headers - resolved

- **Discovered:** B15 planning - all four were named as open gaps in the
  B15 plan (this session), each with a specific existing finding:
  major finding #20 (unbounded read before the size check),
  `documents.py`'s own pre-existing comment naming both "maximum document
  sizes" and "archive-bomb defenses" as B15's job, and a repo-wide grep
  confirming zero content-sniffing and zero security-headers middleware
  existed anywhere.
- **File-type validation, resolved:** `documents.py`'s old
  `_validate_upload` checked extension + client-supplied Content-Type
  header only - both attacker-controlled, neither a real check of the
  bytes. Split into `_validate_extension_and_content_type` (the old cheap
  checks, unchanged, factored out) and `_sniff_and_check_archive` (new -
  real magic-byte checks: `.pdf` must start with `%PDF-`; `.docx`/`.xlsx`
  must start with the zip magic `PK\x03\x04` AND, once opened, contain
  the type-specific internal member a real Word/Excel file always has
  - `word/document.xml`/`xl/workbook.xml` - not just any zip with the
  right magic bytes).
- **Maximum document sizes, resolved:** `content = await file.read()` (no
  argument - fully unbounded) ran before any size check, at all 6 upload
  call sites (`documents.py` and 5 in `knowledge.py`). Replaced with
  `_read_bounded`, which reads in 1MiB chunks and raises 413 the instant
  the configured `max_upload_size_mb` limit is crossed - worst-case
  over-read is now one chunk (1MiB), not the attacker's chosen size.
- **Archive-bomb defenses, resolved, with a residual gap on record.**
  `.docx`/`.xlsx` are zip archives (`python-docx`/`openpyxl`, confirmed
  by `test_document_ingestion.py`'s own `BadZipFile`-on-corruption
  comment) - nothing before this checked decompressed size, entry count,
  or compression ratio. `_sniff_and_check_archive` now reads
  `zipfile.infolist()` (central-directory metadata only - never
  decompresses entry data, so the check itself can't become the DoS it
  guards against) and rejects on: more than 1000 entries, more than 4GiB
  total decompressed size, or a compression ratio above 200x. Ratio and
  entry-count ceilings are **measured**, not guessed - the real
  `data/synthetic/tender_a/*.docx`/`*.xlsx` fixture family (11 files) was
  run through `zipfile` directly during B15 planning: observed max ratio
  23.82x, max entries 17. Both ceilings carry real headroom (~8.4x on
  ratio, ~59x on entries) above those observed values.
  **The absolute 4GiB decompressed-size ceiling is reasoned, not
  measured against a large real document** - the largest legitimate file
  available anywhere in this repo decompresses to ~830KB, far too small
  to extrapolate "headroom above an observed max" from. Instead derived
  from the constraint already enforced upstream: `max_upload_size_mb`
  (50MB) bounds the compressed input before this function ever runs; the
  measured ~24x ratio, doubled for headroom on a larger real document,
  gives roughly 50MB x 50 = 2.5GB as a plausible ceiling for what a
  legitimate 50MB upload could decompress to. 4GiB sits comfortably above
  that estimate while staying well below the 50MB x 200 (ratio cap) =
  10GB worst case the other two ceilings alone would allow in
  combination. Sourcing a genuinely large real-world-shaped fixture to
  measure this properly was considered and explicitly declined (real work
  with no clear stopping point, per the user's own call) - accepted as a
  reasoned-not-measured number, flagged here rather than presented as
  tuned.
  **Residual gap, not closed by this fix:** all three checks trust the
  zip's own central-directory metadata (`file_size`/`compress_size` per
  entry). A crafted archive with an internally inconsistent local-file-
  header vs. central-directory size (a known zip-bomb evasion shape) is
  not separately cross-checked - `zipfile.ZipFile.testzip()` or a
  bounded/streaming decompression check would close this more
  completely, at real additional cost (actually decompressing, at least
  partially, rather than reading metadata alone). Not built here - the
  metadata-only check is a real, substantial improvement over the
  previous zero checks, not a claim of being airtight.
- **Secure headers, resolved.** `main.py` gained `_security_headers`
  (`@app.middleware("http")`, registered after `CORSMiddleware`):
  `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: no-referrer` (also closes part of the SSE
  token-in-URL exposure - a leaked token can no longer travel further via
  a Referer header), `Strict-Transport-Security`, and
  `Content-Security-Policy: default-src 'none'` on every route except
  `/docs`/`/redoc`/`/openapi.json` (FastAPI's own Swagger UI needs to
  load its own JS/CSS - a blanket CSP would break it).
- **Addendum (CodeRabbit pass on this block): "maximum document sizes"
  above was incomplete, real gap found and fixed.** FastAPI/Starlette
  parse the full request body - including resolving `File(...)` into an
  `UploadFile` for a multipart upload - INSIDE routing, before any route
  handler code runs at all. That means `_read_bounded`'s own "never
  materializes an unbounded upload in memory" claim only holds once
  control actually reaches it, which is already too late: Starlette has
  already spooled the entire body (memory, then a temp file past
  `SpooledTemporaryFile`'s threshold) for an upload of any size before
  `_read_bounded`'s chunked-read loop gets a single chance to reject
  anything. Fixed with a new `main.py::_max_body_size` middleware that
  checks the `Content-Length` header and rejects (413) before `call_next`
  - and therefore before routing/multipart-parsing ever starts - reusing
  `max_upload_size_mb` as a blanket per-request ceiling, not an
  upload-route-specific one. **Residual gap, filed as its own open item,
  not just a code comment:** this trusts the declared `Content-Length` - a
  client that lies, or omits it entirely via chunked transfer-encoding, is
  not caught here. See the separate `## [B15] Content-Length pre-check
  residual gap` entry below for the mechanism, what is and isn't caught,
  and the owner decision - not restated in full here to avoid two entries
  drifting apart on the same fact.
- **Status:** resolved-in-B15 for file-type validation, size enforcement,
  archive-bomb defenses, and secure headers. The multipart-buffering
  addendum's own fix is resolved for the honest-client case; its residual
  gap is open, filed separately, not folded into this "resolved" line.
- **Test:** `tests/integration/test_documents_api.py` - 5 new tests
  (content-sniffing mismatch, zip missing the required internal member,
  entry-count bomb, ratio bomb, bounded-read-rejects-oversized via a
  monkeypatched limit) plus one existing test's fixture fixed (
  `test_generic_octet_stream_content_type_is_accepted` previously used
  `b"PK\x03\x04 minimal"` - valid only because no real content check
  existed yet; replaced with genuine `openpyxl`-generated bytes, since
  the property that test actually probes - an unasserted Content-Type is
  accepted - is real and unrelated to content sniffing, but needs a
  genuinely valid file to prove it against now that sniffing exists).
  `tests/route/test_security_headers.py` - expanded from 3 to 5 tests
  (CSP exemption parametrized over `/docs`/`/redoc`/`/openapi.json`, all 5
  headers checked on both the exempt and the error-response case, not 2).
  `tests/route/test_max_body_size.py` - new file, 2 tests, added with the
  addendum above (a genuinely oversized real body is rejected with no
  route needing to exist; an ordinary small request is unaffected). Full
  suite: 510/510 passing. `uv run mypy`: 59 errors/12 files/239 scanned,
  unchanged from the canonical baseline.

---

## [B15] Content-Length pre-check residual gap - a lying declared size or chunked transfer-encoding still bypasses it; unassigned, not B16's build scope

- **Discovered:** CodeRabbit pass on this same block, while fixing the
  multipart-buffering gap named in the "File-type validation, size
  enforcement, archive-bomb defenses, secure headers - resolved" entry
  above (`main.py::_max_body_size`, the middleware built to fix it). This
  entry is that fix's own residual gap, filed on its own rather than left
  as a comment in `main.py`, so it has a real status and owner instead of
  needing to be rediscovered by reading code.
- **What the middleware catches:** `_max_body_size` reads the
  `Content-Length` header and rejects (413) before `call_next` runs -
  therefore before Starlette's own routing/multipart-parsing starts - for
  any request whose declared size exceeds `max_upload_size_mb`. This is
  the honest-client case: every real browser and HTTP client sends an
  accurate `Content-Length` for a request built from an in-memory/on-disk
  file, so this closes the practical DoS vector for ordinary use, with
  zero body bytes read.
- **What it does not catch, named precisely:** a client that (a) declares
  a small `Content-Length` and then sends more bytes than declared, or
  (b) omits `Content-Length` entirely via chunked transfer-encoding.
  Neither is inspected - this middleware only reads the header, never the
  actual byte count arriving on the ASGI `receive()` stream. Either path
  could still force Starlette to spool an arbitrarily large body (memory,
  then disk past `SpooledTemporaryFile`'s threshold) before anything in
  this codebase gets a chance to reject it - the exact resource-
  exhaustion shape the header check exists to prevent, reached by a
  different path.
  **Reach, stated precisely so severity isn't overstated:** every route
  this applies to requires authentication, and the actual upload routes
  additionally require a real RBAC permission (`upload_client_docs` or
  equivalent) - not an anonymous/public attack surface, it requires an
  already-vetted account with some organization's real access. Comparable
  in shape and severity to this same block's own archive-bomb residual
  gap (a crafted local-file-header/central-directory mismatch, also
  authenticated-only, also not built here, recorded in the entry above) -
  not treated as a P0 blocker for that reason, the same judgment already
  applied to that comparable gap in the same pass.
- **What closing it fully would require:** a wrapping ASGI `receive()`
  that counts bytes as they actually arrive and raises once a ceiling is
  crossed, mid-stream - a materially larger change than a header check.
  Nothing in this codebase's ASGI layer does that today; not built here.
- **Owner: unassigned, not silently attached to B16 by default.** B16
  ("Full Evaluation, Reliability, and Load Tests") is a gate/evaluation
  block - its own release gate reads "no P0 security issue remains" - not
  a block that names building new streaming-body enforcement as its own
  scope. Same reasoning this file's own "Cross-tenant route coverage"
  entry already applied to a structurally similar gap: a home that isn't
  real is worse than an honest unassigned. **Not fully separate from B16
  either, though:** whoever runs B16's "no P0 security issue remains"
  review should re-examine this specific gap on its own facts (the
  authenticated-only reach above, the comparable archive-bomb precedent)
  and make the real call then - accept as-is, or promote it to a block of
  its own - rather than this entry silently deciding that by omission.
- **Status:** open, unassigned, explicitly flagged for reconsideration at
  B16's own gate-check rather than left to be rediscovered there.
- **Test:** none needed for what isn't built - `tests/route/
  test_max_body_size.py` (see the entry above) covers exactly what the
  middleware does catch; it takes no position on this gap.

---

## [B15] Content/instruction separation - implemented (TODO(human)), rolled out to 10 call sites

- **Discovered/built:** B15, following directly from the file-type/size/
  archive-bomb pass above. Planning confirmed zero content/instruction
  separation existed anywhere - every `_render_*`/`user_prompt=` site
  across `agents/` concatenated document- or knowledge-base-sourced text
  directly into the prompt with only a bracket label, no delimiter or
  instruction distinguishing it from trusted framing text.
- **The wrapper:** `agents/prompts/untrusted_content.py::
  wrap_untrusted_content(label, content)` - the user's own implementation
  (`TODO(human)`, filled in this pass). Delimits with
  `<<<BEGIN_UNTRUSTED_CONTENT:label>>>...<<<END_UNTRUSTED_CONTENT>>>`,
  neutralizes every literal `<<<`/`>>>` occurrence in `content` before
  insertion (not just full-delimiter-string matches - the atomic
  building blocks, closing the class of attack where a document pads
  around or reconstructs the delimiter from fragments), and prepends one
  instruction sentence telling the model to treat the enclosed text as
  data. Verified directly: `tests/unit/test_untrusted_content.py` (4
  tests - ordinary content, a forged closing delimiter neutralized with
  the real one still unique, a forged opening delimiter for a different
  label, repeated delimiter fragments fully neutralized, not just the
  first occurrence).
  **One deliberate asymmetry, not a defect:** the instruction sentence
  appears once, before the content, not "sandwiched" after it too - some
  prompt-injection literature favors restating the instruction on both
  sides against recency effects. Cheaper on tokens (a real, named
  constraint in the function's own TODO(human) comment, given it runs on
  every chunk of every document); a real tradeoff, not an oversight.
- **Rolled out to 10 call sites** (of the ones identified during
  planning): `requirements/extract.py` (tender chunk text - the
  canonical, most directly attacker-reachable site), `requirements/
  verify.py` (requirement text + source chunk), `compliance/propose.py`
  (requirement text + evidence excerpts), `talent/extract_roles.py`
  (requirement text), `talent/score_candidates.py` (evidence excerpts
  only - short structured fields like title/location/availability left
  unwrapped, a deliberate token-cost judgment call, not an oversight),
  `knowledge/relationships.py` (resume text in `extract_worked_on`),
  `knowledge/extract.py` (resume chunk text and capability-statement
  chunk text, both call sites), `proposal/draft_section.py` (evidence
  excerpts), `redteam/critique.py` (requirement text; claim text too, as
  defense in depth even though it's this system's own drafted output,
  not raw document text), `commercial/explain_findings.py` (`finding.
  note` specifically - checked its real construction in
  `pricing_checks.py` rather than assumed from the "deliberately never
  puts real numbers in the prompt" docstring nearby, and found it embeds
  `row.description` - a free-text pricing-workbook cell - verbatim in at
  least one code path).
  **Deliberately left unwrapped, and why:** `knowledge/relationships.py`
  `_render_candidates`/`_render_capability_candidates` (the organization's
  own already-persisted canonical project/capability list, not raw
  document text - `extract_demonstrates`'s own docstring already states
  this explicitly: "not raw document text needing re-reading"); `proposal/
  draft_section.py`'s `section_name`/`section_guidance` (hardcoded
  `SECTION_TEMPLATES` constants, not document- or user-supplied).
- **Label-escaping gap - fixed on a later CodeRabbit pass over this same
  block.** `knowledge/extract.py`'s resume call site passes `chunk.
  heading_path` (joined) as the wrapper's `label` argument, not just
  `content` - `label` went into the opening delimiter tag unescaped.
  `heading_path` is parsed from the document's own heading text (DOCX
  heading styles), so it is not fully trusted either; a heading crafted
  as something delimiter-shaped (e.g. `Section >>>IGNORE ABOVE<<<`) could
  close the opening delimiter early. Originally left open deliberately
  (the wrapper's own contract wasn't part of the TODO(human) exercise,
  changing it then would have been second-guessing what was just built
  rather than a clean handoff) - independently confirmed by CodeRabbit,
  then fixed: `wrap_untrusted_content` now neutralizes `label` with the
  identical `<<<`/`>>>` substitution `content` already got. Verified by
  `tests/unit/test_untrusted_content.py::
  test_label_cannot_forge_a_closing_delimiter`.
- **Attack-fixture tests and the live-Gemini Part-P resistance script:**
  built and run later in this same B15 session, not left open - see the
  separate `## [B15] Attack-fixture tests and the live-model resistance
  script` entry above for the real result. This bullet previously said
  "not yet wired" - stale, corrected here rather than left standing next
  to an unrelated edit to this same entry.
- **Status:** resolved for the 10 call sites listed, including the
  label-escaping gap as of the CodeRabbit pass above.
- **Test:** `tests/unit/test_untrusted_content.py` (wrapper correctness,
  now 5 tests including the label-forgery regression), plus every
  existing integration/route/node test that exercises these 10 call
  sites now runs the real wrapper, not a bypass (`FakeLLMClient` never
  inspects prompt content, confirmed directly rather than assumed, so
  wrapping changed no test's expected LLM output). Full suite as of the
  CodeRabbit pass: 510/510 passing.

---

## Standing pattern: a safety net attached to a fallback goes dark the moment something real replaces the default

- **Discovered:** B16, while fixing the "[B12] Neither app calls
  `logging.basicConfig()`" entry above. `install_log_redaction`'s own
  docstring (written in B15) had already named the exact risk in writing,
  in advance, before this pass existed to trigger it: "once real logging
  configuration exists anywhere in the hierarchy... this filter's
  redaction stops applying with no error, no warning, nothing to notice
  at the moment it happens." That's the unusual part worth recording
  about *this* instance - most entries in this file discover their own
  failure mode empirically first and explain it afterward; this one was
  predicted correctly, in writing, a full block ahead of the fix that
  would have silently triggered it.
- **The general shape, stated precisely so a second instance is
  recognizable on sight, not just this one:** some safety/cross-cutting
  mechanism M (a filter, a check, a guard) is wired to a *fallback* - a
  handler, a path, a default - that the runtime only actually reaches in
  the *absence* of something more specific claiming that role. M works
  correctly, and stays working, for as long as nothing specific ever
  shows up. The moment something real and legitimate *does* claim that
  role (a real log handler configured, in this instance), the runtime
  stops routing through the fallback at all - not because M broke, M is
  never consulted again. No exception, no log line, nothing structurally
  different about the moment it happens; the only way to notice is to
  already know to check.
- **Why this is a distinct mechanism from this file's other standing
  patterns, not a restatement of one:** the "reader trusts a column its
  writer never keeps current" pattern (above) is about a value going
  stale *at rest* because nothing re-derives it. This is about a
  mechanism going *unreached* because something else now occupies the
  slot it depended on - the mechanism itself never changes at all, its
  entire input (being called) simply stops arriving. Closer in shape to
  the environment-traps pattern's own incident 6/8 (a container reporting
  itself healthy by an internal measure while being unreachable by an
  external one) than to the stale-column family - both are "two
  independent things that happened to move together, until they didn't."
- **The one confirmed instance, and its fix:** `RedactSensitiveDataFilter`
  was attached to `logging.lastResort` (B15) - the correct, only available
  choice at the time, since nothing called `logging.basicConfig()`
  anywhere and `lastResort` was genuinely the one shared object every
  unconfigured logger's WARNING+ output flowed through. B16's own fix for
  the silent-`.info()`-dropping gap necessarily configures a real handler
  - and by the mechanism above, would have silently stopped protecting
  anything the moment it shipped, if the two had been fixed as two
  separate calls at three entrypoints rather than one. Closed by
  `configure_logging()` (`core/log_redaction.py`): `basicConfig()` and the
  filter re-attachment collapsed into one function, so the ordering
  dependency can't be split across call sites again by a future edit that
  only touches one of them.
- **What to check for a second instance, not yet confirmed as one:**
  `configure_logging()`'s own filter-attachment loop iterates
  `logging.root.handlers` - correct for what `basicConfig()` itself
  installs, but unverified against whatever uvicorn's own logging setup
  does independently (uvicorn is known, in general, to configure its own
  `uvicorn.access`/`uvicorn.error` loggers, which may or may not route
  through `root` depending on `propagate` settings this codebase has
  never inspected). Not chased down here - flagged as the first place to
  look if a request-level log line is ever found NOT to carry redaction
  in a real deployment, rather than re-deriving this pattern from
  scratch.
- **Status:** named from one confirmed instance, not yet "confirmed as a
  pattern, not a one-off" the way this file's other five standing
  patterns are - each of those was named only once a second, independent
  instance turned up. This entry exists anyway, ahead of a second
  instance, because the shape is general enough (and the predicted-in-
  writing character of this one instance sharp enough) to be worth
  recognizing early rather than waiting to rediscover it under a
  different name next time. Revisit this status once a genuine second
  instance is found - or leave it exactly this honest if none ever turns
  up.
- **Test:** none - a design/process pattern, not a behavior. The one
  instance's own fix is verified directly (see the `logging.basicConfig()`
  entry above): a live check confirms `RedactSensitiveDataFilter` is
  attached to the real handler `basicConfig()` installs, not to
  `lastResort`, and that it still redacts correctly there.

---

## [B16, owner B17, RESOLVED - with one coverage gap found by the same sibling sweep as the CommercialFindings measurement-bug entry] `Login.tsx`'s "already logged in" guard drops the user's original destination on any transient `/auth/me` failure

- **Discovered:** B16, root-causing a live E2E run's own setup-phase
  failure - a Playwright poll stuck showing the Bids dashboard instead of
  `/talent`. The trigger was harness-shaped (`full-flow.spec.ts`'s own
  unthrottled `reloadAndSettle()` polling burst past B15's rate limit -
  a real, confirmed burst of 24 consecutive 429s on `GET /auth/me`,
  `docker logs tenderos-api-1`) - but the mechanism that turns one
  transient failure into a *permanent* navigation loss is real, deliberate
  app code, not a harness defect, and would fire for a real user hitting
  the same failure by any other cause.
- **Behaviour:** `AuthContext.tsx`'s `refresh()` sets `meResponse` to
  `null` on *any* `me()` failure - a network drop, a 500, a 429, not only
  a genuine 401/403 (the access/refresh token itself is correctly kept in
  every case except a real 401/403, matching that function's own comment's
  stated intent - but `meResponse` still goes `null` regardless of cause).
  `RequireAuth` treats `null` as "not logged in" and redirects to
  `/login`. Once a later `/auth/me` call succeeds (the token was valid the
  whole time), `Login.tsx`'s own top-of-render guard
  (`if (meResponse) return <Navigate to="/" replace />`) sends the user
  back to the fixed default route `/`, not back to wherever they actually
  were - nothing captures or restores the original pathname across this
  round trip.
- **Risk, for a real user, not just this harness:** a deep link into any
  of this app's tender-scoped screens (`RequirementRegister`,
  `ComplianceMatrix`, `TeamWorkspace`, ...), sitting behind a transient
  `/auth/me` failure - a rate limit, a brief API restart, a network blip,
  none of which mean the session is actually invalid - gets silently
  bounced to the Bids dashboard with no error message and no path back
  except manually renavigating. Confirmed reachable in this exact
  codebase this session, not hypothetical - the harness made it fire
  reliably, it didn't invent the mechanism.
- **Why not P0/B16:** doesn't cross a tenant boundary (the redirect only
  ever lands the same authenticated user on their own dashboard), doesn't
  violate an invariant, doesn't block any of the PRD's 24 completion
  scenarios outright (a user can still get back manually). A real
  reliability/UX rough edge, not a security or correctness defect any of
  the release gate's own five conditions name. Same shape and severity
  class as the `CommercialFindings.tsx` stale-write-guard entry above -
  owned by B17 for the identical reason: no block between B16 and B17 has
  a stop-gate that touches this, and it's exactly the class of finding a
  general frontend-correctness sweep should catch systematically rather
  than one screen at a time.
- **Status:** RESOLVED, B17 Phase 1 - built exactly as the candidate fix
  above described: `RequireAuth` now passes `state={{ from: location.
  pathname + location.search + location.hash }}` on its redirect (fragment
  added after a CodeRabbit catch of its own - see below), and `Login.tsx`
  reads `location.state.from` once, used in both the places it needs
  somewhere to send the user back to.
- **Coverage gap found by the same sibling sweep that produced the
  CommercialFindings measurement-bug entry above:** `from` is read ONCE
  but used in TWO places in `Login.tsx` - the top-of-render "already
  logged in" guard (`<Navigate to={from} replace />`) and `handleSubmit`'s
  own `navigate(from, { replace: true })` after a real login/register
  submission. `destinationPreservation.test.tsx`'s three tests all drive
  the FIRST path only (a harness button that flips `meResponse` directly,
  standing in for a recovered `refresh()`) - none of them ever fills the
  form and submits it, so `handleSubmit`'s own `navigate(from, ...)` call
  has never been exercised by any test. Both call sites read the exact
  same `from` value computed the same way, so this is a real but low-risk
  gap (a bug in one would very likely show up in the other) rather than a
  second hidden defect the way CommercialFindings' catch-block gap was -
  logged for the same reason that entry was: a passing test suite implied
  more coverage of this fix than actually exists. Not fixed this pass;
  worth a fourth test (fill the form, submit, assert the deep link is
  where `handleSubmit` navigates to) whenever this file is next touched.
- **Test:** `destinationPreservation.test.tsx` - three tests, all via the
  render-guard path (including one added this session for URL-fragment
  preservation, CodeRabbit-caught); the `handleSubmit` path is untested,
  per the gap noted above.
- **A second live run, after throttling the E2E spec's own reload rate to
  a 2s floor, confirms this is about recovery, not about rate-limit
  tuning.** The first run's 429 burst was 24 consecutive requests; the
  throttled second run's burst was 3 - an 8x reduction - and it bounced
  the exact same way, stranding the browser on `/` for the rest of that
  poll's budget regardless. **Three requests were enough.** No amount of
  further throttling closes this on its own, because the defect isn't
  "too many requests trigger the limit" - it's that hitting the limit
  even once, for a single request, has no recovery path anywhere in the
  app. The E2E spec's own workaround (`reloadAndSettleOnRoute` in
  `full-flow.spec.ts`, B16) detects the bounce and manually re-navigates
  past it, bounded and loud rather than silent - real, working scaffolding
  for THIS test, not a fix for the underlying guard, and it should come
  out once B17 actually fixes `Login.tsx`.

---

## Standing pattern: this frontend has no recovery path from a single transient request failure

- **Discovered:** B16, two independent instances found in the same
  session, at two unrelated call sites - named as a pattern once the
  second one turned up, not assumed from the first alone.
  1. `AuthContext.tsx`'s `refresh()` (the entry above) - any `/auth/me`
     failure, not only a real 401/403, sets `meResponse` to `null`,
     stranding the user on the dashboard once a later request happens to
     succeed, with no memory of where they actually were.
  2. `GateDecisionPanel.tsx`'s `handleDecide` - any `decideGate()`
     failure sets an `error` message and leaves the component in its
     `pending` render branch. Found live: a real E2E run's own resource-
     selection gate approval hit an isolated 429 (a CORS preflight the
     rate limiter was, at the time, still counting - see the "OPTIONS
     preflight" fix elsewhere in this file), the click never resulted in
     a recorded decision, and the "Approve" button simply stayed there,
     unresponsive to anything but a manual retry.
- **The shared shape, stated once so a third instance is recognizable on
  sight:** a real, transient failure - a rate limit, a network blip, a
  brief backend hiccup - is handled correctly in the narrow sense (caught,
  not crashed, a real `error` state or `null` set) but with no path back
  to the state the user was actually in. The component doesn't retry, doesn't
  preserve context across the failure, and doesn't distinguish "this
  session is genuinely over" from "one request happened to fail." Both
  instances are one-shot: a single `try`/`catch` around one `fetch`-
  shaped call, no retry, no backoff, no memory of intent.
- **Why this is a pattern about the frontend, not two isolated bugs:**
  neither instance is a logic error - both correctly detect and report
  their own failure. The gap is architectural: nothing in this codebase's
  own data-fetching conventions (no shared hook, no wrapper around
  `client.ts`'s calls) provides retry-with-recovery as a default any
  component gets for free. Every call site that wants resilience has to
  build its own, and two independent call sites, built at different
  times by different reasoning, both didn't.
- **Status:** RESOLVED, B17 Phase 1 - built exactly the shared, tested
  mechanism this entry's own "Test" note below called for:
  `api/retryTransient.ts`, a plain `() => Promise<T>` wrapper with a
  retryable-status allowlist and bounded exponential backoff, wired into
  both call sites named above. The E2E spec's own workarounds for both
  instances have been removed now that the real fix exists.
- **Coverage asymmetry found by the same sibling sweep that produced the
  CommercialFindings and Login.tsx entries above, closed the same
  session:** the two call sites did NOT have equal test coverage of the
  real wiring. `GateDecisionPanel.test.tsx` renders the real component
  with the real `retryTransient` (not mocked) and drives both a
  retry-then-succeed case and the 409-is-terminal case - genuine coverage
  of instance #2. Instance #1 (`AuthContext.tsx`'s `refresh()`) had none:
  the only test file that touched this bug (`destinationPreservation.
  test.tsx`) explicitly stands in for `AuthContext` with a hand-rolled
  `AuthContext.Provider` harness (its own comment says so directly)
  specifically so it doesn't depend on `retryTransient`'s timing - which
  meant the real `AuthProvider` component, and its real `retryTransient
  (() => me())` call, had never been rendered or exercised by any test in
  this repo. Worse than the Login.tsx gap above (that one had the SAME
  call site tested via a different path; this one had zero tests touching
  the real production code at all). **Closed:** `AuthContext.test.tsx`
  (new) renders the real `AuthProvider`, mocks only `me()`, and drives the
  real backoff with fake timers - a transient 500 that recovers on retry,
  and a 401 that terminates on the first attempt with no retry. Verified
  against a real regression, not assumed: temporarily reverting `refresh()`
  to a bare `await me()` (no `retryTransient`) made the transient-recovery
  test fail exactly as expected (renders "logged out" instead of
  "logged in," since a lone 500 still reaches the unconditional
  `setMeResponse(null)` in the catch block) - and surfaced a real,
  separate test-isolation bug in the process: `vi.clearAllMocks()` in
  `afterEach` does not clear a queued-but-unconsumed `mockResolvedValueOnce`,
  which leaked into the next test once the broken code stopped consuming
  it. Fixed by switching to `vi.resetAllMocks()`. The 401 test passes
  either way (401's terminal handling predates `retryTransient` and isn't
  affected by removing it) - expected, and still worth keeping: paired
  with the transient-recovery test, it pins the terminal/retryable
  contract at this specific call site, which is what was actually asked
  for.
- **Test:** `GateDecisionPanel.test.tsx` covers instance #2 directly;
  `AuthContext.test.tsx` now covers instance #1 directly. Both real call
  sites verified, not just the shared `retryTransient.ts` unit tested in
  isolation.

---

## [B12 escape, owner: B17] Part K's 21st and 22nd components - "audit timeline" and "cost and trace views for developers/admins" - were named by B12, never picked up by B13's screen list

- **Discovered:** B17 planning, cross-checking Part K's 20-item component
  list against what's actually built. `AuditTimeline` (Part K,
  `TenderOS-Build-Guide.md:3815`, 21st in that list - `ImpactPreview` is
  20th, not `AuditTimeline` as B17's own planning prompt first said) has
  no frontend presence anywhere: grepped `apps/web/src` for
  `AuditTimeline` and for any client-side caller of
  `GET /workflow-runs/{run_id}/events` (the real endpoint,
  `routes/workflow.py:201`, returning `AuditEventResponse[]`) - zero
  matches, either search. The backend half exists and is real; nothing on
  the frontend reads it.
- **Traced to its actual origin, not just its absence:** Build Guide
  line 3430 lists "audit timeline" as a **B12** (Readiness and Blockers)
  deliverable, immediately followed at line 3431 by its sibling, "cost
  and trace views for developers/admins." B12 itself has no UI block of
  its own, and B13's "Screens" list (lines 3467-3483, the 17 screens
  `Login.tsx`'s own comment counts) never names an audit or cost/trace
  screen anywhere in it. B12 named both deliverables; nothing downstream
  ever turned either into a screen. Classic sibling-drift shape - a
  deliverable that escapes not because it was rejected, but because the
  block that named it had nowhere of its own to build UI, and the next
  block that owns UI never re-derived the list from B12's own text.
- **The sibling is in worse shape, checked directly rather than assumed
  from the shared paragraph:** grepped `apps/api/src/tenderos_api/routes`
  for `agent_traces`, `AgentTrace`, `/costs`, `/traces`, `cost_usd` - zero
  matches. `AgentTraceRepository.sum_cost_for_run`
  (`db/repositories/agent_traces.py:63`) and the `AgentTrace` rows
  themselves (`input_tokens`/`output_tokens`/`estimated_cost_usd`,
  `db/models/agent_trace.py:37-49`) are real and already populated by
  every one of the 10 agent modules' own `_write_trace` calls - but
  unlike the audit timeline, there is no API route at all, not just a
  missing frontend screen. "Cost and trace views for developers/admins"
  is missing at both layers.
- **Risk:** low on its own (nothing downstream depends on either screen
  existing), but relevant to two things already in B17's own scope: B17's
  Grafana dashboard 4 ("LLM cost and token usage") covers the same
  `AgentTrace` data at the operator/infra layer, which is a different
  audience than "developers/admins" reviewing cost per-tender or
  per-agent inside the product itself - closing the Grafana dashboard
  does not close this gap, the two are complementary, not substitutes.
  And B17's own scope already includes `AuditTimeline` as new work (see
  the UI-polish plan), so this entry exists to record *why* it was never
  built, not to argue it should be.
- **Status:** open, owner B17. Whether B17 actually builds either screen
  is a scope decision for B17 to make explicitly, not something this
  entry decides - Part N line 3931 accepts "limitations documented
  without inflated claims" as a valid close for a deliverable a block
  chooses not to pick up, provided it's written down rather than silently
  dropped a second time.
- **Test:** none - a provenance finding about a missing UI surface, not
  application behavior.

---

## [owner: B17, scope decision pending] A3.2/Part F's typed tool layer - specified for organization authorization at a single choke point, never built

- **Discovered:** B17 planning, investigating whether Part F's tool-
  registry premise (Agent -> Tool Registry -> Authorization Context ->
  Domain Service, F1) matches this codebase's actual architecture.
- **Corrected finding, not the original framing.** An earlier pass of this
  same investigation read Part F as assuming LLM-directed tool-calling and
  concluded this codebase's actual architecture (single-shot structured
  output, no `tools=`/`function_call` anywhere in `agents/llm/`) simply
  never needed it. Checked directly before accepting that: grepped both
  `TenderOS-Build-Guide.md` and `TenderOS-PRD.md` for "tool-calling",
  "function calling", "tool_choice," and every phrasing of an agent
  choosing or selecting a tool - zero hits in either document. Part F
  never specifies LLM-directed selection. F1 (line 2323) says "Agents
  invoke tools directly inside the Python application process" - in-
  process and direct, consistent with A3.3 excluding MCP immediately
  after. An "agent" in this codebase's own vocabulary is a module
  (`agents/knowledge/`, `agents/compliance/`, ...), not an LLM turn
  choosing among options - F1-F5 describe exactly the call pattern this
  codebase actually uses (deterministic Python calling a fixed
  operation), not a pattern it doesn't have. The original framing would
  have retired a locked authorization requirement on the strength of an
  architecture the spec never asked for.
- **The actual load-bearing line, A3.2 (Build Guide line 246):** "The
  tool layer applies organization authorization and calls domain
  services directly." The layer's specified job is organization
  authorization at one choke point in front of F3's seven operations,
  independent of who the caller is. F5's "Validate organization and
  tender ownership on every invocation" and "Reject tools not present in
  the current agent's allowlist" are both satisfiable by deterministic
  callers - nothing about either requirement needs an LLM in the loop.
- **Confirmed via call-site reading, not inferred from a module being
  empty:** none of F3's seven operations goes through any single choke
  point. `search_company_knowledge`, `query_bid_impact`/
  `query_bid_contradictions`, `evaluate_evidence_coverage` are called
  directly from deterministic orchestration nodes/worker jobs, trusting
  `state["tender_id"]`/`organization_id` as passed-in arguments;
  `validate_pricing_document` and the functionality backing the
  traceability screens are reachable only from human-facing FastAPI
  routes. No `TenderTool`, `ToolContext`, `ToolRegistry`, or enum-
  controlled dispatch exists anywhere in `packages/tenderos_backend/src`.
- **The follow-up question, resolved directly rather than left open: does
  the missing choke point leave a reachable tenant-isolation hole?**
  Checked the one thing that actually settles it - every path that
  CREATES a `WorkflowRun` (the source of the `state["tender_id"]`/
  `organization_id` deterministic orchestration code trusts). Exactly one
  exists: `POST /tenders/{tender_id}/workflow-runs`
  (`routes/workflow.py:149`), gated by `require_permission("create_bid")`.
  `require_permission`'s own `_resolve_organization_id`
  (`dependencies.py:114`) resolves `organization_id` from the REAL tender
  record via `tender_service.get_tender(tender_id).organization_id` - not
  a client-supplied value - before `_check_permission` verifies the
  caller has a permitted membership in THAT organization. The route
  handler independently re-derives the same `tender.organization_id` a
  second time (`routes/workflow.py:160`) and that's what actually gets
  stored on the `WorkflowRun`/audit row - not a comparison of a value
  against itself, the specific "guard that looks complete" shape this
  file's other five standing-pattern instances warn about; the org_id
  here comes from an independent DB lookup, checked against a real
  membership row. No worker-initiated creation of a `WorkflowRun` exists
  anywhere (`apps/worker/src` grepped for `create_workflow_run`: zero
  matches - `run_workflow`/`resume_workflow`/`retry_workflow_run` in
  `jobs/workflow.py` all operate on an EXISTING `run_id`, never create
  one). `reanalysis.py`'s `reanalyze_knowledge_selection_change` - the
  third candidate path checked - does not create a `WorkflowRun` at all;
  read in full, it's a separate mechanism entirely (compliance
  reevaluation, claim regrounding, graph-contradiction detection),
  triggered by `KnowledgeSelectionChanged`, never touching
  `workflow_runs`.
- **What this settles, and what it doesn't.** The deterministic
  orchestration/worker code's inherited `state["tender_id"]`/
  `organization_id` trust IS sound for every workflow-run-initiated call
  site (`compliance_analysis_node` and its siblings) - a defence-in-depth
  gap, not a reachable defect, the legitimate documented-close shape Part
  N line 3931 names. The human-route-reachable F3 operations
  (`validate_pricing_document`, `query_bid_impact`/`query_bid_
  contradictions` via `routes/graph.py`, etc.) are separately protected
  the same correct way, via `_resolve_organization_id`'s own per-resource
  branches (`pricing_document_id`, `tender_id`, ...) checked at the route
  layer, not inside the "tool" itself, but checked. **What remains
  genuinely unmet, not closed by this finding:** F5's other two
  requirements - "Record tool name, latency, status, and referenced IDs"
  (no invocation-level audit trail exists for any of the seven F3
  operations; `AgentTraceRepository` records LLM calls, not these) and
  "Reject tools not present in the current agent's allowlist" (no
  allowlist exists in any form, confirmed directly - so nothing today
  would catch a FUTURE call site that forgets the equivalent of
  `require_permission`, the way the current seven all happen to remember
  it). That's the real, still-open half of this finding.
- **Status:** the authorization sub-claim is closed as sound (verified,
  not assumed) - a defence-in-depth gap, Part N line 3931-eligible. The
  observability (F5's audit trail) and allowlist sub-claims are open.
  Whether B17 builds the formal tool layer A3.2 specifies, or accepts the
  current per-route/per-creation-path protection as the permanent shape
  and documents why, is a scope decision for B17 to make, not something
  this entry decides.
- **Test:** none - a provenance and call-site finding, not new
  application behavior.

---

## [B17, resolved] RQ's own JobTimeoutException bypassed run_workflow's except block, leaving a run stuck "running" forever - fixed narrowly, two residuals documented rather than built

- **Discovered:** B17 planning's own live E2E run. A worker job died with
  `rq.timeouts.JobTimeoutException: Task exceeded maximum timeout value
  (1800 seconds)` after ~28 minutes inside a real, sustained Gemini `503`
  outage. Queried the actual `workflow_runs` row directly, not inferred:
  `status='running'`, `completed_at=NULL`, `retry_count=0`, hours after
  the job died. Worker logs for that run_id (3 lines total, confirmed
  nothing missed): the enqueue log, `"starting workflow run ..."`, and
  RQ's own `"exception raised while executing"` - no
  `"workflow run ... failed"` line, which is the literal text
  `run_workflow`'s own `except Exception` block would have logged had it
  run.
- **Root cause, traced to the actual mechanism, not assumed:** RQ's death-
  penalty timeout (`rq/timeouts.py::UnixSignalDeathPenalty`, `SIGALRM`)
  wraps the WHOLE `job.perform()` call (`rq/worker/base.py::perform_job`).
  For a directly-enqueued async function, `rq/job.py::_execute` detects
  the coroutine and calls `loop.run_until_complete(result)` ITSELF - so
  when the signal fires, it interrupts asyncio's own event loop (traced to
  `_run_once`/`selectors.select`, deep inside RQ's own `job.py`), not any
  awaited line inside `run_workflow`'s own `try` block. Nothing of this
  codebase's own code is on the call stack at that moment, so its
  `except Exception` never runs.
- **Fix:** `apps/worker/src/tenderos_worker/jobs/workflow.py::_run_sync`
  is now the real RQ job entrypoint (`run_workflow_job`/
  `resume_workflow_job`/`retry_workflow_run_job`, wired in
  `outbox_dispatcher.py`'s dispatch functions and `_handle_graph_failure`'s
  own `enqueue_in` call) - it owns the sync-to-async transition itself
  (`asyncio.run(coro)`) instead of leaving it to RQ, which puts a real
  stack frame of this codebase's own in the path a `JobTimeoutException`
  unwinds through. Deliberately narrow: catches ONLY
  `JobTimeoutException`, not a bare `except Exception` - every other
  failure mode is already handled correctly inside
  `run_workflow`/`resume_workflow`/`retry_workflow_run`'s own try/except,
  and re-raising those unchanged is what keeps them in RQ's own
  `FailedJobRegistry` (I4: "failed jobs remain inspectable"). On a real
  timeout, `workflow_service.mark_run_failed_on_timeout` marks the run
  `"failed"` and writes a distinguishing audit event
  (`action="workflow_run_timed_out"`, `metadata={"error_code":
  "job_timeout"}`) - deliberately distinct from a normal graph failure,
  which writes no audit event describing why at all, so this one is
  unique to this path by construction, not by a shared "failed" status
  alone. Verified with real Postgres, not asserted from reading the code:
  `tests/integration/test_workflow_timeout_handling.py`, 4 tests,
  including one that drives `_run_sync` with a plain `ValueError` to
  confirm it does NOT touch the run for any failure mode but the one it's
  scoped to.
- **A second, real bug found while writing the test for this, not assumed
  away:** `db/session.py`'s `engine` is a module-level singleton whose
  connection pool holds connections bound to whichever event loop created
  them. `_run_sync`'s own two sequential `asyncio.run()` calls (the main
  run, then the timeout-recovery write) reuse that same engine across two
  different loops - reproduced directly, not hypothesized: without a fix,
  the second call raised `RuntimeError: ... attached to a different loop`
  from deep inside asyncpg's own connection-cleanup path. Fixed by calling
  `await engine.dispose()` (SQLAlchemy's own documented remedy for reusing
  an async engine across more than one `asyncio.run()` in the same
  process) before the recovery write. See the entry immediately below -
  scoped precisely, not broadly, to this one call site: RQ forks a fresh
  process per job, so this exact double-call sequence is the only place
  in this codebase two `asyncio.run()` calls ever share a process.
- **Residual 1 - not a defect, a genuinely separate finding: a "failed"
  workflow run, from ANY cause, is not resumable.** Grepped every real
  (non-test, non-docstring) caller of `retry_workflow_run`: exactly one,
  `_handle_graph_failure`'s own `enqueue_in`, fired only at the moment of
  a failure inside `run_workflow`/`resume_workflow`/`retry_workflow_run`
  themselves. No API route, no manual trigger, nothing resumes an already-
  `"failed"` run on demand, for this failure or any other kind this
  codebase has ever produced. Part N line 3918 ("the workflow pauses and
  resumes after process restart") is a different guarantee - a still-
  active run (`queued`/`running`/`waiting_for_human`) surviving a worker
  process restart via LangGraph's own Postgres checkpointer, not "can a
  human retry a run already marked failed." Pre-existing, not introduced
  or closed by this fix.
- **Residual 2 - named plainly, not built: SIGKILL, OOM, and container
  restart mid-job are NOT covered.** `_run_sync`'s catch only works
  because the process survives long enough for RQ's signal-based timeout
  to raise a normal, catchable Python exception. A SIGKILL or an OOM kill
  ends the process outright - no exception, no except block runs, nothing
  persists the failure. A run killed this way is left exactly as before
  this fix: stuck at whatever status it last had, forever. No
  reconciliation job exists anywhere in this codebase to sweep a run
  stuck this way (checked directly - grepped for any staleness/sweep
  mechanism, found none). Not building one at this block, per B17's own
  scope; a real Phase 4 consideration for a long-lived single-worker demo
  VM, not resolved here.
- **Connection to Phase 3:** this timeout is now a real, honest way to
  produce a genuine `agent_failures_total`/queue-health-dashboard non-zero
  value from a seeded case, without contriving one - see the metrics-
  split note for Phase 3.
- **Test:** `tests/integration/test_workflow_timeout_handling.py` (4
  tests, real Postgres, no LLM cost) plus the two dispatch-routing tests
  updated to expect the new `*_job` function names
  (`test_outbox_dispatcher.py`, `test_workflow_retry.py`).

---

## [B17, resolved, scope corrected] db/session.py's module-level async engine is not safe to reuse across two asyncio.run() calls in the same process - one real call site, not a standing pattern

- **Discovered:** B17, writing a real test for the JobTimeoutException fix
  above. Every OTHER test in this suite runs inside pytest-asyncio's own
  session-scoped shared event loop (`asyncio_default_test_loop_scope=
  session` in `pyproject.toml`) and never calls `asyncio.run()` at all -
  which is exactly why this has never surfaced before now, not because it
  doesn't exist. The two new sync-context tests (deliberately not
  `@pytest.mark.asyncio`, matching how RQ actually calls a job function -
  from a plain synchronous context with no loop running) are the first
  code in this test session to create more than one real event loop, and
  hit it on the very first boundary: a plain setup call raised
  `RuntimeError: ... attached to a different loop` from asyncpg, deep
  inside `SQLAlchemy`'s own pool connection-cleanup path
  (`_close_connection` -> `do_terminate` -> asyncpg trying to create a
  cancellation `Task` on a `Loop` that's already closed).
- **First written up as a standing pattern, across every consecutive job
  pair a worker processes - wrong, corrected the same session, before it
  shipped as a false breadth claim.** The original reasoning: "RQ creates
  a new event loop for every job, so a long-lived worker has always been
  reusing one module-level engine across consecutive jobs." Checked
  directly against RQ's own source before letting that stand, not assumed
  from the `_execute` code alone: `rq/worker/worker_classes.py`'s default
  `Worker.execute_job` (lines 148-155) calls `self.fork_work_horse(job,
  queue)`, which calls `os.fork()` (line 63) - confirmed the same way the
  résumé-name counter bug already demonstrated empirically (this file's
  own job-timeout entry). Every job runs in its own freshly forked CHILD
  PROCESS, not a shared long-lived interpreter - `db/session.py`'s
  `engine` singleton is re-created from scratch on import in every child,
  so there is NO shared engine state across consecutive jobs at all, ever.
  The "latent across every job pair" framing was a real, wrong breadth
  claim, not a cautious generalization - the same "prose asserting a
  wiring that doesn't exist" shape this file has now caught four other
  times, corrected here before becoming a fifth.
- **The actual exposed set, established by reading every `asyncio.run(`
  call site in the codebase (grepped directly, not sampled): exactly
  one.** `_run_sync`'s own timeout-recovery branch
  (`apps/worker/src/tenderos_worker/jobs/workflow.py:312,329`) - and only
  reached within the SAME forked child process, when that one job's own
  execution raises `JobTimeoutException`. Every other call site in this
  codebase calls `asyncio.run()` exactly once per process, checked
  individually: five scripts (`run_eval.py`, `prune_checkpoints.py`,
  `run_compliance_eval.py`, `verify_req073_074_divergence.py`,
  `verify_prompt_injection_resistance.py`, `run_retrieval_eval.py`) each
  call it once and `sys.exit()` immediately after;
  `outbox_dispatcher.py::main` calls it once, wrapping an infinite loop
  that runs for the whole process lifetime; `db/migrations/env.py`
  (Alembic) calls it once per one-shot CLI invocation; every OTHER RQ job
  in this codebase (ingestion, verification, knowledge_import, reanalysis,
  and `_run_sync`'s own SUCCESS path) calls it exactly once, since none of
  them has a second, conditional call the way the timeout-recovery branch
  does.
- **The fix stands, unchanged, now correctly scoped:** `AsyncEngine.
  dispose()` before the recovery call - SQLAlchemy's own documented remedy
  - applied at the one real call site above and at the new test's own
  loop boundaries (which introduce additional `asyncio.run()` calls this
  test file's own harness needs, not production code).
- **Status:** resolved, scope corrected in the same pass rather than left
  standing as a wider claim. Not a standing pattern - one call site, fixed.
  A future `"attached to a different loop"` error elsewhere in this
  codebase would need its own diagnosis, not an assumption that this same
  mechanism explains it, since the fork-per-job model means most code
  paths structurally can't hit this shape at all.
- **Test:** the 4 tests in `test_workflow_timeout_handling.py` exercise
  the one real call site directly (each test's own loop boundary
  explicitly resets the engine, commented with why) - no dedicated
  regression test for the underlying engine behavior itself, since
  reproducing it outside this specific multi-loop shape isn't a
  documented product requirement to test for its own sake.

## [found during B17, owner: NOT B17] `workflow_runs` status transitions: two more read-then-write races and a design question, sitting next to two now-correct atomic instances in the same file family

- **Discovered:** B17, auditing every status transition on `workflow_runs`
  after fixing `mark_run_failed_on_timeout`'s own race against
  `cancel_workflow_run` (the timeout entry above). Fixing one instance
  and then checking whether it was the only one is what this file's own
  standing-pattern discipline asks for - it found two more.
- **Instance A - the shared exception handler in `apps/worker/src/
  tenderos_worker/jobs/workflow.py`, three call sites
  (`run_workflow`/`resume_workflow`/`retry_workflow_run`):**
  ```python
  # Re-checking here, not just before, is what stops "failed" from
  # silently overwriting a real, deliberate cancellation.
  if not await is_cancelled(run_id):
      retried = await _handle_graph_failure(run_id, exc)
      if not retried:
          await sync_run_status(run_id, "failed")
  ```
  That comment describes a working guard - and reads as one, which is
  exactly why it survived: `is_cancelled` is a real, correct check, and
  the surrounding prose says plainly that re-checking here (not just at
  entry) closes the gap. It doesn't. `is_cancelled` and `sync_run_status`
  are two separate awaits, not one atomic statement - a
  `cancel_workflow_run` call landing in the window between them still
  gets silently overwritten to `"failed"`, the identical bug
  `mark_run_failed_on_timeout` had, just a narrower window (one await
  gap, not a whole graph execution). The comment asserts a wiring
  (race-closed) that the code one line below it doesn't actually provide
  - **the fourth instance of this file's own "a comment asserting a
  wiring that doesn't exist is invisible to grep" standing pattern**
  (see that entry above): a plain race-hunting grep doesn't flag this,
  because the code direction (check, then act) looks exactly like a
  closed race unless the two-statement gap is noticed by reading it as
  two separate awaits, not one guarded write.
- **Instance B - `cancel_workflow_run` (`workflow_service.py`):**
  ```python
  if run.status in _CANCELLABLE_STATUSES:
      await uow.workflow.update_run_status(run_id, "cancelled")
  ```
  Same shape: a run completing or failing independently between this
  read and this write still gets force-cancelled over its real, later
  outcome.
- **The pattern across the file, stated as the finding it is:**
  `mark_failed_if_not_terminal` (this session) and
  `try_reserve_retry_budget` (`568755e`, CodeRabbit-caught) both landed
  on the same atomic-conditional shape - `UPDATE ... WHERE <status
  guard> RETURNING id` - but only after a live bug forced each one
  individually. Instances A and B above are the same file's own
  `WorkflowRepository`/`jobs/workflow.py` pair, still on the older
  read-then-write shape, sitting right next to two call sites that
  already prove the correct pattern compiles, tests, and works in this
  exact codebase. **This is sibling drift with the fix already present
  in the same file - the seventh instance of "sequential retry tested,
  genuine concurrency untested"** (see that entry above): two of five
  `workflow_runs` status-writing call sites are now atomic, three are
  not, and nothing forces the other three to notice the two that are.
- **`sync_run_status` itself - a design question, not a bug with a fix
  attached:** it's an unconditional writer with no precondition of its
  own; every guarantee about not overwriting a terminal status is
  distributed across whichever caller checks `is_cancelled` (or nothing)
  before calling it. Making `sync_run_status` itself status-guarded
  would need deciding what a rejected write should do at every one of
  its ~36 call sites across `orchestration/nodes/` (silently no-op?
  raise? return a bool every caller must check?) - a real design
  decision, not a mirror-the-atomic-pattern mechanical fix like
  instances A and B above.
- **Residual, named honestly:** the windows are small (one or two awaits,
  not a whole graph execution), this demo runs a single worker process,
  and no observed incident - live or in this session's own runs - traces
  to instance A or B. This is a found-by-audit risk, not a reproduced
  failure.
- **Owner: explicitly NOT B17.** These are pre-existing concurrency races
  in worker and service code untouched by this block's own scope (UI
  polish, observability, deployment) - B17 is not the place to start
  rewriting `workflow_runs` status transitions, and the one instance this
  block DID fix (`mark_run_failed_on_timeout`) was fixed because B17's
  own timeout-recovery work created the direct occasion to, not because
  B17 owns concurrency correctness generally.
- **Status:** open, not fixed. Deliberately - reported and audited, not
  patched on the strength of "the pattern is right there to copy."
- **Test:** none - a code-reading audit against the real source (both
  call sites read directly, not inferred), not reproduced live.

## [B17, terminal block - documented, not built] A2's locked frontend-data stack (TanStack Query) and forms stack (React Hook Form + Zod) were never installed

B17 is this project's last build block - there is no later block to hand
this to. Confirmed directly against `apps/web/package.json` (the only
`package.json` in the repo - no monorepo root, nothing else declares
frontend deps) and `apps/web/package-lock.json` (checked independently,
in case something were installed without being declared, or vice versa):
neither `@tanstack/react-query`, `react-hook-form`, nor `zod` appears in
either file. `dependencies` is exactly `react`, `react-dom`, `react-
router-dom` - three packages. This entry documents the gap for the
record at ship time; it is not a triage note pointing at future work,
because none exists.

The two absences are not the same severity, and are recorded separately
rather than as one undifferentiated "missing libraries" line.

### TanStack Query (Build Guide A2, line 163: `Frontend data | TanStack
Query | Server state`) - load-bearing, no qualifier in the spec

- **The unbuilt locked requirement:** line 163 names TanStack Query as
  THE server-state layer, with no "nice to have" or "if time permits"
  language anywhere in A2 - the same unconditional phrasing every other
  row in that table uses for a requirement this project actually built
  (Alembic, LangGraph, RQ, ...).
- **The observable consequence, not a hypothetical one:** every screen's
  data fetching is a hand-rolled `useState`/`useEffect` pair calling
  `client.ts` directly, each one independently reinventing loading
  state, error state, and stale-request cancellation. This is not an
  abstract cost - four of this block's own Phase 1 findings are direct
  symptoms of it: `CommercialFindings.tsx`'s stale-write guard (a
  hand-rolled cancellation token, wrong on first landing, wrong again in
  the half CodeRabbit caught), `Login.tsx`'s destination-loss bug (traced
  through `AuthContext.tsx`'s own hand-rolled `refresh()`), `useEventStream`
  errors silently swallowed by the one consumer that forgot to destructure
  them, and the "no recovery path from a transient failure" standing
  pattern itself (see that entry above) - all four are exactly the class
  of bug a real server-state layer's own retry/error conventions exist to
  prevent by construction, not bugs this project's engineers happened to
  write independently four times. `api/retryTransient.ts` is this
  block's own real, working answer to the retry piece specifically - but
  it is a hand-built partial substitute for one slice of what TanStack
  Query would have provided (retry-with-backoff), wired into exactly two
  call sites (`AuthContext.refresh`, `GateDecisionPanel.handleDecide`),
  with no caching, no request deduplication, no shared loading/error
  convention, and no effect on the other data-fetching call sites in this
  app.
- **The residual, stated plainly because there is no later block to
  absorb it:** every data-fetching call site this project ships with -
  today, and any added in Phase 2 (see the containment rule below) -
  inherits none of retry, caching, deduplication, or a shared error
  convention, because no shared layer exists for it to inherit from.
  This is permanent for this project as built, not a gap scheduled to
  close.

### React Hook Form + Zod (Build Guide A2, line 165: `Forms | React Hook
Form + Zod | Client validation only; backend revalidates`) - degraded UX,
not a correctness or security gap

- **The unbuilt locked requirement:** line 165 names React Hook Form +
  Zod for client-side form handling and validation - also absent from
  `package.json`/`package-lock.json`, confirmed the same way as above.
- **Why this is a different severity, per A2's own words:** unlike line
  163, A2's own note on this row - "Client validation only; backend
  revalidates" - declares this layer non-authoritative at the moment it
  was locked. The spec itself does not ask this layer to be where
  correctness or security lives. Treating its absence as equivalent to
  the TanStack Query gap would overstate it - this file's own standard
  holds an overstated finding to the same failure bar as an understated
  one.
- **Verified, not assumed, before writing this severity down:** audited
  every real form in the frontend (8 found: `Login.tsx`, `CreateBid.tsx`,
  `GateDecisionPanel.tsx`, `EmployeeProfile.tsx`, `RedteamFindings.tsx`,
  `ProposalWorkspace.tsx`, `ComplianceMatrix.tsx`, `TalentLibrary.tsx`)
  against their corresponding backend routes and Pydantic request
  schemas. **Every one goes through a real `BaseModel`** - grepped every
  route file for a raw `dict`, `Any`, or `Request`-body parameter
  reachable from a form; found none. Several schemas carry real,
  live constraints, not decorative type hints: `LoginRequest`/
  `RegisterRequest.email: EmailStr` (format-checked), `GateDecisionRequest.
  decision: Literal["approve", "reject", "request_changes"]`,
  `PatchComplianceRequest.status: ComplianceStatus` (a `Literal` of five
  values - confirmed live, not just present, because the frontend's own
  `<select>` offers at least one value outside that `Literal` that would
  be REJECTED with a 422 if submitted), `PatchKnowledgeItemRequest.status`
  (a regex `Field(pattern=...)`). A minor, separate observation, not a
  severity change: some fields on these same schemas are bare `str`/
  `str | None` with no length or format constraint (`EmployeeProfile`'s
  own `email` field, notably, is plain `str`, not `EmailStr` the way
  `auth.py`'s is) - weaker validation than ideal, but A2 only promised
  "backend revalidates," not "every field is maximally constrained," and
  no endpoint trusts client-side shape.
- **Actual severity, named at the level the evidence supports:** degraded
  UX only - no inline client-side validation, so a malformed field
  surfaces as a 422 after a round trip instead of before submission. Not
  a correctness gap (every write is still validated server-side) and not
  a security gap (nothing trusts the client).

### Containment rule for Phase 2 - stated because this block still adds new data-fetching call sites

Phase 2 is not exempt from the TanStack Query gap just because adopting
the real library is out of scope for a terminal block - it adds at least
two new read call sites of its own: `AuditTimeline` reads `GET
/workflow-runs/{run_id}/events` (`routes/workflow.py:201` - see the Part
K B12-escape entry above), and the Cytoscape graph view reads `GET
/tenders/{tender_id}/graph/status` and `GET /tenders/{tender_id}/graph/
trace` (`routes/graph.py`). Left to each screen's own judgment the way
every existing screen was, those become the 18th and 19th bespoke
`useState`/`useEffect` pair in this codebase, growing the exact gap this
entry documents during the one block left to ship it.

**Rule: any new data-fetching call site Phase 2 adds goes through one
small, shared internal hook - not TanStack Query, not a rewrite of any
existing call site.** Proposed shape (to be confirmed as part of the
Phase 2 plan, not built here): a `useApiResource<T>(fetchFn, deps)` hook
factoring the exact pattern already hand-written per-screen (a mount
effect with its own `cancelled` flag, a `try`/`catch` setting `error`,
`retryTransient` wrapping the call for transient recovery) into one
place, returning `{ data, error, loading, refetch }`. Deliberately not a
cache, not cross-component state, not query invalidation - a shared
implementation of the retry/error/cancellation convention this block
already proved out at two call sites, extended to cover new ones instead
of a third and fourth hand-rolled copy.
- **Why this belongs in this entry, not only stated in conversation:**
  it gives this finding a real "what was done about it" line rather than
  only a confession that the gap exists and will grow. Whether Phase 2's
  own plan adopts this exact shape is a decision for that planning pass,
  not settled here.
- **Status:** the TanStack Query and React Hook Form + Zod absences are
  both open, permanently, for this project as scoped - B17 is terminal
  and no block exists to pick either up. The containment rule above is a
  proposal for Phase 2's own plan to accept, amend, or reject - not
  committed code.
- **Test:** none - a dependency/config audit (`package.json`, `package-
  lock.json`, 8 forms and their backend schemas each read directly) and a
  proposal, not a code path.

---

## [found during B17 Phase 0, RESOLVED in Phase 0 (approved)] The api image never pre-downloaded the embedding model, so the first embedding-backed API request on a fresh container fetched it from HuggingFace inside the request (observed: about 63 s)

- **Discovered:** B17 Phase 0's first fake-provider E2E run (2026-10-04,
  label **fake**), against a stack just rebuilt with `--build`. Step 12
  (`full-flow.spec.ts:973-978`, resolve every pricing finding) clicked
  Resolve and waited 10 s for the button count to drop from 8 to 7; it
  stayed at 8 and the test failed.
- **Instrument checked first, per the standing harness rule:** the click
  fired (the CORS preflight `OPTIONS /pricing-findings/dc44dd63-.../resolve`
  is in the api access log at 16:10:19.443 UTC); the row IS resolved in
  Postgres (`pricing_findings.resolved = t` for `dc44dd63-...`); and no
  `"Resolve failed"` alert rendered. The access log has no line for the
  POST itself because uvicorn writes it when the response goes out, and
  the browser closed at the test timeout before that happened. An aside
  on the instrument: the UI's 8 buttons are correct; a first count of 7
  joined through `pricing_documents`, which drops the one finding whose
  `pricing_document_id` is NULL. Counting by `pricing_findings.tender_id`
  gives 8.
- **Root cause, from the api log's own timestamps:** at 16:10:19.519 the
  api process logs `sentence_transformers ... No device provided, using
  cpu`, followed by a run of HuggingFace Hub HEAD/GET requests for
  `sentence-transformers/all-MiniLM-L6-v2`. The weights finish loading at
  16:11:17 and the embedding batch runs at 16:11:22: about 63 s inside one
  request. The path is `commercial_service.resolve_pricing_finding_in_uow`
  -> `_ground_pricing_finding` -> `ingestion/embed.py::embed_texts` ->
  `_get_model()`, which is `lru_cache`d per process and downloads on first
  use. `apps/worker/Dockerfile:27-28` bakes this model into the worker
  image at build time; `apps/api/Dockerfile` does not. Earlier E2E runs
  passed against a long-lived api container that had already downloaded
  it once; a fresh container pays the download on its first such request.
- **Scope, by grep, not assumed:** every `embed_texts` caller that the api
  process can reach has the same cold path:
  `agents/knowledge/retrieval.py:80` (query embedding) and
  `domain/services/document_service.py:47`, besides the pricing grounding
  above. Separately, `agents/knowledge/rerank.py:11`
  (`cross-encoder/ms-marco-MiniLM-L-6-v2`) is baked into **no** image, so
  the worker pays the same first-use download for the reranker.
- **Risk:** latency on the first request after any api restart or
  rebuild, long enough to fail a 10 s UI wait. It is also a runtime
  outbound dependency on huggingface.co from inside request handling: a
  host with restricted egress would fail that request outright, not just
  slowly.
- **Effect on the first Phase 0 baseline:** the second E2E run went
  against the same, by then warm, api container, so it said nothing about
  a fresh one.
- **Fix (approved, B17 Phase 0):** both `apps/api/Dockerfile` and
  `apps/worker/Dockerfile` (the latter also builds `outbox_dispatcher`)
  now download `sentence-transformers/all-MiniLM-L6-v2` AND
  `cross-encoder/ms-marco-MiniLM-L-6-v2` at build time, in a layer before
  the app-code COPY, then set `ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1`
  for runtime. Offline mode turns any model missing from the image into an
  immediate error instead of a silent network fetch inside a request.
- **Observed, not assumed (2026-10-05):** each freshly built image run with
  `docker run --network none` (no network at all, so any fetch attempt
  would fail) loaded both models from the baked cache: first embed 0.27 s,
  first rerank 0.16 s, in both the api and worker images, with both
  offline variables reported set. The request-level check (a fresh stack's
  first pricing-finding resolve, E2E step 12) is in the Phase 0 report.
- **Cost:** image size by `docker image inspect .Size`: api 431,507,363 ->
  626,316,023 bytes (+185.8 MiB, both models); worker 578,223,800 ->
  662,385,029 bytes (+80.3 MiB, reranker only - the embedder was already
  baked in).
- **Test:** none in the suite - verified by the observation above.

---

## [found during B17 Phase 0, owner: NOT Phase 0] `make test` points pytest at the live stack's Neo4j and Redis DB 0; the `_test` guard protects Postgres only, and six fixtures run `MATCH (n) DETACH DELETE n`

- **Discovered:** B17 Phase 0 item 9 (the D4/D5 directed-vs-undirected
  comparison). The live Neo4j held **0 nodes** while the live Postgres
  `tenderos` database held 31 tenders and 22 workflow runs.
- **Mechanism, read directly:** the Makefile's `test` target sets
  `NEO4J_URI=bolt://localhost:7687` and `REDIS_URL=redis://localhost:6379/0`,
  the same Neo4j and the same Redis database the running stack uses.
  `tests/integration/conftest.py::_guard_test_database` refuses any
  `POSTGRES_DSN` not ending in `_test`, but nothing equivalent exists for
  Neo4j or Redis. Six fixtures wipe the whole graph:
  `tests/integration/test_graph_projection.py:37` and its `test_rebuild_from_postgres_after_deleting_neo4j`,
  `test_readiness_service.py:39`, `test_reanalysis_job.py:53`,
  `test_redteam_service.py:36`, `_clean_neo4j` in `tests/route/conftest.py`. Neo4j 5.26
  Community has a single user database, so there is no `_test` graph to
  point at. Redis held 81 keys immediately after the 2026-10-04 pytest
  runs; whether any of them were test-written RQ jobs that a live worker
  could then pick up is not established.
- **Risk:** every `make test` run destroys the live graph projection, and
  shares an RQ namespace with any running worker. Recoverable per tender
  through `graph_service.project_tender(tender_id)` (Part D's "derived and
  rebuildable"; `test_rebuild_from_postgres_after_deleting_neo4j` covers
  it), but at the time **not** through `scripts/rebuild_graph.py`, then a
  4-line stub that raised "rebuild_graph is not implemented yet (arrives
  with B10)", checked directly. Plan item 49 implemented it (the entry on
  Neo4j's uniqueness constraints). At the time there was no whole-graph rebuild command. That is also why pytest must never run at the same
  time as a live E2E run.
- **Redis half resolved (B17 Phase 0, approved config change):** the
  Makefile's `test` target now sets `REDIS_URL=redis://localhost:6379/1`;
  the stack keeps `/0`. Every test reads `settings.redis_url` (grepped - no
  test hardcodes a database index; `test_outbox_dispatcher.py`'s
  `localhost:1/0` is a deliberately unreachable port), so this fully moves
  the test RQ namespace. Only `make test` carries it - a hand-typed
  `uv run pytest` with `/0` still shares, so use the target's exact env.
  CI is unaffected either way (its own throwaway Redis service container).
- **Neo4j half still open.** Candidate fixes, for a later decision: a guard
  that refuses to run unless the stack's api/worker containers are
  stopped; a dedicated Neo4j container for tests, since Community edition
  has no second database. Working rule meanwhile: stop the app containers
  before `make test`, and re-project any tender whose graph is needed
  afterwards (`graph_service.project_tender`).
- **Standing rule (B17 Phase 0):** no pytest between a real-provider run
  and its captures - a test run would wipe that run's graph projection.
- **Test:** none - a harness-isolation finding.
- Update 2026-10-10: scripts/test-backend.ps1 reads the same
  scripts/test-backend.env, so it wipes the shared graph exactly as make
  test does. Its guard refuses to run while api, worker or
  outbox_dispatcher are up, which prevents a race with a live worker but
  not the wipe itself. Observed (measured): after a full suite run, live
  Neo4j held 27 nodes (one tender, re-projected by the following fake
  E2E); scripts/project_all_tenders.py restored 47/47 tenders (3,585
  nodes). Working rule: after any test-backend.ps1 run that includes the
  graph fixtures, run project_all_tenders.py before relying on the live
  graph or on the invariant checker. The guard can be bypassed with
  -AllowRunningStack.

---

## [found during B17 Phase 0, RESOLVED in Phase 0 (approved experiment, all tests passed)] `preview_impact` traversed undirected; D5 specifies a directed traversal, and the difference was large on real data

- **Spec, quoted:** Build Guide D5 (:1955):
  `MATCH path = (fact)-[*1..$maxHops]->(affected)` - a directed
  variable-length path. Every relationship in D2 (:1884-1896) points
  downstream (knowledge item -PROVIDES-> Evidence -SUPPORTS-> Compliance
  -DECIDES-> Requirement; Evidence -CITED_IN-> ProposalClaim -PART_OF->
  ProposalSection; Employee -FILLS-> Role -SATISFIES-> Requirement), so a
  directed path from the fact follows exactly "what depends on this fact".
- **Code:** `graph_projection/queries.py:106`,
  `f"MATCH path = (fact)-[*1..{int(max_hops)}]-(affected) "` - no arrow,
  so undirected. Present since B10's first implementation (`06b5d44`,
  2026-08-16) and never recorded as a deviation before this entry.
- **Measured, B17 Phase 0, zero cost:** tender `14cd7584-...` (the
  synthetic package, data produced by a **real**-provider run on
  2026-09-01 - Gemini), projected today with `graph_service.project_tender`
  (deterministic, no LLM). The production query text ran twice for each
  of the tender's 4 evidence- or selection-linked knowledge items, with
  only the arrow changed, `GRAPH_MAX_HOPS=4`:

  | item | undirected (as coded) | directed (D5) |
  |---|---|---|
  | `26ddfff0` | 24 | 17 |
  | `2e42f140` | 17 | 4 |
  | `c39196e1` | 19 | 2 |
  | `d9cda746` | 16 | 0 |
  | total | 76 | 23 |

  Directed was a strict subset every time (0 nodes returned only by the
  directed query).
- **Why the gap, observed directly:** `d9cda746` is an Employee whose
  only edge is `USED_IN -> Tender` - no evidence, no role, nothing depends
  on it, so D5's directed query correctly returns 0. The undirected query
  walks backwards into the shared Tender node, out along every other
  item's `USED_IN` edge, and forward through those items' evidence,
  reporting 16 "affected" nodes for a fact that affects nothing. Every
  item selected into a tender reaches the others this way, within the
  hop budget.
- **Risk:** the impact preview over-reports. A user deselecting a fact
  is shown dependents that belong to other facts, which weakens the
  screen's signal ("potentially affected" in D4 means affected through
  this fact, not through its neighbours in the same tender). Nothing is
  silently missed - the undirected set contains the directed set - so
  this is a precision problem, not a recall one.
- **Decision and fix (B17 Phase 0, approved as an experiment with a
  revert-on-any-failure condition):** `queries.py:106` (now with a comment
  above it) uses `-[*1..{max_hops}]->`, matching D5. This brings the code
  INTO line with the spec rather than changing the contract.
- **Every graph-related test, run against the directed query
  (2026-10-05): 159 passed, 0 failed**, across 20 files - every test file
  that touches `preview_impact`, `graph_service`, the projector, Neo4j or
  the graph routes, including all 14 tests that exercise impact preview or
  deselection (`test_preview_graph_impact_returns_the_selected_employee`,
  `test_proposal_claim_appears_in_impact_set`,
  `test_preview_impact_surfaces_a_never_selected_item_that_backs_real_evidence`,
  `test_alternate_evidence_remains_compliance_not_broken`, and the rest).
  No existing test depended on the undirected walk.
- **Test:** `test_graph_projection.py::test_employee_with_no_evidence_
  affects_nothing_even_beside_one_that_does` - an evidence-backed employee
  and an unbacked one in the same tender; asserts the backed one reaches
  its claim (control) and the unbacked one shows `[]`. Proven falsifiable,
  not assumed: with the arrow temporarily reverted to undirected it fails
  on exactly that assertion (the unbacked employee leaks the sibling's
  claim through the shared Tender node), and passes once restored.
- **Residual:** the landing page's impact chapter still says TenderOS
  "identifies every affected section" - a Phase 5 copy fix, already planned.

---

## [B17 Phase 0, resolved] D4's 6-hop ceiling existed only as a comment

- **Spec, quoted:** Build Guide D4 (:1940): "The code permits
  configuration up to 6 for experiments, but the public application
  defaults to 4."
- **Before:** `core/config.py` declared `graph_max_hops: int = 4` with a
  comment saying "The code may configure up to 6"; nothing enforced it.
  `GRAPH_MAX_HOPS=50` would have been accepted and interpolated into the
  variable-length Cypher bound in `graph_projection/queries.py:106`. The
  per-request side was already closed (the impact request schema has no
  hop field); the config side was not.
- **Fix:** `graph_max_hops: int = Field(default=4, ge=1, le=6)`. `le=6` is
  D4's ceiling. `ge=1` is not in D4 - added because a bound of 0 renders
  `[*1..0]`, which silently matches nothing and would read as "no impact".
  Both fail at `Settings()` construction, before any process starts work.
- **Test:** `tests/unit/test_graph_max_hops_cap.py` (8 cases, through the
  real `GRAPH_MAX_HOPS` env var): default 4; 1, 4, 6 accepted; 7, 50, 0,
  -1 rejected.

---

## [B17 Phase 0, design limitation, documented] B14 scenario lines 3562-3563 ("compliance task reopens", "claim becomes needs_review") are not demonstrable by design

- **Spec, quoted:** Build Guide B14 scenario test (:3555-3564):
  "Project A supports Requirement R1 and Claim C1 → user requests
  deselection → impact preview lists R1 and C1 → user confirms →
  selection=false → projection refresh → evidence coverage = 0 →
  **compliance task reopens** → **claim becomes needs_review** → readiness
  blocker appears".
- **Why neither step can happen on a deselection, from the code:**
  - Compliance cannot reopen: re-evaluation (`reanalysis.py:152`,
    `evaluate_compliance_for_requirement`) retrieves evidence with
    `selected_only` deliberately unused (`compliance_service.py`),
    so it re-finds the identical evidence; `persist_compliance_decision`
    correctly leaves an unchanged decision alone. Observed live in B16
    (this file, the `[B14] preview_impact ... USED_IN` entry's B16
    addendum: "re-ran evaluate_compliance_for_requirement and re-found the
    identical evidence"). B16 ruled out both ways of changing that (gating
    retrieval by selection; auto-reselecting on evidence materialization)
    for documented reasons in the same entry.
  - A claim cannot become needs_review: the only computed needs_review is
    in `proposal_service.py`, inside `enforce_claim_grounding`, reached
    only when a cited requirement's compliance is non-affirmative. Its two
    callers are draft-time persistence (`:551`) and `reground_claim`
    (`:922`, called by `reanalysis.py:184`). `reground_claim` re-filters
    evidence by `verified and validity_status == "valid"` only
    (`_prepare_evidence_facts`, `:154`) - selection is not an input - and
    compliance stays affirmative per the point above. The model default
    (`ProposalClaim.grounding_status` in `models/proposal.py`, `default="needs_review"`) is an initial
    value, not a transition.
  - The one remaining route is incidental: under a real provider the
    re-evaluation's LLM call could answer differently by nondeterminism.
    That is not caused by the deselection and is not a designed behaviour.
- **The designed signal instead (D6):** reanalysis step 5 runs
  `check_graph_contradictions`, which raises a `deselected_fact_in_use`
  finding. `graph_service.py:387-411` grades it **critical** exactly when
  coverage, ignoring the flagged evidence, says
  `compliance_re_evaluation_required` (no alternate valid path) - the
  spec's "evidence coverage = 0" (:3561) - and **major** otherwise. A
  critical open finding is a readiness blocker
  (`_evaluate_readiness_once` in `readiness_service.py`: "Unresolved critical red-team finding:
  deselected_fact_in_use."), which is the spec's last line (:3564).
- **E2E consequence (B17 Phase 0):** `full-flow.spec.ts` step 14's final
  assertion ("at least one additional claim to become needs_review") was
  removed and replaced by a comment. It was unreachable twice over: the
  above, and a screen mismatch - its after-count polled `/compliance`,
  where `ComplianceMatrix.tsx` has rendered no `<li>` in any version since
  B14 (`85e1124`), against a before-count taken on `/proposal`. It also
  produced run 4's only route "bounce" (page on `/redteam`, expected
  `/compliance`). The D6 finding assertion before it stays.
- **Gap, proposed not added:** step 13 asserts readiness is not "Ready"
  with at least one blocker of any kind; it does not assert that the D6
  finding is that blocker, so the spec's :3564 link is not yet observed
  end to end. The proposed step-13 addition is in the Phase 0 report as a
  diff, for decision.
- **Test:** the D6 half is covered at service level by
  `test_reanalysis_job.py::test_reanalyze_knowledge_selection_change_
  surfaces_deselected_fact_still_used_finding` (asserts no reopen AND an
  unresolved finding); the E2E covers the finding on screen.

---

## [found during B17 Phase 0, RESOLVED in Phase 0b (83262e1)] E2E step 0b's import-batch poll can pass with 1 of 4 items, so the import-items loop can examine a partial list

- **Observed (2026-10-06, final Phase 0 fake run, via the spec's own
  `loop-and-poll-iterations` instrument):** `loop:import-items` ran **1**
  time, while the batch holds **4** items (all `approved`, checked in
  Postgres). The run was green; the partial read did no harm here because
  every item landed `approved` (a fresh org takes the NEW branch, so no
  Approve button is expected), and the later employee-links poll waits for
  all `resumeFiles.length` employees.
- **Mechanism, from the code:** the poll's condition is
  `b.completed + Number(b.failed) >= b.total && b.total > 0`, but
  `knowledge_service.py:1214` computes `total = len(items)`, and items are
  created one per résumé job - so `total` can be 1 while three items do
  not exist yet, and the condition passes. Its own message says "all 4
  items"; the condition never checks 4.
- **Risk:** the same pass-at-partial shape as the Phase 0 count guards: an
  item that lands `needs_review` after the loop has run would never be
  approved, and the loop would not notice.
- **Proposed (diff in the Phase 0 report, not applied - a green run was
  not patched and rerun):** require `b.total === resumeFiles.length` in the
  poll, and assert `itemCount === resumeFiles.length` before the loop.
- **Test:** the E2E itself, once the diff is decided.
- **Resolved in 83262e1:** the proposed diff was applied as written (the
  batch poll requires one item per uploaded resume, and the item count is
  asserted before the loop). Observed (fake): `loop:import-items` ran 4
  times, was 1.

---

## [Phase 4 plan item 49, RESOLVED, with limits] Neo4j had no uniqueness constraints, and concurrent projections wrote wrong graphs that read as fresh

- **Wrong:** `ensure_constraints()` had no callers, so nothing made the 11 node keys
  unique, and `MERGE` alone does not stop concurrent transactions creating one node
  twice. Nothing serialized projections of one tender either, and each deletes that
  tender's nodes and writes them back in separate statements.
- **Observed (fake provider, local data, 2026-11-05), no constraints:**
  - 4 projections of one tender differed from a single projection in 10 of 10
    rounds, up to 972 nodes against 243 and 3,408 relationships against 213. **All
    10 passed `check_freshness`,** which only looks at the completion marker.
  - 4 tenders sharing 40 employees: duplicates in 9 of 10 rounds, up to 80 extra
    `Employee` nodes.
- **Fixed:**
  - The compose `migrate` service applies the 11 constraints after Alembic, and
    fails closed: a duplicate keeps the app services down.
  - Each tender's projection takes an advisory lock (class 3402), waiting at most
    15 s.
  - Rows in statements touching shared nodes are sorted by key, because constraints
    make opposite orders deadlock (40 of 60 statements measured, 0 of 60 in one order).
  - A projection is attempted up to 3 times, on Neo4j's transient errors and the
    lock timeout only.
  - `scripts/check_graph_keys.py` reads the graph; `scripts/rebuild_graph.py --yes`
    wipes it and reprojects from Postgres (154 tenders in 29.1 s).
- **Still true:**
  - **Null keys aren't stopped by a constraint.** Key constraints would, but they are
    Enterprise and our image is Community. The projector only uses `MERGE`, which
    refuses a null key (observed), so `check_graph_keys.py` covers the rest.
  - **A Neo4j outage now stops `docker compose up` of any app service,** because
    `migrate` waits for it. One already running keeps running.
  - **A second projection of one tender waits its turn,** holding a Postgres
    connection while it does, for at most about 46 s in all.
  - **`hashtext()` can map two tenders to one lock key.** They then wait on each
    other briefly, which is harmless.
  - **A deadlock remains possible** on statements locking two shared nodes (`HOLDS`,
    `WORKED_ON`, `DEMONSTRATES`), where sorting can't give one total order. The retry
    covers it. Not seen.
  - **Certification, Project and Capability have no nodes in the local data,** so
    their concurrency behaviour was tested only on seeded rows.
  - **Timings come from one machine.** Adding a constraint took 2.04 s on 1,000,000
    nodes, with writes elsewhere unaffected; Neo4j's docs say only that it "can take a
    while", and one user report describes a long block at over 20 million nodes.
- **Tests:** `tests/integration/test_graph_constraints.py`,
  `test_graph_projection_concurrency.py`, `test_graph_key_scripts.py`,
  `tests/unit/test_advisory_lock_classes.py`, `test_compose_migrate_service.py`.

---

## [B17 Phase 0, RESOLVED by phase 4 plan item 35] Graph-contradiction red-team findings carried the wrong `target_type` for the id they store

- **Fixed:** the label now comes from the row (`redteam_checks._graph_target`):
  `compliance` and `claim` nodes name their own table, and otherwise the
  relationship the row was reached through does (`SUPPORTS` to
  `compliance_entry`, `CITED_IN` and `NAMED_IN` to `proposal_claim`, `FILLS` to
  `role_requirement`). A row it cannot place raises. Migration 025 relabels
  stored rows and recomputes their fingerprints together, keeping every id and
  resolution; its downgrade restores the old labels and fingerprints exactly.
- **Measured on a copy of the local database:** 261 of 1,611 findings relabelled,
  in 80 tenders, 0 collisions, 0 unmatched. All 18 `removed_employee_committed`
  rows there pointed at role requirements, so the "same risk" below was what
  always happened.
- **Still true, and accepted:** the label relies on each relationship type going
  to one node label. A second target label on one would be wrong without
  complaint; `tests/integration/test_redteam_target_type.py` looks every id up
  in the table its label names, which is what would catch it.
- **No CHECK constraint on `target_type`** (decided): it would have to allow the
  old label for any unmatched row the migration leaves, which defeats it.
- **A collision is not merged.** If the fixed code ever ran before the migration,
  the unique constraint fails it and the whole migration rolls back. No finding
  is ever deleted. Tests: `tests/integration/test_migration_025_redteam_target_type.py`.

The original entry, as found:


- **What, read from the code:** `redteam_checks.check_graph_contradictions`
  builds `target_id` from the contradiction row's own node -
  `row.get("compliance") or row.get("claim") or row.get("target") or
  row.get("fact")` (`redteam_checks.py:212-213`) - but takes
  `target_type` from a fixed per-category map, `_GRAPH_TARGET_TYPE`
  (`:182-187`). For `deselected_fact_in_use` the map says `"evidence"`
  (`:184`), while the D6 query returns `fact, evidence, via, target`, so
  `target_id` is the `target`: a Compliance or ProposalClaim node.
  `removed_employee_committed` maps to `"proposal_claim"` (`:185`), and has
  the same risk when its target is a Role reached through `FILLS`.
- **Observed (fake, 2026-10-06, the three Phase 0 green runs):** each run's
  one deselection produced three `deselected_fact_in_use` findings, all
  labelled `target_type = evidence`, with three distinct `target_id`s and
  three distinct fingerprints - not duplicates. Resolved in Postgres, the
  final run's three ids are the compliance entry for the staffing
  requirement, the compliance entry for the technical requirement, and a
  grounded `compliance` proposal claim. None of the three ids exists in
  `evidence_links`, `compliance_evidence` or `proposal_claim_evidence`.
- **Every consumer of a red-team finding's `target_type`, searched
  read-only (backend `packages/`, `apps/api`, `apps/worker`, and
  `apps/web/src`):**
  - `redteam_service.finding_fingerprint` (`redteam_service.py:24-37`,
    called at `:55` and `:192`): hashes `[tender_id, target_type,
    target_id, finding_category, violated_condition]` - the finding's
    identity.
  - `redteam_service.persist_finding_draft` (`:78`) and
    `RedteamRepository.create` (`db/repositories/redteam.py:16-28`):
    store it.
  - `schemas/redteam.py:11`: exposes it in the API response.
  - `apps/web/src/api/client.ts` declares the field; **no screen reads
    it** (no `target_type` in `apps/web/src/routes` or `components`); the
    red-team screen renders category, severity, text, resolved - no link
    built from target_type/target_id.
  - Readiness (`_evaluate_readiness_once` in `readiness_service.py`) builds its blocker from the
    constant `"redteam_finding"` and `finding.id`, never the finding's own
    `target_type`.
  - Targeted replanning (`orchestration/nodes/redteam.py:101`) routes by
    `map_finding_to_corrective_task(finding.finding_category)`
    (`redteam_service.py`), not by `target_type`.
  - `domain/policies/rbac.py:187` mentions the column in a comment only.
  - The other `target_type` hits in the codebase are different columns
    (`audit_events.target_type`, readiness blockers' own, gate targets) -
    not consumers of this one.
  **Conclusion: no consumer misroutes today because of the wrong label.**
  The value is only stored, hashed into identity, and exposed in the API;
  nothing dereferences `target_type` + `target_id` together. The risk is
  latent: the first consumer that does (a "go to target" link on the
  red-team screen, an audit join) would look in the wrong table and find
  nothing.
- **Why this is not a one-line fix:**
  - **Identity.** The fingerprint includes `target_type` (Build Guide
    `:2726`: "Red-team finding: target_type + target_id + finding_category
    + violated_condition"; code `redteam_service.py:24-37`). Correcting the
    label changes every affected finding's fingerprint, so the next
    detection would create a NEW row while the old one stays - a
    duplicate, and a human resolution on the old row would no longer
    protect against redetection. A real fix needs a data migration that
    re-labels and re-fingerprints existing rows together.
  - **Nothing catches it in the database.** `redteam_findings.target_type`
    is `TEXT NOT NULL` with no CHECK constraint (Build Guide `:1562`;
    `RedteamFinding.target_type` in `db/models/redteam.py`), so any string is accepted.
  - **The spec's vocabulary.** Build Guide examples use
    `"target_type": "proposal_claim"` for red-team findings (`:2298`, E8)
    and `"compliance_entry"` for gate targets (`:871`, R5) - so a correct
    label would be per-row (`compliance_entry` or `proposal_claim`, or a
    Role type), derived from the node actually stored, not per-category.
- **Status at the time:** open, not fixed, by decision (B17 Phase 0). Any fix is a
  contract change (labels plus a fingerprint migration), not a cleanup.

---

## [B17 Phase 0b, RESOLVED] The never-run CI workflow would have failed in every backend job on its first run

`.github/workflows/ci.yml` has never executed (no git remote). Run locally,
step by step, on a `git worktree` checkout with no `.env` - what a runner
actually has - three independent failures were reproduced, not inferred:

- **Settings.** The test job set 5 variables; 6 required `Settings`
  fields with no default (`jwt_signing_key`, `s3_access_key`,
  `s3_secret_key`, `s3_bucket`, `neo4j_user`, `neo4j_password`) only ever
  came from the untracked `.env`. Observed: "Run tests" failed collection
  with 15 errors ("6 validation errors for Settings"), and the migration
  check failed ("9 validation errors for Settings"). Fixed with a
  job-level `env:` of throwaway CI-only values, plus both steps sourcing
  `scripts/test-backend.env` (the same file local runs use).
- **Fixtures.** `data/synthetic/` is gitignored except `.gitkeep`
  (generated, never committed - `make_synthetic_fixtures.py`'s own
  docstring). On a fresh checkout 19 tests failed on missing fixtures.
  Fixed with a "Generate synthetic fixtures" step running the repo's own
  deterministic generators (`make_synthetic_fixtures.py`,
  `synthetic_tender_a.py`, fixed seed 20260810) - synthetic only, Part N /
  L3.
- **Secret scan.** `.secrets.baseline` (generated 2026-08-29, B15) lacked
  16 findings, 12 of them from B15/B16 commits - including B15's own test
  files, the commit that introduced the scan. Every one was classified
  without printing its value (length, placeholder marker, real-key shape):
  all are placeholders (`changeme` DSNs and keys, test spy values, a
  throwaway passphrase for an account a measurement script registers in
  a fresh `@tenderos.local` org). None has a real-key shape. Recorded via
  `detect-secrets scan --baseline` (34 -> 49 entries). An initial theory
  that the baseline's Windows backslash paths would also fail on Linux was
  tested in a Linux container and **disproved** (detect-secrets normalises
  separators; the committed baseline failed on Linux with exactly the same
  16 findings) - retracted, not acted on.
- **Also:** tests used to inherit `LLM_PROVIDER` and the real Gemini keys
  from `.env` (a test reaching the real client would have spent money);
  `scripts/test-backend.env` now sets `LLM_PROVIDER=fake` and blank keys,
  and CI does the same. On Windows PowerShell an env var set to "" is
  deleted (observed), which would have let `.env`'s key back in, so
  `test-backend.ps1` substitutes the non-empty sentinel
  `disabled-in-tests` (observed: `Settings` then reads the sentinel, not
  the real key).
- **Observed after the fixes, all locally (the workflow has still never
  run on GitHub):** actionlint 0 errors; lint (ruff) pass; typecheck
  (mypy) pass; migration check pass on a fresh database; tests 642 passed
  on the `.env`-free checkout; web tsc/eslint/vitest 37 pass; landing
  tsc/eslint/build pass; detect-secrets pass in a Linux container on a
  clean committed snapshot. The `security` job's `pip-audit` and
  `npm audit` steps still fail - see the next entry.

---

## [found during B17 Phase 0b, RESOLVED on chore/deps-security, 2026-10-10 - see the entries at the end] Dependency audit: 37 known Python vulnerabilities in 5 packages, 5 high npm advisories

- **Observed 2026-10-06** (`uv run pip-audit`, `npm audit --audit-level=high`
  - the CI security job's own commands; results depend on the date, since
  the advisory databases change without any code change):
  - `pyjwt` 2.13.0: 14 advisories (fixes in 2.14.0 / 2.15.0) - **this is
    the library that signs and verifies the app's auth tokens.**
  - `pypdf` 6.15.0: 11 (fixes 6.16.0 / 6.16.1) - **parses uploaded,
    untrusted tender PDFs.**
    **Correction (2026-10-22): that is wrong.** pypdf is declared in
    `packages/tenderos_backend/pyproject.toml` but never imported, by our
    code or by any installed package (searched). Uploaded PDFs go through
    pdfplumber (`ingestion/parse_pdf.py`), which parses with pdfminer.six
    and renders pages for OCR with pypdfium2.
  - `virtualenv` 21.7.0: 8 (dev tooling); `urllib3` 2.7.0: 3;
    `langgraph-sdk` 0.4.2: 1 (fix 0.4.4).
  - npm: `apps/web` 4 high (transitive: brace-expansion, js-yaml,
    source-map-js, undici - all with fixes available); `apps/landing` 1
    high.
- **Not fixed in Phase 0b:** upgrading is a dependency and lockfile change
  (including the auth library), which needs its own decision and a full
  test pass, not a CI-honesty edit. Until then the CI `security` job fails
  on its first run, by design of the check - which is the honest state.
- **Test:** the CI `security` job.

## [found during B17 pre-push audit, deployment prerequisite, BUILT in Phase 4 batch 1] Phase 4's inspection plan refers to a data/synthetic manifest that does not exist

- **Observed 2026-10-07** (read-only pre-push audit): `data/synthetic/`
  holds only generated, gitignored files plus a tracked `.gitkeep`. No
  manifest file exists there or anywhere in the repo. Phase 4's inspection
  plan refers to "the data/synthetic manifest" as if it did.
- **What stands in for it today:** `scripts/synthetic_tender_a.py`, whose
  record list is the single source for the synthetic documents and both
  `data/ground_truth/*.jsonl` files. The audit regenerated the ground truth
  on a fresh checkout and `git diff --stat -- data/ground_truth` was empty:
  the tracked copies are exactly the generator's output. That is a check
  someone has to rerun, not an artifact anyone can inspect.
- **Prerequisite before deployment (not built):** a manifest of the
  synthetic files with their hashes, generated by
  `scripts/synthetic_tender_a.py` itself, so that "this deployment serves
  only synthetic data" can be verified against a file rather than asserted.
- **Test:** none yet.
- **Built in Phase 4 batch 1 (item 17):** `scripts/synthetic_tender_a.py`
  writes `data/synthetic/tender_a/SHA256SUMS` (tracked, `sha256sum`
  format, paths from the repo root): its 13 documents and both
  ground-truth JSONL files. CI regenerates the fixtures, fails on any diff
  in that file or in `data/ground_truth`, and runs `sha256sum -c` on it.
  - **The generator was not deterministic, which a manifest needs.** Two
    runs a few seconds apart (measured 2026-10-22) gave identical PDFs and
    JSONL but different bytes for every .docx and .xlsx: every zip entry
    carries the time it was written, and openpyxl stamps
    `dcterms:created`/`modified` at save time. Each .docx and .xlsx is now
    repacked with a fixed time, fixed attributes and uncompressed entries
    (`synthetic_common.deterministic_ooxml`); uncompressed because deflate
    output depends on the zlib build.
  - **The JSONL differed by platform:** Windows text mode wrote CRLF, while
    git (`eol=lf`) shows the files as unchanged, so only a hash sees it.
    Both are now written with LF on every platform.
  - **Observed:** two Windows runs give a byte-identical manifest; the same
    generator run in the worker image (Linux, Python 3.12.13; the host has
    3.12.4, both openpyxl 3.1.5) gives the same manifest byte for byte; the
    JSONL hashes equal the tracked blobs.
  - **Not covered:** B4's fixtures from `make_synthetic_fixtures.py`
    (`data/synthetic/*.pdf|docx|xlsx`, used by ingestion tests, not by the
    E2E). Its scanned PDF draws text with DejaVuSans when the system has it
    and Pillow's default font otherwise, so its bytes depend on the host.
    The E2E and the demo flow use only `tender_a`.
  - **Fixture bytes changed** (11 of 13 files: the fixed timestamps and
    stored entries; the two PDFs are unchanged), so the E2E runs on the
    regenerated files: 1 passed in 2.6 min (fake provider, 2026-10-22,
    kept awake; `.playwright-mcp/phase4-review/e2e-awake-batch1.log`).

## [B17 pre-push, documented layout change] data_private/.gitkeep is no longer tracked

- Untracked in 21ad8a3, with its `.gitignore` exception removed. A fresh
  clone therefore has no `data_private/` folder. Nothing in the code,
  scripts, compose files or CI reads it (only `.gitignore`,
  `.dockerignore` and a CI comment name it).
- This departs from the repository layout in the project's spec, which
  lists `data_private/.gitkeep`. The folder is created locally when
  needed, and stays ignored.

## [B17 pre-push, history rewritten 2026-10-07] Commit hashes cited above refer to pre-rewrite history

- Before the first push, the PRD, the Build Guide and CLAUDE.md were
  removed from all history with `git filter-repo` (they had been
  committed once and untracked two days later). Every commit hash
  changed; the final tree did not.
- Hashes cited in this file before that point are pre-rewrite hashes and
  no longer resolve. Find a commit by its subject instead, e.g.
  `git log --all --oneline --grep="import-batch poll"`. An old-to-new
  hash map is kept outside the repository.

## [B17, recorded deviation from A2 under section 0.1, approved 2026-10-07] MinIO replaced by VersityGW v1.8.0 behind the unchanged S3 adapter

- **Why MinIO had to go (observed 2026-10-07 unless marked):** the first
  GitHub CI run failed at "pull access denied for minio/minio".
  `docker manifest inspect minio/minio:RELEASE.2025-09-07T16-13-09Z` is
  denied anonymously; `quay.io/minio/minio` refuses anonymous pulls (its
  anonymous pull token grants no actions, while a control repository's
  grants `pull`); github.com/minio/minio was archived on 2026-04-25 with the
  banner "THIS REPOSITORY IS NO LONGER MAINTAINED". *Reported, not verified
  here:* that MinIO deleted its Docker Hub images on 2026-09-11.
- **The deviation:** A2 (Build Guide :153) names "MinIO locally,
  S3-compatible adapter". The adapter is unchanged: `db/storage.py` is the
  same boto3 S3 client, its endpoint still comes from `S3_ENDPOINT_URL`, and
  nothing in application code is VersityGW-specific. Only the server, the
  compose service (`s3`, volume `s3_data`), the CI step and the readiness
  probe changed.
- **Why VersityGW:** of nine images evaluated in scratch (pgsty/silo,
  pgsty/minio, Chainguard minio, SeaweedFS, Garage, RustFS, VersityGW,
  Scality CloudServer, plus the old MinIO as reference), all passed the
  app's four S3 operations and its 161 storage-touching tests. VersityGW
  ranked first on maintenance evidence: published by Versity (an
  organisation), Apache-2.0, monthly releases, its 5 published GitHub
  advisories all patched at v1.8.0, 31.4 MB image, about 13 MiB idle, a
  built-in health endpoint and two settings. pgsty/silo (a MinIO fork) was
  the smallest-diff alternative.
- **Pinned:** `versity/versitygw:v1.8.0@sha256:30292fc2eeacc67a36993b01f7a7a5e3361a19cced0e80c1d71cfa2a4b0a2499`,
  re-verified to resolve anonymously on 2026-10-07, in both
  docker-compose.yml and ci.yml.
- **Verified (local; fake provider wherever an LLM is involved):** the
  project's own ObjectStorage against VersityGW (head/create bucket, put
  with ContentType, byte-exact get, NoSuchKey); full backend suite 648
  passed via scripts/test-backend.ps1; ci.yml's test job steps run verbatim
  in a clean checkout with no .env (648 passed on the second run; see the
  intermittent dispatcher entry below); persistence on the named volume
  across `restart` and `down`/`up` without `-v`; a fresh stack (no app
  bucket) turns the api container healthy with /health/ready 200 and
  object_storage true; fake E2E green with invariants 6/6, and its 9 new
  uploads present in the store.
- **Facts the swap depends on (observed):** the image has no `/data`, and
  the posix backend exits at once without a mount there ("chdir /data: no
  such file or directory"), so CI mounts `-v s3_data:/data`. The image
  ships `wget`, not `curl`. Object metadata (`user.content-type`,
  `user.etag`, `user.checksums`) and bucket ACLs live in extended
  attributes: a backup must preserve xattrs (GNU tar `--xattrs
  --xattrs-include='*'`). Proven by round trip: GNU tar restore kept
  ContentType and ETag on 18,730/18,730 objects; a busybox tar restore kept
  0/18,730 (every object came back `text/plain; charset=utf-8`).
- **What the readiness probe cannot see:** /health/ready's key is now
  `object_storage` (was `minio`; nothing in the repo read it). It is an
  authenticated HeadBucket on the app bucket: 200, or 404 because the bucket
  is created lazily on the first upload, means ready; 403, any other status
  and any connection error mean not ready. It checks reachability and
  credentials, not the write path: a server whose front end answers while
  its storage cannot yet accept writes reports ready (observed with
  CloudServer's warm-up during the evaluation), and so would a non-S3
  server that answers HEAD with 404.
- **Anyone with an older .env must change one line:**
  `S3_ENDPOINT_URL=http://s3:9000` (it was `http://minio:9000`). Otherwise
  the api reports not ready - the probe refuses, correctly. Observed with
  the old line: api container unhealthy, /health/ready 503 with
  object_storage false, log "object storage not ready:
  EndpointConnectionError". The access and secret keys need no change.
- **Data:** see the migration and backup entry below.
- **Not verified:** behaviour under concurrent multi-writer load and very
  large objects (the app uses single-part put_object only); VersityGW's
  long-term maintenance beyond its release history; S3 operations the app
  does not use (presigned URLs, multipart, listing, deletes); a public
  deployment's configuration (TLS, IAM accounts instead of the root
  credentials, which the public deployment must not use); restore of a
  backup larger than this volume.
- **Test:** tests/unit/test_object_storage_readiness.py (the probe's
  decision table); the integration suite against the live s3 service.

## [B17 incident, RESOLVED in Phase 4 batch 2a (item 7)] An image reference with no tag means `:latest`, and nothing in the repo is pinned by digest

- **What happened:** `docker-compose.yml` said `image: minio/minio` and
  ci.yml ran `minio/minio` - no tag, so `:latest` at pull time. When the
  upstream image became unpullable, CI broke with no change to the repo.
- **Pin audit (2026-10-07, before the swap):**

  | Reference | Where | Tag | Digest | Cached locally | Resolves anonymously |
  |---|---|---|---|---|---|
  | `pgvector/pgvector:pg16` | compose:3, ci.yml | floating | no | PG 16.13, pgvector 0.8.2 | yes, digest moved |
  | `redis:7-alpine` | compose:19, ci.yml | floating | no | 7.4.8 | yes, moved |
  | `neo4j:5.26-community` | compose:31, ci.yml | floating | no | 5.26.28 | yes, moved |
  | `minio/minio` | compose:47, ci.yml | **none (latest)** | no | RELEASE.2025-09-07 | **no** |
  | `prom/prometheus` | compose:123 | **none (latest)** | no | 3.13.1 | yes, moved |
  | `grafana/grafana` | compose:131 | **none (latest)** | no | 13.1.1 | yes, moved |
  | `python:3.12-slim` | api and worker Dockerfiles | floating | no | 3.12.13 | yes, moved |
  | `node:22-slim` | web Dockerfile | floating | no | not cached | yes |

  Only the object store is pinned by digest now (the VersityGW swap). Every
  other reference still floats; `prom/prometheus` and `grafana/grafana`
  have no tag at all.
- **To do in Phase 0c:** pin every image by `tag@sha256:digest`, and pin
  GitHub Actions `uses:` by commit SHA. Not done here (out of scope for
  the swap).
- **Phase 4 item 7 (batch 2a), done:** every image is pinned as
  `tag@sha256:digest` at the version and digest that ran before pinning:
  `pgvector/pgvector:0.8.2-pg16`, `redis:7.4.8-alpine`,
  `neo4j:5.26.28-community`, `prom/prometheus:v3.13.1`,
  `grafana/grafana:13.1.1`, `python:3.12.13-slim`, `node:22.23.4-slim`,
  and uv 0.12.3 from `ghcr.io/astral-sh/uv` (a named FROM stage, replacing
  an unpinned `pip install uv`). The tags are exact versions, not the old
  floating ones, so each new release reaches us as a version bump that
  carries its digest. pgvector's, Neo4j's and Grafana's exact tags have
  been rebuilt since our copies were pulled, so the pinned digests are
  older builds of those tags. Every digest answered HTTP 200 to an
  anonymous registry request (2026-10-23). Actions are pinned by commit
  SHA at the releases Dependabot's #17 proposed (checkout v7.0.1, setup-uv
  v7.6.0, cache v6.1.0, setup-node v7.1.0, the SHAs its green run used);
  setup-uv installs uv 0.12.3 and setup-node Node 22.23.4, whose bundled
  npm 10.9.9 is what both `package.json` files name in `packageManager`
  (a CI step checks it, since npm does not enforce the field).
  setup-uv v3 to v7 changes defaults: from v5 it caches uv's downloads in
  the Actions cache on GitHub-hosted runners; v6 stopped activating a venv
  (we never relied on it); v7 runs on Node 24. Dependabot keeps the pins
  current through new `docker` (the three Dockerfiles) and
  `docker-compose` entries. It never reads ci.yml's service images or the
  setup-uv and setup-node version inputs, so `scripts/check_pins.py`
  (CI lint job) fails when those drift from the images, or when any
  reference is not pinned. Read in dependabot-core's source, not yet
  observed: an exact version tag gets version-bump PRs with the new
  digest; a digest-only refresh of an unchanged versioned tag can be
  suppressed by an experiment flag whose state on GitHub is not visible.
  Observed 2026-10-23 on the rebuilt stack: the same versions as before
  (PostgreSQL 16.13, pgvector 0.8.2, Redis 7.4.8, Neo4j 5.26.28, Python
  3.12.13); `check_pins.py` passes on the repo, and its tests plant each
  kind of drift in a copy and see it fail. CI on the pinned SHAs is
  observed only once this is pushed.

## [B17, backups kept, 2026-10-07] Object-store migration from MinIO to VersityGW, and the backups it left

- **Backups (all in `D:\TenderOS-private-backup\`, outside the repo and
  any synced folder; private):**

  | File | Size (bytes) | SHA-256 |
  |---|---|---|
  | `minio-RELEASE.2025-09-07T16-13-09Z.tar` (the old MinIO image, `docker save`) | 62,259,712 | `ff5c117e1c9bb62adb75c040abdcaa7501f0810c3ec4f18098501ba669516ace` |
  | `versitygw-v1.8.0.tar` (the pinned VersityGW image) | 31,522,816 | `6d796e94a1e7923f5abb6f1871d0def0acd803536f297c2a804dc7851c83afcd` |
  | `tenderos_minio_data-2026-10-07.tar` (old volume, read-only mount; MinIO keeps no xattr metadata) | 702,188,032 | `b4582fab71b8ad2d8010421df0ea5b7896e2b14c5515758663829899b25d12ed` |
  | `tenderos_s3_data-2026-10-07.xattrs.tar` (new volume, GNU tar with xattrs, after the migration) | 674,017,280 | `d93e8e9bd3be4b8899366750376a8d59be84addb7c19f236881c54687f9b46b9` |

- **Migration (scripts/migrate_object_store.py, old MinIO started from the
  saved image on a scratch volume restored from the tar - never the live
  volume):** bucket `tenderos`, 18,730 objects, 574,900,549 bytes copied
  and verified (SHA-256 and ContentType on every object, 0 mismatches,
  0 failures); a re-run skipped all 18,730 as identical.
- **References:** `documents.storage_key` (373 rows, the only column holding
  object keys) - all 373 present in the new store. 18,357 objects in the
  old `tenderos` bucket are referenced by no row (most likely local test
  uploads; see the next entry); they were copied anyway, nothing was
  deleted. Bucket `tenderos-ci` (479 objects) is referenced by no row and
  was not migrated.
- **What a missing object would break:** no screen or endpoint serves stored
  bytes. After ingestion, bytes are re-read only by commercial evaluation
  (`evaluate_commercial_for_tender`, .xlsx documents) and G9's manual
  re-validation (`ingest_pricing_workbook`), and by any re-ingestion. Review
  data already in Postgres does not need them.
- **The old volume `tenderos_minio_data` is kept, unchanged.** Only the
  owner may delete it, with `docker volume rm tenderos_minio_data`.

## [found during B17, RESOLVED on chore/deps-security, 2026-10-10 - see the entries at the end] Local test runs write their uploads into the app bucket

- `scripts/test-backend.env` does not set `S3_BUCKET`, so local pytest
  falls back to `.env`'s value - the app bucket `tenderos`. Only CI sets
  `S3_BUCKET=tenderos-ci`. That is the most likely source of the 18,357
  objects in the old `tenderos` bucket that no `documents` row references.
- During the swap's verification the suite was run with
  `S3_BUCKET=tenderos-ci` set in the process environment, so no test
  objects landed in the migrated bucket.
- **Proposal (not applied):** add `S3_BUCKET=tenderos-ci` to
  scripts/test-backend.env, matching CI.

## [found during B17, test-process only, RESOLVED in Phase 4 batch 1] Alembic's env.py disables every existing logger when migrations run inside pytest

- `packages/.../db/migrations/env.py` calls `logging.config.fileConfig()`,
  whose default `disable_existing_loggers=True` disables every logger that
  already exists. tests/integration/test_schema_diff.py runs the
  migrations in the pytest process, after which `tenderos_api.main`'s
  logger is `disabled=True` (observed) and log assertions see nothing.
- Not a production path: the api and worker never run Alembic in-process.
- tests/unit/test_object_storage_readiness.py re-enables that one logger
  per test. **Proposal (not applied):**
  `fileConfig(..., disable_existing_loggers=False)` in env.py.
- **RESOLVED in Phase 4 batch 1 (item 12, 2026-10-22):** env.py now passes
  `disable_existing_loggers=False`, and all seven workarounds are gone: the
  `main_logger_enabled` fixture in `tests/route/conftest.py` and its three
  uses, two re-enables in `test_idempotency.py`, one in
  `test_outbox_dispatcher.py`, and the autouse fixture here. Observed:
  with `test_schema_diff.py` first in one session, the log-asserting tests
  from those files pass (26 passed); with only the env.py line reverted,
  10 of them fail; the full suite passes. One of the seven re-enables
  (`test_original_cannot_release_after_a_takeover`) never asserted on a
  log, so it was dead code.

## [found during B17, measured 2026-10-10, re-measured 2026-10-22: 20 of 20 passed - see the entries at the end] Intermittent failures in tests/integration/test_outbox_dispatcher.py

- Observed during the swap's verification, against a fresh CI-style stack:
  the first full run had 2 failures
  (`test_dispatcher_marks_event_dispatched_on_success`,
  `test_workflow_run_created_routes_to_the_workflow_queue`); then 3 of 5
  runs of that file failed. Afterwards 11 runs of the file and a full run
  (648) all passed. The error text was not captured.
- One of the two named tests touches no object storage, so the swap is
  not the obvious cause; that is not proven either.
- **Next:** capture the failure (`-x`, full output) when it recurs.

## [chore/deps-security, RESOLVED 2026-10-10] Dependency audit: Python and npm advisories fixed, CI security job can exit 0

- **Python (uv.lock only, no pyproject change - no upper bound blocked any
  of them):** minimum fixing versions, one `uv lock --upgrade-package
  NAME==VERSION` per package, never a blanket upgrade:
  pyjwt 2.13.0 -> 2.15.0, pypdf 6.15.0 -> 6.19.0, urllib3 2.7.0 -> 2.8.0,
  virtualenv 21.7.0 -> 21.7.13, langgraph-sdk 0.4.2 -> 0.4.4. One forced
  transitive change: python-discovery 1.5.0 -> 1.6.2 (virtualenv 21.7.13
  requires `python-discovery>=1.6`); 1.6.2 no longer depends on
  platformdirs, which stays locked for pip-audit and virtualenv. Nothing
  else in the lockfile moved.
- **pip-audit, run as ci.yml runs it (`uv run pip-audit` after `uv sync`),
  measured 2026-10-10:** "No known vulnerabilities found", exit 0, with the
  four skip rows below still printed. Results depend on the date: the
  advisory databases change without any code change.
- **npm, lockfiles only, `npm audit fix` without `--force` (web) and
  `npm update source-map-js` (landing); no package.json change, no major
  bump:**
  - `apps/web/package-lock.json`, 5 entries for the 4 advisory packages:
    brace-expansion 5.0.9 -> 5.0.12, the nested
    `minimatch/node_modules/brace-expansion` 1.1.18 -> 1.1.21 (also inside
    the advisory range), js-yaml 4.3.1 -> 4.3.2, source-map-js 1.2.1 ->
    1.2.2, undici 7.29.0 -> 7.30.0. Every step of ci.yml's web job passed
    locally: `npm ci`, `npx tsc -b`, `npm run lint`, `npm test` (37 passed).
    `npm audit --audit-level=high` exit 0.
  - `apps/landing/package-lock.json`, 1 entry: source-map-js 1.2.1 -> 1.2.2
    (required by postcss 8.5.28 as `^1.2.1`). Every step of ci.yml's
    landing job passed locally: `npm ci`, typecheck, lint, build.
    `npm audit --audit-level=high` exit 0.
  - `npm ci` warns EBADENGINE for eslint-visitor-keys on this machine's
    Node 22.12.0 (needs ^22.13.0). Pre-existing, not touched by this
    change; CI's `node-version: "22"` resolves a newer 22.x.
- **Verified after the upgrade (measured):** backend suite 648 passed via
  scripts/test-backend.ps1; mypy 0 issues (248 files); ruff 0; the rebuilt
  api and worker images carry pyjwt 2.15.0, pypdf 6.19.0, urllib3 2.8.0,
  langgraph-sdk 0.4.4; fake E2E (scripts/fake-stack.ps1 then
  scripts/e2e.ps1, LLM_PROVIDER=fake in api, worker and
  outbox_dispatcher): 1 passed - "golden path: login through readiness,
  live Docker stack", 2.7 min, results fake (FakeLLMClient).
- **Test:** the CI `security` job.

## [chore/deps-security, analysed 2026-10-10] PYSEC-2026-4146 (pyjwt): not reachable from our code, and cleared by 2.15.0 - no ignore added

- **Advisory (GHSA-gvp8-978c-rx2q, CVE-2026-103001), as pip-audit printed
  it:** `jwt.decode()`/`decode_complete()` write `verify_exp`,
  `verify_aud` and five more keys into a caller-supplied `options` dict
  when `verify_signature` is falsy. If the caller reuses that same dict in
  a later verifying call, the registered-claim checks stay off and an
  expired or wrong-audience token is accepted. The signature check is not
  affected. Precondition: one mutable `options` object reused across calls.
  pip-audit listed no fix version.
- **Our usage, every call site:** sign at
  `packages/tenderos_backend/src/tenderos_backend/core/security.py:35`
  (`jwt.encode(..., algorithm="HS256")`); verify at `security.py:47`
  (`jwt.decode(token, key, algorithms=["HS256"])`, **no `options`
  argument**). With `options=None`, `_merge_options` returns its own
  defaults and never reaches the code that writes to the dict. Elsewhere only
  `jwt.PyJWTError` is caught
  (`apps/api/src/tenderos_api/dependencies.py:38`,
  `domain/services/auth_service.py:94`). No other locked package depends
  on pyjwt; no test passes `options`. **Not reachable.**
- **Fixed upstream anyway:** OSV records the affected range as 2.11.0 to
  2.13.0 (`last_affected`). The 2.15.0 and 2.15.1 tags copy the dict
  (`merged_options = dict(options)`), and the advisory's own PoC, run
  against the installed 2.15.0, raises `ExpiredSignatureError` and leaves
  the caller's dict unchanged (measured). pip-audit no longer reports it,
  so no `--ignore-vuln` was added to ci.yml.
- **Re-check if:** any `jwt.decode` call ever starts passing `options`, or
  pip-audit reports this ID again for the locked version.

## [chore/deps-security, documented] What pip-audit and npm audit do not cover

- pip-audit prints four "Skip Reason" rows and does not audit them:
  `tenderos-api`, `tenderos-backend`, `tenderos-worker` (our own workspace
  packages, not on PyPI) and `torch 2.13.0+cpu` (the CPU build from the
  PyTorch index; the `+cpu` local version is not on PyPI). CI runs
  pip-audit without `--strict`, so skips do not fail the job - and torch
  advisories are therefore never seen by CI. Checking torch means reading
  PyTorch's own security advisories by hand.
- CI's `npm audit` covers `apps/web` only. `apps/landing` has its own
  lockfile and no audit step (its one high advisory was found by hand and
  fixed above). Adding a landing audit step is deferred, by decision.
  **Resolved in Phase 4 batch 1 (item 10):** the `security` job now runs
  `npm ci` and `npm audit --audit-level=high` in `apps/landing` too.
- dependabot.yml has no npm entry for `apps/landing` either. **Resolved in
  batch 1 (item 26):** it has one now.
- torch: automated in batch 1 (item 8), see the torch entry below.
- **Checked, not a gap:** virtualenv and pre-commit (root `dev` group) are
  **not** installed in the api or worker images (measured in `/app/.venv`
  of the running containers). `uv export --package tenderos-api` lists
  them, but `uv sync --frozen --package ...` and the CMD's `uv run
  --frozen --package ...` do not install the root dev group. The planned
  "virtualenv in images" Phase 4 hardening item was based on the export
  output and turned out to be false; nothing to harden.

## [chore/deps-security, RESOLVED 2026-10-10] Local test uploads now go to tenderos-ci

- `scripts/test-backend.env` sets `S3_BUCKET=tenderos-ci`, the same value
  ci.yml's test job sets. One file, so test-backend.ps1, the Makefile and
  CI all agree.
- **Before (measured, the two S3-using test files via test-backend.ps1, 17
  passed):** app bucket 18,739 -> 18,758 (+19), tenderos-ci unchanged.
- **After (measured, full suite, 648 passed):** app bucket 18,758 ->
  18,758 (unchanged), tenderos-ci 654 -> 825 (+171).
- A missing bucket is created on first use (`ObjectStorage._ensure_bucket`,
  head then create, `db/storage.py:33-47`): proven with the real adapter
  against a fresh `tenderos-ci-probe-<timestamp>` bucket (absent, created
  on first put, round-trip ok), which was then emptied and deleted.
- The 18,357 unreferenced objects already in the app bucket are untouched.

## [chore/deps-security, measured 2026-10-10] Object store: a maximum-size object round-trips through the real adapter; the HTTP limit is slightly below it

- Configured maximum: `max_upload_size_mb: int = 50`
  (`core/config.py:28`; `.env` does not override it) = 52,428,800 bytes.
- `ObjectStorage.put` is a single `put_object` call, not multipart
  (`db/storage.py:52-54`); the stored ETag has no `-N` part suffix.
- Probe against VersityGW (localhost, bucket tenderos-ci, temporary key
  under `tmp/`): 52,428,800 random bytes, put 0.67 s, get 0.30 s
  (measured, one run), SHA-256 equal, ContentLength equal; deleted with
  `delete_object`, then `head_object` returned 404.
- **Open, not fixed:** the API's body-size middleware rejects any request
  whose Content-Length exceeds 50 MiB (`apps/api/src/tenderos_api/main.py:
  139,152`), and that counts the multipart framing too. A file of exactly
  50 MiB therefore gets 413 from the middleware before `_read_bounded`
  (`routes/documents.py:123-141`, which allows exactly 50 MiB) ever sees
  it. The effective upload limit is 50 MiB minus the framing (a few hundred
  bytes). Inferred from the code, not measured with an HTTP upload.

## [found 2026-10-10, RESOLVED in Phase 4 batch 2a (item 5, decision 0042)] The dev .env's JWT signing key is 8 bytes

- pyjwt emits `InsecureKeyLengthWarning: The HMAC key is 8 bytes long,
  which is below the minimum recommended length of 32 bytes for SHA256`
  in the dispatcher test runs that took their key from `.env` (length
  measured; the value was never printed). `.env` is untracked and local;
  CI uses its own longer throwaway key.
- **Risk:** none locally; any deployment that copies `.env` as-is would
  sign tokens with a guessable key. Settings does not enforce a minimum
  length.
- **Phase 4 item 5 (batch 2a), decision 0042:** importing
  `tenderos_backend.core.config` now refuses a key shorter than 32 bytes
  (UTF-8 bytes, what HS256 and pyjwt measure) in every environment, so
  the api, both workers, the dispatcher and the migrate service all
  refuse to start; the error names `JWT_SIGNING_KEY` and the minimum,
  never the value. With `APP_ENV=production` they also refuse
  `changeme`/empty model names and credentials (the S3 keys, as decided,
  and by the same rule the Neo4j password and the password in
  `POSTGRES_DSN`), and the api refuses a localhost or plain-http
  `WEB_BASE_URL`, rate limiting off, or the E2E bypass on. Each module
  lists all of its own problems in one error, the backend's first: the
  api reports its own only once the backend's are fixed (observed). No
  compose overlay sets `APP_ENV`, so the E2E
  stack runs with the local `.env`'s value. Tests:
  `tests/unit/test_production_settings.py`. Observed 2026-10-23 in the
  api image (`docker compose run`, every relevant variable set
  explicitly): a 15-byte key refused with "JWT_SIGNING_KEY is 15 bytes;
  at least 32 are required ..." and no part of the value; production with
  placeholders refused, listing REASONING_MODEL, NEO4J_PASSWORD and
  POSTGRES_DSN; production with only API-side problems refused, listing
  WEB_BASE_URL, RATE_LIMIT_ENABLED and E2E_RATE_LIMIT_BYPASS_ENABLED; good
  production values imported. The local `.env` key was replaced (64
  bytes, length measured only), and the suite's only warning is now the
  Starlette test-client one: no `InsecureKeyLengthWarning`.

## [measured 2026-10-10; re-measured 2026-10-22 (Phase 4 batch 1): 20 of 20 passed] test_outbox_dispatcher.py flake: counts, two signatures, and a clock-skew mechanism

- **Method:** two scratch clones, at df3dd96 and at 8c275e5 (before the
  VersityGW swap), each with its own `uv sync`; the file run 20 times per
  commit, one after the other, with `--tb=long`, against the same local
  postgres, redis and neo4j (app containers stopped), with
  test-backend.ps1's environment and guards. The file needs no object
  storage (imports at lines 1-16; the integration conftest's autouse
  fixtures are Postgres-only), so both commits used identical infra.
- **Counts (measured):** df3dd96 2/20 failing runs; 8c275e5 3/20. The
  flake predates the swap.
- **Signatures:**
  1. `test_two_concurrent_dispatchers_claim_each_event_exactly_once`,
     `test_outbox_dispatcher.py:177` - not every one of the 10 events is
     `dispatched` after two concurrent `_dispatch_once()` calls (df3dd96
     runs 1 and 15; 8c275e5 runs 7 and 19).
  2. `test_dispatcher_marks_event_dispatched_on_success`,
     `test_outbox_dispatcher.py:38` - the tender's event is still pending
     after `_dispatch_once()` (8c275e5 run 9).
  Neither assertion prints the events' statuses, so the failure text alone
  does not name a cause.
- **Likely mechanism (reproduced in isolation, not yet proven to be the
  cause of these failures):** `outbox_events.available_at` defaults to
  Postgres `now()` (`db/models/outbox.py:29-31`) - the Docker VM's clock,
  at transaction start - while `get_pending` and `claim_by_id` filter
  `available_at <= datetime.now(UTC)` - the host's clock, sent as a
  parameter (`db/repositories/outbox.py:41,81`). Measured skew: Postgres
  about +2 ms ahead of the host (8 samples, +1.8 to +3.4 ms). A
  session-local temp-table probe (insert with `default now()`, then filter
  with the host's now immediately) found the fresh row invisible 499 of 500
  times. Both failing tests create events and dispatch at once; when the
  gap is shorter than the current skew, the event is skipped and stays
  pending. WSL2 clock drift varies over time, which fits failures that
  come in clusters.
- **Second Windows-specific finding:** `test_redis_unavailable_...` points
  Redis at `localhost:1`, expecting an instant refusal (comment at lines
  102-106). On this host a refused connect takes about 2.0 s per address
  family, and a redis-py ping to `localhost:1` took 4.2 s (measured). The
  test still passes but costs seconds per enqueue attempt.
- **Unexplained:** df3dd96 run 1 took 1,149 s (median run 53 s). A py-spy
  sample during a later, normal run showed time spent in the
  `localhost:1` connect; whether that is what stretched run 1 is not
  known (no per-test durations were recorded).
- The dispatcher holds the `FOR UPDATE SKIP LOCKED` row lock across a
  blocking `Queue.enqueue` (`apps/worker/src/tenderos_worker/
  outbox_dispatcher.py:218-258`), which also blocks the event loop, so the
  two `gather`ed dispatchers in signature 1 run effectively one after the
  other.
- **Next (not done):** make the assertions print each event's status and
  `available_at`, then decide whether the comparison should use one clock
  (e.g. `func.now()` on the database side).
- **Follow-up from code reading only (2026-10-10, no new runs, no code
  change):**
  1. **Redis timeouts.** The dispatcher builds its client as
     `Redis.from_url(settings.redis_url)` with no keyword arguments
     (`apps/worker/src/tenderos_worker/outbox_dispatcher.py:262`); the
     URL in `scripts/test-backend.env` carries no query parameters, and no
     file under apps/ or packages/ sets `socket_connect_timeout` or
     `socket_timeout`. Our code sets neither. The installed redis-py 8.0.1
     does, by default: `DEFAULT_SOCKET_TIMEOUT = 5` and
     `DEFAULT_SOCKET_CONNECT_TIMEOUT = DEFAULT_SOCKET_TIMEOUT`
     (`redis/_defaults.py:7-8`), applied per address in the connect loop
     (`redis/connection.py:1546-1567`, `sock.settimeout(
     self.socket_connect_timeout)` before `connect`). A pooled connection
     made through `from_url` gets `Retry(NoBackoff(), 0)` when no retry
     is passed and no retry-on-error list is configured
     (`redis/connection.py:884-894`), which fits the measured 4.2 s for one
     ping to `localhost:1` (two address families, about 2 s each, no
     retry). So one enqueue against the dead port is bounded at about 10 s
     (2 addresses x 5 s), and the 1,149 s run would need on the order of
     100 or more such attempts - nothing in the test creates that many
     events. **Correction to the wording above:** the py-spy sample that
     showed the stack inside the redis connect at
     `outbox_dispatcher.py:242` was taken during a later, normal run
     (df3dd96 run 2), not during the 18-minute run 1. Where run 1 spent
     its time is not known.
  2. **Can the claim's row lock be released before `Queue.enqueue`? No,
     as written.** `claim_by_id`'s `SELECT ... FOR UPDATE SKIP LOCKED`
     (`db/repositories/outbox.py:77-87`) opens the session's transaction
     (SQLAlchemy autobegin; plain `async_sessionmaker(engine,
     expire_on_commit=False)`, `db/session.py:23`). Between the claim
     (`outbox_dispatcher.py:218`) and the enqueue (`:242`) nothing commits
     or rolls back - `build_dispatch` is a pure function and the repository
     methods only `flush()` (`db/repositories/outbox.py:30,95,104`). The
     transaction ends only at `await uow.commit()` (`:258`) or at the
     rollback in `UnitOfWork.__aexit__` on an exception
     (`db/unit_of_work.py:83-84`). Releasing the lock earlier would need a
     commit before the enqueue, which reopens the double-dispatch race the
     lock exists to prevent unless the row is first moved to a separate
     "claimed" status.
  3. **The two failing tests and their signatures:**
     `test_two_concurrent_dispatchers_claim_each_event_exactly_once`
     fails at `test_outbox_dispatcher.py:177`
     (`AssertionError: every event must reach a real terminal dispatched
     state - none silently lost between two concurrent claimants`), 4 of
     the 5 failing runs; `test_dispatcher_marks_event_dispatched_on_success`
     fails at `test_outbox_dispatcher.py:38`
     (`assert all(e.aggregate_id != tender.id for e in still_pending)`),
     1 of 5. **Database clock ahead of host clock - likely, not proven to
     be the cause.** Evidence: `available_at` is stamped by Postgres
     `now()` (`db/models/outbox.py:29-31`) and filtered against the host's
     `datetime.now(UTC)` (`db/repositories/outbox.py:41,81`); the
     container's clock measured +1.8 to +3.4 ms ahead of the host (8
     samples, round trip about 1 ms); an insert-then-filter probe hid the
     fresh row 499 of 500 times. What is missing for proof: neither
     assertion prints the events' statuses or `available_at`, so no
     failing run shows the skipped rows directly.
  4. **CI job timeouts:** no job in `.github/workflows/ci.yml` sets
     `timeout-minutes` (0 occurrences; jobs lint, typecheck, test, web,
     landing, security), so GitHub's default of 360 minutes per job
     applies. A hang like run 1 in CI would hold a runner for up to 6
     hours. Reported only; ci.yml unchanged.
- **Re-measured in Phase 4 batch 1 (item 16, 2026-10-22): 20 of 20 runs
  passed.** `13414ed` (blocker 3) moved every outbox time comparison to the
  database clock, which removes the clock-skew mechanism above by
  construction; this is the 20-run count that was not repeated then.
  Method: the file run 20 times one after the other through
  `scripts/test-backend.ps1` with `--tb=long`, app containers stopped,
  against the same local postgres, redis and neo4j. Every run: 16 passed,
  exit 0, 31.1 to 32.1 s; both formerly failing tests
  (`test_dispatcher_marks_event_dispatched_on_success`,
  `test_two_concurrent_dispatchers_claim_each_event_exactly_once`) ran in
  each. Differences from the 2026-10-10 method: the working tree of batch
  1 (which also carries the new connection-leak guard), not scratch clones
  at old commits. 20 passing runs bound the failure rate; they do not prove
  it is zero (2/20 and 3/20 before).
- **Status (batch 1):** closed as measured; reopen with the failing run's
  `--tb=long` output if it recurs. The 1,149 s run above stays
  unexplained; it was not looked into again.

## [found 2026-10-10 (phase1/design survey), open, docs only] `tenders.status` is never written by the workflow; the dashboard's Status column reads "created" for every tender, and the comment at `BidDashboard.tsx:7-9` is wrong

- **Observed (measured, live dev database, 2026-10-10):** all 47 tenders
  have `status = 'created'` (`select distinct status from tenders` returns
  one row), including tenders whose latest run is `waiting_for_human`,
  `running` or `failed`. The Bids dashboard renders that column directly
  (`apps/web/src/routes/BidDashboard.tsx:70`), so every bid shows
  "created" (screenshot `02_dashboard`, fake-provider data, scratchpad
  only).
- **Mechanism, read directly:** the only write is
  `TenderRepository.update`'s optional `status` argument
  (`packages/tenderos_backend/src/tenderos_backend/db/repositories/tenders.py:60`),
  reached only from `PATCH /tenders/{id}`
  (`apps/api/src/tenderos_api/routes/tenders.py:24`). Nothing in the
  workflow, the run-status sync (`core/enums.py::coarse_status_for`) or
  any node sets it. The column defaults to `created`
  (`db/models/tender.py:21`).
- **The comment is the bug that hid it:** `BidDashboard.tsx:7-9` says the
  list endpoint "returns the coarse 6-value tenders.status column directly
  (core/enums.py::coarse_status_for)". `coarse_status_for` maps
  `workflow_runs.status`, not `tenders.status`; no wiring connects them.
  Another instance of "a comment asserting a wiring that doesn't exist".
- **Pattern: column exists, nothing populates it - fourth instance.** The
  earlier three in this file: `knowledge_import_batches.status` never
  advances past `queued` (`[B6, re-confirmed B9]` entry),
  `tenders.state_version` was never bumped (`[B7/B8]` entry), and
  `agent_traces.estimated_cost_usd` was never populated (`[B16]` entry,
  since resolved). This file has no single heading carrying a count for
  this shape; the count here is assembled from those three entries.
- **Decision recorded in DESIGN.md (decision 9):** restyle batch 2 ships
  the dashboard without a Status column, and the tender-header status badge
  appears only on screens that already fetch the latest run. The real fix
  is a separate backend block - the workflow writes `tenders.status` from
  the coarse run status, or the tender list endpoint returns the latest
  run's status - and it must land before any batch shows the column again.
- **Test:** none. No test asserts the dashboard's Status column (checked:
  `full-flow.spec.ts` only clicks the tender link there, `:513`).

## [found 2026-10-10 (phase1/design survey), open, docs only, cause MEASURED] A rate-limited request (429) is shown as "Could not reach the server"; the 429 carries no CORS header

- **Observed:** a first, unpaced screenshot pass tripped B15's limiter
  (100 requests and 5 logins per 60 s per IP,
  `apps/api/src/tenderos_api/rate_limit.py:32-35`); the API log showed
  `POST /auth/login` 429 and repeated `GET /auth/me` 429, and the login
  form displayed "Could not reach the server. Please try again." That is
  false: the server was reached and refused. The text is what
  `apps/web/src/api/formatErrorDetail.ts:12-13` returns for any error that
  is not an `ApiError`, i.e. for a `fetch` that threw.
- **Cause (measured 2026-10-10, fake-provider stack):** seven
  `POST /auth/login` calls with a wrong password and
  `Origin: http://localhost:5173`, sent with curl (status codes and
  `access-control-*` header names only were printed). Attempts 1-5: `401`
  with `access-control-allow-origin`. Attempts 6-7: `429` with
  `Retry-After` and **no `access-control-*` header at all**. A browser
  discards a cross-origin response without `Access-Control-Allow-Origin`,
  so page code sees a network `TypeError`, never the 429.
- **Why the 429 skips CORS (read directly):** FastAPI's
  `@app.middleware("http")` calls `add_middleware(BaseHTTPMiddleware, ...)`
  (fastapi 0.140.13, `fastapi/applications.py:4718`); Starlette's
  `add_middleware` does `user_middleware.insert(0, ...)`
  (starlette 1.3.1, `starlette/applications.py:101`) and
  `build_middleware_stack` wraps `reversed(...)` (`:75`), so the
  **last-registered middleware is outermost**. `app.user_middleware` is,
  measured from the imported app: `_rate_limit`, `_max_body_size`,
  `_security_headers`, `CORSMiddleware`. `_rate_limit`
  (`apps/api/src/tenderos_api/main.py:182`) is outermost and returns its
  `JSONResponse(429)` itself (`main.py:238-241` and `:252-258`), so the response never
  passes through `CORSMiddleware` (`main.py:70`). The same applies to any
  other response `_max_body_size` or `_security_headers` short-circuits
  (not measured).
- **Consequences:** the UI cannot tell a rate limit from a network drop,
  cannot read `Retry-After`, and shows false copy. `retryTransient` still
  retries (a non-`ApiError` counts as retryable,
  `apps/web/src/api/retryTransient.ts:54-56`), but with its own short
  backoff (3 attempts by default, `:37`) rather than the server's
  `Retry-After`, against a fixed window of up to 60 s.
- **Bearing on the B16 `/auth/me` bounce** (the `Login.tsx` "already
  logged in" entry, which recorded 24 consecutive 429s on `GET /auth/me`
  server-side): consistent with it, not proven to be it. In the browser
  those 429s arrived as network errors; `AuthContext.refresh()` retries
  them through `retryTransient`, exhausts its attempts well inside the
  60 s window, and sets `meResponse` to `null`, which `RequireAuth` turns
  into a redirect to `/login`. A readable 429 with `Retry-After` would at
  least let the client wait the right amount. A deterministic repro needs
  no LLM at all (so no paid run): the fake stack, a logged-in browser
  session on `localhost:5173`, the rate-limit counter for the browser's
  client IP pushed past 100 within one window (100 cheap requests from
  the same IP, or setting `ratelimit:global:<ip>` in Redis DB 0), then one
  deep-link navigation, asserting the page saw `TypeError` rather than a
  429 and where it landed. A backend route test can pin the header half
  alone: a 429 from the limiter must carry `Access-Control-Allow-Origin`
  for an allowed origin. Neither is built.
- **Fix candidates, not decided:** register `CORSMiddleware` last so it is
  outermost, or add the CORS headers to the limiter's own responses. Both
  touch B15's middleware order, so they need their own review.
- **Test:** none.

## [Phase 4 blocker 1, 2026-10-12, resolved with four accepted limits] CORS on every response, fail-open rate limiter, readable rate-limit errors

Resolves the entry above, "A rate-limited request (429) is shown as
'Could not reach the server'; the 429 carries no CORS header".

- **Discovered:** Phase 4 backend-hardening review (read-only), then
  built on `phase4/backend-hardening`.
- **Behaviour (fixed):** `CORSMiddleware` is now registered last, so it
  is the outermost user middleware, and it exposes `Retry-After`
  (`apps/api/src/tenderos_api/main.py:338-351`). The 429, the 413 and a
  new catch-all 500 (`_catch_unhandled_exceptions`, `main.py:280`,
  code `INTERNAL_ERROR`, generic message, traceback logged) now carry
  `Access-Control-Allow-Origin` and the security headers. The web app
  reads the API's `{"error": {"code", "message"}}` envelope, shows a
  429 as "Too many attempts" with the wait from `Retry-After`, and no
  longer auto-retries a 429 (`apps/web/src/api/retryTransient.ts:59`).
  Observed live on the fake-provider stack (real, 2026-10-12): the 6th
  `POST /auth/login` with `Origin: http://localhost:5173` returned 429
  with `access-control-allow-origin: http://localhost:5173`,
  `access-control-expose-headers: Retry-After` and `retry-after: 60`.
- **Accepted limits:**
  1. **The limiter fails open.** On a Redis connection error or timeout
     (`REDIS_UNAVAILABLE_ERRORS`, `rate_limit.py:52`; timeouts 0.25 s
     each, `rate_limit.py:61-62`, design numbers, not measured) the
     request is allowed (`main.py:268`). While Redis is unavailable there
     is no rate limiting at all, the login limit included, and one
     WARNING is logged per request: there is no log throttling, so an
     outage under load produces one log line per request.
     **Changed in Phase 4 batch 2b (plan item 2, decision 0041).**
     `POST /auth/login` now fails closed: while the limiter cannot reach
     Redis it answers 503 `LOGIN_UNAVAILABLE` with `Retry-After: 30`
     (`main.py:284-295`, `rate_limit.py:69`, a design number), inside
     CORS and the security headers like the 429. Every other route still
     fails open. A signed-in user keeps working for the life of their
     access token (a JWT verified without Redis; `jwt_access_minutes`,
     15, configured). `POST /auth/refresh` needs no Redis either (it
     decodes the refresh JWT and reads the user from Postgres; there is
     no refresh-token store) and fails open, so a client that refreshes
     keeps its session; but the web app never calls refresh
     (`getRefreshToken()` in `apps/web/src/api/client.ts` has no caller),
     so a web user whose access token expires during the outage must log
     in again and gets the 503. (Superseded 2026-10-24 by plan item 34: the
     web app calls refresh on a 401 since option 1, and since option 3
     refresh rotates the token in Postgres, still with no Redis call; see
     the session entry near the end of this file.) The outage is logged on the first
     failure, then at most once per 60 s per process with a count of the
     requests it covers (`OutageLog`, `rate_limit.py:72-103`). The login
     screen says "Sign-in is briefly unavailable. Try again in N
     seconds." with N from `Retry-After`, and does not retry the login
     itself. What stays open: with Redis down, nobody can start a new
     session; the rest of the API is still unlimited for the outage.
     Observed live 2026-10-23 on the fake stack with `docker compose stop
     redis`: the browser's one `POST /auth/login` got 503 and the screen
     read "Sign-in is briefly unavailable. Try again in 30 seconds." (no
     second request); two `GET /auth/me` calls in the same outage failed
     open (401 from the route, no token yet); the api logged one WARNING
     for all three. After `docker compose start redis`, the same login
     returned 200.
  2. **A 429 on `/auth/me` ends the session view.** `AuthContext.refresh()`
     no longer retries a 429, so it goes straight to
     `setMeResponse(null)` and keeps the token
     (`apps/web/src/auth/AuthContext.tsx:26-37`): the user is shown as
     logged out until a later refresh succeeds. Before, it retried for
     about 3 s and in practice ended the same way inside a 60 s window.
  3. **One bucket per proxy.** The limiter keys on `request.client.host`
     (`main.py:252`, `rate_limit.py:145`). Behind a reverse proxy or load
     balancer that is the proxy's IP, so every user would share one
     bucket (100 requests and 5 logins per 60 s, configured numbers).
     The fix is planned in Phase 4 with the deployment host.
  4. **Test session only: Alembic disables existing loggers.**
     `packages/tenderos_backend/src/tenderos_backend/db/migrations/env.py:14`
     calls `fileConfig()`, whose default `disable_existing_loggers=True`
     disables `tenderos_api.main`'s logger for the rest of a pytest
     session once `tests/integration/test_schema_diff.py` has run Alembic
     in-process. Found when the new log-asserting tests passed alone and
     failed in the full suite; reproduced with that one file run first.
     Worked around with the opt-in `main_logger_enabled` fixture
     (in `tests/route/conftest.py` until `f93814d` removed it). A real API process never runs
     Alembic, so production logging is unaffected. The root-cause fix
     (`disable_existing_loggers=False`) is planned as a separate small
     commit. **Resolved in Phase 4 batch 1 (item 12)**; see the Alembic
     entry above.
- **Risk:** limit 3 matters at deployment: a proxy in front of the API
  turns per-user limits into one shared limit. Limit 1's login part (an
  attacker who can make Redis unavailable also disabled brute-force
  protection on login) is closed by batch 2b; what is left of it is an
  unlimited API, not an unlimited login, for the length of an outage.
- **Status:** resolved (the CORS and UI defects); limit 1 changed in
  Phase 4 batch 2b (login fails closed, outage logged once per interval),
  the rest of it accepted; limits 2 and 3 accepted; limit 4 resolved in
  Phase 4 batch 1 (item 12).
- **Test:** `tests/route/test_middleware_order.py` (full middleware order,
  CORS and security headers on 429/413/500, the 500 envelope with no
  internal detail), `tests/route/test_rate_limit_middleware.py` (a real
  unreachable Redis: login gets 503 with `Retry-After`, CORS and the
  security headers; another route still passes; a signed-in request still
  works; `POST /auth/refresh` still issues an access token; ten requests
  give one WARNING and the next interval's line
  counts them; a failure on the login check alone also gives the 503),
  `tests/unit/test_rate_limit.py` (the outage log's interval),
  `apps/web/src/routes/Login.test.tsx` (the message with N from
  `Retry-After`, one fetch, no automatic retry),
  `apps/web/src/api/formatErrorDetail.test.ts`, and
  `apps/web/src/api/retryTransient.test.ts` (a 429 is not retried).

## [Phase 4 blocker 2, 2026-10-13, open, accepted] Idempotency leases: what a lease cannot make safe

- **Discovered:** Phase 4 backend-hardening review, then built on
  `phase4/backend-hardening` (migration `019_idempotency_lease`).
- **Behaviour (fixed):** an `in_progress` idempotency record used to stay
  `in_progress` forever if its request died or raised before `finalize()`,
  so every retry with that key got 409 for good. Records now carry a lease
  (`locked_until`, `claim_token`): a live lease answers a retry with 409
  `IDEMPOTENCY_IN_PROGRESS` and `Retry-After`; a lapsed lease (or a NULL
  one, from before migration 019) is taken over by one conditional UPDATE
  (`IdempotencyRepository.take_over_expired`); any exception from the
  route releases the record (`get_idempotency_context` and `IdempotencyContext.release` in `apps/api/src/tenderos_api/idempotency.py`);
  `finalize` and `release` act only while the caller still holds the
  claim. The lease is 60 s (`idempotency.py:30`), a design number, not
  measured, and every time value is on the database clock.
- **Accepted limits:**
  1. **Window 2: work committed, then the process dies before
     `finalize()`.** The record stays `in_progress` with a lease that
     lapses; the next retry takes it over and runs the work again. The
     route's work and `finalize()` are separate transactions, so nothing
     ties "done" to "recorded". Gate decisions are naturally idempotent
     (`gate_service.record_decision` replays an identical decision,
     `domain/services/gate_service.py:37-43`); `create_tender` is not, so
     this window can create a second tender. **RESOLVED for `create_tender`
     (plan item 21):** see below.
  2. **An exception after the work committed releases the key.** The yield
     dependency cannot tell "failed before the work" from "failed after it"
     (for example `finalize()` itself failing, or an error building the
     response). It releases either way, and a retry repeats committed work,
     with the same consequence as limit 1. **RESOLVED for `create_tender`
     (plan item 21):** see below.
  3. **RESOLVED 2026-10-16 (Phase 4 async operations, Stage 2): proposal
     generation outlasted the lease.** `POST
     /tenders/{id}/proposal/generate` called the LLM inline: 6 section
     templates, each a draft call plus a possible repair call, with the
     Gemini adapter's own rate-limit backoff of 5, 10 and 20 s per call
     (configured numbers). With a real provider that could exceed 60 s
     (estimate, not measured), the lease lapsed mid-request, and a retry
     with the same key took the record over and started a second,
     concurrent generation, repeating the LLM spend. The route now records
     a `proposal_generation` async operation and returns 202
     (`apps/api/src/tenderos_api/routes/proposal.py`), so the key records
     and replays that fast response, well inside the lease; the drafting
     runs in the worker under the operation's own claim, and a second
     request while one is active returns the same operation. See the
     Stage 2 entry at the end.
  4. **Replay ignores the stored status code.** Every route unpacks
     `replay_response` as `_, cached_body` (e.g.
     `apps/api/src/tenderos_api/routes/organizations.py:24`) and returns
     the body with its own fixed status code (201, 202 or 200). That equals
     the stored code today only because each route has exactly one success
     code; a route that could succeed with two different codes would replay
     the wrong one. **RESOLVED in Phase 4 batch 2b (plan item 21):** the
     dependency sets the stored code on the response FastAPI shares with
     dependencies (`idempotency.py:157-158`), which overrides the route's
     default, so all seven replaying routes answer with the code first
     stored; the routes themselves are unchanged.
  5. **Expired rows are not yet removed.** `IdempotencyRepository.
     delete_expired` exists and is tested, but nothing calls it yet; Phase 4
     blocker 3 runs it from the outbox dispatcher's loop. **RESOLVED by
     `13414ed`:** the dispatcher's loop calls it
     (`_delete_expired_idempotency_records` in `apps/worker/src/tenderos_worker/outbox_dispatcher.py`); marked
     here in batch 2b.
  6. **A cancelled request does not release its key.** When a client
     disconnects, the request is cancelled with `asyncio.CancelledError`,
     which is not an `Exception`, so the dependency's release-on-error path
     (`get_idempotency_context` and `IdempotencyContext.release` in `apps/api/src/tenderos_api/idempotency.py`) does not run. The
     record stays `in_progress` until its lease runs out; a retry with the
     same key gets `IDEMPOTENCY_IN_PROGRESS` for up to 60 s (the lease, a
     design number) and then takes the record over. **RESOLVED in Phase 4
     batch 2b (plan item 21):** the dependency catches `BaseException`,
     releases, and re-raises (`idempotency.py:164-166`). Catching it was
     not enough on its own (observed): the route runs inside
     `BaseHTTPMiddleware`'s anyio task group, whose cancellation is
     level-triggered, so the release's own first database call was
     cancelled too. The release now runs in a shielded anyio scope,
     bounded at 5 s (`_RELEASE_TIMEOUT_SECONDS`, a design number, inside
     the api's 20 s stop grace) so a stopping server cannot hang on it;
     past that, the lease runs out as before. A failure or cancellation
     that arrives after the route called `finalize()` also runs the
     release, which is safe because `IdempotencyRepository.release`
     deletes only a record still `in_progress`: the completed record
     stays and the retry replays it (tested both ways).
- **Limits 1 and 2, `create_tender` (plan item 21, 2026-11-05):** the
  route completes its record in the tender's own transaction
  (`IdempotencyContext.finalize_in`, through `tender_service.create_tender`'s
  `before_commit`), so the tender, its outbox event, its audit row and the
  replayable response commit as one. A crash after that commit leaves a
  completed record, and the retry replays with no wait. A crash before it
  leaves nothing, and the retry waits out the lease and does the work once.
  An exception after it finds a record `release` cannot delete. New
  behaviour: a request whose claim was taken over while it worked rolls its
  own work back and answers 409 `IDEMPOTENCY_IN_PROGRESS`, `Retry-After: 1`;
  it used to keep its tender and log a warning, which is how one key made
  two. The lease (60 s) and what counts as lapsed are unchanged.
- **Limits 1 and 2, the other six routes (accepted):** they still finalize
  in a separate transaction, so a crash in that window, or an exception
  after their work committed, still repeats the work on retry. What a repeat
  does (read in code, none of it observed):
  - `decide_gate`: nothing; `record_decision` replays an identical decision.
  - `trigger_graph_projection`: one more projection run; the projection is a
    rebuild, so the graph ends the same.
  - `set_knowledge_selection`, `select_candidate`: the same selection, plus
    one more audit row and one more `state_version` bump, so a report built
    on the old version reads as stale and is generated again.
  - `generate_proposal`: the active operation if it is still running, else
    a second generation, which repeats the LLM spend on a live provider.
  - `create_workflow_run`: 409 `WORKFLOW_RUN_ACTIVE` naming the first run
    while it is active (decision 0065), else a second run, which repeats
    the LLM spend on a live provider.
  None makes a duplicate business record, which is why `create_tender`
  came first. The last two are the ones worth doing next; each touches what
  items 43 and 19 changed, so each gets a design first.
- **Risk:** on the six routes above, the repeats listed, after a crash or a
  late failure in a narrow window. (Limit 3's duplicate real-provider
  proposal generation was closed in Stage 2.)
- **Status:** limit 3 resolved (2026-10-16, Stage 2); limits 4 and 6
  resolved in Phase 4 batch 2b; limit 5 resolved by `13414ed` (the
  dispatcher calls `delete_expired`; this line was stale until batch 2b);
  limits 1 and 2 resolved for `create_tender` (plan item 21) and accepted
  for the other six routes.
- **Test:** `tests/integration/test_idempotency.py` (lease on the database
  clock, release on APIError and on a 500, live lease with Retry-After,
  takeover of an expired and of a NULL lease, exactly one winner between
  concurrent takers, an overtaken original can neither release nor
  finalize, bounded cleanup; batch 2b: a replay answers with a stored 200
  where the route's default is 201, and a request cancelled while it
  holds the key releases it, so the retry creates the one tender; item 21:
  a real process that dies with `os._exit` after the commit is replayed, and
  one that dies before it leaves no tender, outbox row or audit row, and the
  retry after the lease does the work once; an exception or a cancellation
  after the commit leaves the record completed and the retry replays it; an
  exception before it rolls everything back and releases the key; a request
  whose claim was taken over rolls its work back; a takeover waiting on the
  row lock loses to the commit). The other six routes' windows have no test.
  Limit 3's fix:
  `tests/route/test_proposal_routes.py::test_generate_proposal_returns_202_and_the_same_operation_while_active`
  (a same-key retry replays the 202; a second click joins the operation).

## [Phase 4 blocker 3, 2026-10-13, open, accepted] Outbox delivery is at-least-once; the consumer guard is the fix, not the dispatcher

- **Discovered:** Phase 4 backend-hardening review, then built on
  `phase4/backend-hardening` (no migration).
- **Behaviour (fixed):** the dispatcher compares and writes every time on
  the database clock (`now()`: eligibility, retry `available_at`,
  `dispatched_at`; `packages/tenderos_backend/src/tenderos_backend/db/repositories/outbox.py`),
  which removes by construction the clock-skew mechanism recorded above
  for the `test_outbox_dispatcher.py` flake (not re-measured over 20 runs
  in this block). It uses one Redis connection per process with 2 s
  socket and connect timeouts and no client retries
  (`apps/worker/src/tenderos_worker/outbox_dispatcher.py:71-72`, design
  numbers), enqueues off the event loop through `asyncio.to_thread` with
  RQ's `unique=True` (`:315`), treats an already-existing job as dispatched
  (`:317`), bounds the claiming transaction with `SET LOCAL lock_timeout`
  and `idle_in_transaction_session_timeout` of 30 s (`:82-83`), survives a
  failing tick with a logged, growing backoff (`:398`), and runs one
  bounded idempotency cleanup batch every 12 ticks (`:95`). api, worker
  and outbox_dispatcher restart unless stopped (`docker-compose.yml`).
  `run_workflow` invokes the graph only after claiming its run from
  `queued` to `running` in one conditional UPDATE
  (`apps/worker/src/tenderos_worker/jobs/workflow.py:146`,
  `db/repositories/workflow.py:119`).
- **Accepted limits:**
  1. **Delivery is at-least-once, by design.** The enqueue and the row's
     `dispatched` mark are in different systems, Redis and Postgres, with
     no shared transaction. `unique=True` turns a redelivery into a no-op
     only while the job's hash is still in Redis (a finished job's for
     500 s, RQ 2.10.0's default result TTL); after that, a redelivery
     enqueues the job again. What makes a duplicate harmless is each
     consumer's own guard.
  2. **Option A is designed, not built.** Claim the row (move it out of
     `pending` with a lease), commit, then enqueue with no transaction
     open, then mark it dispatched. That stops the row lock from being
     held across the network call, but it does not remove duplicates (a
     crash between the enqueue and the mark still redelivers), so the
     consumer guard is still the real fix, and it needs a new status or
     column. Not built in this block.
  3. **The row lock is still held across the enqueue.** One event's row
     lock and one pooled connection stay held while Redis answers,
     bounded by the 2 s socket timeouts (a worst case of roughly 12 s for
     one enqueue, an estimate) and backstopped by the 30 s
     `SET LOCAL` timeouts. Only the dispatcher touches outbox rows, so
     the lock blocks no request path.
  4. **Consumers that still repeat work on a duplicate.** Only
     `run_workflow` is guarded. `parse_and_embed_document` re-parses and
     re-embeds the document (local SentenceTransformer, CPU time, no
     money; chunk rows converge on their unique key). `verify_requirement_job`
     repeats its LLM verification call, which costs one real-provider call
     per duplicate. `process_knowledge_document_import`,
     `reanalyze_knowledge_selection_change` and `resume_workflow` have
     proposed guards, not built (since built: "Phase 4 consumer guards"
     below, and plan item 18 for reanalysis): the resume case is the
     dangerous one, since
     a duplicate `Command(resume=...)` can be consumed by a later gate's
     interrupt (see the blocker 3 report).
  5. **Fixed in this block: a Redis outage no longer strands events.**
     Observed live first (real, 2026-10-13, fake-provider stack): Redis
     stopped for about 22 s, and a TenderCreated event written during the
     outage failed its enqueue at 06:10:12, 06:10:20 and 06:10:28 UTC,
     used up the old 3 attempts (1, 2 and 4 s apart) and was never
     delivered, although Redis was back a second later. Now Redis being
     unreachable (redis-py `ConnectionError` and its subclasses, and
     `TimeoutError`; `INFRASTRUCTURE_ERRORS`,
     `apps/worker/src/tenderos_worker/outbox_dispatcher.py:79`) does not
     consume an attempt: the claim is rolled back, the event stays pending
     with `available_at` unchanged, the rest of the tick's batch is skipped
     (`RedisUnavailableError`, `:137`, raised at `:369`), and the tick
     backoff (5 s doubling, capped at 60 s) paces the retries. Any other
     enqueue error consumes an attempt: `MAX_ATTEMPTS = 8` (`:62`), waits of
     min(300 s, 30 s x 2^(n-1)) scaled by a jitter factor in [0.75, 1.0]
     (`_retry_delay_seconds`, `:144`), about 17 to 22.5 minutes in all
     (design numbers). Events marked `failed` under the old policy with
     fewer than 8 attempts become eligible again once this is deployed, a
     one-time redelivery of old failures.
  6. **A dead-lettered event needs a manual re-drive.** After its 8th
     counted failure an event is marked `failed` (`OutboxRepository.mark_failed`)
     and logged at ERROR with its id and event type
     (`outbox_dispatcher._dispatch_claimed_event`, "dead letter, needs a
     manual re-drive"), the hook for Phase 3 alerting. Nothing re-drives it
     automatically, on purpose: an event that failed 8 times on its own
     merits would loop. **RESOLVED 2026-11-04 by plan item 19 and plan item 20**
     (decision 0034): item 19 fails the run of a dead-lettered
     `WorkflowRunCreated` or `GateDecisionRecorded` as `DISPATCH_FAILED`, so
     it no longer holds its tender; `scripts/redrive_outbox.py` resets a
     chosen dead letter (`status` to `pending`, `attempt_count` to 0,
     `available_at` to `now()`, one conditional UPDATE) after checking
     that its job would still do something, and otherwise refuses it with
     one line saying why. Dry run by default; `--apply` needs `--reason`,
     which the reset's log line carries. See "Plan item 20" below.
  7. **A worker that dies mid-run leaves the run in `running`.** The run
     is claimed `queued` -> `running` before the graph is invoked
     (`apps/worker/src/tenderos_worker/jobs/workflow.py:146`). If the
     worker process is killed (OOM, host restart) while the graph runs,
     nothing moves the run on: a redelivery of WorkflowRunCreated is now a
     no-op by design, RQ's own job timeout path
     (`mark_run_failed_on_timeout`,
     `packages/tenderos_backend/src/tenderos_backend/domain/services/workflow_service.py:123`)
     only runs when the worker survives to handle the timeout, and the
     in-job retry (`retry_workflow_run`, `jobs/workflow.py:246`) only runs
     when a failure is caught. A reaper for runs stuck in `running`
     (no task progress for longer than the workflow queue's 1800 s job
     timeout: mark failed or schedule a retry) is designed, not built.
     **RESOLVED 2026-11-04 by plan item 19**, redesigned: not "no task
     progress", which would also fail a run only waiting behind another
     tender's job. Each run records its current RQ job (migration 024),
     and the dispatcher fails the run when that job will never move it on
     (RQ's failed, canceled or finished registry, unknown to RQ, or its
     started entry past due on two looks two minutes apart), with a
     6-hour backstop. Observed in the fake stack: the workflow worker
     killed mid-run, the run failed as `WORKER_LOST` 257 s later; see
     "Plan item 19" below.
- **Risk:** limit 4's unguarded consumers can repeat paid LLM calls on a
  redelivery, and a duplicate resume could answer the wrong gate. Limit 6
  leaves a dead-lettered event undelivered until someone re-drives it
  (now one command, plan item 20).
  Limit 7 left a run shown as running forever after a worker crash
  (resolved by plan item 19).
- **Status:** limits 1-3 accepted; limit 4 open (guards being built
  separately); limit 5 fixed; limit 6 resolved 2026-11-04 (plan item 20,
  a manual re-drive by design); limit 7 resolved 2026-11-04 (plan item 19).
- **Test:** `tests/integration/test_outbox_dispatcher.py` (eligibility on
  the database clock with the host clock 30 s behind, a hung Redis times
  out and the loop keeps running, an existing job id is treated as
  dispatched after a crash between enqueue and mark, the loop survives a
  database error and a cleanup failure) and
  `tests/integration/test_run_workflow_claim.py` (a duplicate and two
  concurrent `run_workflow` deliveries start the graph once; a failed
  first attempt is retried and invokes the graph again). Limit 5:
  `test_outbox_dispatcher.py` (an event written during a long outage is
  dispatched when Redis returns with attempt_count unchanged, a connection
  error stops the tick after that row, other errors consume attempts with
  growing, capped delays and end failed with an ERROR log) and
  `tests/unit/test_outbox_backoff.py`.

## [Phase 4 plan item 19, 2026-11-04, resolved; residuals accepted] A run whose RQ job is lost no longer stays active

- **Before (read in code):** nothing recorded which RQ job was moving a
  run forward. A run whose worker was killed mid-job, whose work horse
  died, or whose retry Redis lost stayed `queued` or `running` forever;
  a dead-lettered `WorkflowRunCreated` left its run `queued`, and a
  dead-lettered `GateDecisionRecorded` left it waiting at a decided gate.
  Since plan item 43 each such run also blocks its tender from starting
  another (409 `WORKFLOW_RUN_ACTIVE`). A retry job checked only for
  `cancelled`, so one firing on a failed run built the graph.
- **Changed:**
  - `workflow_runs.current_job_id`, `current_job_at` and `error_code`
    (migration 024, a CHECK on the codes). The start and resume claims,
    and the scheduling of a retry, record the run's current job in the
    UPDATE that moves it; the retry is recorded and committed before it
    is enqueued under that id (`retry_{run}_{n}`).
  - The dispatcher, about once a minute, fails a run whose current job
    will never move it on (`domain/services/run_reaper.py`, the RQ check
    `outbox_dispatcher.RunJobInspector`): running, `WORKER_LOST`; queued
    with its start job lost, `DISPATCH_FAILED`; waiting at a decided gate
    whose resume never claimed the run, `DISPATCH_FAILED`; anything
    queued or running past a 6-hour cap. Dead letters of the two events
    fail their run in the dead-letter's own transaction. Every fail is
    one conditional UPDATE naming the status and job it checked.
  - A retry starts only if the run is running and the retry is its
    current job. The provider's retry-after is clamped to 15 minutes.
  - `WorkflowRunResponse.error_code`, and one sentence under "Last run".
- **Observed (fake stack, 2026-11-04):** the workflow worker killed with
  a run blocked mid-job; the run failed as `WORKER_LOST` (rule
  `abandoned`) 257 s after the kill; the restarted worker's own startup
  maintenance then filed the same job as failed with `AbandonedJobError`.
- **Limits (accepted):**
  1. **About 4 minutes to notice a dead worker:** up to 90 s for the
     started entry to fall past due, two looks 120 s apart, and the
     reaper's minute. Nothing re-runs a reaped run (paid LLM calls); the
     user starts processing again, from the beginning (there is no
     resume route; decision 0035's "existing retry route" was wrong).
  2. **Runs from before migration 024** have no recorded job: a queued or
     running one is caught only by the 6-hour cap, and one waiting at a
     decided gate is never reaped, because nothing tells whether its
     resume claimed it. Found in the observed check: the first build of
     the gate rule failed a pre-024 dev run that was waiting on purpose
     after a "request changes" decision (its resume job gone with a Redis
     flush); fixed, with a test.
  3. **A worker killed while a resumed gate replays:** the gate node
     writes `waiting_for_human` again before the next node writes its own
     status. A run caught in that window has its resume as current job
     and is waiting, so neither rule reaps it. A window of a few queries.
  4. **A zombie judged abandoned** (a worker cut off from Redis for longer
     than the two looks) may still write domain rows before its next
     status write or gate is refused (plan item 44's guard). The same
     residual as the cancellation race.
  5. **A failure in the replay window above** now fails the run instead of
     scheduling a retry: retry scheduling requires the run to be running.
     Only requirement extraction and the three specialists record failed
     tasks, and all four run while the run is running.
  6. **Redis persistence** is the image default (snapshots only, read in
     Redis's documentation, not observed): a Redis crash loses a
     just-scheduled retry, which the reaper then fails as `WORKER_LOST`.
     Appendonly persistence is a deployment host item.
  7. **Test order (pre-existing, found on the way):** running
     `test_workflow_timeout_handling.py` before `test_outbox_dispatcher.py`
     in one process fails two dispatcher tests ("attached to a different
     loop"; the timeout tests call `asyncio.run` themselves). Observed on
     main's code too. The full suite runs them in the other order.
- **Test:** `tests/integration/test_run_reaper.py`,
  `tests/integration/test_workflow_retry.py` (the retry recorded before
  its enqueue, none once the run is not running under the failing job),
  `test_workflow_timeout_handling.py` (`JOB_TIMEOUT`),
  `test_workflow_api.py` (`error_code`), `test_outbox_dispatcher.py`
  (the reaper is on the loop's cadence), `tests/unit/test_retry_classification.py`
  (the clamp), `apps/web/src/routes/UploadAndProcessingStatus.test.tsx`
  (the sentences). Each mechanism fails its tests when removed (35
  mutations).

## [Phase 4 plan item 20, 2026-11-04, resolved; residuals accepted] A dead-lettered outbox event can be re-driven, and the tool says when it should not be

- **Before (read in code):** a dead-lettered outbox event (`status
  failed` after its 8th counted failure) needed a hand-written SQL reset.
  Only an enqueue that raises something other than an infrastructure
  error counts towards the 8, so a dead letter means the event's route
  could not handle it (for example a payload it cannot read), not a Redis
  outage.
- **Behaviour (fixed):** `scripts/redrive_outbox.py` (the rule in
  `domain/services/outbox_redrive.py`) resets the outbox row only, in one
  conditional UPDATE matching only a row still `failed`; the dispatcher
  delivers it on its next pass through the same route, job id and
  consumer guard as any delivery. It never enqueues to Redis and never
  writes a run, operation, claim or document. Before resetting, it reads
  what the event's job would find and refuses, in one line, an event
  whose job would do nothing: a run already failed (naming its code, for
  example `DISPATCH_FAILED` from item 19's dead-letter handling) or
  finished; a run waiting at a later gate; an operation no longer queued;
  a reanalysis whose claim completed or whose lease is still live (item
  18), naming the lease's end; a document already ingested, an import
  batch completed, a requirement already verified for that event; an
  unreadable payload; an event that is pending or dispatched. `--list`
  shows every dead letter with its line; dry run by default; `--apply`
  needs `--reason`, which each reset's INFO line carries with the event
  id and type (log only, no audit row: the outbox has no organization).
  Exit status 0 (all re-driven or would be), 2 (any refused), 1 (error,
  including a usage error). Output and logs carry ids, event types,
  statuses and the line, never a payload.
- **Accepted limits:**
  1. **Re-driving before the route's fix is deployed dead-letters the
     event again** after 8 more counted failures (its run or operation is
     failed again by the same dead-letter path).
  2. **The check and the reset are two steps.** If the target changes in
     between, the consumer's own guard stops the job, as for any
     redelivery: at most one job that does nothing.
  3. **It runs from a checkout** against `POSTGRES_DSN`, like the other
     operator scripts; `scripts/` is in no image. A host runbook item
     (plan item 25).
  4. **`--list` shows the oldest 200 dead letters.**
- **Test:** `tests/integration/test_outbox_redrive.py` (18 tests: the dry
  run, a reset delivered once by the real dispatcher, each refusal line,
  the conditional reset losing to another, `--list`, the exit statuses,
  the reason, no payload in output or logs, the command run as a process,
  only the outbox row written, and every dispatcher route having its own
  check, so a route added later cannot be re-driven unchecked).

## [Phase 4 plan item 45, 2026-11-04, resolved; residual accepted] Idle RQ workers quit on a Redis timeout in the Docker Desktop VM

- **Seen (observed, fake stack, Docker Desktop on Windows):** each of the
  four RQ workers logged "Redis connection timeout, quitting..." 27 to 28
  times between 08:41 and 12:27 UTC on 2026-11-04, about every 6.5 minutes
  while idle, and Docker restarted it each time. The outbox dispatcher and
  the API logged none. RQ catches the `TimeoutError` and logs that one line,
  so there is no traceback.
- **Cause (measured):** an idle worker blocks in BLMOVE (one queue, Redis
  6.2 or later; BLPOP for several queues) for RQ's dequeue timeout,
  `worker_ttl - 15` = 405 s. RQ gives the client socket 10 s more
  (`connection_timeout` = 415 s), and the socket timeout runs on the
  monotonic clock, while Redis times the wait on its wall clock (read in
  Redis's own source, not in this repo; the numbers below fit it). In the
  Docker Desktop VM the monotonic clock ran fast: a BLPOP(405) took
  414.2 s by the container's monotonic clock and 405.1 s by its wall clock,
  0.8 s short of the 415 s timeout, while the same wait from the Windows
  host took 405.1 s by both clocks. RQ's margin is about 2.5%; past it the
  socket times out first, redis-py raises `TimeoutError`, which RQ's
  dequeue loop does not retry (it retries `ConnectionError`), and the
  worker quits. The connection settings themselves were right (observed on
  a live socket: 415 s, keepalive 30/5/3). Probably specific to Docker
  Desktop on Windows; the drift varies (no quits at all in 22 idle minutes
  after the stack restarted at 14:02 UTC). Not caused by item 44's split
  (read in code; the operations worker, older than item 44, quit the same
  way).
- **Behaviour (fixed):** the workers' Redis connection gets an explicit
  socket timeout of RQ's dequeue wait plus 120 s, derived from RQ's
  `worker_ttl` (`worker.worker_socket_timeout`: 405 + 120 = 525 s). RQ
  only raises a timeout below its own 415 s, so it keeps 525 s; each worker
  logs the effective value after RQ's setup ("redis socket timeout 525 s
  for a 405 s dequeue wait (worker_ttl 420 s)", observed on all four live
  workers). The API's rate limiter (0.25 s, the login fail-closed path of
  decision 0041) and the outbox dispatcher (2 s) are unchanged.
- **A dead Redis still shows:** a Redis that goes away (connection dropped,
  new ones refused) ends the wait with a `ConnectionError` within seconds
  (tested: about 1 s), which RQ retries and logs as "could not connect";
  redis-py's keepalive (30 s idle, 3 probes 5 s apart) ends a connection to
  a host that has gone silently (read in code).
- **Accepted limit:**
  1. **A Redis that keeps the connection but stops answering is noticed
     after 525 s** of an idle wait, instead of 415 s.
- **Test:** `tests/integration/test_worker_redis_timeout.py` (a proxy in
  front of the test Redis holds the empty answer to a blocking wait back
  12 s, through RQ's real `Worker(...)` and `work()` at a 16 s
  `worker_ttl`: RQ's own 11 s timeout quits, the fix outlasts it; the
  effective 525 s on a live socket at the production `worker_ttl`;
  `worker.main()` building its worker on it and logging it; a Redis that
  goes away failing the wait at once).

## [Phase 4 consumer guards, 2026-10-13, accepted; limit 1 resolved 2026-11-04] What each outbox consumer does with a duplicate delivery

- **Discovered:** Phase 4 blocker 3 (delivery is at-least-once, see the
  blocker 3 entry above); guards built on `phase4/backend-hardening`.
- **Behaviour (fixed):**
  1. `resume_workflow` first reads the thread state and resumes only if the
     thread is paused at the interrupt for the decided gate
     (`apps/worker/src/tenderos_worker/jobs/workflow.py:246`), then claims
     the run `waiting_for_human` -> `running` in one conditional UPDATE
     (`:266`), then resumes that interrupt by id. A duplicate resume for an
     earlier gate no longer answers a later gate's interrupt with the
     earlier gate's payload.
  2. `process_knowledge_document_import` (capability statements) stops
     before the LLM call when the batch's `completed_at` is set
     (`jobs/knowledge_import.py:176`; stamped when the import finishes),
     and `import_past_project`/`import_capability` claim or resume their
     rows instead of failing on `uq_knowledge_import_item_source_ref`.
  3. `verify_requirement_job` skips only when a completed verification of
     the requirement already carries this exact outbox event's id
     (`jobs/verification.py:41`; the verification trace now records
     `requirement_id` and `event_id` in `input_refs`). Deduplicating on the
     event, not on "any verification since the event was created", keeps a
     second request made while an earlier verification is still running
     from being skipped when that earlier one finishes after it.
  4. `parse_and_embed_document` skips when the ingestion run for
     `(document, PARSER_VERSION)` already completed
     (`parse_and_embed_document` in `jobs/ingestion.py`). An explicit reprocess reopens that run
     (`document_service.reprocess_document`), so it still re-runs. The
     ingest job id now includes the event id: with blocker 3's
     `unique=True` and an id shared across events, a reprocess within RQ's
     500 s result TTL of the previous ingest was treated as already
     dispatched and never ran (a bug introduced in blocker 3, fixed here).
  5. Every outbox route's job id now identifies exactly one event. Audited
     in `apps/worker/src/tenderos_worker/outbox_dispatcher.py`: `outbox-`,
     `verify_` and `ingest_` contain the event id; `workflow-{run}`,
     `resume_{run}_{gate}` and `reanalyze_{tender}_{state_version}` are one
     event each because the event's outbox idempotency key (unique) is
     built from the same ids. `knowledge_import_{document}` was shared:
     `replace_employee_resume` re-queues an existing document under a
     fresh request id, and with `unique=True` that second import was
     silently "already dispatched". It now includes the event id.
- **Accepted limits:**
  1. **RESOLVED by plan item 18 (2026-11-04, decision 0036, migration
     023): `reanalyze_knowledge_selection_change` is guarded.** A second
     delivery used to repeat the compliance re-evaluation (LLM calls), the
     compliance step's unconditional `state_version` bump (which can make
     the graph projection stale) and the graph-contradiction findings.
     Built as designed:
     - `reanalysis_claims`, unique on `(tender_id, knowledge_item_id,
       state_version)`, with `status` (`running`, `completed`), a
       `claim_token` and a lease (`locked_until`);
     - the job claims first, in its own committed transaction
       (`reanalysis_service.claim_reanalysis`): one `INSERT ... ON
       CONFLICT DO NOTHING RETURNING`, then, if that found a row, a
       takeover of a `running` claim whose lease lapsed (one conditional
       UPDATE, the shape of `IdempotencyRepository.take_over_expired`).
       Neither matching means a duplicate: one INFO line, and the job
       returns before any work;
     - marked `completed` at the end, only while the caller's token is
       current.
     The lease is 1860 s: the workflow queue's 1800 s job timeout plus the
     60 s margin (pinned by `tests/unit/test_reanalysis_lease.py`). It
     starts at the claim, not the enqueue, so waiting in the queue never
     uses it up. `knowledge_item_id` has no foreign key: the row records a
     delivery, and must neither block deleting the item nor vanish with it.
     Observed (`tests/integration/test_reanalysis_guard.py`): two
     deliveries released together by a barrier, the first insert held
     open until `pg_locks` showed the second waiting on it, 5 of 5: one
     ran, one was refused with one INFO line; a redelivery after the first
     finished is refused; a different `state_version` for the same tender
     runs; a live lease is not taken over, a completed claim never is, a
     lapsed lease goes to exactly one of two takers (overlap seen in
     `pg_locks`), and only the current token completes. Each fails under
     its mutation (the job ignoring a refused claim, the key without
     `state_version`, the takeover without its lease or status check,
     completion without the token, a plain insert, the lease without its
     margin). **Accepted limit:** a job that raises part-way keeps its
     claim `running` until the lease lapses, so a re-drive inside those 31
     minutes is refused (tested). A claim row is kept per selection
     change, with no cleanup; one row per change is small.
  2. **Two concurrent resumes for the same gate have a narrow window.**
     Resuming re-runs the gate node from its start, and gate nodes set the
     run back to `waiting_for_human` before their `interrupt()` returns,
     while the interrupt is still pending in the checkpoint. A second
     resume arriving inside that window can pass both checks and resume the
     same interrupt again. Not reproduced; the outbox claim and RQ's
     `unique=True` make a concurrent duplicate of one event unlikely.
  3. **A capability-statement import that crashed part-way still repeats
     its LLM extraction on redelivery** (it has not stamped
     `completed_at`); the rows it already wrote are resumed, not
     duplicated.
- **Risk:** limit 1 can repeat paid LLM calls and re-stale the graph on a
  duplicate reanalysis delivery.
- **Status:** guards 1-5 built; limit 1 resolved (plan item 18,
  2026-11-04); limits 2 and 3 accepted.
- **Test:** `tests/integration/test_graph_execution.py` (a duplicate
  resume for an earlier gate leaves the next gate pending and the run
  waiting; the genuine resume still works),
  `tests/integration/test_knowledge_import_worker_job.py` (a duplicate
  capability-statement delivery makes no LLM call and no rows),
  `tests/integration/test_consumer_guards.py` (a duplicate verification
  makes no LLM call; a request made while another is in flight still runs;
  a job without an event id still runs),
  `tests/integration/test_outbox_dispatcher.py` (two DocumentUploaded and
  two KnowledgeDocumentUploaded events for one document enqueue two jobs each), `tests/integration/test_document_ingestion.py` (a duplicate
  does not re-embed; an explicit reprocess still does).

## [Phase 4 async operations, Stage 1, 2026-10-15, open, accepted] Red team runs in the worker as an async operation; what the operation framework does not cover

- **Discovered:** Phase 4 review: `POST /tenders/{id}/redteam/run` ran the
  whole red team, including its LLM critique, inside the HTTP request, and
  the Gemini client had no timeout at all (`genai.Client(api_key=...)` with
  no `http_options`), so a hung provider connection held the request
  indefinitely.
- **Behaviour (fixed in Stage 1):**
  1. A generic `async_operations` table (migration
     `020_async_operations`): kind, status (`queued`, `running`,
     `succeeded`, `failed`), error code and message, lease
     (`locked_until`) and `claim_token`. A partial unique index
     `uq_async_operation_active` on `(tender_id, kind)` while queued or
     running decides "one active operation per tender and kind".
  2. `POST .../redteam/run` now returns 202 with the operation. A second
     POST while one is queued or running returns that same operation (same
     id), not a second run.
  3. The outbox delivers `OperationRequested` to the worker's
     `run_operation_job` (job id `op_{operation_id}`, job timeout from the
     kind: red team 600 s, proposal generation and commercial validation
     1800 s). `run_operation` claims the operation queued to running in
     one conditional UPDATE, so a duplicate or concurrent delivery does
     nothing; only the claim's holder can finish it.
  4. The lease is the job timeout plus 60 s, set at claim on the database
     clock. The outbox dispatcher's loop reaps running operations whose
     lease lapsed (every 12 ticks, about 60 s) to `failed` with
     `WORKER_LOST`. A reaped operation is never taken over or re-run (that
     would repeat paid LLM calls); the user starts it again, and the lost
     worker's late finish is refused.
  5. LLM timeouts (`agents/llm/gemini_adapter.py`, design numbers, not
     measured): 60 s per provider HTTP request, 180 s per logical call
     including the adapter's own retries. Both raise `LLMTimeoutError`,
     which ends an operation `failed` with `LLM_TIMEOUT`; a rate limit
     ends it `LLM_RATE_LIMITED`, RQ's job timeout `JOB_TIMEOUT`, anything
     else `INTERNAL_ERROR`. Only the code and a fixed safe message reach
     the web app; the exception is logged in the worker.
  6. `GET /operations/{id}` and a per-operation SSE stream
     (`/operations/{id}/events/ticket`, `/operations/{id}/events/stream`).
     Another organization's operation answers 404, exactly like a missing
     one. SSE tickets now carry a scope (`workflow_run` or `operation`), so
     a ticket minted for one stream is refused on any other.
  7. The web app's Run red team button stays busy until the operation is
     finished: `useOperation` follows the SSE stream and polls
     `GET /operations/{id}` every 3 s while the stream is down, then the
     findings are re-read; a failed operation shows its own message.
  8. An operation can no longer stay `queued` for good (found in the
     Stage 1 review, fixed 2026-10-16). Queued counts as active in
     `uq_async_operation_active`, so a stuck queued operation made every
     later POST for that tender and kind return it. Two paths now fail it
     with `DISPATCH_FAILED` and a generic message, each with its
     `OperationStatusChanged` event, and only while it is still queued:
     - when the dispatcher dead-letters its `OperationRequested` event, in
       the same transaction (`_fail_dead_lettered_operation`,
       `apps/worker/src/tenderos_worker/outbox_dispatcher.py`). Plan item
       20's re-drive tool refuses that event afterwards ("operation ... is
       failed (DISPATCH_FAILED): request it again"); it re-drives an
       `OperationRequested` only while its operation is still queued;
     - as a backstop, the reaper fails any operation still queued 60
       minutes after it was requested
       (`operation_service.MAX_DISPATCH_WINDOW`, a design number). The
       outbox's full retry span is 22.5 minutes at most (8 attempts,
       waits 30 to 300 s), so the window is about 2.7 times that, and it
       also covers a job waiting behind one full 30-minute job ahead of it
       (in Stage 1 a workflow job on the shared queue; since Stage 2 an
       operation on the operations queue, re-checked 2026-10-16, the number
       stands). This catches what the dead-letter path
       cannot: a Redis outage longer than the window (outages do not use
       up attempts, so they never dead-letter), or a stopped dispatcher or
       worker.
     A job that reaches the worker after its operation was failed finds it
     no longer queued and does nothing.
- **Accepted limits:**
  1. **An operation queued for more than 60 minutes is failed even if its
     job is still waiting in the RQ queue.** The user sees
     `DISPATCH_FAILED` and clicks again; when the old job runs, its claim
     fails and it does nothing, so no LLM call is paid twice. Updated
     2026-10-16 (Stage 2): operations now have their own `operations`
     queue and `operations_worker` service, so workflow jobs no longer
     delay them. Operations still run one at a time on that worker: a
     30-minute proposal generation (its job timeout) can delay a red team
     queued behind it, and two or more such jobs ahead can push a third
     past the window. The fix at scale is more `operations_worker`
     replicas (each RQ worker runs one job at a time; the claim already
     makes concurrent workers safe).
     **RESOLVED by plan item 44 (2026-11-03):** waiting no longer counts.
     An operation is failed as lost only when its request was dispatched
     and RQ no longer knows its job (not in the queue or any registry),
     and otherwise only past a 6-hour absolute cap
     (`operation_service.MAX_QUEUED_AGE`); see the item 44 entry. Either
     way the user requests the operation again: plan item 20's re-drive
     tool refuses an `OperationRequested` whose operation is no longer
     queued, rather than start a job its claim would stop.
  2. **RESOLVED 2026-10-16 (Stage 2): tracking did not survive a page
     reload.** The screen kept the operation id in component state only, so
     after a reload the button was enabled while the run continued. Each
     screen now asks `GET /tenders/{id}/operations/active?kind=...` on load
     and follows an active operation exactly as after a click (see the
     Stage 2 entry below).
  3. **`LLMTimeoutError` inside a workflow node is classified as an
     unknown error** by `orchestration/retry.py` (it is an `LLMError` but
     none of the classified subclasses), so it gets the unknown-exception
     branch: one retry after a fixed delay. Reasonable for a timeout; not
     a dedicated branch.
  4. **The SSE stream's starting point moved from the API host's clock to
     the database's `now()`** (`apps/api/src/tenderos_api/outbox_stream.py`,
     shared by the workflow stream), for the same clock-gap reason as
     blocker 3. Events written before a stream opens are not replayed; the
     web app re-reads the resource when the stream opens, so none is
     missed.
     **Corrected (plan item 54, 2026-11-10):** "none is missed" was wrong.
     An event whose transaction began before the stream's starting point
     and committed after it was never sent, and the web app's read went out
     before the stream opened, so it could miss that event too. The stream
     now starts from the ids of the events that exist when it opens and
     sends every other one; the web app reads again once the stream is
     open. See the plan item 54 entry.
  5. **`OperationStatusChanged` events are routed to the generic
     placeholder consumer** (`jobs/placeholder.py`, logs and returns), like
     every other event type without a worker consumer. They exist for the
     SSE stream, which reads them from the outbox table directly.
  6. **The reaper can fail an operation up to about 60 s after its lease
     lapses** (it runs every 12 dispatcher ticks), so a red-team run whose
     worker died shows `failed` after at most about 12 minutes.
- **Earlier references:** the B13 entry above cites
  `test_trigger_red_team_runs_clean_on_a_fresh_tender`. That test is now
  `test_trigger_red_team_queues_an_operation_and_returns_202`; the
  fresh-tender red-team run it covered is now
  `tests/integration/test_async_operations.py::test_a_real_red_team_run_succeeds_through_the_worker`.
- **Status:** Stage 1 (red team) built. Stage 2 (proposal generation,
  commercial re-validate, reattach after a reload) built 2026-10-16; see
  the entry below. Limit 2 resolved there; the others stand.
- **Test:** `tests/integration/test_async_operations.py` (one claim under
  two concurrent deliveries; a duplicate delivery is a no-op; finish is
  claim-guarded; the reaper fails a lapsed operation and leaves a live one;
  `test_the_reaper_fails_an_operation_queued_past_the_window_and_leaves_a_recent_one`;
  a second request while active returns the same id; LLM timeout ends
  `LLM_TIMEOUT`), `tests/integration/test_operations_api.py` (202; same id
  on a second POST;
  `test_a_dead_lettered_request_fails_its_operation_and_a_new_run_starts`;
  cross-org 404; ticket scope; the stream's messages),
  `tests/unit/test_gemini_adapter_timeouts.py`,
  `tests/integration/test_outbox_dispatcher.py` (route, job id, timeout;
  reaper failure contained), `apps/web/src/hooks/useOperation.test.ts`,
  `apps/web/src/routes/RedteamFindings.test.tsx`.

## [Phase 4 async operations, Stage 2, 2026-10-16, open, accepted] Proposal generation and commercial re-validation run in the worker; every screen reattaches after a reload

- **Discovered:** the same Phase 4 review as Stage 1. `POST
  /tenders/{id}/proposal/generate` drafted the proposal inside the request
  (up to 12 LLM calls, configured), and `POST
  /pricing-documents/{id}/validate` ran the commercial checks and one
  explanation LLM call per finding inside the request, explaining every
  finding again on each re-validation, even one already stored.
- **Behaviour (fixed in Stage 2):**
  1. Both routes now follow Stage 1's pattern: they record an async
     operation (`proposal_generation`, `commercial_validation`) and return
     202 with it (`generate_proposal` in `apps/api/src/tenderos_api/routes/proposal.py`,
     `validate_pricing_document` in `routes/commercial.py`); the worker runs the work in
     `run_operation` (`apps/worker/src/tenderos_worker/jobs/operations.py:52`,
     `:61`). Job timeout 1800 s each, lease 1860 s (configured in Stage 1,
     `operation_service.JOB_TIMEOUT_SECONDS`). A second POST while one is
     queued or running returns the same operation; failures end with their
     safe message, as for the red team.
  2. Commercial validation is one operation per tender, by design, as the
     work always was: `evaluate_commercial_for_tender` re-checks every
     pricing document of the tender together (cross-document checks need
     every sheet), whichever row's Re-validate started it, and a request
     from any of the tender's documents while one is active joins it.
     Observed on the fake stack (2026-10-16, fake data: a fresh tender with
     two pricing documents and no findings, `operations_worker` stopped):
     Re-validate on document A (a browser click) and document B (the same
     POST sent to the API, since the screen disables B while A runs) both
     answered 202 with the same operation; once the worker started, that
     one run (07:34:38.966 to 07:34:39.177 UTC) wrote 4 findings on A, 3 on
     B and 1 cross-document finding on both. No request is dropped, so the
     screen shows every row busy while it runs (behaviour 6).
  3. Proposal generation keeps its required Idempotency-Key, which now
     records and replays the fast 202. This closes blocker 2's limit 3
     (generation outlasting the 60 s lease), marked resolved above.
  4. Commercial validation explains only new findings. Each finding is
     looked up by its fingerprint first, in its own short transaction, and
     `explain_finding` is called only when it is not stored yet, then the
     lookup is repeated before the insert
     (`packages/tenderos_backend/src/tenderos_backend/domain/services/commercial_service.py:218`).
     The fingerprint depends only on the deterministic draft, never on the
     explanation. This also applies to the workflow's own commercial step,
     which calls the same function.
  5. `GET /tenders/{id}/operations/active?kind=...`
     (`apps/api/src/tenderos_api/routes/operations.py:90`) returns the
     tender's queued or running operation of that kind, or `null`. One
     organization check (`_authorize`, `:63`): another organization's
     tender answers 403 `ORG_ACCESS_DENIED`, like every other
     tender-scoped route (changed from 404 on 2026-10-22, review); a tender
     that does not exist is 404; a member whose role may not create that
     kind gets 403; an unknown kind is 422. `GET /operations/{id}` keeps
     its 404 for another organization's operation.
  6. The web app: `useTenderOperation`
     (`apps/web/src/hooks/useTenderOperation.ts`) is used by the red team,
     proposal and commercial screens. The button stays busy from the click
     until the operation is finished and the screen has re-read its
     results; on load each screen asks the active-operation endpoint and,
     if one is queued or running, shows its button busy and follows it
     exactly as after a click. The proposal screen re-reads the sections
     and every section's claims before releasing Generate
     (`full-flow.spec.ts:949-953` waits for the button, then expects a
     section at once; `:974` counts claim rows without waiting). Commercial
     shows every Re-validate busy while its tender-wide operation runs,
     after a click and after a reload alike (behaviour 2), labelled
     "Re-validating..." (the quiet button has no other visible busy look;
     before, only `aria-busy` marked it).
  7. Observed live (fake provider, 2026-10-16, final build): with
     `operations_worker` stopped and the workflow worker running, Generate
     proposal answered 202 (queued); after a page reload the button still
     read "Generating..." and was disabled, and the page made
     `GET .../operations/active`, minted a ticket and opened
     `/operations/{id}/events/stream` for that operation; 10 s later it was
     still queued. Once `operations_worker` was started, the operation
     succeeded and the Company Overview section appeared 10.6 s later
     (measured, fake-provider; it includes the worker's own 7 to 8 s preload
     at startup) with no second click. The same check for the other two
     screens (2026-10-16, one tender seeded with two pricing documents, fake
     data): with `operations_worker` stopped, Run red team and Re-validate
     (on document A) each answered 202 (queued); after reloading both tabs,
     Run red team read "Running..." and both Re-validate rows read
     "Re-validating...", all disabled, and each tab requested its active
     operation, minted a ticket and opened its operation's stream; 10 s
     later both were still queued. Once `operations_worker` was started,
     both succeeded and the buttons were released 10.4 s later (measured,
     fake-provider) with the 8 findings on screen and no second click.
     Screenshots: `.playwright-mcp/phase4-review/live-check-stage2-others/`.
  8. **Operations have their own queue and worker.** The dispatcher
     enqueues `OperationRequested` on a new `operations` RQ queue
     (`apps/worker/src/tenderos_worker/outbox_dispatcher.py`,
     `_operation_requested_dispatch`); every other event keeps its queue.
     A new compose service, `operations_worker` (same image and env as
     `worker`, `restart: unless-stopped`, no healthcheck, like `worker`),
     runs `python -m tenderos_worker.worker operations` and listens only to
     it; the default worker no longer listens to it. Found by the E2E:
     with one worker, an operation waited behind the workflow job already
     running. Measured on the fake stack, E2E run 2 (UTC): proposal
     requested 06:10:23.579, the commercial gate's resume job ran
     06:10:25.179 to 06:10:32.419, the proposal job started 06:10:32.485
     (66 ms after it), the red team was clicked at 06:10:40.858, and no
     workflow event came after that click (the last at 06:10:32.409).
  9. **The operations worker imports its job module before forking
     jobs** (`apps/worker/src/tenderos_worker/worker.py`,
     `preload_operations_module`). RQ forks a fresh child per job and
     imports the job's module there; for operations that import took 7.2
     to 7.9 s (measured, 5.5 s of it sentence_transformers and torch,
     through `commercial_service` -> `document_service` -> ingestion).
     Per-job startup, RQ job start to the first line of job code, 3
     samples per kind (measured, fake stack): before 5.8 to 7.3 s red
     team, 5.4 to 5.9 s proposal, 5.6 to 5.7 s commercial; after, 0.000 s
     in all 9 (below the logs' 1 ms resolution). The parent forks every
     job with one thread besides its main thread: RQ's own (item 10).
  10. **BLAS is pinned to one thread in the operations worker.** Without
     the pin, the preload would have added 30 threads to the parent that
     forks the jobs: numpy's and scipy's bundled OpenBLAS each start a
     pool when they load (observed: 1 -> 16 -> 31 OS threads on 16 CPUs;
     0 sockets before and after). `operations_worker` sets
     `OPENBLAS_NUM_THREADS=1` and `OMP_NUM_THREADS=1`
     (`docker-compose.yml`), which keeps the import at one thread
     (observed in the running parent: OS threads 1 -> 1, open sockets 0 ->
     0). The worker refuses to start, logging an ERROR that names the
     thread count and both variables and exiting non-zero, if the import
     leaves more than one OS thread or opens a socket (Linux). What this
     guarantees is that the preload adds no thread, not that the parent
     forks single-threaded: the check runs before the RQ `Worker` exists,
     and RQ 2.10.0's `Worker.bootstrap` then starts a Redis pubsub listener
     thread (`rq/worker/base.py`, `subscribe` -> `pubsub.run_in_thread`,
     daemon) that runs until teardown. Observed 2026-10-22, idle: the
     parent python process has 2 OS threads and 3 open sockets in both
     `operations_worker` and the default `worker`. That listener thread is
     RQ's own design, present in every RQ worker; RQ's scheduler is a
     separate forked process, not a thread.
  11. With 8 to 10: E2E passed 2 of 2 (2.6 and 2.6 min, fake-provider),
     after failing 3 of 3 before. Run B1 (UTC): the commercial gate's
     resume job ran 06:50:49.925 to 06:50:57.559 and the proposal
     operation started at 06:50:50.046, inside it; red team clicked
     06:50:52.421, 9 workflow events after the click. Run B2: resume job
     06:56:00.941 to 06:56:08.402, proposal started 06:56:01.057; red team
     clicked 06:56:03.095, 9 events after. Memory (`docker stats`, 1 s
     samples, measured): `operations_worker` idle about 126 MiB without
     the preload and 520 MiB with it, peak 521.3 MiB during the E2E (474.7
     MiB without the preload, when each job's child imported torch);
     `worker` idle 126 to 174 MiB, peak 605.5 to 612.8 MiB. The operations
     worker imports `sentence_transformers` but never loads the embedding
     or reranker weights: no operation calls `embed_texts` or `rerank`
     (the only embed on its path, `create_synthetic_chunk`, runs on
     resolve, in the API), and its memory moved by under 2 MiB across all
     its jobs.
- **Accepted limits:**
  1. **A replayed proposal 202 shows the status from the first request**
     (usually `queued`). The client follows the operation for the current
     status, so nothing reads the stale one.
  2. **A manual run and the workflow's own step are not one operation.**
     `proposal_drafting_node` and `commercial_analysis_node` call the same
     services directly, so a manual run can overlap them, as before Stage
     2. For commercial, the repeated lookup prevents duplicate rows, but
     both runs may pay for the same new explanation inside that window.
  3. **Reattach is best effort.** If the active-operation request fails,
     the button is enabled after a reload; a click then returns the same
     active operation, so no second run starts.
  4. **In development the active-operation request is made twice on load**
     (React StrictMode runs effects twice; seen in the live check). A
     production build makes it once.
  5. **BLAS runs single-threaded in the operations worker.** Operations do
     no numeric work today; if one ever embeds or does maths, it runs
     slower but correctly.
  6. **The preload costs the operations worker about 400 MiB** at idle
     (about 126 MiB without it, about 520 MiB with it; measured), almost
     all of it sentence_transformers and torch, a library no operation
     uses. Follow-up (a) below removes it.
  7. **Workflow jobs, gate resumes included, still spend 7 to 8 s
     importing per job** (the same per-fork import on the default worker,
     unchanged in this stage). Measured 2026-10-16:
     `tenderos_worker.jobs.workflow` takes 7.7 to 7.9 s to import in a
     fresh interpreter (3 samples), through the same chain
     (`orchestration.graph` -> `nodes.commercial` -> `commercial_service`
     -> ingestion); the commercial gate's resume job lasted 7.5 to 7.6 s
     in total in runs B1 and B2, so that job is almost all import.
     Follow-ups, owner Phase 4 deployment prep, in this order, each tested
     with its own E2E runs:
     - (a) **First item of deployment prep: lazy-import the embedder**
       (`ingestion/embed.py`), so code that never embeds never imports
       torch. It recovers the operations worker's ~400 MiB (limit 6) and
       cuts API and default-worker memory and start-up too.
     - (b) **Preload the default worker** the same way. It needs the same
       BLAS pin, and workflow jobs do embed (ingestion, retrieval), so
       measure single-threaded BLAS there before choosing. It is expected
       to break the E2E's step 11: the resume job would end well before
       the red-team click instead of about 5 s after it (see the next
       entry). Re-measure the workflow job's own import time (after (a))
       before trying it.
- **Risk:** limit 2 can repeat an explanation LLM call when a manual
  re-validation and the workflow's commercial step run at the same time.
- **Status:** built (Stage 2). Limits 1-7 accepted; limit 7's two
  follow-ups open.
- **Test:** `tests/integration/test_async_operations.py`
  (`test_a_duplicate_delivery_runs_each_kind_once`, all three kinds;
  `test_a_real_proposal_generation_runs_once_under_a_duplicate_delivery`;
  `test_a_real_commercial_validation_runs_once_and_explains_only_new_findings`),
  `tests/integration/test_operations_api.py`
  (`test_the_active_operation_is_returned_while_running_and_null_when_idle`,
  `test_the_active_operation_is_a_403_across_organizations_and_a_404_for_a_missing_tender`,
  `test_the_active_operation_needs_a_known_kind`),
  `tests/route/test_proposal_routes.py`
  (`test_generate_proposal_returns_202_and_the_same_operation_while_active`),
  `tests/route/test_commercial_routes.py`
  (`test_validate_pricing_document_queues_an_operation_the_worker_runs`),
  `tests/integration/test_commercial_service.py`
  (`test_evaluate_commercial_for_tender_is_idempotent_on_retry` now asserts
  no LLM call on the retry). The three commercial tests fail with the
  stored-finding skip removed. Vitest: `ProposalWorkspace.test.tsx`,
  `CommercialFindings.test.tsx`, `RedteamFindings.test.tsx` (busy until
  finished, re-read before release, reattach on load); the release-order
  and reattach tests fail with those parts of the hook switched off.
  Queues: `tests/unit/test_dispatch_queues.py` (OperationRequested goes to
  `operations`, every other event to its previous queue, the default
  worker does not listen to `operations`) and
  `tests/integration/test_outbox_dispatcher.py::test_operation_requested_routes_to_run_operation_with_its_kinds_timeout`.
  Preload: `tests/unit/test_operations_preload.py` (the import starts no OS
  thread with the pin; an import that starts a thread makes the preload
  refuse to start, on any CPU count; with `OPENBLAS_NUM_THREADS=4` and
  `OMP_NUM_THREADS` unset it refuses too). Linux only (it reads /proc),
  so it skips on the Windows dev host; CI's backend job runs `uv run
  pytest` on `ubuntu-latest`, where it runs. The pin-missing test also
  skips when the process can use only one CPU: OpenBLAS then starts no
  pool even with `OPENBLAS_NUM_THREADS=4` (observed with `taskset -c 0`),
  so no setting can make that one CPU-independent; the thread-at-import
  test covers the refusal there. On 2026-10-16 all four were run inside
  the `operations_worker` container: 4 passed on 16 CPUs; on one CPU 3
  passed and the pin-missing test skipped.
  Review fixes (2026-10-22): the three routes that create operations,
  called by another organization's admin, answer 403 `ORG_ACCESS_DENIED`
  (as every tender-scoped route does through `require_permission`; so does
  the active-operation route; only the `/operations/{id}` routes answer
  404) and write no `async_operations`
  row and no operation outbox event
  (`tests/route/test_operation_routes_cross_org.py`, all three kinds; the
  403 itself was already asserted by
  `test_trigger_red_team_rejects_a_caller_outside_the_organization`,
  `test_list_generate_and_patch_reject_a_caller_outside_the_organization`
  and `test_commercial_routes_reject_a_caller_outside_the_organization`).
  The proposal and red-team screens guard their re-read after a
  finished operation, and their load-time read, against the user moving
  to another tender meanwhile, like the commercial screen: the two new
  stale-tender tests in `ProposalWorkspace.test.tsx` and
  `RedteamFindings.test.tsx` fail without the guard.

## [Phase 4 async operations, Stage 2, 2026-10-16, RESOLVED 2026-11-03 by the one-time spec edit] E2E step 11 depends on the workflow still running when the red team is clicked

- **Discovered:** Stage 2's first E2E runs failed 3 of 3 at
  `apps/web/e2e/full-flow.spec.ts:1024` (fake provider): the second tab
  waits up to 60 s for a `GET /workflow-runs/{id}/tasks` refetch after
  the red-team click, and none came.
- **Behaviour:** that refetch is pushed only by a workflow event (the
  spec's comment names `red_team_node`'s status write). The manual red
  team is an async operation and writes no workflow event, so step 11
  passes only if the workflow, resumed by the commercial-gate approval at
  step 8, is still running when step 11 clicks. Before Stage 2, step 9's
  proposal generation ran inside the API, alongside the workflow job, and
  step 11 began while that job still ran. With operations on the shared
  worker queue, the proposal waited for the workflow job to finish, so
  step 11 began after its last event (run 2: last event 06:10:32.409,
  click 06:10:40.858, 0 events after). Fixed by giving operations their
  own queue and worker and preloading their job module (Stage 2 entry
  above, behaviour 8 to 11); now 9 workflow events follow the click in
  each passing run.
- **Margin (measured, fake provider):** in the two passing runs the
  red-team click came 5.1 s (B1: 06:50:52.421, job ended 06:50:57.559)
  and 5.3 s (B2: 06:56:03.095, job ended 06:56:08.402) before the
  workflow's resume job ended. That job lasts about 7.5 s, and its own
  module import alone measures 7.7 to 7.9 s in a fresh interpreter, so
  the job is probably almost all import; the margin exists only because
  workflow jobs still pay that import.
- **Observed 2026-10-23 (batch 2b's E2E, passed):** the margin was
  5.150 s (click 10:57:56.438, resume job 10:57:54.224 to 10:58:01.588).
  The api log shows one `GET /workflow-runs/<id>/tasks` 0.855 s before the
  click (10:57:55.583) and none after: the red team caused none.
  Playwright's `waitForRequest` matches only requests issued after it is
  registered (`full-flow.spec.ts:1024`, after the second tab's
  `networkidle`, before the click), so that earlier fetch met the wait and
  was issued after the registration. Two candidate causes:
  (a) the tab's own first data fetch, issued after `networkidle` had
  already resolved; (b) a refetch driven by a workflow event pushed to the
  tab while the resume job ran (the last workflow event the worker logged
  before the fetch was `WorkflowRunStatusChanged`, 1.366 s earlier). The
  same log weighs against (b): the fetch reached the api in the tab's first
  burst (`/auth/me` 55.42, runs and documents 55.48, ticket mints 55.53)
  and before either of its event streams opened (55.584, 55.592), so no
  event could yet have been pushed to it. Neither is proven: server logs
  cannot show when the wait was registered, or which tab a request came
  from. Either way the margin did not decide that run: the wait was met
  before the click. CI's `e2e` job records the fetches on both sides of
  the click and the last workflow event before the fetch (the entry
  below); its runs settle which cause holds.
- **Observed 2026-10-23, CI run 2 (run 39980533388, passed; run 1 ran out
  of disk before the E2E):** margin +14.776 s (click 15:51:39.069, resume
  job 15:51:35.201 to 15:51:53.845). The job took 18.6 s on the runner
  against about 7.5 s locally, so the slower runner widened the margin
  rather than narrowing it. 0 task fetches after the click; the last one
  1.437 s before it; the last workflow event the worker logged before that
  fetch (`WorkflowRunStatusChanged`) 7.414 s before it. A refetch driven
  by a workflow event would follow its event within about a second, so a
  7.4 s gap points to cause (a), the second tab's own first data fetch.
  **Tally (runs with a timeline):** 2 of 2 point to (a), batch 2b's local
  run and CI run 2. Counted until 5 CI runs are recorded.
- **Observed 2026-10-23, three more CI runs (all passed):** main
  `ca9feda` (39983007910): margin +19.642 s, 4 task fetches after the
  click, the last before it 1.423 s, its workflow event 8.448 s before
  it; PR #38 (39989695235): +15.251 s, 1 after, 1.812 s, 2.119 s; main
  `fbe383e` (39991181703): +24.249 s, 0 after, 1.906 s, 3.267 s. By the
  event gap alone #38's 2.1 s is ambiguous, but in all four CI runs the
  pre-click task fetch reached the api before either of the second tab's
  event streams opened (by 61, 40, 4 and 85 ms; uvicorn logs a streaming
  response when it starts), so no pushed event could have caused it.
  **Tally: 4 of 4 CI runs point to (a)**, an inference (server logs
  cannot name the tab). Two runs had task fetches after the click, which
  earlier runs did not; neither follows a worker-logged workflow event
  within a second, their source is not identifiable from the logs, and
  neither decided the wait. On the runner the margin is +14.8 to +24.2 s
  (about +5 s locally).
- **Accepted limit:** a timing coupling in a spec we do not edit (blob
  `318599ac`). Anything that makes the workflow's last steps after the
  commercial gate finish sooner, or the steps between that gate and the
  red-team click take longer, can fail step 11 without a product bug.
  Preloading the default worker (Stage 2 entry, limit 7, follow-up (b))
  is expected to break it, since it would remove most of the resume job's
  7.5 s; measure that job's own import time before trying it, and give it
  its own E2E runs. Lazy-importing the embedder (follow-up (a)) shortens
  the same job too, by less, so it also needs its own E2E runs.
- **Resolved (the one-time spec edit, 2026-11-03):** the live-progress
  check moved from the red-team click, which writes no workflow event, to
  step 8's commercial-gate approval, which resumes the workflow (the
  revision loop this check was written about). A second tab on the
  Upload/Processing screen is settled first: its own first `/tasks` has
  answered and both of its event streams have been requested. Every
  `/tasks` request it sends before the approval request goes out is
  excluded, so only a request that starts after the approval can count;
  the wait is for its response (a request cancelled by closing the tab
  would leave no trace on the server), and no stream may reopen before it,
  since a reconnect refetches without a push. Read in code: the tab
  refetches only after minting a ticket, on a stream message, or on a
  reconnect, and the server's heartbeats are SSE comments, which raise no
  message.
  - **Proven falsifiable (observed):** with the client made to ignore
    stream messages (a temporary scratch build), the E2E failed 3 runs of 3,
    each at the live-tab wait after its 60 s.
  - **Observed passing:** the approval, then the resume job, then the open
    tab's `GET /tasks` 0.26 s after the job's last write, with no stream
    reopened in between (2026-11-03).
  - `scripts/e2e_step11_timeline.py` now reports this sequence (approval,
    stream opens, the refetch, the resume job, the last workflow event)
    in CI's job summary.
- **Status:** resolved; the timing coupling is gone (the check no longer
  depends on the workflow still running at some click).
- **Test:** the E2E itself; `tests/unit/test_e2e_step11_timeline.py`.

## [deps, 2026-10-22, open, accepted; torch automated in batch 1] torch is never audited by pip-audit, nor is native code in the images; check by hand before each release

- **What happens:** `uv run pip-audit`, locally and in CI's `security`
  job, skips `torch 2.13.0+cpu` with "Dependency not found on PyPI and
  could not be audited" (measured 2026-10-22, the same skip as in the
  2026-10-10 entry "What pip-audit and npm audit do not cover"). The lock
  takes torch from the PyTorch CPU index, and the `+cpu` local version
  does not exist on PyPI, so pip-audit never looks it up. CI runs without
  `--strict`, so the skip passes silently: a torch advisory would never
  turn the job red.
- **Why it matters:** torch is installed in the api and worker images
  (measured: `2.13.0+cpu` in both), and sentence_transformers loads it for
  embedding (`ingestion/embed.py`) and reranking
  (`agents/knowledge/rerank.py`), so it is runtime code, not dev tooling.
- **The manual check, before each release:** query OSV (the database
  pip-audit uses) for the locked version without the local suffix, and
  read PyTorch's GitHub security advisories for anything OSV has not
  picked up yet:
  `curl -s -X POST https://api.osv.dev/v1/query -d '{"package":{"name":"torch","ecosystem":"PyPI"},"version":"2.13.0"}'`
  (replace the version with the one in `uv.lock`). On 2026-10-22 it
  returned no advisories for 2.13.0; the same query for 2.5.1 returned
  22, so the query itself works (measured).
- **Also outside pip-audit: native code.** pip-audit matches Python
  package versions only. It does not see native libraries bundled inside
  wheels, or the images' OS packages. What is there (measured in the
  running containers, 2026-10-22):
  - **PDFium**, bundled in the pypdfium2 5.12.1 wheel as
    `site-packages/pypdfium2_raw/libpdfium.so` (7.6 MB, PDFium build
    152.0.7947 from pdfium-binaries, listed in the wheel's RECORD), in the
    api and worker images. pdfplumber uses it to render pages of uploaded
    PDFs for OCR. PDFium is Chromium's PDF engine: its security fixes ship
    as Chromium advisories, not PyPI ones, so a PDFium flaw shows up in
    pip-audit only if pypdfium2 itself gets an advisory.
  - **Pillow's bundled libraries**, `site-packages/pillow.libs/` (18 of
    them, among them libwebp, libjpeg, libtiff, libopenjp2, libpng,
    freetype), in both images. A flaw in one of them is filed against the
    C library; pip-audit reports it only if Pillow also gets an advisory
    for the release that updates it.
  - **Tesseract**, from Debian 13 (trixie) OS packages installed by
    `apps/worker/Dockerfile`: `tesseract-ocr` and `libtesseract5`
    5.5.0-1+b1, `libleptonica6` 1.84.1-4, in the worker image only. It
    reads images rendered from uploaded PDFs. The `python:3.12-slim`
    base's own OS packages are likewise unaudited.
  - **Who should cover this:** a container image scan (one that reads both
    OS packages and binaries inside site-packages) as part of deployment
    prep. Nothing in CI does it today.
- **Not done:** no automated step. A CI step running that query would
  close the torch gap; it is a separate change, as is the image scan.
- **Update (Phase 4 batch 1, item 8):** the torch half is automated.
  `scripts/check_torch_osv.py` reads every torch version in `uv.lock`
  (2.13.0 for macOS and 2.13.0+cpu elsewhere, one base version today),
  drops the local suffix, queries OSV and fails on any advisory; CI's
  `security` job runs it after pip-audit. Exit codes: 0 none, 1 advisories,
  2 the check could not run (no torch in the lock, the lock unreadable, OSV
  unreachable or answering something unexpected), so a network failure
  never passes as "no advisories". Observed locally 2026-10-22: the locked
  2.13.0 gives 0 advisories and exit 0; `--version 2.5.1` gives 22 and exit
  1; an unreachable OSV host gives exit 2. The manual query above stays as
  the fallback, and reading PyTorch's own GitHub advisories before a
  release still covers anything OSV has not picked up. Native code in the
  images is unchanged: still no image scan (plan item 9).
- **Status:** torch automated (batch 1); native code open, accepted.
- **Test:** the CI `security` step; the three exit codes observed by hand.

## [deps, recorded deviation from A2 under section 0.1, 2026-10-22] pypdf removed: declared, never imported

- **The deviation:** A2 (Build Guide :154) lists pypdf among the
  document-parsing libraries. It is no longer a dependency: removed from
  `packages/tenderos_backend/pyproject.toml`, and only pypdf left
  `uv.lock` (6.21.2; nothing else moved).
- **Why:** nothing ever imported it. Searched on 2026-10-22: no import in
  our code, and none in any installed package (its only dependant in the
  lock was `tenderos-backend` itself). Uploaded PDFs are parsed by
  pdfplumber (`ingestion/parse_pdf.py`), which uses pdfminer.six to
  extract text and pypdfium2 to render pages for OCR. Keeping an unused
  PDF parser in the runtime images only added advisories to track (11 in
  October's first audit, more since) with no code path reaching it.
- **What the docs still say:** A2's table is unchanged (the Build Guide is
  not edited from here). `scripts/synthetic_common.py:5` cites that table
  for why fixtures hand-write their PDFs; that stays true and was left as
  is. The 2026-10-06 audit entry's claim that pypdf parses uploads carries
  a dated correction.
- **Verified (local, fake provider):** backend suite 739 passed, 4
  skipped, via scripts/test-backend.ps1; `uv run pip-audit` "No known
  vulnerabilities found", exit 0; the fake stack rebuilt with
  scripts/fake-stack.ps1, every service running (api healthy), and pypdf
  neither installed nor importable in the api, worker,
  operations_worker and outbox_dispatcher containers. No E2E: no code
  path changed.
- **Reverse if:** a feature needs PDF writing or low-level PDF object
  access that pdfplumber and pypdfium2 do not give; re-add it then, at a
  version pip-audit passes.
- **Status:** recorded, accepted.

## [tests, 2026-10-22, open, accepted] One deprecation warning left in the backend suite: Starlette's test client on httpx

- **What it says:** `StarletteDeprecationWarning: Using httpx with
  starlette.testclient is deprecated; install httpx2 instead`, raised
  once per run when `fastapi.testclient` is imported (Starlette 1.3.1,
  FastAPI 0.140.13, httpx 0.28.1).
- **Why it stays:** it concerns the test client only; the api does not
  serve requests through httpx. 17 test files use `TestClient`, and
  httpx is also a runtime dependency (LLM and HTTP clients), so moving
  the tests to httpx2 (2.16.0 on PyPI) is a dependency change of its
  own, with its own test pass.
- **When it matters:** the release of Starlette that removes httpx
  support from `starlette.testclient`. The suite then fails at import
  rather than passing with a warning, so it cannot go unnoticed.
- **The other four warnings** of CI's 2026-10-22 run are fixed: the
  deprecated `HTTP_413_REQUEST_ENTITY_TOO_LARGE` constant (three
  warnings, `routes/documents.py`) is now `HTTP_413_CONTENT_TOO_LARGE`,
  the same 413 status; and `alembic.ini` sets `path_separator = os`
  (one warning).
- **Status:** open, accepted.
- **Test:** the backend suite's warnings summary (1 entry expected).

## [found 2026-10-22, open, fix before deployment] The web container serves the Vite dev server, not a production build

- **What happens:** `apps/web/Dockerfile`'s CMD is `npm run dev -- --host
  0.0.0.0`, the Vite dev server listening on every interface of the
  container (observed 2026-10-22: the container logs `VITE v8.3.5 ready`).
  No compose file or overlay changes it, and no production build is
  served anywhere in the stack.
- **Consequences:**
  - Vite's dev-server advisories reach the running stack. The npm
    advisories fixed in `cc74b1a` were of this kind (cross-origin script
    execution through custom middleware; files outside `server.fs.allow`
    served).
  - The E2E clicks through the dev server's on-demand module transforms,
    not the build users would get, so a failure that only appears in a
    production build would not be caught.
- **Fix before deployment:** plan item 27. A multi-stage image builds the
  app and serves `dist/` from a small web server, with no Vite in the
  runtime image. A compose dev override keeps the dev server with hot
  reload for local work. The E2E runs against the build, with 3 runs,
  because it changes what the E2E clicks through.
- **Status:** open.
- **Test:** none yet; item 27's proof is no `node_modules/vite` in the
  image, plus the E2E runs.

## [found 2026-10-22, RESOLVED: delivering since batch 1 merged; batch 2a adds a catch-all group and docker entries] Dependabot is configured but not delivering

- **What happens (observed 2026-10-22, `gh pr list` and `gh api`):**
  `.github/dependabot.yml` has existed since B15 (`020b65c`), with entries
  for uv, `apps/web` npm and GitHub Actions.
  - All 8 Dependabot PRs were opened on 2026-10-07, the first push, and
    are still open and unreviewed: 5 npm, 3 GitHub Actions. Dependabot
    stops opening version-update PRs at 5 open per ecosystem by default,
    so npm updates have been blocked since then.
  - No uv PR has ever been opened. The cause is not visible from the API.
  - The repository's `security_and_analysis` came back empty, and the
    vulnerability-alerts endpoint answered 404. Security updates (PRs
    opened because of an advisory) appear to be off. This is a repository
    setting to verify in GitHub, not something this file can confirm.
  - `apps/landing` has no entry.
- **Consequence:** two CI security jobs went red on 2026-10-22 (35 Python
  advisories, then 3 npm) that Dependabot should have raised as PRs first.
- **Fix:** plan item 26.
  - Verify the setting, and turn on Dependabot alerts and security
    updates.
  - Group minor and patch updates, so one PR per ecosystem can't hit the
    open-PR limit.
  - Add `apps/landing`.
  - Find why uv gets no PRs, from Dependabot's job log in the
    repository's Insights.
  - Close the 8 stale PRs once `phase4/backend-hardening` is merged.
- **Why uv never got a PR (observed 2026-10-22, Dependabot job log of
  run 39558240249, "uv in /."):** every uv job has failed (runs on
  2026-10-07, 10-14 and 10-21; only the 10-21 log was read). Finding no Python version it
  reads (no `.python-version`, and the root `pyproject.toml` is a bare
  workspace root with no `requires-python`), Dependabot ran
  `pyenv local 3.14.7`, its own default, which writes `.python-version`;
  then each `uv lock --upgrade-package ...` failed with
  `dependency_file_not_resolvable`: "The Python request from
  `.python-version` resolved to Python 3.14.7, which is incompatible with
  the project's Python requirement: `==3.12.*`". All 24 updates in that
  job failed the same way, torch included; the CPU index was never the
  problem. Batch 1 adds a tracked `.python-version` of `3.12`.
- **Built in batch 1 (item 26), `.github/dependabot.yml`:** `apps/landing`
  added; per ecosystem, three groups: minor and patch version updates,
  major version updates, and security updates (none for Actions);
  `open-pull-requests-limit: 5` set explicitly; no `ignore` at all.
  Majors get their own group instead of an ignore because GitHub applies
  ignore conditions to security updates as well (its how-to page
  "Controlling which dependencies are updated by Dependabot": ignore
  conditions apply "when it opens pull requests for version updates and
  security updates"). A `"*"` major ignore could have blocked exactly the
  2026-10-22 case, where pillow's advisories were fixed only in a major
  (12 to 13). A majors group cannot block a security update, and skipped
  majors stay visible as one open PR per ecosystem.
- **The 8 PRs of 2026-10-07 (checked 2026-10-22 against main after the
  merge, 4526e7b):** the merge superseded none of them; main still has
  every "from" version. Five are majors that will come back inside the
  majors PRs: #1 (setup-node 4 to 7), #2 (setup-uv 3 to 7), #3 (checkout
  4 to 7) in the Actions majors PR, #4 (globals 15 to 17) and #6 (vitest 4
  to 5) in the web majors PR. Three are minor or patch updates the grouped
  web PR will carry: #5 (@playwright/test 1.62.1 to 1.64.1), #7 (react
  19.2.8 to 19.3.0 with @types/react), #8 (prettier 3.9.6 to 3.9.9). So
  all 8 can be closed once this merges; closing them is a step in GitHub.
- **Still a repository setting, not a file:** `security_and_analysis` was
  still empty and the vulnerability-alerts endpoint still answered 404 on
  2026-10-22, so security updates stay off until they are turned on in
  the repository settings.
- **Status:** cause found; config built, not observed (Dependabot reads it
  from the default branch, so nothing changes until this merges).
- **Test:** none in the repo. The proof after merge: the next uv job (or a
  manual "Check for updates") succeeds, and one grouped PR per ecosystem
  appears.
- **First run under the new config (observed 2026-10-22 and 10-23, `gh`):**
  - The uv jobs now succeed (Python 3.12.13 from `.python-version`), and
    Dependabot opened ten PRs, #17 to #26. It closed the 8 old PRs itself
    as superseded.
  - #18, #20, #22, #24, #25 and #26 were merged and tested as one branch
    (decision 0050) and merged as PR #28; GitHub marked all six merged.
    #22's title said "5 updates" but its lock moved 25 packages (torch
    2.14.1, rq 2.12.0, langgraph 1.2.16, alembic 1.20.0 among them).
  - The four dependencies declared with extras (`sqlalchemy[asyncio]`,
    `psycopg[binary,pool]`, `uvicorn[standard]`, `pydantic[email]`) have
    no current version in Dependabot's view, so they matched no
    update-types group: three came as single PRs (#24 to #26), and
    pydantic[email]'s update was submitted but never opened as a PR
    (probably the uv open-PR limit of 5; not confirmed). Batch 2a adds a
    catch-all `uv-other` group after the typed ones.
  - Still open: #17 (Actions majors; closes with item 7, which pins those
    releases by SHA), #19 and #21 (npm majors; `npm ci` fails with
    ERESOLVE because TypeScript 7 is outside typescript-eslint 8's peer
    range `>=4.8.4 <6.1.0`; plan item 33, no ignore), #23 (neo4j 6, and
    sentence-transformers 5 to 6 in the same PR; plan items 31 and 32).
  - Security updates are on now: `automated-security-fixes` answered
    `{"enabled":true}` on 2026-10-23 (it was `false` on 10-22), and the
    first security PR, #27 (Mako 1.3.12 to 1.4.2, GHSA-5639-2j2p-m4mx,
    through alembic), came through the `uv-security` group and was
    merged. pip-audit did not report that advisory on 2026-10-22, when
    GitHub's alert already listed it (observed); why its sources lagged
    was not checked.
- **Status:** the uv cause is fixed and observed; grouping and security
  updates observed working. Majors are planned items, not ignores. The
  catch-all group and the docker entries (item 7) are not observed until
  merged.
- **Expected red (batch 2a, item 7):** `scripts/check_pins.py` (CI's lint
  job) requires ci.yml's `services:` images to equal docker-compose.yml's
  for postgres, redis and neo4j, and Dependabot's docker-compose PRs
  update only the compose file. Every such PR that moves one of those
  three images will fail the lint job until ci.yml's matching image is
  updated on the same branch. The fix: one commit on the Dependabot
  branch changing that ci.yml image line to the PR's new `tag@digest`.
  The red is intended; without it, CI would test against a different
  image than the stack runs.

## [found 2026-10-22, open: guarded in batch 1, cause unknown] An intermittent connection-leak warning in CI's backend run

- **What happened:** CI on `cc74b1a` (run 39775453912) passed 743 tests with
  2 warnings. One was the known test-client deprecation (the entry above on
  Starlette's test client). The other was `SAWarning: The garbage collector
  is trying to clean up non-checked-in connection`, raised during
  `tests/integration/test_readiness_service.py::test_clean_tender_is_ready`.
- **Seen:** once, in CI. Not in CI on `53a9ec8` or `ca998a6`, and never in
  a local run. It appears only when the garbage collector runs at that
  point, so its absence from a run proves nothing.
- **Cause:** not yet known. Some path opens a database connection and drops
  it without returning it to the pool; it may be in the test's own code or
  fixtures, or on the readiness path itself.
- **Fix:** plan item 29, batch 1.
  - Reproduce with the warning raised as an error and tracemalloc on, to
    find where the connection was created.
  - Close it on that path.
  - Make `SAWarning` an error in the pytest config, so a leak fails CI
    instead of passing.
- **Status:** open.
- **Test:** none yet; item 29's proof is 20 runs of the file with the
  warning as an error.
- **Phase 4 batch 1 (item 29, 2026-10-22): guard built, leak not found.**
  - **Not reproduced (observed):** one full local suite with a scratch
    plugin forcing a collection after every test: 739 passed, 0 leaks. The
    instrument was checked first: a planted leak was caught and attributed.
    Two product paths read as candidates, both in `UnitOfWork.__aexit__`
    (a `rollback()` that raises skips the session's close; a cancellation
    mid-query), were tried in scratch tests and did not leak: the abandoned
    `get_session()` generator is closed by asyncio's async-generator
    finalizer, and a cancelled query rolls back cleanly. No product code
    was changed. Where CI's connection came from is still unknown.
  - **Read from the CI log:** the warning was raised in neo4j's
    `hydration_handler.py:98`, unrelated code, which is how a cyclic-GC
    collection looks. The test it is attributed to is probably not the one
    that leaked; `test_proposal_service.py` ran just before it in CI.
  - **Why `error::SAWarning` alone would not have helped:** the warning is
    raised inside a GC callback, where an exception cannot reach the test
    (read in code, `sqlalchemy/pool/base.py:1016-1032`). Observed: with
    only `error::sqlalchemy.exc.SAWarning` set and no conftest guard, a
    leak in a plain local and a leak in a cycle both passed ("3 passed,
    2 warnings"; pytest turned each into a `PytestUnraisableExceptionWarning`).
  - **Built:** `pyproject.toml` makes `SAWarning` and
    `PytestUnraisableExceptionWarning` errors; a new `tests/conftest.py`
    collects after every test (with the objects from before the first test
    frozen, so it costs nothing measurable: 297.9 s against a 301.9 s
    baseline) and appends, to SQLAlchemy's own ERROR line, the repo frames
    that checked the leaked connection out. Decision record 0047 (local).
  - **Proof (observed):** three planted shapes (a plain local; a reference
    cycle found at the end of its test; a cycle collected during a later
    test) each fail, each report naming the exact line that called
    `engine.connect()`; a clean test passes. The planted file was deleted
    afterwards.
  - **Limits:** a connection held by something still alive (a pending
    task) is reported when it is released, possibly in a later test (the
    stack still names its origin). Only SQLAlchemy pools are watched;
    LangGraph's checkpointer has its own psycopg pool.
- **Status (batch 1):** guarded; cause unknown. The next occurrence fails
  CI and names its origin.

## [Phase 4 batch 2a, 2026-10-23] Starting and stopping the stack: migrations at start, and what a stop does to a job in progress

- **Migrations (item 4):** before, a fresh database stayed empty until
  someone ran `alembic upgrade head` by hand; nothing in compose or the
  api's CMD ran it. Now a one-shot `migrate` service on the api image runs
  `alembic upgrade head`, and api, worker, operations_worker and
  outbox_dispatcher start only after it has exited 0
  (`service_completed_successfully`). It is a service, not part of the
  api's entrypoint, because api replicas would each race to migrate. On a
  host with a release phase, the same command becomes the release
  command. Observed 2026-10-23 on throwaway compose projects with their
  own volumes (the project's volumes untouched): a fresh database ran all
  20 upgrades to `020_async_operations (head)` and the api became
  healthy; a second start re-ran migrate (new start time), applied 0
  upgrades, exited 0; with a conflicting `organizations` table planted,
  migrate exited 1 (`DuplicateTableError`), `up` failed with "service
  migrate didn't complete successfully", and the api container was
  created but never started.
- **Stopping (item 28), observed 2026-10-23 on the fake stack before any
  change:** every Python container ran `uv run` (uv 0.12.3) as PID 1, and
  uv forwarded SIGTERM to Python. Idle stops: worker 1.7 s (RQ's "warm
  shut down requested", the scheduler releasing its locks, exit 0); api
  0.8 s (uvicorn's full shutdown, exit 143, which is uvicorn re-raising
  the signal after it); outbox_dispatcher 0.6 s (no handler: killed by
  the signal, exit 143). With a 45 s job running, the worker began a warm
  shutdown, waited for the job, and Docker's default 10 s grace ran out:
  SIGKILL, exit 137, and the job stayed `started` in RQ's started
  registry; RQ's own `Job.delete()` then failed on it ("Execution ... not
  found in Redis"). With `stop -t 60` and a 20 s job, the job finished and
  the worker exited 0 after 16.4 s. RQ's warm shutdown never requeues the
  job in progress (read in code, rq 2.12.0 `worker/base.py`,
  `request_stop`, `_shutdown`); a second SIGTERM is a cold shutdown that
  kills it. Every start also rebuilt and reinstalled the two workspace
  packages ("Uninstalled 2 packages ... Installed 2 packages").
- **Changed:** the images install the workspace packages at build time,
  after their code, and each CMD runs the venv's program directly as PID
  1 (`uvicorn` with `--timeout-graceful-shutdown 10`, `python -m ...`).
  As PID 1 a process gets no default signal action, so the dispatcher now
  handles SIGTERM itself: the tick in progress finishes and it exits.
  Grace periods (`stop_grace_period`): api 20 s, outbox_dispatcher 30 s,
  worker and operations_worker 31 min. A worker's job can run 1800 s. A
  killed workflow job's run is failed by the dispatcher's run reaper as
  WORKER_LOST about 4 minutes later (plan item 19; 257 s observed
  2026-11-04, decision 0035); a killed operation is
  failed by the dispatcher's reaper as WORKER_LOST, but only once its
  lease (job timeout plus 60 s) has lapsed, so killing it early only
  loses the work. An idle worker still stops in seconds; the 31 minutes
  apply only while a job runs. `docker compose stop -t <seconds>` stops
  sooner, accepting the loss. Compose also sets `init: true` on the five
  Python services (api, worker, operations_worker, outbox_dispatcher,
  migrate): Docker's tini is PID 1, forwards signals and reaps orphaned
  children, and the program runs with ordinary signal defaults; the
  dispatcher's handler is what lets it finish its tick.
  **Observed 2026-10-23 after the change:** `docker-init` is PID 1 in each
  app container, the program its child, and no reinstall lines in any
  start log. Idle stops: outbox_dispatcher 2.2 s, exit 0, logging
  "outbox dispatcher stopping after tick 7" (it was killed before); api
  0.8 s (uvicorn's shutdown, exit 143 as before); worker 0.8 s and
  operations_worker 2.4 s, warm shutdowns, exit 0. A 30 s job started 4 s
  before a plain `docker compose stop worker` ran to completion
  ("Successfully completed"), and the worker exited 0 after 26.4 s. The
  reaping of orphans was not observed (Docker's documented init
  behaviour).
- **Limits:**
  - **Host-dependent (accepted 2026-10-23 for compose):** the 31-minute
    grace is compose's. A host that caps shutdown time (a platform's
    deploy or stop timeout, the Docker daemon's own timeout at host
    shutdown, which was not checked) kills a running job sooner, and the
    two reaper paths above apply. Choosing the host settles this.
  - A job killed anyway is not retried by RQ. Operations are failed by
    the reaper; workflow runs by the run reaper (plan item 19).

## [Phase 4 batch 2b, 2026-10-23, resolved for the api; the proxy's log is a deployment item] SSE tickets in the access log

- **Before:** `GET .../events/stream?ticket=...` carries its single-use
  ticket in the query string (EventSource cannot send headers), and
  uvicorn writes every request's path and query string to the
  `uvicorn.access` log, so each ticket was in the api log (observed in
  Phase 4 Stage 1 logs; decision 0008). A ticket is consumed on first use
  (Redis GETDEL) and lives 30 s, so a logged one was normally dead
  already.
- **Changed (plan item 3):** a filter on the `uvicorn.access` logger
  replaces the value with `[redacted]`
  (`apps/api/src/tenderos_api/logging_filters.py`, installed by
  `main.py:74` at import). It sits on the logger, not a handler, because
  uvicorn's access handler does not propagate to the root handlers that
  `configure_logging()` filters. Read in uvicorn 0.54.0's `config.py`:
  uvicorn applies its logging config in `Config.__init__`, before
  `Config.load()` imports the app, so the filter is attached after
  uvicorn's own setup, and a later `dictConfig` keeps a logger's filters
  (tested in both orders). The query parameter stays.
- **Observed 2026-10-23:** after one E2E on the rebuilt fake stack,
  `docker compose logs api` held 8 `events/stream` lines (4 workflow, 4
  operation), all 8 with `ticket=[redacted]`, and no line with `ticket=`
  followed by anything else.
- **Limits:**
  - The reverse proxy in front of the api, once there is one, keeps its
    own access log; it needs the same redaction, configured at
    deployment (plan item 3, host part).
  - Browser history still holds the stream URL with its ticket (dead
    after first use).
- **Test:** `tests/unit/test_logging_filters.py` (a record made as
  uvicorn 0.54.0 makes it, written through uvicorn's own `LOGGING_CONFIG`
  handler and formatter; the filter installed after and before uvicorn's
  `dictConfig`; installing twice adds one filter; the pattern's edges).

## [found 2026-10-23 in batch 2b's review; the 15-minute session RESOLVED for the web app in plan item 34; revocation RESOLVED in option 3 (2026-10-24); access tokens outlive a logout (accepted); the httpOnly cookie waits for item 27] A web session ends when its 15-minute access token expires; refresh tokens cannot be revoked

- **Resolved for the web app (plan item 34, option 1, decision 0057):**
  `apps/web/src/api/client.ts` now answers a 401 from any route except
  `/auth/login` and `/auth/refresh` by calling `/auth/refresh` and
  retrying the request once. One refresh at a time: every 401 that
  arrives while it runs waits for it, and a 401 whose request carried the
  token the refresh has just replaced retries without refreshing again. A
  retried POST keeps its Idempotency-Key; the API answers an expired
  token with 401 before it claims the key, on every route that takes one
  (`tests/route/test_idempotency_after_auth.py`). A refresh the API
  rejects (a 4xx other than 429), or a 401 on the retry, clears the
  tokens and lands on the login screen with the destination kept; a
  refresh that fails on the network, with a 5xx or with a 429 (transient,
  and plausible once a proxy puts every user behind one IP, blocker 1,
  limit 3) keeps the session and returns the original 401.
  SSE ticket mints take the same path, and a dropped stream reconnects
  with a fresh ticket on any error. **Observed (live check, 2026-10-23,
  fake provider, `JWT_ACCESS_MINUTES=1` for the api only, not committed):**
  logged in at 16:30:48, waited until 16:32:14 (26 s past the token's
  expiry), opened Talent then Bids: Talent's two parallel requests got
  401, one `POST /auth/refresh` (200), both retried with 200; Bids
  loaded with the new token; no logout; exactly one refresh request in
  both the browser's network log and the api's access log. The E2E
  passed once afterwards on normal settings (2.7 min).
- **Found by the live check and fixed before commit:** storage cleared
  while a page load's refresh was in flight got a new access token
  written back into it by that refresh's answer, so the next load was
  signed in again (an access token with no refresh token). The answer is
  now dropped when the stored refresh token is no longer the one sent; a
  vitest case reproduces it and failed before the fix.
- **Resolved: revocation (plan item 34, option 3, 2026-10-24).** Every
  issued refresh token has a row in `refresh_tokens` (migration 021):
  its `jti` as the id, a SHA-256 of the token (never the token), the
  family (one per login or register), and issued, expires, used and
  revoked times, why it was revoked and what replaced it.
  - **Rotation:** every `POST /auth/refresh` marks the presented token
    used and returns a new one in the same family, so the answer now
    carries both tokens and the web app stores both. A rotated token keeps
    the family's expiry: a session still ends 7 days after login however
    active it is (absolute, as before).
  - **Reuse detection:** a used token presented again more than 30 s after
    its use (`REFRESH_REUSE_GRACE`, a design number), or a superseded one
    at any time, revokes every token of its family (`revoked_reason =
    'reuse'`) and is a 401; the revocation is committed before the 401 is
    raised, and a WARNING names the token, family and user ids, never the
    token.
  - **The grace window:** a used token presented again within 30 s, whose
    successor has never been used (a lost response, or two tabs at once),
    supersedes that successor and gets a new one. A thief replaying within
    the window gets a token, but the real client's next refresh then
    presents the superseded one, which is reuse: detection one rotation
    later.
  - **The invariant:** at most one live (unused, unrevoked) token per
    family, enforced by a partial unique index
    (`uq_refresh_token_live_per_family`) as well as by the code's order of
    writes. Every refresh and logout first takes a per-family advisory lock
    (`pg_advisory_xact_lock`), so a family's changes run one at a time. A
    row lock on the presented token alone would not serialise two
    different tokens of one family; with the family lock removed, two
    spent tokens presented together deadlocked (observed, the mutation
    check below).
  - **Logout:** `POST /auth/logout` with the refresh token in the body, no
    access token needed, revokes the family (`'logout'`) and answers 204
    whatever the token was. The web app's `logout()` clears storage, then
    calls it, bounded at 5 s (the order changed in item 39). Other tabs of
    the same browser share that storage, so they find out at their next
    request (no token, then the login screen), never through a refused
    refresh; the live check below showed exactly that. The server-side
    revocation is what ends a copy of the refresh token held anywhere
    else. A family is one login in one browser; another device has its
    own.
  - **Legacy tokens:** a refresh token issued before migration 021 has no
    `jti` and is refused: everyone signed in before the deploy logs in
    once.
  - **Cross-tab:** the web client refreshes under a Web Lock shared by every
    tab (`navigator.locks`, name `tenderos-refresh`) and re-reads the stored
    refresh token after taking it, so a tab that waited uses the token the
    other tab just stored instead of sending the spent one. A rejected
    refresh clears storage inside the lock, so a waiting tab never sends a
    token from a session that has ended (found by the two-tab vitest case).
    The refresh request is bounded at 10 s (`REFRESH_TIMEOUT_MS`, a design
    number, added in review): an abort counts as a network failure, so
    the session is kept, the lock released, and the request that needed
    the refresh gets its original 401.
  - **Cleanup:** the outbox dispatcher deletes expired rows in bounded
    batches on the idempotency cleanup's tick (about once a minute),
    failures logged and swallowed. One row per login plus one per refresh:
    about 33 a day for an active 8-hour session at 15-minute tokens (one
    login and 32 refreshes; estimate). The self-referencing foreign key's
    referencing side is indexed (`ix_refresh_tokens_replaced_by_id`, found
    in review): without it each deleted row's `ON DELETE SET NULL` scans
    the table. Measured on the test database, 50,000 expired rows in chains
    of 10, one batch of 500: 2,033.1 and 1,982.9 ms without the index, 8.8
    and 9.8 ms with it (two rounds, same seed, each in a rolled-back
    transaction).
- **Observed (live checks, 2026-10-24, fake provider, rebuilt stack,
  `JWT_ACCESS_MINUTES=1` for the api only through a scratch compose file,
  not committed; the api recreated without it afterwards):**
  - **Two tabs:** registered at 10:49:15.9 UTC, a second tab on Talent,
    75 s later both reloaded in the same instant. Four `GET /auth/me`
    401s (two per tab, StrictMode's second effect run in the dev
    server), then **one** `POST /auth/refresh` (200, from tab B) and
    tab A retried with the token B stored, sending no refresh of its own.
    No logout. The api log has one "rotated refresh token" line; the
    family has two rows, the first used and pointing at the second, the
    second live, nothing revoked.
  - **Logout:** `logout()` from the page (the app's own `client.ts`
    imported from the dev server; no screen offers it, item 39, so
    `AuthContext.logout()` was not exercised live): `POST /auth/logout`
    204, both tokens cleared, the refresh token held from before the
    logout replayed: 401. Both rows `revoked_reason = 'logout'`; one INFO
    line ("2 token(s)"). The second tab, reloaded, landed on `/login`,
    but because shared storage was empty, not through a refused refresh.
  - **Reuse:** a new account; its first refresh token held outside the
    page; the browser rotated at 10:55:13.9; the held token replayed 32 s
    later: 401, one WARNING with the token, family and user ids. After the
    browser's access token expired, its own next refresh (with the token
    that had been live) got 401 and it landed on `/login`, storage
    cleared. Both rows `revoked_reason = 'reuse'`.
  - **A harness bug first:** the first reuse attempt kept the held token
    in a page variable, which the reload erased, so the replay sent no
    token and got 422; nothing was tested and the family was left as
    after two rotations. Not counted; redone as above.
  - **E2E:** passed once on normal settings (2.7 min); one register
    (201), no refresh during the run; the run's family has one live row,
    lifetime 7 days and 82 ms (`issued_at` is the database's transaction
    start, `expires_at` the api host's time after the password hash).
    Login's request time could not be compared with earlier runs: the
    access log records no durations.
- **Accepted limits of option 3:**
  - **Access tokens outlive a logout.** Logout revokes the refresh family,
    not the access tokens already issued: one stays valid until its own
    `exp`, up to `JWT_ACCESS_MINUTES` = 15 (configured), wherever a copy of
    it is held. The same holds after a reuse revocation: revoking a family
    ends its refresh tokens, not the access tokens already issued from
    them. Closing that
    needs a denylist checked on every request, which brings back the Redis
    dependency decision 0041 kept out of authenticated requests; not
    planned. Shortening `JWT_ACCESS_MINUTES` narrows it at the cost of one
    refresh per expiry. Asserted in a test, so the limit stays visible.
  - **The refresh token is still in `localStorage`**, readable by any
    script on the page. It is now single-use: a stolen copy works until
    either the thief or the user uses the family next, and the other's
    next refresh is reuse, which ends the family for both. The `httpOnly`
    cookie (option 2) waits for item 27.
  - **A Log out that can't reach the API (plan item 39 built the button):**
    this browser signs out, but that family stays live until it expires.
  - **A tab closed during Log out (plan item 39):** storage is cleared first,
    and the call uses `keepalive`. Observed in Chromium only, with the tab
    closed while a proxy held the CORS preflight: the POST arrived and the
    family was revoked 10 of 10; the same request without `keepalive`, 0 of
    10. Not observed: Firefox, Safari, or the whole browser quitting. A
    browser that drops the call leaves the family live until it expires;
    this browser stays signed out either way.
  - **Without Web Locks** (read in code, corrected in review): two tabs
    whose access tokens expire together both send the same token T1. The
    API rotates it for the first (T2), then takes the grace path for the
    second (T2 superseded, T3 issued). The first answer usually arrives
    first and is stored, and the second is then dropped by the client's
    changed-storage check (`requestTokens`), so storage keeps the
    superseded T2. The next refresh is reuse: the user is logged out once,
    and the API logs a reuse WARNING. That is the usual outcome, not a
    rare one. Web Locks exist only in secure contexts (HTTPS; localhost
    counts), so a deployment that serves the web app over plain HTTP
    always takes this path; serving it over HTTPS is a deployment item.
  - **A hung refresh holds the lock for up to 10 s:** other tabs that need
    a refresh wait that long, then take the lock and refresh themselves.
  - **A lost response is treated as reuse after 30 s:** a refresh the API
    completed whose answer never reached the browser (the tab closed
    mid-refresh, the connection dropped), retried more than 30 s later,
    presents a spent token. The user is logged out once and the reuse
    WARNING is a false positive. 30 s is a design number; Okta's default
    grace period for rotated refresh tokens is the same (configurable 0
    to 60 s), which is context for the choice, not a measurement. With the
    fallback above, this makes the reuse WARNING a signal to look at, not
    a page.
  - **Clocks:** `expires_at` is the JWT's own `exp`, set on the api host;
    cleanup compares it with the database clock (decision 0030). A skew
    of s seconds deletes a row up to s seconds early, so a token in the
    last seconds of its 7-day family may get a 401 instead of rotating.
  - **`revoked_reason = 'user'` has no writer:** nothing deactivates a
    user today. The refresh path already refuses an inactive user's
    token; a deactivation path, when one exists, should also revoke the
    user's families (`user_id` is indexed for it).
  - **The family lock's key is `hashtext(family_id)`:** two families can
    share a key and then wait on each other briefly; harmless.
  - **The test that inserts a second live row by hand proves the database
    enforces the index**, not that the model declares it: the test
    database keeps objects an earlier `create_all()` made. The schema diff
    test is what fails when the model loses the index (observed).
  - **Each refresh now writes:** three statements and one row, under a
    lock, where it used to read only.
- **Behaviour before item 34 (read in code, not observed):** the web app never calls
  `POST /auth/refresh`. `getRefreshToken()`
  (`apps/web/src/api/client.ts:18`) has no caller, and the only code that
  reacts to a 401 is `AuthContext.refresh()`, which runs when the app
  mounts and clears the tokens (`apps/web/src/auth/AuthContext.tsx:33-35`).
  So every session ends when its access token expires,
  `JWT_ACCESS_MINUTES` = 15 (configured,
  `packages/tenderos_backend/src/tenderos_backend/core/config.py:20`),
  whether or not Redis is up. Mid-session the screens' requests start
  failing with 401 one by one, an SSE ticket mint gets 401 and the live
  stream stops with an error, and the next reload shows the login screen.
  A real-provider run lasts longer than that: 15.8 min for an attempt that
  reached red team and 45.1 min for the last complete live run
  (`docs/eval/b16-scorecard.md:91`, `:37`). Not observed: no fake E2E
  lasts 15 minutes (the longest recorded is 2.7 min).
- **Refresh tokens before option 3 (read in code):** plain HS256 JWTs
  (`user_id`, `token_type`, `iat`, `exp`; `core/security.py:27-46` at
  the time) with no server-side store, no `jti`, no rotation and no
  revocation. Both tokens sit in `localStorage`
  (`client.ts:11-34`), readable by any script on the page. Logout only
  clears `localStorage` (`AuthContext.tsx:40-43`): a refresh token copied
  before logout, or stolen, keeps minting access tokens for
  `JWT_REFRESH_DAYS` = 7 (configured, `core/config.py:21`). Rotating
  `JWT_SIGNING_KEY` is the only way to end one, and it ends every session.
- **Relation to batch 2b:** decision 0041's "signed-in users keep working
  during a Redis outage" holds for the life of the access token only;
  `/auth/refresh` itself needs no Redis and fails open
  (`tests/route/test_rate_limit_middleware.py::test_refresh_still_works_during_the_outage`),
  and since item 34 the web app uses it, so a signed-in user now keeps
  working through a Redis outage for the life of the refresh token's
  family. Option 3's rotation is in Postgres only; it adds no Redis call.
- **Risk:** before option 3, a leaked refresh token was a 7-day credential
  that logout could not end. Now a leaked one is single-use and ends its
  family at the next use by either side; what remains is the access-token
  window after logout (up to 15 minutes) and the `localStorage` exposure
  above.
- **Status:** the 15-minute session resolved for the web app (item 34,
  option 1); revocation, rotation and reuse detection resolved (option 3).
  Open: the refresh token in an `httpOnly` cookie once item 27 settles
  whether the web app and the API share an origin (option 2). The Log out
  control on screen is built (item 39).
- **Test:** `apps/web/src/api/sessionRefresh.test.tsx` (two parallel 401s
  make one refresh; a 401 after the refresh finished makes none; a 401
  after a successful refresh logs out once, no loop; a retried POST keeps
  its Idempotency-Key; an SSE mint is refreshed; a refresh that cannot
  reach the API keeps the session; a rate-limited (429) refresh keeps the
  session; a logout during a refresh stays logged
  out; login's own 401 is not refreshed; a rejected refresh lands on login
  and logging in returns to the same page), `useEventStream.test.ts` (a
  drop the browser would retry is reconnected with a fresh ticket),
  `tests/route/test_idempotency_after_auth.py` (all seven routes that
  take a key, found by walking the routers). Checked to fail with their
  mechanism removed: single-flight, the replaced-token check, the logout
  on a retried 401, the network-failure split, the 429 exclusion, the
  stream reconnect, the logout race (before its fix), and the route test
  with one route's dependencies swapped.
  **Option 3:** `tests/integration/test_refresh_tokens.py` (rotation and
  the successor link; the family expiry kept; a new family per login;
  reuse after the window revokes the family, committed; a used token
  whose successor was used is reuse inside the window; the grace re-issue
  supersedes the unused successor; a superseded token presented later is
  reuse; logout revokes the family, also from an older token, and is
  silent for garbage, unknown, legacy and access tokens; an access token
  still verifies after logout; a token without `jti` is refused; a hash
  mismatch and an inactive user are refused; two refreshes racing with one
  token, five times; six at once plus the live token raced against the
  first, three times, at most one live token every time; two spent tokens
  of one family at once, no deadlock; a second live row by hand refused by
  the index; cleanup only of expired rows, in batches of 2, across a
  linked chain), `tests/route/test_auth_routes.py` (refresh returns a new
  refresh token; reuse is a 401 and ends the family over HTTP; logout 204
  with an empty body, for any token and twice, with no access token; an
  access token works after logout; a token without `jti` 401),
  `tests/integration/test_outbox_dispatcher.py` (a failing refresh-token
  cleanup stops neither the loop nor the idempotency cleanup and reaper),
  `tests/unit/test_migration_heads.py` (one Alembic head),
  `apps/web/src/api/refreshRotation.test.tsx` (both tokens stored; no
  `navigator.locks` falls back; two tabs make one refresh under the lock;
  a tab that waited while the other's refresh was rejected lands on login
  with no second refresh; a hung refresh is aborted after 10 s (fake
  timers) and the waiting tab then refreshes, with the session kept;
  `logout()` calls the API before clearing, and
  clears on a network failure, an error status, a hang past the timeout
  and with no token stored; the auth context's `logout()` calls the API
  and ends the session). Checked to fail with their mechanism removed (16
  backend mutations, a planted second Alembic head, and 9 web mutations;
  each mutated file restored and compared by SHA-256; the refresh timeout
  added in review):
  the family lock (deadlock detected), both locks (the partial index's
  unique violation), the index in the model (schema diff), the grace rule,
  the supersede, the used-successor check, the commit before the 401, the
  superseded-is-reuse rule, the kept expiry, marking used, the hash check,
  logout's revoke, the rotated token in the response, cleanup's expiry
  filter, the dispatcher's catch and its call, a planted second Alembic
  head; the web lock, the re-read, storing the rotated token, clearing
  inside the lock (failed first, before the fix), the refresh timeout (the
  test times out without it), logout's order, its clear on failure, its
  timeout, and the context's call.

## [E2E spec edit, 2026-11-03, accepted, no fix now] Leaving a screen during a write hides its outcome; two screens navigate after their write

- **Found:** reviewing whether the web app itself can cut off a write the
  way the E2E did (read in code). It cannot: nothing aborts a request on
  an in-app route change (the only aborts are the refresh and logout
  timeouts), and the app never does a full-page navigation. Only the
  browser itself, by a reload, closing the tab or a typed URL, cancels a
  request in flight. Observed on the way: a request cut off before its
  body is read leaves no idempotency record and no run, so the next click
  starts cleanly.
- **Quirk 1:** a write whose screen you leave before it answers still
  completes on the server, but neither its result nor its error is shown:
  the screen that would have shown it is gone, and no other screen is
  told.
- **Quirk 2:** Create bid and Login call `navigate()` after their await. If
  the user has moved to another screen while the request was in flight,
  the answer pulls them to `/` (Create bid) or to their login destination
  (Login).
- **Status:** accepted, no fix now: neither loses or duplicates data.
- **Related, planned:** two tabs that click Start processing at once each
  get a run; nothing on the server stops it (plan item 43).
  **Resolved by plan item 43 (2026-11-03):** the second gets a 409
  naming the first, and its screen shows that run; see the item 43 entry
  below.

## [Item 43, 2026-11-03] At most one active workflow run per tender - RESOLVED

- **The gap:** `create_workflow_run` inserted a run whenever every
  document was parsed, and each click of Start processing sends a fresh
  Idempotency-Key, so two tabs, or two clicks from screens that had not
  seen each other's run, each started a run on one tender.
- **Active statuses:** `queued`, `running` and `waiting_for_human`
  (`ACTIVE_RUN_STATUSES`, the screen's `NON_TERMINAL_STATUSES`). A run
  waiting at a gate is active, and so is one with a retry scheduled: it
  stays `running` until the retry succeeds or the budget runs out (read
  in code). `completed`, `failed` and `cancelled` are terminal; nothing
  moves a run out of them on purpose (read in code; see the cancellation
  race below for the one way it can happen by accident).
- **Now:** a partial unique index,
  `uq_workflow_runs_one_active_per_tender` on `workflow_runs (tender_id)`
  over the active statuses, on the model and in migration 022. The
  service does no lookup first; the index is the one check, so two
  concurrent calls cannot both pass it. On the unique violation it rolls
  back, re-reads the active run and raises `WorkflowRunActiveError`; the
  route answers 409 `WORKFLOW_RUN_ACTIVE` with `error.details.active_run_id`.
  `details` is new in the error envelope and is sent only when set, so
  every other error's body is unchanged (a route test checks
  `DOCUMENTS_NOT_READY` has none). The screen, on that code, follows the
  named run as if this tab had started it; any other 409 keeps the
  documents message.
- **Observed (2026-11-03):** two concurrent starts under a barrier, 5 of
  5: one run, one refusal naming it, one `WorkflowRunCreated` event. A
  second active run inserted directly fails on the index. After a
  completed, failed or cancelled run, a new one starts. The route's 409
  body, exactly. The screen test shows the named run's tasks with no
  error. Mutations: the index removed, the race makes 2 runs and the
  second click gets 202; the service's mapping removed, the race test and
  the route test fail; the route's mapping removed, the route test fails;
  the screen's branch removed, its test fails. The dev database had 0
  tenders with two active runs before the migration (107 runs, one per
  tender).
- **Existing tests changed:** four tests created two active runs on one
  tender to test something else (cost per run, the waiver payload, a
  ticket scoped to one run, stream isolation between runs); each now
  finishes or cancels the first run, or uses a second tender, before the
  second run.
- **How the index meets the cancellation race:** see the note added to
  the "Cancellation-during-execution race, remaining half" bullet.
- **Narrowed by plan item 44 (2026-11-03):** the unique violation is
  mapped to `WorkflowRunActiveError` only when the driver names
  `uq_workflow_runs_one_active_per_tender`; any other `IntegrityError` is
  re-raised as it is. Before, a different violation on a tender that had
  an active run was reported as that run (observed:
  `test_a_different_violation_is_not_reported_as_an_active_run` failed
  with `WorkflowRunActiveError` before the check).
- **Tests:** `tests/integration/test_one_active_run_per_tender.py`,
  `tests/route/test_workflow_routes.py`,
  `apps/web/src/routes/UploadAndProcessingStatus.test.tsx`.

## [Item 44, 2026-11-03] One worker per queue; operations are failed only when lost or past a cap - RESOLVED

- **The gap:** one RQ worker served ingestion, workflow, projection,
  evaluation and maintenance, in that order, one job at a time. Every
  tender's workflow jobs waited behind each other, an upload's parse
  waited for a running workflow job (up to 1800 s), and the maintenance
  consumer waited behind both. Separately, the queued-operation reaper
  failed any operation queued 60 minutes, even one only waiting its turn
  behind other tenants' operations.
- **Topology now (observed in the fake stack, RQ's worker registry):**
  `workflow_worker` (workflow), `ingestion_worker` (ingestion),
  `maintenance_worker` (maintenance, projection, evaluation),
  `operations_worker` (operations), each one job at a time, as before;
  nothing new runs concurrently, so the single worker's accidental
  same-tender serialization of workflow jobs is kept (item 44's audit is
  still a prerequisite to replicas). The stack scripts now pass
  `--remove-orphans`: the old `worker` container would otherwise keep
  running and consume every queue (observed: it was still up after the
  rename until removed by hand).
- **Memory (observed, fake-provider E2E, `docker stats` every 2 s, 188
  samples):** idle: workflow_worker 84.0 MiB, ingestion_worker 83.7,
  maintenance_worker 84.6, operations_worker 462.8 (it preloads the
  operations module), outbox_dispatcher 498.5, api 504.2; busy peak:
  569.1, 504.7, 87.8, 466.1, 499.2 and 551.9 MiB. The one worker it
  replaced measured about 160 MiB idle and 606 to 613 MiB peak. A 2 s
  sample can miss a shorter peak.
- **The queued reaper:** an operation is failed as `DISPATCH_FAILED` when
  its `OperationRequested` event was dispatched and RQ no longer knows its
  job (no job hash, not in the operations queue, not in any registry:
  started, scheduled, deferred, finished, failed, canceled), or when it
  is still queued past `MAX_QUEUED_AGE`, 6 hours, whatever RQ says. A
  pending event (no job yet; the outbox may be retrying) is never taken
  for lost. A Redis error stops only the lost check for that tick, never
  the cap. The cap's reasoning is in `operation_service.py`: 12
  maximum-length operations waiting ahead, 16 times the outbox's longest
  retry span, a several-hour outage; and it bounds to 6 hours the one case
  only it catches, a job RQ holds that will never run (one that failed
  before claiming stays in the failed registry for a year).
- **The retry's timeout:** the scheduled `retry_workflow_run_job` got RQ's
  180 s default (observed: `job.timeout == 180`); it now gets the same
  1800 s as every workflow job, from one constant.
- **The step 11 timeline:** "last workflow event" now uses the outbox
  rows' creation times, exported after the run
  (`scripts/e2e_outbox_export.sql`, CI's `outbox.tsv`), not the worker's
  dispatch lines, which waited behind the resume job. Observed locally on
  the new topology: the last row 0.174 s before the refetch.
- **Tests:** `tests/integration/test_async_operations.py` (waiting behind
  another job: not failed; job gone: failed; past the cap: failed; each
  failing under its mutation, and the old 60-minute rule failing the
  first), `tests/integration/test_workflow_retry.py`,
  `tests/unit/test_e2e_step11_timeline.py`.
- **Changed in review (2026-11-03, `6e30050`): a failed or canceled job
  is lost too.** RQ keeps a failed job's hash for a year, so the check
  above counted it alive and its operation waited for the 6-hour cap,
  blocking that tender from requesting that kind again. Read in code
  first: operations are enqueued without RQ's Retry (`job_id`,
  `job_timeout`, `unique` only), and RQ files a job as failed only with no
  retries left (with retries it requeues or schedules it:
  `rq/worker/base.py` `handle_job_failure`, `rq/job.py` `retry`), so a job
  in the failed or canceled registry never runs again. Both are now
  checked first and mean lost; the cap stays as the last resort.
  Observed: a job filed as failed, and a canceled one, fail their
  operation on the next pass; a started job (RQ's own execution record)
  and a scheduled one are left alone; each case fails under its mutation.

## [Phase 4 item 30, 2026-10-23, blocking since 2026-11-03 after 5 of 5 passed] The golden-path E2E runs in CI on a fake-provider stack

- **Before:** the E2E ran only on the dev laptop, by hand, so no pull
  request met the E2E stop-gate in CI, Dependabot's included.
- **Now:** `ci.yml`'s `e2e` job builds the api, worker and web images once
  (BuildKit, GitHub Actions cache, one scope per Dockerfile), starts the
  stack from the three compose files plus `docker-compose.ci-e2e.yml`
  (which points the services at those images), checks migrate exited 0,
  the api is ready, the web app answers and all four app services run the
  fake provider, then runs `full-flow.spec.ts` unchanged. The `.env` is
  generated from `.env.example` for each run (`scripts/ci_e2e_env.py`): no
  secrets, `LLM_PROVIDER=fake`, every Gemini key blank. Every run saves the
  compose logs, writes its step 11 timeline to the job summary
  (`scripts/e2e_step11_timeline.py`), and uploads the logs, the timeline
  and the Playwright report.
- **Limits:**
  - **Blocking since 2026-11-03** (`phase4/e2e-required`): the job-level
    `continue-on-error: true` is removed, so a failing `e2e` fails the
    run. It was the only thing making the job non-blocking: no step had
    `continue-on-error`, and the job has no `if:` and no `needs:`. While
    it was there, a red `e2e` still left the run green (main's runs for
    #43, 40116721087, and #44, 40163720306, failed their e2e and
    concluded success). Main has no branch protection rule and no
    ruleset (checked 2026-11-03), so no check is required for a merge,
    this one included; requiring `e2e` (the job's name as GitHub shows
    it) is a repository setting.
  - **Earlier, non-blocking** until 5 consecutive runs passed with their
    step 11 timelines recorded. **Decided 2026-10-23:** a product failure
    counts against the run of passes (Dependabot #33's run failed on the
    upload-list race, the entry at the end of this file), so the count of
    5 consecutive passes started once that fix was merged; the count was
    restarted from the one-time spec edit's merge (Status below).
  - **Artifacts are not masked.** The logs are uploaded only if none of
    the run's generated values appears in them (`--scan`); the Playwright
    report (traces on failure) holds the run's own access tokens, signed
    by that run's throwaway key, so they open nothing after the run.
  - **Cost:** cold image builds were estimated at 10 to 15 min and warm at
    2 to 4 min (not measured on a runner yet); a lockfile change, every
    Dependabot uv PR, invalidates the dependency layer and pays close to
    the cold cost. The two Python images are about 2.5 GB each locally;
    the job prints `df -h` before and after the builds, since runner disk
    is the likeliest limit.
  - **Actions cache size, to check on the first runs:** the repository's
    GitHub Actions cache is capped at 10 GB, and the three image scopes
    (`e2e-api`, `e2e-worker`, `e2e-web`) are written with `mode=max`, so
    the torch and model layers of two images are cached; that can approach
    the cap and evict older entries, such as the test job's Hugging Face
    model cache. Check the Caches page under Actions after the first runs
    (each scope's size, and whether the `hf-...` entry survives). Fallback:
    `mode=min` (only the final image's layers), or fewer scopes (one shared
    scope for api and worker, whose dependency layers are the same).
    **Measured after the first run (2026-10-23,
    `gh api .../actions/cache/usage` and `gh cache list`):** 2,782,146,941
    bytes in 56 entries, 27.8% of the 10 GB cap. By key prefix: image
    layers (`buildkit-blob-`, 40 entries, shared by the three scopes and
    not attributable to one; the largest blob 345 MiB) 1.32 GiB; the model
    cache (`hf-`, one copy per ref, 6 entries of about 160 MB) 0.94 GiB;
    setup-uv (`setup-uv-`, 7 entries, one of 358 MB) 0.33 GiB; the three
    `index-e2e-<scope>` entries under 25 KB together. One cold run of all
    three scopes added about 1.3 GiB, far less than the images' size,
    since layers are stored compressed and once. Re-check after a
    lockfile-changing run, which writes new dependency layers beside the
    old ones until they are evicted.
  - **Runner disk (first run, 2026-10-23, run 39959278103):** the job ran
    out of disk (ENOSPC) at setup-node, after the three builds (api 2 m
    50 s, worker 3 m 5 s, web 41 s, cold) and a healthy stack start; "Save
    the stack's logs" then failed partway (`compose.log` stops at 21 KB).
    The runner could not write its own diagnostics, so the job's log never
    reached GitHub, and that run's `df -h` figures were lost with it. The
    fix: preinstalled toolchains this job never uses are removed first
    (.NET, Android, GHC, CodeQL) and the runner's preloaded images pruned;
    the host installs only what its four scripts import (python-docx,
    openpyxl, Pillow and their three dependencies, at uv.lock's versions;
    the fixtures' manifest is identical), not the whole workspace with
    torch; the builder's own copy of the layers is pruned once the images
    are loaded (they are already in the Actions cache); and every big
    stage ends with `df -h /`, so a failure names its stage. Observed on
    run 2 (run 39980533388, passed, warm cache): 14 GB free at the start,
    37 GB after freeing space, 29 GB at the lowest (after the Chromium
    install).
  - **Step 11** (the entry above): the runner's timing is a new variable
    in a race the spec cannot be changed to avoid.
- **Status:** merged (#37); run 1 ran out of disk before the E2E; runs
  39980533388, 39983007910, 39989695235 and 39991181703 passed;
  Dependabot #33's run (39983426118) failed on the upload-list race. Non-
  blocking until 5 consecutive passes, counted from the race's fix.
  **Restarted 2026-11-03:** main's runs for #43 and #44 failed on step 4's
  navigation race (the B13 entry's second instance). The one-time spec edit
  fixed it and step 11's check, so the tally restarts at 0 from the main
  run after the spec edit merges. The edited spec's blob, `0d6adac6`, is the
  new frozen reference: the spec is not edited again without the same kind
  of explicit, recorded decision. The E2E folder is typechecked from this
  edit on (`apps/web/tsconfig.e2e.json`, referenced from `tsconfig.json`,
  so CI's web job's `npx tsc -b` covers it). Local runs on the final tree:
  3 of 3 passed, 2.6, 3.1 and 3.1 min (2026-11-03, fake provider).
  **5 of 5 passed (2026-11-03), counted from the spec edit's merge:** main's
  five consecutive push runs, every job passing in each, each with its
  step 11 timeline recorded:

  | # | Run | Merge (main) | Started (UTC) | E2E | Approval to refetch |
  |---|---|---|---|---|---|
  | 1 | 41617547377 | #48, `ebaeff3` | 2026-11-03 10:14 | 4.1 min | 10.044 s |
  | 2 | 41626122200 | #50, `9eb9463` | 2026-11-03 11:16 | 4.6 min | 12.012 s |
  | 3 | 41632548214 | #51, `406899b` | 2026-11-03 12:03 | 3.2 min | 7.960 s |
  | 4 | 41643808156 | #52, `2a22cee` | 2026-11-03 13:22 | 3.3 min | 10.974 s |
  | 5 | 41675559313 | #53, `5cd508d` | 2026-11-03 16:34 | 2.8 min | 7.997 s |

  0 stream reopens between the approval and the refetch in all five. Run
  5, the first on one worker per queue (plan item 44), read the last
  workflow event from the outbox rows: written 0.716 s before the
  refetch. Runs 1 to 4 read it by dispatch time on the single worker:
  9.207 s (run 1) and 7.808 s (run 3) before the refetch, and none found
  in runs 2 and 4. **The job is blocking from then** (`continue-on-error`
  removed, above).
- **Test:** `tests/unit/test_ci_e2e_env.py` (every credential generated,
  the DSN's password matches, an existing `.env` is never overwritten, a
  new placeholder credential is refused, no value printed outside GitHub
  Actions, and under it every value only on its `::add-mask::` line, the
  log scan; the file passes with `GITHUB_ACTIONS` unset and set to
  `true`),
  `tests/unit/test_e2e_step11_timeline.py` (the batch 2b run's figures, a
  job that ended before the click, fetches on either side of the click,
  the last workflow event before the fetch);
  the job itself on the pull request.

## [deps/pip-audit-2026-10-24, RESOLVED on the branch] pip-audit: filelock and setuptools advisories published overnight; no Dependabot PR

- **Found:** CI's security job failed on PR #39 (run 40059074368) with no
  code change: pip-audit reported filelock 3.32.0 (CVE-2026-112940,
  CVE-2026-112913, CVE-2026-112907, fixed in 4.2.1) and setuptools 83.0.0
  (GHSA-xmj3-9gf7-h52w, fixed in 84.1.0). Main's last run (16:58 UTC on
  2026-10-23) had passed; the advisories were published in GitHub's
  database between 21:44 and 22:28 UTC that day, all reviewed. Reproduced
  locally on main with `uv run pip-audit`.
- **Why no Dependabot security PR (checked, 2026-10-24 about 03:45 UTC):**
  not a cap and not a missing advisory. The advisories are in GitHub's
  database, the dependency graph lists filelock 3.32.0 and setuptools
  83.0.0 from `uv.lock`, and Dependabot alerts are enabled, but no alert
  had been raised for either about five hours after publication (the
  repository's only Python alert is Thursday's Mako one). A security PR
  starts from an alert, so no uv security job ran; the last uv job ran at
  10:27 UTC on 2026-10-23, before the advisories. Inference: GitHub's
  alert generation had not reached this repository yet. Version updates
  would not have helped either: both are transitive, and Dependabot's uv
  version updates bump direct dependencies.
- **Fixed (uv.lock only):** `uv lock --upgrade-package filelock
  --upgrade-package setuptools`: filelock 3.32.0 -> 4.2.4, setuptools
  83.0.0 -> 84.2.1 (latest, released 2026-10-19 and 2026-10-20; the
  minimum fixes are 4.2.1 and 84.1.0). Nothing else moved; nothing caps
  either below its fix (virtualenv allows `filelock<5`, torch
  `setuptools>=77.0.3`).
- **Who uses them:** our code imports neither. filelock reaches the
  runtime images through huggingface_hub and torch (sentence-transformers'
  model cache), plus dev tools (virtualenv, python-discovery, pip-audit's
  cache); setuptools only through torch, which imports it in
  `cpp_extension` and the dynamo backend registry, neither on our path.
  All four app images carry the new versions (checked in each container's
  `/app/.venv`). huggingface_hub uses only `FileLock(path, timeout=...,
  mode=0o664)` and `SoftFileLock`; filelock 4's breaking changes are in
  `ReadWriteLock`, `SoftReadWriteLock`, singletons and timeout types, and
  4.0.7's owner-mode check accepts 0o664. setuptools 84's breaking changes
  are in compiler internals and `archive_util`, used only when building.
- **Proof (2026-10-24):** pip-audit "No known vulnerabilities found"; the
  torch OSV check 0 advisories; backend suite 821 passed, 5 skipped (only
  the Starlette warning; no filelock `FutureWarning`); ruff check and mypy
  clean; the fake stack rebuilt; the E2E passed once (2.5 min), with no
  filelock warning in any service's log.
- **Status:** resolved on the branch. Advisory databases change daily, so
  the security job can fail again with no code change.

## [deps/pip-audit-2026-10-24, RESOLVED on the branch] npm audit: undici 7.0.0 to 7.30.0 (high) in apps/web, through jsdom

- **Found:** CI's security job on PR #40 failed a second way, at `npm
  audit --audit-level=high` in `apps/web`: undici 7.30.0, high, three
  advisories (GHSA-4qv8-2p9j-2685, EventSource parser memory;
  GHSA-f72v-6w45-q472, Subresource Integrity bypass; GHSA-xjj6-f33q-929c,
  excessive content-encodings). `apps/landing`: 0 vulnerabilities.
  Reproduced locally.
- **What pulls it in:** only jsdom 28.1.0 (`undici ^7.21.0`), which vitest
  loads as the test environment; `dev: true` in the lockfile.
- **Does it reach the stack:** installed, not loaded (read in code, not
  observed at runtime). The web image runs `npm ci`, which installs dev
  dependencies (Vite is one), so undici is in the image's `node_modules`.
  Nothing the dev server runs imports it: `vite.config.ts` imports
  `vitest/config`, whose two chunks import only `node:os` and `std-env`;
  `environment: "jsdom"` there is a string vitest reads when it runs tests;
  no app source imports jsdom or undici, so nothing is served to the
  browser. Item 27's static build will drop dev dependencies from what
  runs entirely.
- **Fixed (apps/web/package-lock.json only):** `npm audit fix`, no
  `--force`: undici 7.30.0 -> 7.30.1 (released 2026-10-15, inside jsdom's
  range); nothing else moved. npm 10.9.9 on this Windows host also dropped
  the `libc` fields of six optional native packages (all six rolldown
  bindings, `@rolldown/binding-linux-*`), which npm uses to pick glibc or musl binaries on
  Linux; that was the host rewriting the file, not the fix, so the commit
  keeps the original lockfile with only undici's three lines changed. `npm
  ci` installs from it without rewriting it.
- **Proof (2026-10-24):** `npm audit` 0 vulnerabilities in both apps;
  vitest 360 passed; eslint and `tsc -b` clean; both production builds
  pass; the fake stack rebuilt (the web image carries undici 7.30.1); the
  E2E passed once (2.7 min).
- **Status:** resolved on the branch.

## [found 2026-10-23 in CI run 39983426118, RESOLVED on phase4/upload-list-race] A list read that answered after a change undid the change on screen

- **Observed (CI, Dependabot #33, fake provider):** the E2E failed at
  `full-flow.spec.ts:540`: after the first tender-document upload the
  documents table stayed at 0 rows for 30 s. The api log: `GET
  .../documents` 200, the upload's `POST` 202, a second `GET` 200 0.5 ms
  later, then nothing; no error in any service's log. Passing runs show
  the same GET, POST, GET order, 2.5 ms apart.
- **Cause (read in code, then reproduced in vitest):**
  `UploadAndProcessingStatus.tsx` read the documents on mount and replaced
  the list with the answer, and appended an upload's row when the upload
  returned. The file input works before the first read has answered, so
  the read (sent before the upload, answered with the list as it was) can
  be handled after the append and replace the list with its empty picture.
  Nothing read the list again. A vitest case that holds the first read
  until the upload has landed fails on main with 0 rows. The CI timing
  fits this; that it is what happened in CI is an inference.
- **Same shape elsewhere (searched: every screen that updates rows in
  place and also replaces its whole list from a read):**
  - `CommercialFindings.tsx`, `RedteamFindings.tsx`: the re-read after an
    operation finishes, against Resolve (the Resolve buttons are not
    disabled during that read);
  - `ProposalWorkspace.tsx`: the sections re-read after a generation,
    against Save edits;
  - `RequirementRegister.tsx`: Re-verify's whole-list re-read, against
    Confirm and Reject on another row;
  - `TalentLibrary.tsx`: the mount read and the re-read after a capability
    statement upload, against a status change.
  Not affected: `ComplianceMatrix.tsx` (reads once on mount; its rows
  exist only after that read), `TeamWorkspace.tsx` (keeps only the latest
  role's matches, and its table is empty while they load),
  `GraphExplorer.tsx` (appends to a history, replaces no list).
- **Fix (all six screens, one rule, `apps/web/src/hooks/useListReads.ts`):**
  one clock per screen orders reads and changes. A read older than one
  already applied is dropped. Otherwise its rows replace the list by id,
  except rows changed after the read started, which keep their local
  version and are kept even when the read lacks them (a fresh upload).
  Merging instead of dropping the stale read keeps what else it carries
  (the new findings after a re-validation). The documents screen also
  reads the list once more after each upload, so it ends on the server's
  state. Not reproduced live: the window is milliseconds wide.
- **Status:** resolved on `phase4/upload-list-race`; the E2E passed once
  locally (2.6 min), every upload followed by its re-read in the api log.
  CI's 5 consecutive passes are counted from this fix's merge (the item 30
  entry).
- **Test:** `src/hooks/useListReads.test.ts` (the merge cases, an older
  read dropped, safe to run twice); one race test per screen in that
  screen's own test file (the read held, the change landed, the stale
  answer plus a new row released). Every race test fails with the merge
  replaced by a plain replace, the documents test also on main's screen,
  and the hook's drop test fails without the drop.

## [Phase 4 item 31] Neo4j driver 6: what moved and what stays open

- **Status:** upgraded from 5.28.4 to 6.4.1 (decision 0071). Server stays
  5.26.31, Bolt 5.8. Same projection, same impact previews and contradictions
  on one rich tender: a dump of all of them is byte-identical on both versions.
  That tender is `14cd7584`, real-provider data from 2026-09-01, not fake as
  this entry first said. Reprojecting calls no LLM.
- **Supersedes in `[B12] Neo4j driver has no explicit request timeout`:** the
  worst case before a Neo4j-touching request fails is now 10 s, not about 15 s,
  because `connection_acquisition_timeout` now spans everything needed to get a
  connection.
- **Fixed by the upgrade:** with Neo4j stopped, `GET .../graph/trace` answered
  `400 "Cannot resolve address neo4j:7687"`: driver 5 raised `ValueError` for a
  name that did not resolve, and the route's `except ValueError` is for a bad
  label. It now answers the catch-all's 500, with the hostname kept private.
- **Fixed alongside it:** nothing called `close_driver()`, so the API's shared
  driver was never closed. The API now closes it on shutdown. An unclosed Neo4j
  driver or session collected during a test now fails that test.
- **New defaults, kept (read in the driver's source, not observed firing):**
  - `session.run` retries once, immediately, only when the server marks the
    error idempotent and the statement failed before running. The driver's
    PR says the set is admission-control errors only, guaranteed not to have
    changed any database. Nothing in our logs shows one has fired.
  - `connection_write_timeout` defaults to 30 s. A write stuck that long now
    fails instead of waiting indefinitely.
- **Accepted, open:**
  - Forked job processes and scripts still do not close the shared driver.
    RQ's work horse leaves through `os._exit`, so no finalizer runs, and closing
    it after each job would break an in-process worker's next job, since a
    closed driver cannot be reused. A script warns at exit, hidden by default,
    and its SQLAlchemy engine does exactly the same. The OS closes the sockets
    when the process ends.
  - The impact route answered 500 in a Neo4j outage on both versions, and the
    trace route now does too. Decided 2026-11-05: it stays a 500, and the
    observability item alerts on readiness instead.
  - `ensure_constraints()` had no callers, and the stack's Neo4j had 0
    constraints. **Corrected and resolved (plan item 49):** this first said
    duplicates had not been observed. They have, and worse; see the entry on Neo4j's uniqueness constraints.
  - A test entering `with TestClient(app)` closed the shared driver for every
    later test, unless it patched `close_driver`. **Resolved (plan item 49):**
    `close_driver()` now clears the driver and the next session builds a fresh one.

## [Phase 4 plan item 54, 2026-11-10, environment limit; the SSE stream no longer depends on it] On the Windows dev host, Postgres's `now()` steps back about 1 s every ~28 s between transactions

- **Seen (observed, Docker Desktop on Windows, 2026-11-10):** sequential,
  committed transactions 250 ms apart on the test suite's own connection
  (`127.0.0.1:5433`, the same server as `docker exec`): `now()` went
  backwards six times in about 100 s. Each event was about 1 s: in one
  step (-944 ms, -986 ms) or in two consecutive readings (-227 then -485 ms).
  Events came every 110 or so transactions, about every 28 s. Seen from the
  host, Postgres's clock runs about 4.3% fast and then drops back about
  1.2 s, so its gap with the host swings between about -810 and +480 ms.
  The host's own wall clock stayed exactly on its monotonic counter.
- **Not seen inside one statement:** a single long PL/pgSQL loop, or one
  long SELECT, reading `clock_timestamp()` every 10 ms saw no backward step
  and no stall: 10 minutes idle, 12 minutes during a full suite, and 52 s
  through each connection path. Why only separate statements see the steps
  is not known.
- **Likely source (read, not proven):** the VM's kernel runs Hyper-V's time
  sync with `hv_utils.timesync_implicit=1`, which lets the host set the
  guest's clock. Related to plan item 45's entry above, where the same VM's
  monotonic clock ran about 2% fast.
- **What it broke, now fixed (plan item 54):**
  - The SSE stream (`apps/api/src/tenderos_api/outbox_stream.py`) asked
    only for events stamped after the last one it sent. An event written in
    the second after a step, following one sent in the second before it,
    was never sent. The same cursor also skipped an event whose transaction
    began before a later-stamped one but committed after it, and one whose
    transaction spanned the stream's opening. The stream now tracks the ids
    it has sent and depends on neither clock order nor commit order.
  - The reaper test's intermittent failure, events read as `running,
    failed, queued` (2 of 4 full suites): `test_async_operations.py`'s
    `_statuses_announced` ordered by `created_at`. It now orders by the
    inserting transaction's id (`xmin`), a test-only ordering.
- **Still shown out of order here, nothing skipped or decided (read):**
  `OutboxRepository.list_failed` (the operator's `--list`),
  `WorkflowRepository.list_waiting_runs_with_a_decided_gate` (batch order),
  and `scripts/e2e_step11_timeline.py` (a diagnostic report). Comparisons
  of a stamp with `now()` (leases, retry delays, the reaper's caps) can be
  off by up to about 1.2 s, small against their minutes.
- **Production:** a server whose clock is slewed (NTP) rather than stepped
  should not do this. Not measured.
- **The stream's poll reads the whole outbox table**, before item 54 and
  after it: no index covers the payload key. Measured on the dev data's
  6,639 rows (largest run 34 events): 1.2 to 1.3 ms for the old cursor
  query, 1.7 ms for the new one. It grows with the table, and nothing
  deletes outbox rows (read). An index on the payload key, or pruning
  dispatched rows, would be the fix when it matters.
