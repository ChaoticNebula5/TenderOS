import { expect, test } from "@playwright/test";
import type { Locator, Page, Request } from "@playwright/test";
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
// then the 13 named steps, then the 14th step (knowledge-selection
// impact preview + B14's own reanalysis-cascade assertions) last, after
// step 12's findings are resolved and immediately before step 13's
// readiness check - not "right after extraction" as an earlier version
// of this comment claimed (stale; corrected during B14, checked against
// the actual code position directly, not reworded from assumption - see
// KNOWN_LIMITATIONS.md's B14 §1 placement finding). This ordering is
// exactly what B14 needs: real drafted claims and real compliance
// decisions already exist by this point in the run, which is the
// precondition its own reanalysis-cascade assertions depend on.
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

const MAX_GATE_APPROVE_RETRIES = 2;

// B16 workaround for the SECOND instance of a real frontend pattern, not
// a generic robustness improvement - see KNOWN_LIMITATIONS.md, "Standing
// pattern: this frontend has no recovery path from a single transient
// request failure". GateDecisionPanel's own handleDecide has no retry:
// one failed decideGate() call (a live run hit exactly this - a single,
// isolated 429 on a gate-approval click, from before OPTIONS preflights
// were exempted from rate limiting) leaves the Approve button visible
// forever, with nothing in the app able to recover. This wrapper retries
// the CLICK itself, bounded, not a poll - belt and braces alongside the
// OPTIONS exemption fix, which should make this rare rather than
// unnecessary: a genuinely transient failure (a network blip, anything
// else that isn't the now-fixed preflight issue) can still hit the same
// dead end. Delete this - and revert every gate-approval call site back
// to a bare click() + toBeHidden() - once B17 gives GateDecisionPanel a
// real retry/recovery mechanism of its own.
async function approveGate(gate: Locator): Promise<void> {
  const approveButton = gate.getByRole("button", { name: "Approve" });
  for (let attempt = 1; attempt <= MAX_GATE_APPROVE_RETRIES; attempt++) {
    await approveButton.click();
    try {
      await expect(approveButton).toBeHidden({ timeout: 15_000 });
      return;
    } catch (err) {
      if (attempt === MAX_GATE_APPROVE_RETRIES) throw err;
    }
  }
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

const MAX_ROUTE_BOUNCE_RECOVERIES = 2;

// B15's rate_limit.py, read directly rather than assumed: the window is
// keyed to whichever request first RE-CREATES the Redis counter after it
// last expired (_INCR_WITH_EXPIRE_SCRIPT only calls EXPIRE when
// `current == 1`), not a wall-clock-aligned boundary - and it does not
// decay or extend on later requests within an already-active window.
// That means a bare, immediate retry can never escape a tripped window
// (it just becomes one more request inside the same window, confirmed
// live: a zero-delay version of this loop produced 14 consecutive 429s
// in one real run, docker logs tenderos-api-1) - only waiting out the
// window does. 65s, not exactly B15's own 60s GLOBAL_WINDOW_SECONDS:
// small margin since this wrapper doesn't read the real Retry-After value
// off the failed request (would need intercepting network responses, not
// attempted here), so it waits the full nominal window plus a buffer
// rather than the precise remaining TTL.
const RATE_LIMIT_RECOVERY_WAIT_MS = 65_000;

// B16 workaround for a real product bug filed for B17 - NOT a generic
// robustness improvement, and not meant to outlive that bug. See
// KNOWN_LIMITATIONS.md, "[B16, owner B17] Login.tsx's 'already logged in'
// guard drops the user's original destination on any transient /auth/me
// failure": a single 429 on /auth/me - confirmed reachable at this spec's
// own real request volume even after throttling every poll below to a 2s
// reload floor (rate_limit_middleware.py's global ceiling, B15) -
// permanently strands the browser on "/" or "/login", with nothing in the
// app itself able to recover. Every reloadAndSettle(page) call that sits
// inside a poll below goes through this wrapper instead, so a bounce gets
// detected and manually re-navigated past, loudly, rather than silently
// reading the wrong page's DOM as "0" for the rest of that poll's own
// budget - which is exactly what turned one real, transient 429 into an
// hour spent chasing a "Received: 0" message that gave no hint where the
// actual problem was. Bounded, not looped forever, AND throttled between
// attempts, not just bounded: an unbounded OR zero-delay retry here both
// feed the same rate limiter that caused the bounce in the first place -
// bounded alone was not enough on its own (see the 14-429 incident
// above). Delete this wrapper - and revert every call site below back to
// a bare reloadAndSettle(page) - once B17 actually fixes the underlying
// guard; there is no reason for this scaffolding to exist once a
// transient auth failure can no longer strand the browser.
// B17 Phase 1's own instrumentation, not a permanent addition: counts every
// real bounce-recovery this wrapper performs across the WHOLE run, attached
// as an artifact at the end (see "route-bounce-recoveries" below) - the
// concrete answer to "did Login.tsx's destination-loss fix actually work,"
// not just "did this run happen to pass." Deliberately does not change the
// wrapper's own behavior or remove it: per B17's own plan, this run is
// meant to CONFIRM the fix, and removing the scaffolding in the same pass
// that's supposed to verify it would leave no fallback if the fix turned
// out incomplete.
//
// B17 Phase 0: the bare count above conflated two different things - a
// real /login landing (the destination-loss symptom this instrument exists
// to measure) and any other route mismatch (e.g. a poll that reloads while
// the page is on a different screen than the one it names - run 4's only
// "bounce" was exactly that: on /redteam, expected /compliance). Each
// recovery now records the URL the page actually landed on, and the
// attachment reports /login landings separately. Only loginLandings is
// evidence on the destination-preservation fix.
type RouteBounceRecovery = { expectedPath: string; landedUrl: string; loginLanding: boolean };
const routeBounceRecoveries: RouteBounceRecovery[] = [];

// B17 Phase 0 instrument: how many times every loop body and poll callback
// in this spec actually ran in this run, plus the actions each loop took,
// attached as "loop-and-poll-iterations". Increment-only - no selector or
// flow change - so "not vacuous" is evidence from the run, not a claim.
const iterations: Record<string, number> = {};
function tick(name: string): void {
  iterations[name] = (iterations[name] ?? 0) + 1;
}

async function reloadAndSettleOnRoute(page: Page, expectedPath: string): Promise<void> {
  await reloadAndSettle(page);
  let bounces = 0;
  while (!page.url().endsWith(expectedPath) && bounces < MAX_ROUTE_BOUNCE_RECOVERIES) {
    bounces += 1;
    const landedUrl = page.url();
    routeBounceRecoveries.push({
      expectedPath,
      landedUrl,
      loginLanding: new URL(landedUrl).pathname === "/login",
    });
    await page.waitForTimeout(RATE_LIMIT_RECOVERY_WAIT_MS);
    await page.goto(expectedPath);
    await reloadAndSettle(page);
  }
  if (!page.url().endsWith(expectedPath)) {
    throw new Error(
      `bounced away from ${expectedPath} to ${page.url()} ${bounces} time(s), waiting ` +
        `${RATE_LIMIT_RECOVERY_WAIT_MS / 1000}s between each - Login.tsx's destination-loss ` +
        `bug (KNOWN_LIMITATIONS.md, "[B16, owner B17]"), not a poll timeout; giving up after ` +
        `${MAX_ROUTE_BOUNCE_RECOVERIES} recovery attempts`,
    );
  }
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
        tick(`poll:gate-open:${headingText}`);
        await reloadAndSettleOnRoute(page, url);
        return await gatePanel(page, headingText).getByRole("button", { name: "Approve" }).count();
      },
      // B16: 2s floor - shared by every waitForGateToOpen call site (each
      // gate wait in this run), same reload-burst-triggers-429 mechanism
      // fixed at the /talent poll below, fixed once here since this is the
      // one function all of them go through.
      { timeout: timeoutMs, intervals: [2_000], message: `waiting for the ${headingText} to open` },
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
  // A later real run (B16, KNOWN_LIMITATIONS.md's "Workflow retry
  // controls are inert" entry) reached 45.1 minutes of real workflow
  // wall-clock before failing on an unrelated RateLimitedError - past
  // this 40-minute cap. Bumped with real margin above that observed
  // number, not re-guessed from zero.
  test.setTimeout(60 * 60_000);

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
  // The server's answer is the proof the batch exists; the input's
  // re-enable below is kept as the UI's own check.
  const resumeBatchCreated = page.waitForResponse(
    (resp) =>
      resp.request().method() === "POST" &&
      new URL(resp.url()).pathname.endsWith("/knowledge/import-batches/resumes"),
    { timeout: 30_000 },
  );
  await resumesInput.setInputFiles(resumeFiles);
  const resumeBatchResponse = await resumeBatchCreated;
  expect(
    resumeBatchResponse.ok(),
    `POST .../knowledge/import-batches/resumes answered ${resumeBatchResponse.status()}`,
  ).toBe(true);
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
    await reloadAndSettleOnRoute(page, "/talent/import");
    // B17 Phase 0 guard: this reads .first() on the assumption there is
    // exactly one batch (step 0b's single résumé upload, in a fresh org).
    // A second batch would make .first() silently read the wrong one.
    expect(
      await page.locator("table").first().locator("tbody tr").count(),
      "expected exactly one import batch in this run's fresh organization",
    ).toBe(1);
    const row = page.locator("table").first().locator("tbody tr").first();
    const cells = await row.locator("td").allTextContents();
    return {
      status: cells[1],
      total: Number(cells[2]),
      completed: Number(cells[3]),
      failed: cells[4],
    };
  }
  // B16: 300_000, matching the /talent poll below - same underlying wait
  // (sequential, per-resume LLM extraction for all 4 employees) plus the
  // same bounce-recovery contingency (this poll uses reloadAndSettleOnRoute
  // via readSoleBatchRow too, so it's equally exposed): ~95s documented
  // worst case + up to 130s of possible rate-limit recovery + overhead
  // exceeds 180s, per the /talent poll's own worked accounting below.
  await expect
    .poll(
      async () => {
        tick("poll:import-batch-finished");
        const b = await readSoleBatchRow();
        // B17 Phase 0b: b.total is len(items) (knowledge_service.py:1214),
        // and items are created one per résumé job - so "completed >=
        // total" alone passed with 1 of 4 items existing (observed: the
        // import-items loop ran once against a 4-item batch). Require the
        // batch to hold one item per uploaded résumé first.
        return b.total === resumeFiles.length && b.completed + Number(b.failed) >= b.total;
      },
      {
        timeout: 300_000,
        intervals: [2_000],
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
    .poll(async () => (tick("poll:import-items-loaded"), await itemRows.count()), {
      timeout: 30_000,
      message: "waiting for the import items table to load",
    })
    .toBeGreaterThan(0);
  const itemCount = await itemRows.count();
  // B17 Phase 0b guard: one import item per uploaded résumé, so the loop
  // below examines the whole batch, not the first item to land.
  expect(itemCount, "expected one import item per uploaded résumé").toBe(resumeFiles.length);
  for (let i = 0; i < itemCount; i++) {
    tick("loop:import-items");
    const row = itemRows.nth(i);
    const approveButton = row.getByRole("button", { name: "Approve" });
    // A brand-new org has no existing employees to collide with, so
    // knowledge_service.import_resume's duplicate-resolution should take
    // the NEW branch and land each item at status="approved" directly
    // (db/models/knowledge.py's own item-status vocabulary includes
    // needs_review as a real possibility though - handled, not assumed
    // away, in case an LLM-extraction ambiguity puts one there anyway).
    if ((await approveButton.count()) > 0) {
      tick("action:import-item-approved");
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
  // B16 - this was the site that first found the Login.tsx destination-
  // loss bug (a real run landed on "/" instead of /talent, root-caused
  // against the real API container's own logs - a confirmed burst of 24
  // consecutive 429s on /auth/me - not guessed from code reading alone).
  // Throttled to a 2s reload floor below (keeps this poll's own request
  // volume comfortably under the B15 global rate limit) AND routed
  // through reloadAndSettleOnRoute (recovers if a 429 still gets through
  // and bounces this to "/" anyway - see that function's own docstring
  // and KNOWN_LIMITATIONS.md's "[B16, owner B17]" entry). Both matter: a
  // second live run, after the throttle alone, still hit a smaller-but-
  // real 3-request 429 burst and bounced the same way - one isolated 429
  // is enough, so throttling lowers the odds without closing the gap;
  // the route-recovery wrapper is what actually closes it.
  //
  // Timeout: 300_000, not 180_000 (which a fourth run already showed is
  // tight once bounce-recovery is real). Budget: ~95s documented worst
  // case for the sequential per-resume extraction itself (KNOWN_
  // LIMITATIONS.md B14; 71s measured directly one run) + up to
  // MAX_ROUTE_BOUNCE_RECOVERIES * RATE_LIMIT_RECOVERY_WAIT_MS = 130s if
  // this poll's own reloads trip the limiter + real overhead (review/
  // approve loop, navigation, page renders). 95 + 130 = 225s already
  // exceeds 180s on its own, before any render overhead - confirmed the
  // hard way, not derived in advance: the fourth live run bounced to
  // /login, and reloadAndSettleOnRoute's own (then undelayed) retries
  // produced a fresh 14-429 burst trying to recover inside a 180s budget
  // that had no room left for a real wait. 300s gives real margin over
  // 225s, not just past it.
  await expect
    .poll(
      async () => {
        tick("poll:talent-employee-links");
        await reloadAndSettleOnRoute(page, "/talent");
        return await page.locator('a[href^="/talent/employees/"]').count();
      },
      {
        timeout: 300_000,
        intervals: [2_000],
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
        tick("poll:documents-parsed");
        await reloadAndSettleOnRoute(page, `/tenders/${tenderId}`);
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
        // B16: 300_000 left unchanged, checked rather than skipped -
        // different risk category from every other timeout touched in
        // this pass, not just unmeasured-but-probably-fine. parse_and_
        // embed_document (apps/worker/jobs/ingestion.py) makes zero LLM
        // calls - confirmed directly (grepped the file) - it's local
        // chunking/embedding only. No Gemini latency variance to budget
        // for here at all.
        timeout: 300_000,
        intervals: [2_000],
        message: "waiting for every uploaded document to reach parse_status=parsed",
      },
    )
    .toEqual({ count: tenderDocs.length, allParsed: true });

  // ------------------------------------------------------------------
  // 4. Wait for extraction
  // ------------------------------------------------------------------
  // Wait for the server's answer to POST /workflow-runs before navigating
  // away: navigating cancels a request still in flight, and a cancel that
  // lands before the API has read the body creates no run, so the gate
  // below never opens (main's runs 40116721087 and 40163720306). The
  // button is not the signal: while busy its name is "Starting...", so
  // "Start processing" counts as hidden the moment it is clicked. The
  // listener is registered before the click so the response cannot be
  // missed.
  const workflowRunCreated = page.waitForResponse(
    (resp) =>
      resp.request().method() === "POST" &&
      new URL(resp.url()).pathname.endsWith(`/tenders/${tenderId}/workflow-runs`),
    { timeout: 30_000 },
  );
  await page.getByRole("button", { name: "Start processing" }).click();
  const workflowRunResponse = await workflowRunCreated;
  expect(
    workflowRunResponse.ok(),
    `POST /tenders/${tenderId}/workflow-runs answered ${workflowRunResponse.status()}`,
  ).toBe(true);
  // No route returns "extraction is done" as a boolean - the observable
  // signal this scenario uses is the requirement_review gate opening.
  // B17 Phase 0: waits for the gate panel's Approve button to be PRESENT
  // (waitForGateToOpen, like every other gate in this file), not for the
  // "No open requirement review gate" text to be ABSENT - absence also holds
  // for a page that has not rendered at all, which would pass at zero.
  // Real LLM-driven extraction across 5 documents, not a fixed
  // guess at duration - generous timeout, polled via reload since this
  // screen fetches once on mount with no auto-refresh.
  //
  // B16: bumped 300_000 -> 450_000. Extraction batches multiple
  // requirements per call (MAX_EXTRACTION_BATCH_TOKENS), so real call
  // volume here is well below compliance's 94-98 individual calls below -
  // but no direct measurement exists for THIS step either, "generous"
  // above was never checked against a real number, and this is the one
  // gate that everything downstream depends on: a false timeout here
  // loses the whole run before any of the expensive per-requirement work
  // even starts. Moderate bump, not compliance's 2x, matching the smaller
  // (but still real and unmeasured) exposure.
  await waitForGateToOpen(
    page,
    `/tenders/${tenderId}/requirements`,
    "Requirement review gate",
    450_000,
  );

  // ------------------------------------------------------------------
  // 5. Approve requirements - every row shown, not one (consistent with
  // the same "act on everything rendered" principle applied at steps
  // 6/7/12 below, for the same reason: a scripted "just one" leaves the
  // step-13 outcome dependent on how many requirements exist).
  // ------------------------------------------------------------------
  await reloadAndSettle(page);
  const reqRows = page.locator("table").first().locator("tbody tr");
  // B17 Phase 0 guard: RequirementRegister initialises its list to [], so
  // a count taken before the fetch lands is 0 and the confirm loop below
  // would be skipped, passing vacuously. Wait on the data condition first.
  await expect
    .poll(async () => (tick("poll:guard-requirement-rows"), await reqRows.count()), {
      timeout: 30_000,
      message: "expected the requirement register to list at least one requirement",
    })
    .toBeGreaterThan(0);
  const reqCount = await reqRows.count();
  for (let i = 0; i < reqCount; i++) {
    tick("loop:requirements");
    const row = reqRows.nth(i);
    const confirmButton = row.getByRole("button", { name: "Confirm" });
    if ((await confirmButton.count()) > 0) {
      tick("action:requirement-confirmed");
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
    // decideGate is async (GateDecisionPanel's handleDecide); its own
    // onDecided callback is what makes the Approve button disappear
    // (gate.status flips off "pending"). Waiting for that before the
    // next page.goto() below is the same fix as the resume-upload race
    // above - a goto fired while the decision POST is still in flight
    // would cancel it before it reaches the server.
    await approveGate(reqGate);
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
  // B16: 600_000, not the original 300_000. This gate waits on
  // compliance_analysis_node running propose_compliance once per real
  // requirement - this exact fixture measured at 94-98 individual calls
  // in this session's own citation-correctness investigation
  // (KNOWN_LIMITATIONS.md), ~24x the 4-call resume-extraction step the
  // /talent poll above now budgets 180s for. No direct wall-clock
  // measurement exists for this step specifically (unlike the /talent
  // poll's 71s) - this value is order-of-magnitude reasoning from real
  // call-volume, not a repeat of the same precise evidence, stated as
  // such rather than dressed up as measured. The asymmetry justifies
  // erring high regardless: this is the most expensive step in the whole
  // run to fail past (94-98 already-spent calls sunk if this times out
  // short), and timeout headroom costs nothing until it fires.
  await waitForGateToOpen(
    page,
    `/tenders/${tenderId}/compliance`,
    "Compliance review gate",
    600_000,
  );
  const complianceRows = page.locator("table").first().locator("tbody tr");
  // B17 Phase 0 guard: the gate opening (waitForGateToOpen) does not mean
  // ComplianceMatrix's separately fetched entry list has landed - it starts
  // as [], and a count of 0 would skip the approve/override loop below.
  await expect
    .poll(async () => (tick("poll:guard-compliance-rows"), await complianceRows.count()), {
      timeout: 30_000,
      message: "expected the compliance matrix to list at least one entry",
    })
    .toBeGreaterThan(0);
  const complianceCount = await complianceRows.count();
  const overriddenEntries: string[] = [];
  const alreadyAffirmativeEntries: string[] = [];
  for (let i = 0; i < complianceCount; i++) {
    tick("loop:compliance-entries");
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
        tick("action:compliance-approved");
        await approveButton.click();
        await expect(approveButton).toBeHidden({ timeout: 15_000 });
      }
    } else {
      overriddenEntries.push(`${requirementId} (was ${currentStatus})`);
      tick("action:compliance-overridden");
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
    await approveGate(complianceGate);
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
  // B16: 300_000 left unchanged, checked rather than skipped. Lower real
  // exposure than the compliance gate above - talent_analysis_node scores
  // every role against every eligible pool candidate as a cross-product
  // (orchestration/nodes/talent.py), which for this fixture's real scale
  // (5 roles, at most 4 eligible candidates) is at most ~20 real LLM
  // calls, not compliance's 94-98. Existing margin plausible; still
  // unmeasured, same caveat as extraction above.
  await waitForGateToOpen(page, `/tenders/${tenderId}/team`, "Resource selection gate", 300_000);
  const roleRows = page.locator("table").first().locator("tbody tr");
  // B17 Phase 0 guard: same shape as the compliance one above - the gate
  // being open does not mean TeamWorkspace's role list (initialised to [])
  // has loaded, and a count of 0 would skip candidate selection entirely.
  await expect
    .poll(async () => (tick("poll:guard-role-rows"), await roleRows.count()), {
      timeout: 30_000,
      message: "expected the team workspace to list at least one role",
    })
    .toBeGreaterThan(0);
  const roleCount = await roleRows.count();
  const rolesWithNoCandidate: string[] = [];
  for (let i = 0; i < roleCount; i++) {
    tick("loop:roles");
    const roleRow = page.locator("table").first().locator("tbody tr").nth(i);
    const roleName = (await roleRow.locator("td").first().textContent()) ?? `role-${i}`;
    // handleSelectRole's own listMatches(roleId) call is async, and
    // TeamWorkspace.tsx doesn't clear the previous role's matches before
    // it resolves (see KNOWN_LIMITATIONS.md) - a bare read right after
    // click() can observe the PREVIOUS role's candidates, or select a
    // candidate for the wrong role. The section shows no role name or
    // busy indicator to poll for instead.
    //
    // B16: this used to be a bare await page.waitForLoadState("networkidle")
    // AFTER the click, on the theory that it "actually confirms this
    // role's own fetch has landed" - it does not. Found live, root-caused
    // with a real trace, not assumed: click-to-check gaps of 10-40ms,
    // real GET /roles/{id}/matches responses (200, real bodies, matching
    // role_matches already 5+ minutes old in Postgres) that hadn't been
    // applied to React state yet by the time the very next check ran, for
    // all 4 roles in one run. networkidle observes the BROWSER's network
    // layer, not React's own post-fetch re-render commit - it can resolve
    // before setMatches() has run, especially this late into a long test
    // when there's little OTHER network traffic to make "idle" meaningful.
    // Same class of gap as this file's own "page.reload() does not wait
    // for React's own data-fetching effects" and "premature navigation
    // cancelled in-flight requests" findings - waiting on the actual
    // response this click triggers, registered BEFORE the click so the
    // response can't land in the gap between click() and the wait
    // starting, closes it directly instead of hoping a proxy signal lines
    // up.
    const [matchesResponse] = await Promise.all([
      page.waitForResponse(
        (resp) => resp.request().method() === "GET" && /\/roles\/[^/]+\/matches$/.test(resp.url()),
      ),
      roleRow.getByRole("button", { name: "View candidates" }).click(),
    ]);
    const candidateSection = page.locator("section", {
      has: page.getByRole("heading", { name: "Candidates" }),
    });
    // waitForResponse above only guarantees the response was RECEIVED, not
    // that handleSelectRole's own setMatches(result) has committed a React
    // re-render yet - a real, separate gap one layer up from the network
    // one just closed. "No candidates found for this role yet." is TRUE in
    // the DOM from the moment of the click (matches starts as []).
    //
    // B17 Phase 0: "how many candidates does this role have" is now read
    // from the response this click triggered, not inferred from a timing
    // window. It used to wait a fixed 3 s for rows and, if none rendered in
    // time, record the role as having no candidates - a false-empty decided
    // by timing (same bug shape as the networkidle finding above). Now: an
    // empty body is the only way a role counts as having no candidates;
    // otherwise wait for exactly that many rows AND for the first row to
    // show this response's first employee id (TeamWorkspace does not clear
    // the previous role's matches, so a count alone could match a stale
    // render of an earlier role with the same number of candidates).
    expect(matchesResponse.status(), "GET /roles/{id}/matches should return 200").toBe(200);
    const matchesBody = (await matchesResponse.json()) as { employee_knowledge_item_id: string }[];
    if (matchesBody.length === 0) {
      rolesWithNoCandidate.push(roleName);
      continue;
    }
    await expect(candidateSection.locator("tbody tr")).toHaveCount(matchesBody.length, {
      timeout: 15_000,
    });
    await expect(candidateSection.locator("tbody tr").first().locator("td").first()).toHaveText(
      matchesBody[0].employee_knowledge_item_id,
      { timeout: 15_000 },
    );
    // Scoped to the first candidate ROW, not a global "first Select
    // button" locator - every candidate for this role starts as
    // "Select" (talent_analysis_node scores the whole pool per role, so
    // several rows can carry that name at once), and a role+name
    // locator re-resolves live: if it weren't row-scoped, waiting for
    // it to hide after clicking would just re-point at the NEXT
    // still-unselected candidate's button instead of confirming this
    // click's own effect.
    const firstCandidateRow = candidateSection.locator("tbody tr").first();
    // B16: exact: true - a live run found getByRole's default name match is
    // a substring match, and "Deselect" contains "Select". Without exact,
    // this locator kept matching the SAME button after it correctly
    // flipped to "Deselect" on a real, successful selection (confirmed
    // directly against role_matches.human_selected and the real
    // audit_events row for this exact selection - the PATCH worked; only
    // the assertion below was checking the wrong thing), so toBeHidden()
    // could never become true and the test timed out waiting for
    // something that was never going to happen.
    const firstSelectButton = firstCandidateRow.getByRole("button", {
      name: "Select",
      exact: true,
    });
    tick("action:candidate-selected");
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
  // B17 Phase 0 guard: a role with no candidate is a legitimate outcome, but
  // EVERY role ending up there means step 7 selected nothing and passed at
  // zero (e.g. each role's candidate list missing its 3 s wait above).
  expect(
    rolesWithNoCandidate.length,
    `expected at least one of ${roleCount} role(s) to get a candidate selected; ` +
      `none did: ${JSON.stringify(rolesWithNoCandidate)}`,
  ).toBeLessThan(roleCount);
  {
    const resourceGate = gatePanel(page, "Resource selection gate");
    await approveGate(resourceGate);
  }

  // ------------------------------------------------------------------
  // 8. Inspect pricing finding - inspection only, per step 12's own
  // decision below: nothing here gets resolved yet.
  // ------------------------------------------------------------------
  // B16: 300_000 left unchanged, checked rather than skipped. Commercial
  // validation is largely deterministic (pricing_checks.py's arithmetic
  // checks, B8B) - the only LLM exposure is explain_findings, at most one
  // call per pricing document (2 in this fixture). Lowest real LLM
  // exposure of the three gates in this run; existing margin is
  // comfortable, not just assumed.
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
    // Live progress, moved here from step 11 (KNOWN_LIMITATIONS, "E2E step
    // 11 depends on the workflow still running"). A second tab on the
    // Upload/Processing screen, settled before the approval: its own first
    // GET .../tasks has answered and both of its event streams (tasks and
    // run) have been requested. The tab refetches tasks only after minting a
    // ticket, on a stream message, or on a reconnect (useEventStream.ts), and
    // the server sends heartbeats as SSE comments, which raise no message. So
    // a tasks request after this point, with no new stream request before
    // it, can only come from an event pushed to the open stream. Approving
    // the commercial gate resumes the workflow (the revision loop that once
    // added findings no open screen showed), which writes
    // WorkflowRunStatusChanged and task events; the manual red team writes
    // none, which is why this no longer waits on the red-team click.
    const liveProgressTab = await page.context().newPage();
    const isTasksRequest = (req: Request) =>
      req.method() === "GET" && /\/workflow-runs\/[^/]+\/tasks$/.test(req.url());
    // Every /tasks request the live tab sends before the approval request
    // goes out, its own after-connect refetch included, is excluded: only a
    // request that starts after the approval can count as the push.
    const tasksBeforeApproval = new Set<Request>();
    let approvalSent = false;
    let liveTabStreamRequests = 0;
    let streamRequestsWhenRefetchStarted: number | null = null;
    const onApprovalRequest = (req: Request) => {
      if (req.method() === "POST" && /\/gates\/[^/]+\/decision$/.test(req.url())) {
        approvalSent = true;
      }
    };
    page.on("request", onApprovalRequest);
    liveProgressTab.on("request", (req) => {
      if (/\/workflow-runs\/[^/]+\/events\/stream/.test(req.url())) liveTabStreamRequests += 1;
      if (!isTasksRequest(req)) return;
      if (!approvalSent) {
        tasksBeforeApproval.add(req);
      } else if (streamRequestsWhenRefetchStarted === null) {
        streamRequestsWhenRefetchStarted = liveTabStreamRequests;
      }
    });
    const liveTabFirstTasks = liveProgressTab.waitForResponse(
      (resp) => isTasksRequest(resp.request()),
      { timeout: 30_000 },
    );
    await liveProgressTab.goto(`/tenders/${tenderId}`);
    await liveTabFirstTasks;
    await expect
      .poll(() => liveTabStreamRequests, {
        timeout: 30_000,
        message: "waiting for the live tab to open its tasks and run event streams",
      })
      .toBeGreaterThanOrEqual(2);
    const streamRequestsBeforeApproval = liveTabStreamRequests;
    // A response, not just a request: the tab is closed right after, and a
    // request cancelled by that close would leave no trace on the server.
    const liveTaskListRefetch = liveProgressTab.waitForResponse(
      (resp) => isTasksRequest(resp.request()) && !tasksBeforeApproval.has(resp.request()),
      { timeout: 60_000 },
    );

    const commercialGate = gatePanel(page, "Commercial approval gate");
    await approveGate(commercialGate);

    // Fails (times out), not passes, if no pushed event reaches the open tab.
    const pushedRefetch = await liveTaskListRefetch;
    page.off("request", onApprovalRequest);
    expect(pushedRefetch.ok(), `the live tab's refetch answered ${pushedRefetch.status()}`).toBe(
      true,
    );
    expect(
      streamRequestsWhenRefetchStarted,
      "the live tab reconnected its stream before refetching, so the refetch may not be a push",
    ).toBe(streamRequestsBeforeApproval);
    await liveProgressTab.close();
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
  // B16: bumped 180_000 -> 300_000 during the same timeout audit that
  // fixed the /talent and compliance-gate polls. This step drafts the
  // exact claims the citation-correctness instrument (propose.py/
  // draft_section.py's input_refs/output_refs, this session) measures -
  // still no direct wall-clock measurement exists, same honest caveat the
  // comment above already carried, but leaving the most consequential
  // unmeasured step in the run at an admitted guess didn't survive this
  // pass once the others got real numbers.
  await expect(page.getByRole("button", { name: "Generate proposal" })).toBeEnabled({
    timeout: 300_000,
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
  // 11. Run red team, plus B14's own no-reload live-progress assertion.
  //
  // This reproduces a real, previously-observed failure, not a
  // hypothetical: a prior live run of this exact scenario cleared the
  // red-team screen, then 3 new findings appeared 10s later from the
  // bounded revision loop (targeted_replan -> proposal_drafting ->
  // project_graph -> red_team) still running in the background with zero
  // human interrupt() in between - nothing told the reviewer watching it
  // (KNOWN_LIMITATIONS.md, "[B13, owner B14] Every screen in this UI is a
  // snapshot"). §5a of this block's own plan names that finding as B14's
  // concrete justification for building SSE at all.
  //
  // A second tab left open on the Upload/Processing screen
  // (UploadAndProcessingStatus.tsx, useEventStream's one real consumer)
  // is the direct test: trigger red team on the FIRST tab, then assert -
  // on the SECOND tab, with zero page.reload()/reloadAndSettle() calls
  // on it from this point forward - that a genuinely new network request
  // actually fires, driven by the live tab's own SSE connection noticing
  // one of db/repositories/workflow.py's 4 choke-point writes (red_team_
  // node's own sync_run_status call, at minimum). Asserting on a real,
  // falsifiable signal (a new request arriving) rather than "the tab
  // didn't error" or "some text looked different" - a rendered value
  // that happens not to visibly change (e.g. the coarse run status
  // staying "running" across the whole revision loop) would make a DOM-
  // content assertion here pass for the wrong reason, the same
  // unfalsifiable shape this project has been burned by before.
  // ------------------------------------------------------------------
  // The live-progress half of this step now runs at step 8's commercial
  // approval (above), where a workflow event is certain; see there.
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
    // B17 Phase 0 guard: step 8 already asserted unresolved pricing findings
    // exist, and nothing resolves them before here, so 0 Resolve buttons
    // means the list (initialised to []) has not loaded - not "nothing to
    // do". Without this the loop below is skipped and step 12 passes vacuously.
    await expect
      .poll(async () => (tick("poll:guard-resolve-buttons"), await resolveButtons.count()), {
        timeout: 30_000,
        message: "expected unresolved pricing findings (step 8 saw them) to be listed",
      })
      .toBeGreaterThan(0);
    let remaining = await resolveButtons.count();
    while (remaining > 0) {
      tick("loop+action:finding-resolved");
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
    // B17 Phase 0 guard: RedteamFindings initialises its list to [] and
    // shows "No findings." until the fetch lands. Step 11's run produces
    // deterministic pricing_mismatch findings on this fixture (8 of them,
    // plus 1 unsupported_claim, in the 2026-10-04 fake run), so 0 here
    // means not loaded yet; without this the loop would be skipped.
    await expect
      .poll(async () => (tick("poll:guard-resolve-buttons"), await resolveButtons.count()), {
        timeout: 30_000,
        message: "expected open red-team findings from step 11's run to be listed",
      })
      .toBeGreaterThan(0);
    let remaining = await resolveButtons.count();
    while (remaining > 0) {
      tick("loop+action:finding-resolved");
      await resolveButtons.first().click();
      await expect(resolveButtons).toHaveCount(remaining - 1, { timeout: 10_000 });
      resolveButtons = page.getByRole("button", { name: "Resolve" });
      remaining = await resolveButtons.count();
    }
  }

  // ------------------------------------------------------------------
  // §1's added 14th step: knowledge-selection deselection and B14's own
  // reanalysis-cascade assertions - Part J's own scenario for this block
  // (TenderOS-Build-Guide.md:3554-3565): deselect -> impact preview ->
  // confirm -> projection refresh -> evidence coverage=0 -> compliance
  // task reopens -> claim becomes needs_review -> readiness blocker
  // appears.
  //
  // A completed graph projection already exists by this point in the
  // run - confirmed directly against real Postgres data from a prior
  // live run of this exact scenario, not assumed (KNOWN_LIMITATIONS.md's
  // B14 §1 placement finding: graph_projection_runs had 3 completed rows,
  // all well before this step's own position in the flow). The 409
  // GRAPH_STALE this step's own project-then-retry path exercises
  // reflects ordinary one-version staleness - the tender's state has
  // moved past the last completed projection's version - not "no
  // projection has ever run," which was this comment's own earlier,
  // incorrect claim.
  //
  // Which item to deselect is discovered, not hardcoded, and requires
  // SELECTING first - corrected across three live runs, not assumed
  // correct from reading code alone. preview_impact's own Cypher hard-
  // requires a USED_IN edge as its literal first match clause
  // (graph_projection/queries.py:63-65: MATCH (fact)-[u:USED_IN]->
  // (:Tender)), and the projector only ever creates USED_IN from
  // tender_knowledge_selections rows (graph_projection/projector.py:
  // 400-412) - never from evidence_links directly, even though B7's own
  // compliance evidence retrieval is deliberately NOT gated by selections
  // (compliance_service.py's own comment: "retrieves evidence straight
  // from the org's knowledge base with no prior 'select knowledge for
  // this tender' step"). Nothing before this step ever calls the
  // selection PUT, so every row here starts with no USED_IN edge - a
  // passive preview-only discovery loop (runs 2 and 3's own approach)
  // can never show real impact for an item nobody has ever selected,
  // regardless of what it actually backs in Postgres. Confirmed live,
  // not assumed: run 3's loop found zero qualifying rows even after
  // relaxing its own criterion to "names a Requirement alone."
  //
  // Also corrected: an earlier version targeted a specific certification
  // name ("Tax Clearance Certificate") observed grounded in two
  // independent past live runs (B9, B12) - checked directly against this
  // spec's own actual data before trusting that again, and it doesn't
  // hold: this spec's own setup phase imports resumes only, never
  // certifications.xlsx, so no certification knowledge item exists here
  // at all (KNOWN_LIMITATIONS.md's own corrected entry). The 4 imported
  // employees are the only real candidates, with no empirical precedent
  // either way for whether any of them backs a claim that actually cites
  // its evidence, given this project's own well-documented low grounding
  // rate (historically ~1 in 10 claims cite real evidence).
  //
  // So: select every employee once (creating each one's own USED_IN edge
  // and triggering one real reanalysis pass each - a bounded, known
  // cost, not an open-ended loop), record what each one's impact preview
  // actually shows, and use the first one that names a real Requirement
  // as the deselection target. If none does, fail here, loudly, with
  // what was actually observed attached - asserting a cascade against an
  // item with no real downstream effect would be the same unfalsifiable
  // shape this file's own reload-race and readiness-assumption bugs
  // already were.
  //
  // A claim being graph-connected to the SAME item is not required
  // (corrected during run 1's own failure): enforce_claim_grounding has
  // two separate downgrade paths (proposal_service.py) - a claim whose
  // OWN cited evidence disappears becomes unsupported, not needs_review;
  // needs_review is the OTHER path, firing for any compliance-type claim
  // citing a requirement whose compliance status just turned non-
  // affirmative, regardless of whether that claim is itself graph-
  // connected to the deselected item. The needs_review assertion below
  // is intentionally count-based across the whole proposal, not tied to
  // this specific item, for exactly this reason.
  // ------------------------------------------------------------------
  await page.goto(`/tenders/${tenderId}/knowledge-selections`);
  await expect
    .poll(
      async () => {
        tick("poll:selection-rows");
        await reloadAndSettleOnRoute(page, `/tenders/${tenderId}/knowledge-selections`);
        return await page.locator("table").first().locator("tbody tr").count();
      },
      {
        // B16: bumped 30_000 -> 180_000. Found during the recovery-wait
        // audit, not from an observed failure here specifically: this poll
        // uses reloadAndSettleOnRoute (same bounce exposure as every other
        // site), and MAX_ROUTE_BOUNCE_RECOVERIES * RATE_LIMIT_RECOVERY_
        // WAIT_MS alone is 130s - more than 4x the old 30s budget. A single
        // bounce here would have failed this poll immediately regardless of
        // whether the underlying rows were ever going to appear. Real work
        // itself should be fast (the import happened well before this
        // point), so 180s is generous runway around a small real-work
        // component, not a claim that the real work itself takes anywhere
        // near that long.
        timeout: 180_000,
        intervals: [2_000],
        message: "waiting for the imported knowledge items to appear as selectable rows",
      },
    )
    .toBeGreaterThan(0);

  const selectionRows = page.locator("table").first().locator("tbody tr");
  const selectionRowCount = await selectionRows.count();
  const candidateFindings: { title: string; labels: string[] }[] = [];
  // B17 Phase 0, renamed from affectedRequirementId: this is the id of the
  // first Requirement seen in ANY employee's post-select preview, used only
  // by the existence assertion after this loop. It is not the requirement
  // the deselection target affects - the target is chosen separately below
  // (Requirement AND Compliance) and may be a different employee.
  let firstPreviewRequirementId: string | null = null;

  for (let i = 0; i < selectionRowCount; i++) {
    tick("loop:knowledge-selection-rows");
    const row = selectionRows.nth(i);
    const title = (await row.locator("td").first().textContent())?.trim() ?? `row-${i}`;

    // Not yet selected - previewing here is expected to show nothing
    // (no USED_IN edge exists yet), which is why this loop doesn't
    // bother reading it: the real read happens after selecting, below.
    await row.getByRole("button", { name: "Preview impact" }).click();
    await expect(page.getByRole("heading", { name: "Impact preview" })).toBeVisible({
      timeout: 30_000,
    });
    const preSelectConfirm = page.getByRole("button", { name: "Confirm select" });
    await expect(preSelectConfirm).toBeVisible({ timeout: 30_000 });
    await preSelectConfirm.click();
    await expect(page.getByRole("heading", { name: "Impact preview" })).toBeHidden({
      timeout: 10_000,
    });

    // Now selected - re-preview the same row. KnowledgeSelections.tsx's
    // own handlePreview already retries once through a real project-
    // then-retry cycle on 409 GRAPH_STALE (Part I4), so this reflects
    // the just-created USED_IN edge without this test needing to wait
    // for this row's own background reanalysis job separately - that
    // job's real effect (compliance reopening, claim regrounding) is
    // what the downstream assertions below poll for, generously, after
    // the real deselection.
    // The poll below counts every <li> in <main> as this preview's rows, so
    // none may exist yet: the confirm above closed the previous panel.
    await expect(page.locator("main li")).toHaveCount(0);
    await row.getByRole("button", { name: "Preview impact" }).click();
    await expect(page.getByRole("heading", { name: "Impact preview" })).toBeVisible({
      timeout: 30_000,
    });
    // B16: 60_000 left unchanged, checked rather than skipped. This poll
    // has no reloadAndSettle call at all (no fresh mount, no /auth/me re-
    // fire risk) and no direct LLM exposure either - preview_impact reads
    // Neo4j directly, and the 409-retry cycle handlePreview's own comment
    // above describes is a graph reprojection (project_tender), not a
    // Gemini call. Different risk category from the gates above.
    await expect
      .poll(
        async () =>
          (tick("poll:impact-preview-rendered"), await page.locator("main li").count()) +
          (await page.getByText("No downstream facts would be affected.").count()),
        { timeout: 60_000 },
      )
      .toBeGreaterThan(0);
    const impactTexts = await page.locator("main ul li").allTextContents();
    const labels = impactTexts.flatMap((t) => {
      try {
        return (JSON.parse(t) as { labels?: string[] }).labels ?? [];
      } catch {
        return [];
      }
    });
    candidateFindings.push({ title, labels: [...new Set(labels)] });

    if (firstPreviewRequirementId === null) {
      const requirementRowText = impactTexts.find((t) => t.includes('"Requirement"'));
      if (requirementRowText) {
        const parsed = JSON.parse(requirementRowText) as { affected?: { id?: string } };
        firstPreviewRequirementId = parsed.affected?.id ?? null;
      }
    }

    // Close this row's preview panel without deselecting (Confirm select
    // already fired above; the target is chosen and deselected in a
    // separate pass below, once every candidate's own findings are in) -
    // clicking the NEXT row's own "Preview impact" button does this
    // naturally (handlePreview unconditionally resets previewedItemId),
    // so nothing extra is needed here except when this is the last row.
  }

  // The durable output of this discovery pass, per the plan: which of
  // the 4 real employees actually shows real Requirement/Claim impact on
  // this fixture - attached regardless of outcome, not just on failure,
  // so a future run can target directly instead of re-discovering this.
  await test.info().attach("step-14-knowledge-item-impact-findings", {
    body: JSON.stringify(candidateFindings, null, 2),
    contentType: "application/json",
  });
  expect(
    firstPreviewRequirementId,
    "expected at least one of the 4 imported employees to show a real Requirement in its " +
      `impact preview after being selected - none did. Findings: ${JSON.stringify(candidateFindings, null, 2)}`,
  ).not.toBeNull();

  // The real tested action: deselect the chosen candidate (already
  // selected above). Every other candidate is left selected - deselecting
  // them back would cost another real reanalysis pass each for no
  // assertion this step makes, and nothing downstream depends on their
  // selection state.
  // Deselect an employee whose impact runs through EVIDENCE (Compliance), so
  // the D6 category asserted below is the one this deselection produces. A
  // role-filler with no evidence (FILLS -> Role -> SATISFIES -> Requirement)
  // correctly yields removed_employee_committed instead (B17 Phase 0, run 5:
  // the old Requirement-only choice depended on table order once roles
  // existed - KNOWN_LIMITATIONS.md, fake-client entry, harness error 7).
  const targetTitle = candidateFindings.find(
    (f) => f.labels.includes("Requirement") && f.labels.includes("Compliance"),
  )?.title;
  expect(
    targetTitle,
    "expected an employee whose impact preview includes Compliance",
  ).toBeDefined();
  const chosenRow = page
    .locator("table")
    .first()
    .locator("tbody tr")
    .filter({ has: page.locator("td", { hasText: targetTitle ?? "" }) })
    .first();
  await chosenRow.getByRole("button", { name: "Preview impact" }).click();
  const confirmButton = page.getByRole("button", { name: /^Confirm (select|deselect)$/ });
  await expect(confirmButton).toBeVisible({ timeout: 30_000 });
  await expect(confirmButton).toHaveText("Confirm deselect", { timeout: 10_000 });
  await confirmButton.click();
  await expect(page.getByRole("heading", { name: "Impact preview" })).toBeHidden({
    timeout: 10_000,
  });

  // B16: this used to poll for the compliance entry's human_approved to
  // flip to "no" after the deselection above. It never can, and that's
  // not a bug - do not "fix" this back without reading the investigation
  // it's the result of (KNOWN_LIMITATIONS.md's "USED_IN" entry, B16
  // addendum). compliance_service.py's own B7 comment ("selected_only is
  // deliberately not used here") was a build-sequencing decision made
  // before tender_knowledge_selections existed, not a permanent statement
  // that compliance ignores selection - but the Build Guide's D6 section
  // independently settles it regardless of that comment's intent:
  // "Deselected fact still in use" (USED_IN.selected=false AND fact still
  // provides evidence that supports a compliance decision) is a state D6
  // explicitly anticipates and requires a DETECTOR for
  // (find_deselected_fact_still_used_contradictions,
  // graph_projection/queries.py) - not an automatic reversal.
  // reanalyze_knowledge_selection_change's own docstring is explicit that
  // step (3) only clears human_approved "when the reevaluated status
  // differs" - re-evaluating after this deselection finds the SAME
  // evidence (compliance retrieval was never gated by selection) and
  // correctly persists the SAME status, so nothing about step (3) alone
  // was ever going to make this assertion pass, confirmed status quo, not
  // a defect.
  //
  // What the design actually promises, and what's now wired in (B16):
  // reanalyze_knowledge_selection_change's own step (5) runs
  // check_graph_contradictions after every selection change and persists
  // any drafts via the same upsert-by-fingerprint path run_red_team uses -
  // proven with a dedicated service-level integration test
  // (test_reanalysis_job.py::test_reanalyze_knowledge_selection_change_
  // surfaces_deselected_fact_still_used_finding, FakeLLMClient, no LLM
  // cost) before this assertion was ever changed to depend on it. This is
  // the full-stack proof of the same thing: the deselection above must
  // surface a real, open deselected_fact_in_use finding on the red-team
  // screen WITHOUT a fresh manual "Run red team" click (step 11 already
  // ran once, before this deselection existed to be detected) - the
  // reanalysis job's own step (5) is what has to produce it. Same
  // 300_000/reloadAndSettleOnRoute budget as the assertion this replaces,
  // for the same reason (one real LLM call plus bounce-recovery
  // overhead, not a re-guess).
  await page.goto(`/tenders/${tenderId}/redteam`);
  await expect
    .poll(
      async () => {
        tick("poll:d6-finding");
        await reloadAndSettleOnRoute(page, `/tenders/${tenderId}/redteam`);
        const rows = page.locator("table").first().locator("tbody tr");
        const count = await rows.count();
        for (let i = 0; i < count; i++) {
          tick("loop:d6-poll-rows-scanned");
          const row = rows.nth(i);
          const category = (await row.locator("td").first().textContent())?.trim();
          if (category === "deselected_fact_in_use") {
            return (await row.locator("td").nth(3).textContent())?.trim();
          }
        }
        return null;
      },
      {
        timeout: 300_000,
        intervals: [2_000],
        message:
          "waiting for a deselected_fact_in_use finding to surface on the red-team screen " +
          "(D6's own answer to a deselection that doesn't change the compliance verdict it " +
          "backs - see the comment above)",
      },
    )
    .toBe("no");

  // B17 Phase 0: capture every open deselected_fact_in_use finding's
  // severity (RedteamFindings.tsx renders it in column 2), for step 13's
  // severity-aware readiness assertion. graph_service.py:387-411 grades it
  // critical only when, ignoring the flagged evidence, no valid path remains
  // (D7 compliance_re_evaluation_required); otherwise major. readiness_
  // service.py:237-245 blocks only on open CRITICAL red-team findings. The
  // page is still on /redteam from the poll above.
  const d6Rows = page
    .locator("table")
    .first()
    .locator("tbody tr")
    .filter({ has: page.locator("td", { hasText: /^deselected_fact_in_use$/ }) });
  const d6Severities = (await d6Rows.locator("td:nth-child(2)").allTextContents()).map((s) =>
    s.trim(),
  );
  expect(d6Severities.length, "expected the open D6 finding rows just polled for").toBeGreaterThan(
    0,
  );
  const d6AnyCritical = d6Severities.includes("critical");
  await test.info().attach("step-14-d6-severities", {
    body: JSON.stringify({ d6Severities, d6AnyCritical }, null, 2),
    contentType: "text/plain",
  });

  // B17 Phase 0: the needs_review assertion that stood here (B14) was
  // unreachable in every run, real or fake, for two independent reasons,
  // both checked directly (KNOWN_LIMITATIONS.md, "B14 scenario lines
  // 3562-3563 are not demonstrable by design"):
  //  - its after-count read /compliance, which has rendered no claim <li>
  //    in any version (claims render only in ProposalWorkspace.tsx), so the
  //    count was always 0 against a /proposal before-count;
  //  - B16 made a deselection deliberately never reopen compliance
  //    (reanalysis.py's docstring, scenario 15), and a claim only turns
  //    needs_review when a cited requirement's compliance turns non-
  //    affirmative (proposal_service.enforce_claim_grounding).
  // D6's deselected_fact_in_use finding, asserted just above, is the
  // designed signal for a deselection, and stays.

  // ------------------------------------------------------------------
  // 13. Inspect readiness - flipped for B14, deliberately, not a
  // regression. Steps 5/6/7/12 leave the tender clean enough that this
  // check would have read "Ready" before the 14th step ran (that was
  // this file's own pre-B14 assertion) - but the 14th step's own
  // deselection just reopened a compliance decision and put a claim back
  // to needs_review on purpose, so the honest final state of this whole
  // scenario is now "not ready, with a real blocker," matching Part J's
  // own scenario text ending on "readiness blocker appears," not
  // "ready: true." The blockers list is always attached as an artifact
  // (not just on failure) so the specific category reached is visible
  // in the run's own output either way.
  // ------------------------------------------------------------------
  await page.goto(`/tenders/${tenderId}/readiness`);
  await page.getByRole("button", { name: "Generate readiness report" }).click();
  await expect(page.locator("main strong")).toBeVisible({ timeout: 30_000 });
  const readyText = (await page.locator("main strong").first().textContent())?.trim();
  const blockers = await page
    .locator("h2", { hasText: "Blockers" })
    .locator("xpath=following-sibling::ul[1]/li")
    .allTextContents();
  await test.info().attach("step-13-readiness-blockers", {
    body: JSON.stringify({ readyText, blockers }, null, 2),
    contentType: "application/json",
  });
  expect(
    readyText,
    `Expected readiness NOT "Ready" (B14's own deselection reopened a compliance decision ` +
      `and reground a claim) - got "${readyText}" with zero blockers, meaning the cascade's ` +
      `own effects never reached readiness_service. Blockers: ${JSON.stringify(blockers, null, 2)}`,
  ).not.toBe("Ready");
  expect(
    blockers.length,
    "expected at least one real blocker attached to the non-ready result",
  ).toBeGreaterThan(0);

  // B17 Phase 0: B14 scenario :3564 ("readiness blocker appears"), checked
  // against what D6/D7 actually decided for step 14's deselection, not
  // assumed. Blocker text is readiness_service.py:245's own f-string.
  // Critical (no valid path left): the finding must block readiness.
  // Not critical (valid_path_count > 0 - D7 review_optional): it must NOT
  // appear as a blocker; readiness blocks only on open critical findings.
  const d6Blocker = blockers.some((b) =>
    b.includes("Unresolved critical red-team finding: deselected_fact_in_use"),
  );
  if (d6AnyCritical) {
    expect(
      d6Blocker,
      `step 14's D6 finding was critical (${JSON.stringify(d6Severities)}), so it must appear ` +
        `as a readiness blocker. Blockers: ${JSON.stringify(blockers, null, 2)}`,
    ).toBe(true);
  } else {
    expect(
      d6Blocker,
      `step 14's D6 finding was not critical (${JSON.stringify(d6Severities)}), so it must ` +
        `not appear as a readiness blocker. Blockers: ${JSON.stringify(blockers, null, 2)}`,
    ).toBe(false);
  }
});

// B17 Phase 1 verification artifact - see reloadAndSettleOnRoute's own
// comment above. A hook, not an inline attach at the test's own end,
// specifically so this still reports if the run fails on an unrelated step
// partway through - exactly the case where "did Login.tsx's fix even get
// exercised before this failed" is worth knowing, not just on a clean pass.
// {} below is deliberate, not accidental: Playwright's own fixture-
// injection requires an object DESTRUCTURING PATTERN as the first argument
// to statically detect which fixtures a hook uses (confirmed directly - a
// plain named parameter here fails at test-collection time with "First
// argument must use the object destructuring pattern"); {} is correct for
// "this hook uses no fixtures."
// eslint-disable-next-line no-empty-pattern
test.afterEach(async ({}, testInfo) => {
  await testInfo.attach("loop-and-poll-iterations", {
    body: JSON.stringify(iterations, null, 2),
    contentType: "text/plain",
  });
  const loginLandings = routeBounceRecoveries.filter((r) => r.loginLanding);
  await testInfo.attach("route-bounce-recoveries", {
    body: JSON.stringify(
      {
        total: routeBounceRecoveries.length,
        loginLandings: loginLandings.length,
        otherMismatches: routeBounceRecoveries.length - loginLandings.length,
        recoveries: routeBounceRecoveries,
      },
      null,
      2,
    ),
    // text/plain, not application/json: the list reporter prints only
    // text/plain attachments inline, so this lands in every run's console
    // output (and so in the report), not only inside the HTML report.
    contentType: "text/plain",
  });
});
