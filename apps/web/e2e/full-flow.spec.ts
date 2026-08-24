import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

// §7 step 11a (B13 plan). The golden-path scenario Part J's own "do not
// proceed if the system can only be demonstrated through direct database
// edits or Swagger" gate names directly. Drives the real web/api/worker
// containers (playwright.config.ts's own baseURL, no webServer block) -
// no mocked network layer anywhere in this file.
//
// Covers, in order: a setup phase (not one of Part J's 13 named steps,
// but a real dependency both step 7 and the added 14th step share - see
// KNOWN_LIMITATIONS.md's "§1's own dependency analysis missed..." entry),
// then the 13 named steps, with the 14th step (knowledge-selection
// impact preview, §1) inserted where §1 places it - right after
// extraction, before the requirement/compliance/team/commercial review
// steps that follow it in the golden path.
//
// Because setup drives real import -> approve -> verify through screens
// 2/3/4, this run exercises 5 screens Part J's own 13 named steps never
// touch on their own (2, 3, 4, plus 17 is still not reached - graph
// exploration has no named step either). Worth noting in the run report:
// the scenario as written covers more of the 17 screens than the step
// list by itself implies.

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SYNTHETIC_DIR = path.resolve(__dirname, "../../../data/synthetic/tender_a");
const AFFIRMATIVE_COMPLIANCE_STATUSES = new Set(["fully_compliant", "partially_compliant"]);

// register_actor-equivalent password (tests/route/conftest.py's own value) -
// no product meaning, just satisfies the real validation the register
// route applies.
const PASSWORD = "correct horse battery staple 1";

