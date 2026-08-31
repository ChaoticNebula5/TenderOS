# Citation Correctness — Investigation Resolution

**Status: root cause found and fixed at the retrieval layer in B16 (`014_knowledge_item_chunks.py`) — downstream effect on grounded-claim rate is unmeasured. This is the project's longest-open question — first raised in B9, still open through B12 and B14, its cause closed in B16.**

**What the fix closes, stated precisely so 0→10 doesn't get misread as a grounding-rate number:** it closes the retrieval-to-knowledge-item join for résumé-imported employees - proven 0→10 candidates for REQ-010 on the preserved investigation dump, before vs. after the migration, in every org that has the résumé fixture loaded (`docs/eval/citation-correctness-fix-options.md` has the full verification). **It does not yet demonstrate a single end-to-end grounded claim.** REQ-010 now surfaces real candidates from `search_company_knowledge` - that is one stage earlier than where this investigation's own traced chain (§3 below) showed the break. Nothing in the fix's verification exercised compliance evaluation proposing against those candidates, evidence materializing, or drafting citing it - the three later stages this document's own §3 traced as depending on exactly this join. Until a real live run does that, the honest status is **"root cause fixed and verified at the retrieval layer; downstream effect on the 0% grounded-claims number is unmeasured."** Not "citation correctness is fixed," and not a predicted percentage - genuinely unmeasured until that run happens.

*This document is left as originally written below — the investigation, not the fix. It correctly ends at "not fixed"; the two paragraphs above are the accurate, precise update to that, not a claim that the original 0% finding has since improved to some other number.*

This document is self-contained. It does not require re-reading KNOWN_LIMITATIONS.md's own four-session history of this question to follow, though every claim below traces back to a real entry there or to real data produced this session.

---

## The headline finding, stated once before the detail

The pipeline grounds **0% of factual proposal claims** on this fixture, and the cause is now known and located precisely: **`knowledge_items.source_chunk_id` is never set when an employee record is created from a résumé, so retrieval's own chunk→knowledge_item mapping throws away every real match before it can become a citable candidate.** Retrieval itself - lexical, vector, fusion - works correctly; a live check against a requirement this fixture's knowledge base genuinely covers found vector search returning the exact right match. The break is one step later, in an unset foreign key. Drafting was never given evidence to cite because compliance never had evidence to attach, because retrieval's own real results never survived the trip back to a knowledge item. This is a materially different, and far more specific, place than four blocks of investigation had assumed the problem lived.

**This does not fix anything.** The pipeline still produces 0% grounded claims after this investigation, exactly as it did before it. What changed is that the next block now knows where to look.

---

## 1. The question, as B9 first posed it

B9's own live measurement (KNOWN_LIMITATIONS.md, `:1219`, "[B9] Drafting mostly produces ungrounded text") found 22 drafted claims - 3 narrative (`not_required`), 18 `unsupported`, 1 `grounded` - and asked, plainly, without being able to answer it from code alone:

> "was the underlying evidence thin, or did the model have it and not cite it?"

Two hypotheses, both live from B9 onward:

- **(a) Evidence was thin** - the org's own knowledge base genuinely had little content relevant to what drafting needed to cite, so an empty citation list was the *correct*, honest output.
- **(b) Evidence was available and uncited** - real, citable evidence existed, and the model (or the prompt) failed to cite it - a genuine model/prompt defect.

Nothing in this project's code could distinguish these two from the outside. The thing that would settle it - what the drafting model was actually shown, per section, per run - was never recorded anywhere. B12 re-ran the measurement (2/19 grounded, `:1219`'s own B12 update) after fixing the two most plausible candidate causes it could name (`target_updates` batch-approval never being wired; talent-evidence never receiving a `validity_status`). Neither fix moved the number. B14 (`:4811`, "[joins the B9/B12 cluster]") found the same shape one layer earlier - two independent live runs showing ~0 of 94-98 compliance entries ever reaching an LLM-affirmed status, with `compliance_evidence` at 0 rows both times - and could not determine why either.

Four blocks. The question stood open the entire time: **not "evidence was thin" confirmed, not "the model declined to cite" confirmed - genuinely unknown, because nothing had ever recorded what was actually offered.**

---

## 2. What this session found - two hypotheses eliminated, and a third, distinct outcome

### 2a. The instrument that finally answered it

