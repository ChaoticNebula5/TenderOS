# Fixing the missing `source_chunk_id` — design options and the fix that shipped

**Status: decided and implemented in B16.** Option (a) (join table) shipped as `014_knowledge_item_chunks.py`. Verified before/after on the preserved investigation dump: 0 candidates for REQ-010 in every org before the migration, 10 candidates (including Priya Ostrowski's employee item via the exact PMP chunk the investigation named) in every org that actually has the résumé fixture loaded, after. See `KNOWN_LIMITATIONS.md`'s B16 entries for the full verification numbers (test suite, mypy).

This follows directly from [`citation-correctness-investigation.md`](citation-correctness-investigation.md) §5c, which located the root cause: `create_employee` never sets `knowledge_items.source_chunk_id`, unlike its three siblings. That document is closed (investigation only, no fix). This one lays out the fix's real design options — kept below exactly as written when the decision was still open, for the record — followed by the decision and its implementation.

**The trap in the obvious version, stated up front:** `source_chunk_id` is a scalar FK — one chunk per knowledge_item row. An employee's résumé is not one chunk; it chunks into several (summary, experience, certifications, education — B4's ~256-token chunker, confirmed live via `heading_path: ["Priya Ostrowski", "Certifications"]`). Pointing `source_chunk_id` at "the résumé's first chunk" would make the column non-NULL and *still* fail REQ-010: the PMP match lives in the certifications chunk, not the first one. That's a different bug wearing the same symptom (0 candidates), not a fix.

---

## 1. What `get_knowledge_items_by_chunk_id` actually needs

Read directly (`db/repositories/knowledge.py:139-152`). Its query is `WHERE knowledge_items.source_chunk_id == chunk_id`, returning `list[KnowledgeItem]` — plural because the *other* direction (one chunk, several items) is already real and handled: the function's own comment notes two capability-statement projects landing in the same chunk. That direction needs nothing new.

The direction this fix needs is the reverse: one knowledge_item (an employee), many chunks. The current column cannot represent that at all, regardless of which chunk gets written to it — it's one FK, not a set. Confirmed in the model (`KnowledgeItem.source_chunk_id` in `db/models/knowledge.py`): `source_chunk_id: Mapped[uuid.UUID | None]`, a single `ForeignKey("document_chunks.id")`, no list, no secondary table anywhere in this file.

**A many-to-many association would serve it, and cleanly.** What changes: the query becomes a join through an association table instead of a direct column match (`WHERE knowledge_item_chunks.chunk_id == chunk_id` instead of `WHERE knowledge_items.source_chunk_id == chunk_id`), same return shape, same plurality guarantee already documented for the other direction. I checked for other callers: `get_knowledge_items_by_chunk_id` has exactly one, `search_company_knowledge` (`agents/knowledge/retrieval.py:118`) — so this change has one call site to update, not a scattered surface.

**One finding worth surfacing before the options, because it changes what "sufficient" means for all three:** `agents/knowledge/extract.py`'s own docstring (lines 132-138) claims "an employee profile has no per-field citation need... since B6's own gate only ever cites certifications/past_projects as evidence." I checked this against the actual downstream code rather than trusting the comment. It doesn't hold as a code-capability claim: `compliance_service.py:64-66` has a dedicated branch for `item_type == "employee"` (checks `profile_review_status == "verified"` for its verification signal), `prepare_evidence_candidates`/`to_llm_candidates` apply no item_type filter anywhere, and the compliance-proposal prompt (`prompts/versions/v1/compliance_proposal.md:8`) lists "employee" first among the source kinds the model is told to expect. The pipeline is fully wired to accept and cite employee-typed evidence directly — it may be true that the *eval ground truth fixture* never happened to exercise that path (I didn't check the fixture itself, and that's a narrower, separate claim), but nothing in the code precludes it. This matters because it means none of the three options below needs to also detour through creating certification/past_project rows just to become "citable" — an employee item with a correctly-populated chunk association is already a valid citation target as the code stands today.

---

## 2. The real options, with costs

### (a) Join table: all of a résumé's chunks associate to the employee

