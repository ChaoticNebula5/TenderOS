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
- **Status:** unverified. Flagged from the B5 investigation, not confirmed
  as a real defect against divergent (non-identical) content, not fixed.
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
- **Scheduled verification:** B7, once evidence-linking exists. This test
  case is currently an artifact with no consumer - nothing in B0-B17 as
  built so far actually runs it, so left alone it would drift all the way
  to B16's release gate still marked unverified without ever being
  checked. B7 is where a silently-dropped divergent restatement would
  actually cause harm: a compliance decision built on the wrong version of
  a requirement, with no trace that a different version was ever dropped.

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

## [B6] knowledge_import_batches.status never advances past 'queued'

- **Discovered:** B6's own Docker smoke test, run end to end against the
  live API/worker/outbox_dispatcher stack. Every upload's batch showed
  `status: "queued"` via `GET /knowledge/import-batches/{id}` even after
  `process_knowledge_document_import` had fully completed and every item
  in the batch showed `approved`/`needs_review`.
- **Behaviour:** the worker job (`apps/worker/jobs/knowledge_import.py`)
  updates individual `knowledge_import_items` rows as it processes each
  row/extraction, but never writes back to the parent
  `knowledge_import_batches.status` field itself - there is no code path
  that ever sets it to anything but its `'queued'` default.
- **Risk:** low, and not a correctness problem - a reviewer polling
  `GET .../import-batches/{id}/items` (which this session's smoke test
  and the actual review flow both use) sees accurate, real-time item
  status regardless. The batch-level status field is simply dead/
  unreliable if anything ever reads it expecting a `completed` value -
  a future UI built against the batch summary endpoint rather than the
  items list would show a permanently-misleading "queued" badge.
- **Status:** unverified / accepted. Not fixed this block - noted rather
  than patched under time pressure at the end of B6, since the fix
  (update batch.status + total_items/completed_items/failed_items as
  process_knowledge_document_import runs) is mechanical but touches a
  file already carrying a lot of new logic this block.
- **Test:** none dedicated. Observed directly via the live smoke test's
  batch responses; no automated assertion exists for batch.status
  specifically.