`AgentTrace.input_refs`/`output_refs` (`db/models/agent_trace.py`) are JSONB columns that have existed since early in this project, declared for exactly this purpose - and never once populated by any real LLM-call trace writer, anywhere in the codebase, until this session. This was itself a new finding: the exact "column exists, no writer ever populates it" shape this project has hit repeatedly elsewhere (`knowledge_import_batches`' progress fields, `tenders.state_version`), just never previously noticed on this specific column.

This session populated both, at the two call sites that matter for this question:

- **`agents/compliance/propose.py`** - `_candidate_input_refs` records the candidate set as *offered* (count, label, knowledge_item_id, source_kind, validity_status - no excerpt text, deliberately, matching B15's own document-content-out-of-logs discipline) and `_proposal_output_refs` records what the model *cited*.
- **`agents/proposal/draft_section.py`** - the same shape for drafting: `_fact_input_refs` (fact count, label, kind) and `_draft_output_refs` (claim/factual-claim counts, cited evidence/requirement/employee labels, deduped per section).

Both are wiring-only, unit-tested with no real LLM cost, verified live before being trusted (a direct fixture check confirmed no excerpt/document content leaks into either record - see KNOWN_LIMITATIONS.md's own logging entry for why that specific care matters here).

### 2b. Hypothesis (b) - eliminated, completely

Drafting ran **15 real calls across 3 full revision passes** in the live measurement run. `cited_evidence` (evidence-kind labels actually cited in a claim) is **0 in every single one of the 15.** The model did not have evidence available and decline to cite it. There was categorically nothing evidence-shaped in what it was ever shown.

### 2c. The third outcome - not "evidence was thin" either, stated as its own finding

`fact_count` (the total facts offered per drafting call) was **not** zero - it ranged up to 29 per call, and `cited_requirement` (requirement-kind citations) ran as high as 29 in the same calls. Facts were offered, real ones, on every call. They were never evidence.

This is neither hypothesis (a) as usually read ("there wasn't much to cite") nor hypothesis (b) ("something citable was skipped"). It is a third, distinct outcome: **drafting was offered real content on every call, exclusively on a dimension - requirement text - that cannot ground a factual claim, ever, regardless of volume.** Part C's own proposal-claim invariant requires a verified evidence link; a requirement reference alone does not satisfy it no matter how many are offered. `_facts_from_compliance` (`domain/services/proposal_service.py`) appends a `requirement`-kind fact unconditionally for every approved entry - that does not depend on evidence existing at all - and only ever appends `evidence`-kind facts when `list_evidence_for_compliance_entry` returns something. It never did.

---

## 3. The full traced chain, with the instrument data behind every link

```
search_company_knowledge (compliance_service.py)
    → 87/87 real requirements: 0 candidates returned, every time
        (agent_traces.agent_name='compliance_proposal', input_refs.candidate_count,
         GROUP BY candidate_count: {0: 87} — not a distribution, one value)

propose_compliance
    → nothing to cite, 87/87 calls
        (output_refs.cited_labels = [] in every row; output_refs.status =
         "evidence_unavailable" — the model's own reasoning_summary, sampled
         directly from audit_events.before on the 192 real override actions
         this fixture's own two live runs produced: "No candidate evidence
         was retrieved regarding...")

materialize_evidence / persist_compliance_decision (compliance_service.py)
    → compliance_evidence: 0 rows (both this session's investigation and
      B14's own two prior live runs) — not a bug in this write path, which
      is correctly wired (ON CONFLICT DO NOTHING, called unconditionally
      from persist_compliance_decision) — it never receives anything to write

_facts_from_compliance (proposal_service.py)
    → requirement-kind facts only, unconditionally, for every approved entry
      (real: up to 29 per drafting call) — evidence-kind facts: never,
      because list_evidence_for_compliance_entry has nothing to return

draft_section
    → 15/15 calls, 3 full revision passes: cited_evidence = 0, always
        (agent_traces.agent_name='proposal_drafting', output_refs.cited_evidence_labels)

proposal_claims (final persisted state)
    → 0/27 factual claims grounded (0%), 27 unsupported, 6 not_required (narrative)
        (SELECT is_factual, grounding_status, count(*) FROM proposal_claims ...)
```

Every arrow above is backed by a real query against this session's own preserved live-run database (`backups/b16_citation_run_20260830T134032Z.dump`), not inferred from code reading.

---

## 4. Predictions vs. actuals

Written down before the run, per this project's own pre-flight discipline (matching B13's precedent):

| | Predicted | Actual |
|---|---|---|
| Compliance-stage `input_refs` | Mostly empty candidate sets (hypothesis a favored) - stated as a strong prior from historical `input_tokens` near-constancy, not a certainty | **87/87 calls, `candidate_count = 0` - absolute** |
| Drafting-stage `input_refs` | Non-empty, requirement-heavy, "almost no" evidence-kind facts | **15/15 calls, 3 passes: `cited_evidence = 0` in every one - not "almost none," none** |
| Citation correctness | 1/20-3/20 grounded (5-15%), reasoned from B9 (1/19, 5.26%) and B12 (2/19, 10.5%) continuing at a similar order of magnitude | **0/27 grounded (0%) - missed, below the predicted range** |

**Why the miss matters, not just that it happened:** B9 and B12 each found a small, nonzero trickle of grounded claims (1 and 2 respectively) - consistent with a *degraded* path that mostly failed but occasionally worked. This run found *no* path at all: retrieval didn't return thin results, it returned nothing, on every single call. The prediction assumed a leaky pipe; the actual finding is a disconnected one. That's a different failure shape, not just a lower number on the same one - worth naming precisely rather than reading "0% vs. a predicted 5-15%" as a small miss on the same story.

---

## 5. Resolved: neither retrieval-is-broken nor fixture-coverage, as originally framed - a specific, located, unfixed defect

The question this section originally left open - is `search_company_knowledge` broken, or does the fixture's own knowledge base lack relevant content - is answered, and the answer is neither of the two framings on offer. **Retrieval works. A specific missing foreign-key write, one step past retrieval, throws the result away before it can ever become a candidate.**

### 5a. The test case, chosen before running anything

**REQ-010**: *"Where possible, the proposed team should include a certified project manager."* Chosen because this org's own knowledge base holds exactly 4 knowledge items, all `item_type='employee'`, one of them titled, literally, "Project Manager" (Priya Ostrowski) - the single most obviously-answerable pairing this fixture can offer. A miss here would be very hard to explain as "the fixture lacks relevant content."

### 5b. Stage by stage, each checked directly against real data, not inferred

| Stage | Result |
|---|---|
| Lexical search | 0 hits - expected; no literal word overlap between the requirement text and the certification record |
| Vector search | **10 hits, including a direct, correct match**: chunk `dcb94707-...`, score 0.554, text *"PMP (Project Management Institute, expires 2027-02-15)\nPRINCE2 Practitioner..."* - Priya Ostrowski's own certifications chunk. Vector search found the exact thing the requirement asks for. |
| RRF fusion | The PMP chunk confirmed present in the ranked, fused chunk-id list |
| Chunk → knowledge_item mapping (`get_knowledge_items_by_chunk_id`) | **0 knowledge_items, for every one of the 10 ranked chunks, including the PMP chunk** - this is where the candidate list goes to zero |
| Rerank | Never runs - nothing left to rerank once the candidate list is already empty |

### 5c. Root cause, located precisely

`get_knowledge_items_by_chunk_id` (`db/repositories/knowledge.py`) is a plain, correct lookup: `WHERE knowledge_items.source_chunk_id == chunk_id`. Checked the data directly: **`knowledge_items.source_chunk_id` is `NULL` for all 79 employee-type knowledge items in this live database** (every org, not just this one). `document_chunks` genuinely holds the real, correctly-parsed, correctly-headed content (`heading_path: ["Priya Ostrowski", "Certifications"]`, real PMP/PRINCE2 text). The chunks exist. Vector search finds them. The one thing missing is the FK that would let a matched chunk resolve back to the employee it belongs to - and it is never set when an employee record is created.

**Scope, checked rather than assumed - this is one import path, not the whole knowledge-item creation surface:**

- `db/repositories/knowledge.py`'s `create_certification`/`create_past_project`/`create_capability` **all** correctly declare a `source_chunk_id` parameter and pass it into the `KnowledgeItem` they construct. Their real callers thread a real value through - `import_certification_row` even builds a synthetic chunk deliberately (`_create_synthetic_chunk`) for exactly this purpose; `import_past_project`/`import_capability` pass the extraction result's own `chunk_id` directly. This is proven working, end to end, not just present in a function signature.
- `create_employee`, the sibling sitting directly above these three in the same file, under a comment literally reading *"Certifications / past projects / capabilities / company documents (each a knowledge_items row + one child row, **same shape as employees**)"* - **has no `source_chunk_id` parameter at all.** Only `source_document_id` (whole-document, not chunk-level).
- The gap isn't only at the repository layer. Both real callers of `create_employee` - `import_employee_master_row` (the CSV/XLSX path) and `import_resume` (the résumé path) - never have chunk-level granularity in scope to begin with. `import_resume` receives `resume_document_id` (whole-document) and the LLM's own parsed `profile` object; it never receives or threads through a chunk reference, even though the underlying résumé was genuinely chunked (the same `document_chunks` rows this investigation's own vector search just matched against).
- `create_company_document` also lacks the parameter - structurally different, not the same drift: a company document's own knowledge item is document-level by design (`doc_subtype`, no natural chunk granularity), not an omission relative to a sibling that has one.

**This is the sixth confirmed instance of this project's own "column exists, nothing populates it" pattern** (KNOWN_LIMITATIONS.md's own standing-pattern entry family: `knowledge_import_batches`' progress fields, `tenders.state_version`, `evaluate_evidence_coverage`'s `valid_until`, and others) - one import path missing what its own siblings, sitting in the same file, already do correctly and provably.

**A separate, already-documented design fact worth naming precisely so it isn't confused with this bug:** résumé-extracted certification/project mentions never become separate `certification`/`past_project` knowledge_items at all - `import_resume`'s own docstring states this outright: they "ride along inside `parsed_profile` as claims for a reviewer to see." This is why this live database has zero certification/past_project-type knowledge_items anywhere to cross-check against directly - not because those creation paths are broken (they're proven correct above), but because résumé import was deliberately designed to never call them. A genuine limit on how far this session's own live-data comparison can reach, stated plainly rather than glossed over.

**Not fixed.** Per instruction, this stops at the finding - scoped, located, and evidenced - not at a patch.

---

## 6. Historical numbers this document supersedes

| Block | Measurement | Sample | Note |
|---|---|---|---|
| B9 | 1/19 grounded (5.26%) | 22 claims, real live run | First observation; cause unknown at the time |
| B12 | 2/19 grounded (10.5%) | 19 claims, real live run, two candidate fixes applied | Neither fix moved the number; cause still unknown |
| B14 | ~0/94-98 compliance entries LLM-affirmed | Two independent live runs | Found the gap one layer earlier; cause still unknown |
| **B16 (this document)** | **0/27 grounded (0%)** | 27 claims, 87 compliance calls, 15 drafting calls across 3 revision passes, fully instrumented | **Cause found and located precisely: `knowledge_items.source_chunk_id` unset for employee records severs the chunk→knowledge_item join; retrieval itself proven working. Not fixed.** |

None of the earlier numbers were wrong - each was an honest measurement of what it could see at the time, correctly scoped and never overclaimed (matching this project's own standing discipline on exactly this point). This document doesn't correct them; it explains what they were all downstream of.

---

## 7. Provenance

- Live run: `backups/b16_citation_run_20260830T134032Z.dump` (Postgres custom-format dump, preserved immediately after the run, before any other command touched the DSN).
- Instrument: `agents/compliance/propose.py::_candidate_input_refs`/`_proposal_output_refs`, `agents/proposal/draft_section.py::_fact_input_refs`/`_draft_output_refs`; tests: `tests/unit/test_branch_gemini_api_keys.py` covers the per-branch key wiring this run also depended on, and the instrument functions themselves were verified directly against realistic fixtures (no dedicated pytest file - see the B16 KNOWN_LIMITATIONS.md entries for the direct-execution verification performed before this run).
- Getting a real run to complete at all required a real chain of its own fixes this session (E2E rate-limit bypass, per-branch Gemini API keys for the three parallel `Send()` branches, a substring-matching Playwright locator bug, an OPTIONS-preflight rate-limiting gap, a gate-approval retry) - see KNOWN_LIMITATIONS.md's own B16 entries for each, not repeated here since none of them bear on the finding itself, only on reaching it.
- §5's own root-cause trace (REQ-010 against the preserved run's org) was produced entirely against the live, preserved database and this project's real retrieval code (`search_company_knowledge`, `search_chunks_lexical_for_org`, `search_chunks_vector_for_org`, `get_knowledge_items_by_chunk_id`), executed directly inside the `worker` container - zero LLM calls, zero additional cost. The write-site audit (`create_employee` vs. `create_certification`/`create_past_project`/`create_capability`) is a direct code read, cross-checked against real caller sites, not inferred from behavior alone.