New table, e.g. `knowledge_item_chunks(knowledge_item_id FK, chunk_id FK, PRIMARY KEY (knowledge_item_id, chunk_id))`, both cascade-deleted from their parents.

- **Migration:** yes — new table (`014_*.py`, the project's migrations are sequential, currently at `013_readiness.py`). Purely additive; existing employee rows get zero associations until reprocessed, which is a real gap to state rather than gloss over if this ships alone.
- **Code cost:** `get_knowledge_items_by_chunk_id` rewritten to join instead of direct-match (one call site, per above). `create_employee` (or a new repository method) needs to accept and insert a list of chunk ids instead of a single one. `import_resume` needs to pass some or all of the résumé's own chunks — `extract_employee_profile` already receives the full `resume_chunks: list[DocumentChunk]` (`agents/knowledge/extract.py:51-65`), so the chunks are already in scope at the point they'd need threading through; nothing new needs to be fetched.
- **What it fixes:** full recall — every résumé section becomes independently matchable. REQ-010 finds the PMP chunk regardless of which section it's in.
- **What it costs beyond the migration:** every chunk of the résumé (summary, experience, certifications, education) becomes a candidate that resolves to the *same* employee item, undifferentiated by which section actually matched. Retrieval would return "Priya Ostrowski" as the candidate whether the real match was her certifications or her years of experience — coarser attribution than certifications/past_projects get today (which point at one specific fact each). Whether that granularity is good enough is itself a judgment call.

### (b) `source_chunk_id` covers one chunk; something else covers the rest

The vague version of this ("stash extra chunk ids in `metadata_` JSONB") is realistically worse than (a) on every axis — no FK, no cascade-delete cleanup, and the query becomes a JSONB containment check instead of an indexed join. Not worth costing further as its own option; it's option (a) done without a proper table.

The concrete version worth costing: **route the employee case around `source_chunk_id` entirely**, using data that's already correct. `Employee.resume_document_id` (`db/models/knowledge.py`) is already set correctly by `import_resume` today (`knowledge_service.py:337`) — this is not a broken column. Retrieval could resolve a matched chunk to an employee via `document_chunks.document_id == employee.resume_document_id` instead of via `get_knowledge_items_by_chunk_id` at all, for the `employee` item_type specifically.

- **Migration:** none. Zero new tables or columns — this uses a foreign key that already exists and is already populated.
- **Code cost:** a second, employee-specific lookup path alongside `get_knowledge_items_by_chunk_id`, not a change to it. `search_company_knowledge` would need an `item_type`-aware branch: certifications/past_projects/capabilities resolve via the existing chunk FK, employees resolve via document-id match instead.
- **What it costs:** breaks the "employee is the same shape as its siblings" abstraction the comment at `knowledge.py:305-308` currently asserts — this makes it explicitly not the same shape, a special case carved out rather than a uniform fix. It also only helps résumé-sourced employees; `import_employee_master_row` has no `resume_document_id` either (see §3), so this doesn't extend that path's coverage any further than (a) or (c) would.

### (c) Résumé import creates per-section knowledge_items, the way certifications already do

Instead of associating the employee item with multiple chunks, stop treating the employee item as the citation target at all for certification/project facts — extract them as real `certification`/`past_project` knowledge_items, chunk-resolved exactly the way `import_certification_row` and capability-statement extraction already do it.

This is a closer fit than it first looks: `extract_employee_profile` already receives the full, real, already-persisted `resume_chunks` (`agents/knowledge/extract.py:54`, `62-64`), labeled by `heading_path` — the exact same shape `extract_capability_statement` uses to resolve `source_chunk_ref` back to a real `chunk.id` (`extract.py:176-220`). Adding a `source_chunk_ref` field to `ExtractedCertificationMention`/`ExtractedProjectMention` and resolving it the same way is mechanically a close copy of working code, not new design.

- **Migration:** none. `create_certification`/`create_past_project` already declare and correctly use `source_chunk_id` — this reuses existing, proven paths.
- **Code cost:** schema addition to two Pydantic models, a resolution loop copied from `extract_capability_statement`'s pattern, and a call-site change in `import_resume` to call `create_certification`/`create_past_project` instead of only stashing mentions in `parsed_profile`.
- **The real cost is not code — it's a product decision this project already made deliberately, and this option reverses it.** `ExtractedCertificationMention`'s own docstring (`agents/schemas/knowledge.py:5-11`) states plainly: a résumé's self-reported certification is "NOT the same thing as an authoritative certifications.xlsx row... not written into the certifications table directly." `import_resume`'s own docstring says the same. That's not an oversight — it's a considered claim/authority distinction: a résumé is what someone says about themselves, not verified fact. Option (c) as stated would silently erase that distinction by promoting every résumé claim straight into the authoritative table. Doing this safely would need a real gate — e.g., creating these as `status="unverified"` (the CHECK constraint on `knowledge_items.status` already allows it, at `knowledge.py:99-102`; nothing currently writes it) pending a reviewer's confirmation — which is scope this option doesn't get to skip just because the mechanical extraction part is cheap.

---

## 3. Can `import_employee_master_row` satisfy any of these?

No, and this is a real coverage limit independent of which option wins, not a gap in the analysis. Read directly (`knowledge_service.py:163-254`): its only inputs are `EmployeeMasterRow` fields — `employee_code`, `name`, `role`, `status` — spreadsheet cells with no document, no chunk, no free text behind them at all. There is no chunk to associate (a), no `resume_document_id` to route through (b — the field is `None` for this path), and no résumé prose to extract certification/project mentions from (c).

An employee created from `employee_master.xlsx` alone will have zero chunk-level evidence under any of these three options — the same 0-candidate outcome this whole investigation traced, just for a structurally different reason (no source text exists, not a missing FK). Whichever option ships, this stays true and is worth its own line in `KNOWN_LIMITATIONS.md` rather than being implied by the fix and then rediscovered later.

---

## 4. Does this need a migration?

Depends entirely on the option:

| Option | Migration needed |
|---|---|
| (a) join table | **Yes** — new table, additive, no backfill for existing rows |
| (b) route via `resume_document_id` | **No** — reuses an existing, already-correct FK |
| (c) per-section knowledge_items | **No** — reuses `create_certification`/`create_past_project`, already correct |

---

## 5. Data confirmation: scope of the 79-row, 100%-NULL finding

Restored the preserved dump (`backups/b16_citation_run_20260830T134032Z.dump`) into a scratch database to check rather than assume — matching this session's standing discipline of querying the preserved artifact directly.

`knowledge_import_items` (the real per-row ledger, not `knowledge_import_batches`' own progress counters — those are already a separate, named "column exists, not populated" limitation and not trustworthy for this question) shows:

| import_type | status | items |
|---|---|---|
| `bulk_resumes` | `approved` | 76 |
| `employee_master_csv` | `approved` | 2 |

**No `certification_documents` import items exist anywhere in this database — zero.** Combined with `knowledge_items` grouped by `item_type` showing only `employee` rows exist at all (79, matching §-scope from the investigation artifact), this answers your question directly: **`import_certification_row`'s correctness is established entirely by code reading — it has never actually executed in any preserved run this session produced.** Its own callers (`create_certification` and the `_create_synthetic_chunk` path it uses) are correct by inspection and by a cross-check against `create_past_project`/`create_capability`'s parallel structure, but there is no live output anywhere to point to as confirmation. Worth stating precisely rather than letting "proven correct" and "proven correct by inspection, never observed" blur together.

---

## TODO(human): pick the option (and what `import_employee_master_row`'s gap means for shipping it)

**Steps:**
1. Re-read the three options in §2 above, plus the migration table in §4 and the coverage limit in §3.
2. Decide which option to implement — or state explicitly if you want a combination (e.g. (a) for general correctness, with (c)'s review-gated promotion added later as a separate, bigger change).
3. If you pick (c), decide the gate: do résumé-promoted certifications land as `status="unverified"` pending review, or is there a different mechanism you'd rather use? This is the part option (c) can't skip per §2's own cost note.
4. Decide whether `import_employee_master_row`'s permanent gap (§3) is acceptable as a stated, documented limitation, or whether it needs its own follow-up (e.g., a future capability to attach a document to a master-row-created employee).
5. Write your decision and reasoning below this line — prose, not code. This is the design record for whichever block implements the fix (B17 per the existing `KNOWN_LIMITATIONS.md` ownership notes), not the implementation itself.

**What's already available to use:** the three fully-costed options above; the code-verified fact (§1, checked against `compliance_service.py`, `prepare_evidence_candidates`, and the compliance-proposal prompt directly, not assumed from the stale-sounding `extract.py` comment) that employee-typed evidence is already fully wired as a citable candidate — so recall is the only thing missing, not eligibility; the migration cost table (§4); the existing `certifications.status` CHECK constraint already allowing `'unverified'` with no current writer, if (c) is chosen.

**Why this is worth deciding by hand:** there's no single correct answer here — it's a real trade-off between recall completeness (a), schema minimalism at the cost of a special-cased lookup path (b), and reusing proven code at the cost of reopening a deliberate "self-report isn't authoritative" product decision (c). Whichever gets picked also has to say what happens to `import_employee_master_row`'s employees, which none of the three options actually solves. I'd be picking one of these silently if I chose for you, which is exactly the kind of call this project's process reserves for you.

## Decision

**Option (a), join table.** It's the only option that fixes the actual mismatch — one employee, many chunks — rather than routing around it. (b) carves out a special case for one item type, which makes "employees are the same shape as their siblings" false *in code* rather than just in a comment; that's creating the sibling-drift pattern, not closing it. (c) reverses a deliberate product decision (self-reported ≠ authoritative) and needs a review gate to do safely, which is real scope this fix doesn't need to take on.

Two decisions this document left open, now settled:

1. **Backfill, not re-import.** Existing employees have `resume_document_id` populated correctly, so their chunks are derivable without re-running extraction — associate every chunk of an employee's résumé document to that employee, done as part of the migration (matching the existing `op.execute` data-backfill precedent in `012_redteam_resolution_source.py`) so schema and data land atomically together, not as a separate script that can be silently skipped.
2. **`import_employee_master_row`'s gap is accepted as a documented limitation.** A spreadsheet row has no source text; nothing to cite is an honest outcome, not a bug. Gets its own `KNOWN_LIMITATIONS.md` entry stating it applies regardless of which option shipped.

**The "coarser attribution" cost from §2(a) does not actually hold — checked, not assumed.** I read `compliance_service.py`'s evidence-materialization path (`materialize_evidence`, `EvidenceLink` model) and the traceability endpoint (`get_compliance_trace`) directly. `evidence_links` carries `knowledge_item_id` and `source_chunk_id` as two independent nullable FKs (`db/models/compliance.py:35-40`, both populated "when both are present rather than one crowding out the other" per `get_compliance_trace`'s own docstring). `materialize_evidence` writes `source_chunk_id=p.result.chunk_id` — the actual chunk that matched at retrieval time (`KnowledgeSearchResult.chunk_id`), not `knowledge_items.source_chunk_id`. `get_compliance_trace` resolves `heading_path` from `link.source_chunk_id`, i.e. from the real matched chunk, independent of whichever join-table associations led `get_knowledge_items_by_chunk_id` to surface the employee as a candidate. **Section-level granularity is not discarded — it was never routed through the column this fix touches.** The join table only widens *recall* (which knowledge_items are candidates for a chunk); *which* chunk actually matched, and its heading_path, already flows through a separate, already-correct path untouched by this fix. The §2(a) cost note was wrong and should be read as superseded by this paragraph, not as a remaining risk.

**The `extract.py` docstring finding, filed separately:** `agents/knowledge/extract.py:132-138`'s claim that "an employee profile has no per-field citation need... since B6's own gate only ever cites certifications/past_projects as evidence" does not hold against the actual code — `compliance_service.py:64-66` has a dedicated employee-verification branch, `prepare_evidence_candidates`/`to_llm_candidates` apply no item_type filter, and the compliance-proposal prompt lists "employee" as an expected source kind. This is a stale comment that would have sent the next reader down the wrong path — logged in `KNOWN_LIMITATIONS.md` as its own entry, not fixed by silently deleting the claim (the comment gets corrected as part of implementation, in §3 of the KNOWN_LIMITATIONS entry).