function uniqueSuffix(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

// Every gate-bearing screen (RequirementRegister, ComplianceMatrix,
// TeamWorkspace, CommercialFindings, Readiness) shares the identical
// shape: a bare <h2>{X} gate</h2> immediately followed by
// GateDecisionPanel's own unlabeled <section> (only rendered once a gate
// exists) - GateDecisionPanel itself carries no heading of its own to
// select by, so the panel is found via its position relative to the
// heading, not by any attribute of its own.
function gatePanel(page: Page, headingText: string) {
  return page
    .getByRole("heading", { name: headingText })
    .locator("xpath=following-sibling::section[1]");
}

// Root cause of the third real run's own failure, found in its trace,
// not guessed: page.reload({waitUntil:"load"}) only waits for the
// page's own HTML/JS/CSS to finish loading - it does NOT wait for
// React's own useEffect-triggered data fetches (listWorkflowRuns then
// listGates, sequential awaited calls) to resolve. Every polling loop in
// this file that does `await page.reload()` immediately followed by a
// one-shot DOM read (queryCount, textContent, etc.) races against those
// fetches. Three of four gate-waits in the third run happened to win
// that race often enough across ~250 retries to succeed within budget;
// the fourth (Commercial approval) did not, confirmed directly from the
// trace: repeated queryCount calls landing ~170-200ms after each reload
// call, every one reading the pre-fetch "No open gate yet" fallback,
// for the entire 300s window, even though the gate itself existed
// server-side within 11 seconds of the previous gate's approval.
// waitForLoadState("networkidle") - discouraged for general use by
// Playwright's own docs, but the right tool for this specific, narrow
// need - waits for no in-flight network requests for 500ms, which is
// what actually confirms a page's own initial data-fetching effects
// have settled, closing this race everywhere it was previously left to
// chance rather than fixing every polling reload individually.
async function reloadAndSettle(page: Page) {
  await page.reload();
  await page.waitForLoadState("networkidle");
}

// Root cause found in the second real run's own trace, not guessed: each
// of these gate-bearing screens fetches its gate ONCE on mount, with no
// polling (the same "screen X is polling-free" design already noted at
// screen 3/8's own waits) - but the compliance_review/resource_selection/
// commercial_approval gates are each opened ASYNCHRONOUSLY by the worker
// continuing the LangGraph workflow after the PREVIOUS gate's approval,
// which takes real time (specialist processing across, in one real run,
// 89 requirements). Arriving on the next screen before that gate exists
// yet leaves `gate` stuck null forever - the screen never re-checks, so
// no amount of waiting on the existing DOM would ever surface it; only a
// fresh navigation re-triggers the fetch. Confirmed directly from the
// second run's own trace: the network log showed listGates returning
// only requirement_review, with compliance_review absent, at the exact
// moment this test tried to read the (also still-empty) compliance
// entries table and then click a gate button that could structurally
// never appear without this. Same fix shape as step 4's own
// extraction-to-gate wait, applied here too - waiting for the GATE
// itself (not just the data table) also guarantees the underlying
// specialist data is ready, since a gate never opens before its own
// specialist's writes are done.
async function waitForGateToOpen(page: Page, url: string, headingText: string, timeoutMs: number) {
  await page.goto(url);
  await expect
    .poll(
      async () => {
        await reloadAndSettle(page);
        return await gatePanel(page, headingText).getByRole("button", { name: "Approve" }).count();
      },
      { timeout: timeoutMs, message: `waiting for the ${headingText} to open` },
    )
    .toBeGreaterThan(0);
}

test("golden path: login through readiness, live Docker stack", async ({ page }) => {
  // First run's own real timing (not guessed): 15 minutes was consumed
  // by setup (4 real resume-extraction LLM calls) + document parsing (5
  // real docs) + extraction-to-gate (real requirement-drafting LLM
  // calls) alone - the test timed out mid-click on the FIRST gate
  // approval, before proposal generation or red team had even started.
  // The individual per-step poll timeouts below (300s/120s) were never
  // the problem; this outer budget was too tight for their real sum
  // against live Gemini latency, not a guess corrected in the abstract.
  test.setTimeout(40 * 60_000);

  // ------------------------------------------------------------------
  // Setup (not one of Part J's 13 - see KNOWN_LIMITATIONS.md's entry on
  // why step 7 and the added 14th step both need this and neither was
  // originally flagged as needing it)
  // ------------------------------------------------------------------

  // 0a. Register - creates org + user + admin membership in one call
  // (auth_service.register), same as every other live check this
  // project has done.
  const email = `e2e-${uniqueSuffix()}@example.com`;
  await page.goto("/login");
  await page.getByRole("button", { name: "Create organization" }).click();
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByLabel("Organization name").fill(`E2E Org ${uniqueSuffix()}`);
  // The mode-switch button and the form's own submit button carry
  // identical text ("Create organization") once in register mode - the
  // submit button is the one inside <form>.
  await page.locator("form").getByRole("button", { name: "Create organization" }).click();
  await expect(page).toHaveURL("/");

  // 0b. Import all 4 distinct-candidate resumes the fixture provides
  // (resume_dana_whitfield_resubmit.docx is a resubmission of the same
  // person, not a 5th candidate - tender_a has exactly 4 real people).
  // Originally scoped to "at least one employee" (§1's own literal
  // minimum), then widened while writing this spec after tracing
  // readiness blocker 6 (role_with_no_selected_candidate,
  // readiness_service.py).
  //
  // Two passes at "why" this matters, the first one wrong in a way a
  // real run caught. Pass 1 (code-reading only): talent_analysis_node
  // (orchestration/nodes/talent.py) scores every extracted role against
  // every pool candidate unconditionally - a real cross-product, no
  // threshold - so this looked like coverage could never be the risk.
  // That claim about the MATCHING MECHANISM was correct; what it missed
  // is that "every pool candidate" is find_candidate_pool's own result,
  // which hard-filters on eligible_for_bid - and a real E2E run's own
  // post-run DB dump showed blocker 6 firing on Project Manager with
  // exactly one RoleMatch row, not four, because only Dana Whitfield
  // (the first of the four employees to finish real, sequential LLM
  // extraction) had actually been verified/made eligible by that point -
  // the OTHER THREE arrived on /talent 13-95 seconds later, after the
  // setup step below had already moved on. So the real risk was never
  // "the matching mechanism can't cover 5 roles with 4 candidates" - it
  // was "the setup step below can silently verify fewer than 4 people if
  // it stops waiting too early," which it did. Fixed at that step, not
  // here - see its own comment.
  await page.goto("/talent/import");
  const resumeFiles = [
    "resume_dana_whitfield.docx",
    "resume_marcus_ilori.docx",
    "resume_priya_ostrowski.docx",
    "resume_tomas_reyes.docx",
  ].map((f) => path.resolve(SYNTHETIC_DIR, f));
  const resumesInput = page.getByLabel("Resumes (multiple files):");
  await resumesInput.setInputFiles(resumeFiles);
  // setInputFiles only waits for the input's own DOM value to update -
  // handleResumesUpload's own uploadResumes(...) call is async and still
  // in flight at that point. The input is disabled for exactly its
  // duration (handleResumesUpload's try/finally); waiting for it to
  // re-enable is what actually confirms the request reached the server,
  // before this test does anything that could interrupt it. Without
  // this, the very next reload below cancels the in-flight upload
  // request before the batch is ever created - confirmed directly this
  // way, not assumed: the first run of this spec hit exactly that,
  // batch count staying at zero through repeated reloads.
  await expect(resumesInput).toBeEnabled({ timeout: 30_000 });

  // Screen 3 is deliberately polling-free (its own comment: no live
  // worker in the browser, re-selecting the batch or a reload is how
  // progress becomes visible) - this test builds its own wait on real
  // batch state rather than a fixed sleep. Waiting specifically for
  // completed_items + failed_items to reach total_items (the batch's
  // own terminal condition), not just batch.status, since status alone
  // doesn't distinguish "still processing" from "done."
  async function readSoleBatchRow() {
    await reloadAndSettle(page);
    const row = page.locator("table").first().locator("tbody tr").first();
    const cells = await row.locator("td").allTextContents();
    return {
      status: cells[1],
      total: Number(cells[2]),
      completed: Number(cells[3]),
      failed: cells[4],
    };
  }
  await expect
    .poll(
      async () => {
        const b = await readSoleBatchRow();
        return b.completed + Number(b.failed) >= b.total && b.total > 0;
      },
      {
        timeout: 120_000,
        message: "waiting for the resume import batch to finish processing all 4 items",
      },
    )
    .toBe(true);
  const finishedBatch = await readSoleBatchRow();
  expect(
    Number(finishedBatch.failed),
    `resume import batch had failures: ${JSON.stringify(finishedBatch)}`,
  ).toBe(0);

  await page.getByRole("button", { name: "Review items" }).click();
  const itemRows = page
    .locator("section", { has: page.getByRole("heading", { name: "Items" }) })
    .locator("tbody tr");
  // click() only waits for the DOM click event, not the async fetch it
  // triggers - a bare .count() read here can land before the items
  // table populates and see 0 rows, silently skipping every approval
  // below instead of failing. Same class of race already fixed for the
  // resume upload above; this call site was simply never touched.
  await expect
    .poll(async () => await itemRows.count(), {
      timeout: 30_000,
      message: "waiting for the import items table to load",
    })
    .toBeGreaterThan(0);
  const itemCount = await itemRows.count();
  for (let i = 0; i < itemCount; i++) {
    const row = itemRows.nth(i);
    const approveButton = row.getByRole("button", { name: "Approve" });
    // A brand-new org has no existing employees to collide with, so
    // knowledge_service.import_resume's duplicate-resolution should take
    // the NEW branch and land each item at status="approved" directly
    // (db/models/knowledge.py's own item-status vocabulary includes
    // needs_review as a real possibility though - handled, not assumed
    // away, in case an LLM-extraction ambiguity puts one there anyway).
    if ((await approveButton.count()) > 0) {
      await approveButton.click();
      // handleApprove awaits approveImportItem then re-fetches items,
      // which is what removes the Approve/Reject buttons for a row no
      // longer "needs_review" - waiting for that before the loop moves
      // on (and, on the last iteration, before the page.goto below)
      // avoids the same in-flight-cancellation risk fixed above.
      await expect(approveButton).toBeHidden({ timeout: 15_000 });
    }
  }

  // Verify every imported employee (screen 2 -> screen 4 per employee).
  // Employee names are LLM-extracted from real resume content, not
  // derivable from the filenames ahead of time - iterating every
  // employee-type row rather than matching by name sidesteps that.
  //
  // toBeGreaterThan(0) here (not the real expected count) was the actual
  // root cause of a live-run finding: the resume-import batch finishing
  // (readSoleBatchRow's own poll above) only means every import ITEM is
  // approved, not that every Employee/KnowledgeItem row has been created
  // and is visible on /talent yet - profile extraction runs per resume,
  // sequentially, and each employee's own row appears at whatever real
  // moment its own extraction finishes (confirmed against a real run's
  // knowledge_items.created_at: the four employees appeared 13s, 85s,
  // and 14s apart, not atomically). A poll that stops at "at least one"
  // captures only the FIRST employee to appear, verifies just that one,
  // and moves on - the other three stay unverified/ineligible for the
  // rest of the run, silently short-changing every downstream role's
  // candidate pool. Waiting for the real expected count, matching the
  // document-upload loop's own toHaveCount(expectedDocCount, ...)
  // discipline a few steps above, is what actually closes this.
  await page.goto("/talent");
  await expect
    .poll(
      async () => {
        await reloadAndSettle(page);
        return await page.locator('a[href^="/talent/employees/"]').count();
      },
      {
        timeout: 60_000,
        message: `waiting for all ${resumeFiles.length} imported employees to appear in the knowledge library`,
      },
    )
    .toBe(resumeFiles.length);
  const employeeHrefs = await page
    .locator('a[href^="/talent/employees/"]')
    .evaluateAll((links) => links.map((l) => (l as HTMLAnchorElement).getAttribute("href")));
  for (const href of employeeHrefs) {
    if (!href) continue;
    await page.goto(href);
    await page.getByRole("checkbox", { name: "Eligible for bid" }).check();
    await page.getByRole("button", { name: "Verify" }).click();
    await expect(page.locator("dl").getByText("yes", { exact: true })).toBeVisible();
  }

  // ------------------------------------------------------------------
  // 1. Login - already done above (register IS the first login for a
  // fresh org; Part J's step 1 covers both per Login.tsx's own design).
  // 2. Create bid
  // ------------------------------------------------------------------
  const tenderName = `E2E Tender ${uniqueSuffix()}`;
  await page.goto("/tenders/new");
  // getByLabel matches on accessible name by substring unless exact -
  // "Name" alone also matches "Client name" without this.
  await page.getByLabel("Name", { exact: true }).fill(tenderName);
  await page.getByRole("button", { name: "Create bid" }).click();
  await expect(page).toHaveURL("/");
  await page.getByRole("link", { name: tenderName }).click();
  await expect(page).toHaveURL(/\/tenders\/[0-9a-f-]{36}$/);
  const tenderId = page.url().split("/tenders/")[1];

  // ------------------------------------------------------------------
  // 3. Upload synthetic package (tender-side documents only - the
  // capability statement is org-level knowledge (screen 2, not exercised
  // by the named steps), employee_master/certifications/resumes are
  // company knowledge (screen 3, covered above in setup) - neither
  // belongs on this route).
  // ------------------------------------------------------------------
  const tenderDocs = [
    "rfp.pdf",
    "amendment_1.pdf",
    "technical_annexure.docx",
    "pricing_schedule_1.xlsx",
    "pricing_schedule_2.xlsx",
  ];
  const fileInput = page.locator('input[type="file"]').first();
  let expectedDocCount = 0;
  for (const filename of tenderDocs) {
    await fileInput.setInputFiles(path.resolve(SYNTHETIC_DIR, filename));
    expectedDocCount += 1;
    // Real state, not a fixed wait: the documents table gains exactly
    // one row per successful upload, and the input is disabled while
    // uploading (UploadAndProcessingStatus.tsx) - toHaveCount polls the
    // real DOM until the row appears rather than assuming a duration.
    await expect(page.locator("table").first().locator("tbody tr")).toHaveCount(expectedDocCount, {
      timeout: 30_000,
    });
  }

  // create_workflow_run requires every document's parse_status == "parsed"
  // (workflow_service.py's own all_parsed_for_tender - "failed" does NOT
  // count, by design, so a genuinely failed parse would leave this
  // waiting forever rather than silently starting a run that extracts
  // nothing). Uploaded != parsed - parsing is real, async, per-document
  // work the worker picks up after upload; clicking "Start processing"
  // right after the upload loop above risked the documented 409 this
  // route returns for exactly this precondition.
  await expect
    .poll(
      async () => {
        await reloadAndSettle(page);
        const statuses = await page
          .locator("table")
          .first()
          .locator("tbody tr td:nth-child(3)")
          .allTextContents();
        // Order isn't guaranteed to match tenderDocs' own upload order -
        // checked as a set condition (every row parsed, right count),
        // not a positional array comparison.
        return { count: statuses.length, allParsed: statuses.every((s) => s === "parsed") };
      },
      {
        timeout: 300_000,
        message: "waiting for every uploaded document to reach parse_status=parsed",
      },
    )
    .toEqual({ count: tenderDocs.length, allParsed: true });

  // ------------------------------------------------------------------
  // 4. Wait for extraction
  // ------------------------------------------------------------------
  await page.getByRole("button", { name: "Start processing" }).click();
  // handleStartProcessing awaits createWorkflowRun before setting
  // workflowRunId, which is what makes the "Start processing" button
  // disappear (the render switches to the task-list branch) - waiting
  // for that is what confirms the POST actually completed, before this
  // test navigates away and risks cancelling it the same way the resume
  // upload above did.
  await expect(page.getByRole("button", { name: "Start processing" })).toBeHidden({
    timeout: 30_000,
  });
  await page.goto(`/tenders/${tenderId}/requirements`);
  // No route returns "extraction is done" as a boolean - the observable
  // signal this scenario uses is the requirement_review gate opening
  // (screen 8's own fallback text disappears once orchestration reaches
  // it). Real LLM-driven extraction across 5 documents, not a fixed
  // guess at duration - generous timeout, polled via reload since this
  // screen fetches once on mount with no auto-refresh.
  await expect
    .poll(
      async () => {
        await reloadAndSettle(page);
        return await page.getByText("No open requirement review gate for this tender yet.").count();
      },
      {
        timeout: 300_000,
        message: "waiting for extraction to finish and the requirement_review gate to open",
      },
    )
    .toBe(0);

  // ------------------------------------------------------------------
  // 5. Approve requirements - every row shown, not one (consistent with
  // the same "act on everything rendered" principle applied at steps
  // 6/7/12 below, for the same reason: a scripted "just one" leaves the
  // step-13 outcome dependent on how many requirements exist).
  // ------------------------------------------------------------------
  await reloadAndSettle(page);
  const reqRows = page.locator("table").first().locator("tbody tr");
  const reqCount = await reqRows.count();
  for (let i = 0; i < reqCount; i++) {
    const row = reqRows.nth(i);
    const confirmButton = row.getByRole("button", { name: "Confirm" });
    if ((await confirmButton.count()) > 0) {
      await confirmButton.click();
      // click() only waits for the DOM event, not handleConfirm's own
      // async PATCH - waiting for the "Reviewed" cell (index 5) to flip
      // to "yes", which patchRequirement's action:"confirm" sets
      // server-side, is what actually confirms this row's mutation
      // landed before the next iteration or the gate Approve click
      // below can fire against a still-in-flight request.
      await expect(row.locator("td").nth(5)).toHaveText("yes", { timeout: 15_000 });
    }
  }
  {
    const reqGate = gatePanel(page, "Requirement review gate");
    await reqGate.getByRole("button", { name: "Approve" }).click();
    // decideGate is async (GateDecisionPanel's handleDecide); its own
    // onDecided callback is what makes the Approve button disappear
    // (gate.status flips off "pending"). Waiting for that before the
    // next page.goto() below is the same fix as the resume-upload race
    // above - a goto fired while the decision POST is still in flight
    // would cancel it before it reaches the server.
    await expect(reqGate.getByRole("button", { name: "Approve" })).toBeHidden({ timeout: 15_000 });
  }

  // ------------------------------------------------------------------
  // 6. Approve compliance - instrumented per the pre-flight's own named
  // seam: readiness blocker 2 (compliant_decision_without_valid_
  // evidence) can fire specifically on an entry this step overrides,
  // since the PATCH override sets status without creating any
  // evidence_link. Recording which entries were overridden vs already
  // affirmative makes that observable in the run's own output - if step
  // 13 comes back not-ready with blocker 2, the answer is already
  // attached here, not something requiring a DB session after the fact.
  // ------------------------------------------------------------------
  await waitForGateToOpen(
    page,
    `/tenders/${tenderId}/compliance`,
    "Compliance review gate",
    300_000,
  );
  const complianceRows = page.locator("table").first().locator("tbody tr");
  const complianceCount = await complianceRows.count();
  const overriddenEntries: string[] = [];
  const alreadyAffirmativeEntries: string[] = [];
  for (let i = 0; i < complianceCount; i++) {
    const row = complianceRows.nth(i);
    const requirementId = (await row.locator("td").first().textContent()) ?? `row-${i}`;
    const select = row.locator("select");
    const currentStatus = await select.inputValue();
    // Both branches below mutate this row asynchronously (selectOption's
    // onChange and the Approve click both fire a PATCH); the row's own
    // Approve button only renders while !human_approved (ComplianceMatrix.
    // tsx), so waiting for it to hide is the observable confirmation
    // either mutation actually persisted - same class of race as the
    // requirement-confirm loop above, at a call site that fix never
    // touched.
    const approveButton = row.getByRole("button", { name: "Approve" });
    if (AFFIRMATIVE_COMPLIANCE_STATUSES.has(currentStatus)) {
      alreadyAffirmativeEntries.push(`${requirementId} (already ${currentStatus})`);
      if ((await approveButton.count()) > 0) {
        await approveButton.click();
        await expect(approveButton).toBeHidden({ timeout: 15_000 });
      }
    } else {
      overriddenEntries.push(`${requirementId} (was ${currentStatus})`);
      // handleOverride always sets human_approved=true server-side
      // (compliance_service.override_compliance_entry's own documented
      // behaviour) - no separate Approve click needed for these rows.
      await select.selectOption("fully_compliant");
      await expect(approveButton).toBeHidden({ timeout: 15_000 });
    }
  }
  await test.info().attach("step-6-compliance-entries-touched", {
    body: JSON.stringify(
      { overridden: overriddenEntries, alreadyAffirmative: alreadyAffirmativeEntries },
      null,
      2,
    ),
    contentType: "application/json",
  });
  {
    const complianceGate = gatePanel(page, "Compliance review gate");
    await complianceGate.getByRole("button", { name: "Approve" }).click();
    await expect(complianceGate.getByRole("button", { name: "Approve" })).toBeHidden({
      timeout: 15_000,
    });
  }

  // ------------------------------------------------------------------
  // 7. Select candidate - every role, every match set gets its first
  // available candidate selected (not "select the first role only").
  // talent_analysis_node scores every extracted role against every POOL
  // candidate unconditionally (a full cross-product, no threshold -
  // orchestration/nodes/talent.py) - but "pool candidate" means
  // eligible_for_bid=true, and that's a real gate a real run can fail to
  // clear for everyone: a live run's own DB dump showed exactly this -
  // blocker 6 fired on Project Manager because the setup step's employee
  // loop above had only verified 1 of 4 employees (fixed there now, see
  // its own comment), leaving every role's pool artificially down to
  // one candidate instead of four. The "0 failures" assertion on the
  // import batch earlier proves the IMPORT succeeded; it says nothing
  // about how many of the imported employees later got VERIFIED, which
  // is what actually determines this loop's real candidate pool. The
  // "No candidates found" branch below stays defensive on purpose - not
  // because it's expected now, but because "the pool came back non-empty"
  // turned out to be a claim worth actually checking, not assuming.
  // ------------------------------------------------------------------
  await waitForGateToOpen(page, `/tenders/${tenderId}/team`, "Resource selection gate", 300_000);
  const roleRows = page.locator("table").first().locator("tbody tr");
  const roleCount = await roleRows.count();
  const rolesWithNoCandidate: string[] = [];
  for (let i = 0; i < roleCount; i++) {
    const roleRow = page.locator("table").first().locator("tbody tr").nth(i);
    const roleName = (await roleRow.locator("td").first().textContent()) ?? `role-${i}`;
    await roleRow.getByRole("button", { name: "View candidates" }).click();
    // handleSelectRole's own listMatches(roleId) call is async, and
    // TeamWorkspace.tsx doesn't clear the previous role's matches before
    // it resolves (see KNOWN_LIMITATIONS.md) - a bare read right after
    // click() can observe the PREVIOUS role's candidates, or select a
    // candidate for the wrong role. The section shows no role name or
    // busy indicator to poll for instead, so networkidle is what
    // actually confirms this role's own fetch has landed.
    await page.waitForLoadState("networkidle");
    const candidateSection = page.locator("section", {
      has: page.getByRole("heading", { name: "Candidates" }),
    });
    const noCandidates = await candidateSection
      .getByText("No candidates found for this role yet.")
      .count();
    if (noCandidates > 0) {
      rolesWithNoCandidate.push(roleName);
      continue;
    }
    // Scoped to the first candidate ROW, not a global "first Select
    // button" locator - every candidate for this role starts as
    // "Select" (talent_analysis_node scores the whole pool per role, so
    // several rows can carry that name at once), and a role+name
    // locator re-resolves live: if it weren't row-scoped, waiting for
    // it to hide after clicking would just re-point at the NEXT
    // still-unselected candidate's button instead of confirming this
    // click's own effect.
    const firstCandidateRow = candidateSection.locator("tbody tr").first();
    const firstSelectButton = firstCandidateRow.getByRole("button", { name: "Select" });
    await firstSelectButton.click();
    // handleSelectCandidate's PATCH flips this row's own button to
    // "Deselect" on success - waiting for that (rather than just the
    // DOM click event) before the loop advances to the next role avoids
    // the same in-flight-cancellation risk fixed elsewhere in this file.
    await expect(firstSelectButton).toBeHidden({ timeout: 15_000 });
  }
  await test.info().attach("step-7-roles-with-no-candidate", {
    body: JSON.stringify(rolesWithNoCandidate, null, 2),
    contentType: "application/json",
  });
  {
    const resourceGate = gatePanel(page, "Resource selection gate");
    await resourceGate.getByRole("button", { name: "Approve" }).click();
    await expect(resourceGate.getByRole("button", { name: "Approve" })).toBeHidden({
      timeout: 15_000,
    });
  }

  // ------------------------------------------------------------------
  // 8. Inspect pricing finding - inspection only, per step 12's own
  // decision below: nothing here gets resolved yet.
  // ------------------------------------------------------------------
  await waitForGateToOpen(
    page,
    `/tenders/${tenderId}/commercial`,
    "Commercial approval gate",
    300_000,
  );
  const pricingFindingCountAtStep8 = await page.locator("table").nth(1).locator("tbody tr").count();
  expect(
    pricingFindingCountAtStep8,
    "expected the seeded pricing discrepancies to produce real findings",
  ).toBeGreaterThan(0);
  {
    const commercialGate = gatePanel(page, "Commercial approval gate");
    await commercialGate.getByRole("button", { name: "Approve" }).click();
    await expect(commercialGate.getByRole("button", { name: "Approve" })).toBeHidden({
      timeout: 15_000,
    });
  }

  // ------------------------------------------------------------------
  // 9. Generate proposal
  // ------------------------------------------------------------------
  await page.goto(`/tenders/${tenderId}/proposal`);
  await page.getByRole("button", { name: "Generate proposal" }).click();
  // Real timing not yet observed for this tender's own richer content (up
  // to 6 section templates, each its own LLM call) - budgeted generously
  // rather than guessed tight, given the cost of finding out this was too
  // short a third time.
  await expect(page.getByRole("button", { name: "Generate proposal" })).toBeEnabled({
    timeout: 180_000,
  });
  const proposalSections = page.locator("main > section");
  await expect(proposalSections.first()).toBeVisible();
  const sectionCountAtStep9 = await proposalSections.count();
  expect(
    sectionCountAtStep9,
    "expected at least one proposal section to be drafted",
  ).toBeGreaterThan(0);

  // ------------------------------------------------------------------
  // 10. Inspect claim trace - predicting the surrounding facts, not the
  // sample itself. Expected: multiple sections, real claims within them,
  // at least one is_factual claim with grounding_status='unsupported'
  // (§7 step 11's pre-flight, based on step 9's own live observation on
  // comparably rich data). NOT predicted: whether that claim's
  // currently_in_scope_evidence comes back empty or non-empty - that is
  // the actual sample this step exists to produce, recorded via
  // attachment rather than asserted on either way.
  // ------------------------------------------------------------------
  const unsupportedClaimButton = page
    .locator("li", { hasText: "/ unsupported]" })
    .getByRole("button", { name: "Inspect trace" })
    .first();
  const unsupportedClaimCount = await page.locator("li", { hasText: "/ unsupported]" }).count();
  expect(
    unsupportedClaimCount,
    "expected at least one unsupported factual claim in the drafted proposal",
  ).toBeGreaterThan(0);
  await unsupportedClaimButton.click();
  const drawer = page.locator("aside");
  await expect(drawer.getByRole("heading", { name: "Claim trace" })).toBeVisible();
  const inScopeWarning = drawer.getByRole("alert").filter({ hasText: "not cited" });
  const inScopeEmpty = drawer.getByText("No additional in-scope evidence.");
  const sampleOutcome =
    (await inScopeWarning.count()) > 0
      ? await inScopeWarning.textContent()
      : await inScopeEmpty.textContent();
  await test.info().attach("step-10-claim-trace-sample", {
    body: JSON.stringify({ sample: sampleOutcome }, null, 2),
    contentType: "application/json",
  });

  // ------------------------------------------------------------------
  // 11. Run red team
  // ------------------------------------------------------------------
  await page.goto(`/tenders/${tenderId}/redteam`);
  await page.getByRole("button", { name: "Run red team" }).click();
  // Same reasoning as proposal generation above - 5 deterministic checks
  // across 89 requirements plus one real redteam_critique LLM call, real
  // timing not yet observed, budgeted generously.
  await expect(page.getByRole("button", { name: "Run red team" })).toBeEnabled({
    timeout: 180_000,
  });

  // ------------------------------------------------------------------
  // 12. Resolve seeded issue - decided as "all," not "one" (§7 step 11's
  // pre-flight): readiness blocker 5 (unapproved_required_commercial_
  // item) reads pricing_findings.resolved directly, independent of
  // whatever redteam_findings say, so both resource types need clearing
  // for a clean step-13 result. Resolve every open pricing_finding
  // first (screen 12), then every open redteam_finding (screen 14) -
  // resolving a redteam_finding never touches the pricing_finding it
  // targets (redteam_service.resolve_finding's own read-only scope), so
  // the order here doesn't create a false pass either way, but pricing
  // first matches the scenario's own step 8/12 ordering.
  // ------------------------------------------------------------------
  await page.goto(`/tenders/${tenderId}/commercial`);
  await reloadAndSettle(page);
  {
    const findingsTable = page.locator("table").nth(1);
    let resolveButtons = findingsTable.getByRole("button", { name: "Resolve" });
    let remaining = await resolveButtons.count();
    while (remaining > 0) {
      await resolveButtons.first().click();
      await expect(resolveButtons).toHaveCount(remaining - 1, { timeout: 10_000 });
      resolveButtons = findingsTable.getByRole("button", { name: "Resolve" });
      remaining = await resolveButtons.count();
    }
  }

  await page.goto(`/tenders/${tenderId}/redteam`);
  await reloadAndSettle(page);
  {
    let resolveButtons = page.getByRole("button", { name: "Resolve" });
    let remaining = await resolveButtons.count();
    while (remaining > 0) {
      await resolveButtons.first().click();
      await expect(resolveButtons).toHaveCount(remaining - 1, { timeout: 10_000 });
      resolveButtons = page.getByRole("button", { name: "Resolve" });
      remaining = await resolveButtons.count();
    }
  }

  // ------------------------------------------------------------------
  // §1's added 14th step: knowledge-selection impact preview, including
  // the 409 GRAPH_STALE -> project -> retry path. This tender has never
  // had a graph projection run at this point in the scenario, so the
  // staleness branch fires organically here, same as verified live
  // against the real stack before step 9 (screen 10's own live-browser
  // check) - not re-verified at the network level here, just exercised
  // end to end through the UI as the golden path's own real trigger.
  // ------------------------------------------------------------------
  await page.goto(`/tenders/${tenderId}/knowledge-selections`);
  await expect
    .poll(
      async () => {
        await reloadAndSettle(page);
        return await page.locator("table").first().locator("tbody tr").count();
      },
      {
        timeout: 30_000,
        message: "waiting for the imported knowledge items to appear as selectable rows",
      },
    )
    .toBeGreaterThan(0);
  await page
    .locator("table")
    .first()
    .locator("tbody tr")
    .first()
    .getByRole("button", { name: "Preview impact" })
    .click();
  await expect(page.getByRole("heading", { name: "Impact preview" })).toBeVisible({
    timeout: 30_000,
  });
  const confirmButton = page.getByRole("button", { name: /^Confirm (select|deselect)$/ });
  await expect(confirmButton).toBeVisible({ timeout: 30_000 });
  await confirmButton.click();
  await expect(page.getByRole("heading", { name: "Impact preview" })).toBeHidden({
    timeout: 10_000,
  });

  // ------------------------------------------------------------------
  // 13. Inspect readiness - the honest assertion. Expected: ready: true,
  // following from steps 5/6/7/12 each having been scripted to resolve
  // everything shown, not "unless there happen to be more findings."
  // If it fails, the real blockers list is attached to the failure
  // output (and to the compliance/role instrumentation above) so the
  // cause is diagnosable from the run's own artifacts, not a second
  // DB session.
  // ------------------------------------------------------------------
  await page.goto(`/tenders/${tenderId}/readiness`);
  await page.getByRole("button", { name: "Generate readiness report" }).click();
  await expect(page.locator("strong")).toBeVisible({ timeout: 30_000 });
  const readyText = (await page.locator("strong").first().textContent())?.trim();
  if (readyText !== "Ready") {
    const blockers = await page
      .locator("h2", { hasText: "Blockers" })
      .locator("xpath=following-sibling::ul[1]/li")
      .allTextContents();
    await test.info().attach("step-13-readiness-blockers", {
      body: JSON.stringify({ readyText, blockers }, null, 2),
      contentType: "application/json",
    });
    throw new Error(
      `Expected readiness "Ready", got "${readyText}". Blockers: ${JSON.stringify(blockers, null, 2)}`,
    );
  }
  expect(readyText).toBe("Ready");
});
