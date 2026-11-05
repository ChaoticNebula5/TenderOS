---
name: TenderOS operator app
description: Design system for apps/web, the working bid-operations UI, in the landing's Custody look, tuned for dense daily use.
colors:
  surface: "#15110D"
  surface-sunk: "#100D09"
  surface-raise: "#221B14"
  ink: "#F3ECE1"
  ink-2: "#BCB0A0"
  ink-3: "#948979"
  accent: "#C98A47"
  on-accent: "#15110D"
  status-ready: "#7DB88A"
  status-running: "#7FA5AA"
  status-warning: "#D4CC63"
  status-blocked: "#B79BE0"
  status-failed: "#EC6B73"
typography:
  page-title:
    fontFamily: "Newsreader, Georgia, 'Times New Roman', serif"
    fontSize: "1.75rem"
    fontWeight: 300
    lineHeight: 1.15
    letterSpacing: "-0.015em"
  section-title:
    fontFamily: "Archivo, system-ui, -apple-system, 'Segoe UI', sans-serif"
    fontSize: "1.125rem"
    fontWeight: 600
    lineHeight: 1.3
  body:
    fontFamily: "Archivo, system-ui, -apple-system, 'Segoe UI', sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.5
  data:
    fontFamily: "Archivo, system-ui, -apple-system, 'Segoe UI', sans-serif"
    fontSize: "0.8125rem"
    fontWeight: 400
    lineHeight: 1.4
    fontFeature: "'tnum' 1"
  label:
    fontFamily: "Archivo, system-ui, -apple-system, 'Segoe UI', sans-serif"
    fontSize: "0.75rem"
    fontWeight: 500
    lineHeight: 1.3
rounded:
  sm: "2px"
  md: "4px"
  pill: "999px"
spacing:
  "1": "4px"
  "2": "8px"
  "3": "12px"
  "4": "16px"
  "5": "24px"
  "6": "32px"
  "7": "48px"
  "8": "64px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.on-accent}"
    rounded: "{rounded.md}"
    padding: "6px 14px"
    height: "32px"
  button-secondary:
    backgroundColor: "{colors.surface-raise}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "6px 14px"
    height: "32px"
  input:
    backgroundColor: "{colors.surface-sunk}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "6px 10px"
    height: "32px"
  # Status badges: text in the status hue on a 14% tint of that hue. The
  # backgroundColor hex is that tint composited over `surface`, rounded to
  # 8-bit (the CSS will use the translucent value; same ratio within 0.02).
  status-badge-ready:
    backgroundColor: "#24281E"
    textColor: "{colors.status-ready}"
    rounded: "{rounded.sm}"
    padding: "2px 8px"
  status-badge-running:
    backgroundColor: "#242623"
    textColor: "{colors.status-running}"
    rounded: "{rounded.sm}"
    padding: "2px 8px"
  status-badge-warning:
    backgroundColor: "#302B19"
    textColor: "{colors.status-warning}"
    rounded: "{rounded.sm}"
    padding: "2px 8px"
  status-badge-blocked:
    backgroundColor: "#2C242B"
    textColor: "{colors.status-blocked}"
    rounded: "{rounded.sm}"
    padding: "2px 8px"
  status-badge-failed:
    backgroundColor: "#331E1B"
    textColor: "{colors.status-failed}"
    rounded: "{rounded.sm}"
    padding: "2px 8px"
  # Polish pass: neutral has a tint too - --rule-2 (ink at 7%), laid
  # translucent over whatever is behind it (amends "neutral sets no
  # background"). The hex is that tint over `surface`.
  status-badge-neutral:
    backgroundColor: "#25201C"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.sm}"
    padding: "2px 8px"
---

# Design System: TenderOS operator app

> **Status: polish pass done. All six batches are built - 1 (foundation),
> 2 (shell and shared components), 3 (intake and requirements), 4
> (analysis screens), 5 (proposal, evidence and readiness) and 6 (talent) -
> and the polish pass after them (shared fixes, screen fixes, this
> consistency pass; see "Polish pass: as built"). What is left is listed
> under Deferred; anything not listed as built is still a proposal.**
> Batch 1's tokens, `@font-face`
> rules and base element styles exist (`apps/web/src/styles/tokens.css`,
> `apps/web/src/index.css`, woff2 files in `apps/web/public/fonts/`).
> Batch 2's app shell, workflow nav, shared components, status label map and
> formatters exist (`apps/web/src/shell/`, `apps/web/src/components/ui/`,
> `apps/web/src/status/`, `apps/web/src/format/`) and are applied to Login,
> Bid dashboard and Create bid; see "Batch 2: as built" under Restyle
> batches. Batch 3 applies them to the processing screen, the requirement
> register and the shared gate panel, and adds the tender bar's run status
> badge; see "Batch 3: as built". Batch 4 applies them to compliance, team,
> commercial findings and knowledge selections; see "Batch 4: as built".
> Batch 5 applies them to the proposal workspace, the traceability drawer,
> red-team findings, the graph explorer and readiness; see "Batch 5: as
> built". Batch 6 applies them to the talent import and review queue, the
> talent library and the employee profile, and deletes the last
> transitional colour alias; see "Batch 6: as built". The polish pass then
> changes shared components on every screen and fixes eight screens; see
> "Polish pass: as built".
> Lines marked "**Before the restyle (historical):**" describe the app as
> surveyed before batch 1, with that survey's line numbers; they are kept so
> each "Built" entry has its starting point. Every other statement about
> the UI cites a file and line as it is now. A batch's section moved from
> "will" to "does" in the same commit as its code - never ahead of it (see
> Copy rules).
>
> Numbers are labelled **measured** (computed or counted this session, method
> stated) or **fake-provider** (screens rendered from `LLM_PROVIDER=fake`
> runs). Nothing here is real-provider data.

## Overview

**Creative North Star: "The Custody Ledger"**

The landing page (`apps/landing`) tells the story of a tender passing through
custody: documents, evidence, claims, each one accounted for. The operator
app is where that custody is actually kept. Same materials, different job:
the landing is a cinematic walk-through of one tender; the app is a ledger
an operator scans thirty times a day. So the app takes the landing's warm
dark surface, its ink ramp, its amber, and its two typefaces, and drops the
cinema - no WebGL world, no scroll choreography, no display-size serif
outside page titles.

This is Impeccable's **Operate** mode: the visitor's success is finishing a
task (verify a requirement, decide a gate, resolve a finding), so
scanability, consistency and earned familiarity outrank expression. The
brand lives in precise details - the amber focus ring, tabular numerals, the
Newsreader page title - not in decoration.

**Principles for a dense working tool**

1. **State first.** Every row, gate and finding shows its state before its
   detail. An operator scanning the requirement register reads the status
   column, not the prose.
2. **Status is never colour alone.** Five states, five hues, and every one
   also carries an icon and a text label (decided; see Colors).
3. **Density is a feature.** 32px controls, 13px tabular data, real tables
   with many rows. No cards standing in for rows.
4. **Show the evidence, not a summary of it.** Raw `JSON.stringify` dumps
   (before the restyle: `KnowledgeSelections.tsx:165`, `GraphExplorer.tsx:112`,
   `GraphExplorer.tsx:151`, `GraphExplorer.tsx:164`,
   `TalentImportReview.tsx:216`) become labelled fields; nothing is hidden
   that was shown before.
5. **The UI never claims more than the code does.** Copy, labels and empty
   states describe what the backend actually did (Copy rules).
6. **One way to do each thing.** One button vocabulary, one status badge,
   one notice, one empty state, one INR formatter.

**Key Characteristics:**
- Warm near-black surface (landing `--bg`), three-step ink ramp, amber as the
  only brand colour.
- Archivo for everything an operator reads or types; Newsreader for page
  titles and empty-state headings only.
- Flat, rule-separated layout; depth from tone, not shadows.
- Dark only for this restyle; tokens are semantic and theme-hooked so a light
  set can be added later.

## Colors

The palette is the landing's (`apps/landing/src/styles/tokens.css:3-15`),
with one ink step lifted for small text and five status hues added. Amber is
reserved; status hues sit well away from it on the hue wheel.

### Primary
- **Custody Amber** (`accent`): brand, links, primary actions, the focus
  ring, current selection in navigation. **Nothing else** (decided).
  6.45:1 on `surface`, 5.84:1 on `surface-raise` (measured).
- **Ledger Black on amber** (`on-accent`): text on a primary button.
  6.45:1 on `accent` (measured).

### Neutral
- **Lamp-black Surface** (`surface`, landing `--bg`): page background.
- **Well** (`surface-sunk`, landing `--bg-2`): input fields, code/excerpt
  wells, the table header band.
- **Raised Sheet** (`surface-raise`, landing `--raise`): panels, the gate
  panel, the traceability drawer, secondary buttons, row hover.
- **Paper Ink** (`ink`): primary text. 16.01:1 on `surface` (measured).
- **Faded Ink** (`ink-2`): secondary text, column headers, metadata.
  8.82:1 on `surface`, 7.98:1 on `surface-raise` (measured).
- **Margin Ink** (`ink-3`, `#948979`): tertiary text - timestamps, ids,
  placeholders. 5.47:1 on `surface`, 4.95:1 on `surface-raise` (measured).
  **Deliberate deviation from the landing's `--ink-3` (`#8B8073`)**, which
  measures 4.40:1 on `surface-raise` - below the 4.5:1 body-text floor for
  the 12-13px sizes this app uses for metadata. The landing uses its ink-3
  mostly on `--bg` and at display sizes; the app cannot.
- **Rules** (not frontmatter tokens because they are translucent, carried as
  CSS custom properties):
  - `--rule`: `rgba(243,236,225,.14)` (landing value) - row dividers and
    section rules. 1.43:1 (measured); decorative separation only, never the
    only boundary of an interactive control.
  - `--rule-2`: `rgba(243,236,225,.07)` (landing value) - zebra/hover tint.
  - `--control-border`: `rgba(243,236,225,.40)` - **new**: the boundary of
    inputs, selects, checkboxes and secondary buttons. 3.44:1 on `surface`,
    3.41:1 on `surface-raise` (measured), meeting WCAG 1.4.11's 3:1 for UI
    components. At the landing's .14 or at .32 it measures 1.43:1 and
    2.63:1 (measured), which fail.

### Status (decided: amber is not a status; each status has icon + label + colour)

| State | Token | Value | OKLCH hue | on `surface` | on `surface-raise` | on own 14% tint | Icon (authored SVG) |
|---|---|---|---|---|---|---|---|
| Ready | `status-ready` | `#7DB88A` | 150.6 | 8.14:1 | 7.37:1 | 6.49:1 | check in circle |
| Running | `status-running` | `#7FA5AA` (landing `--slate`) | 206.2 | 7.04:1 | 6.37:1 | 5.73:1 | half-filled circle |
| Warning | `status-warning` | `#D4CC63` | 104.9 | 11.29:1 | 10.22:1 | 8.49:1 | triangle with bar |
| Blocked | `status-blocked` | `#B79BE0` | 302.6 | 7.85:1 | 7.11:1 | 6.29:1 | circle with bar (no-entry) |
| Failed | `status-failed` | `#EC6B73` | 18.5 | 6.19:1 | 5.60:1 | 5.17:1 | x in circle |

All ratios measured with the WCAG 2.x relative-luminance formula
(script kept in the session scratchpad, not committed); every cell is
measured, including the two warning cells, recomputed to confirm. "Own 14%
tint" is the hue at 14% opacity composited over `surface`. Accent amber's OKLCH hue is 65.5 (measured), so
warning sits 39 degrees away and failed 47 degrees away. The landing's
`--alarm` (`#CF5F45`, OKLCH hue 34.4, 4.81:1 on `surface`, measured) was
rejected as the failed colour: at 31 degrees from amber the two read as one
warm family on a dark ground, which is exactly the accent/status collision
decision 3 rules out.

**Colour-vision deficiency (measured).** Simulated with the Machado et al.
(2009) full-severity matrices in linear RGB, distance in OKLab:
- *Deuteranopia:* **ready and failed are the closest pair** (0.041), then
  failed and accent (0.044), ready and running (0.071).
- *Protanopia:* ready and running closest (0.078), then running and
  blocked (0.082), failed and accent (0.083).
- *Normal vision,* for comparison: ready and running closest (0.082).

Green-for-done next to red-for-failed is the classic pair that collapses,
which is why the Three Signals Rule exists: the icon and label carry the
meaning, the hue only speeds up scanning. **Each batch's visual check
includes a greyscale pass and a simulated deuteranopia and protanopia pass**
(Chrome DevTools' Rendering > "Emulate vision deficiencies", no new tool).

**Why blocked is violet, not red or yellow.** Blocked means "cannot advance
until someone acts" - the most common state an operator sees, and not an
error. Giving it red would make every pending gate look like a failure;
giving it yellow would collide with warning. Violet is unclaimed by every
other meaning in this app.

**Status mapping.** Every value below is read from a CHECK constraint or
`Literal` in the backend; the column assignment is this document's
decision, the vocabulary is not. Paths are under
`packages/tenderos_backend/src/tenderos_backend/`.

Sources:
- Gate: `db/models/workflow.py:86` - `pending, approved, rejected, changes_requested`
- Run: `db/models/workflow.py:15` - `queued, running, waiting_for_human, completed, failed, cancelled`
- Task: `db/models/workflow.py:42` - `pending, running, completed, failed, skipped, blocked`
- Document parse: `db/models/document.py:25` - `uploaded, queued, parsing, parsed, failed`
- Severity (red-team and commercial findings): `db/models/redteam.py:22`,
  `db/models/commercial.py:68`, and the agents' `Severity` literal at
  `agents/schemas/redteam.py:5` and `agents/schemas/commercial.py:5` -
  `critical, major, minor` (there is no "high"; an earlier draft of this
  file said so and was wrong)
- Proposal claim grounding: `db/models/proposal.py:58` - `grounded, unsupported, needs_review, not_required`
- Compliance entry: `db/models/compliance.py:53-56` - `fully_compliant, partially_compliant, non_compliant, clarification_required, evidence_unavailable, not_reviewed`
- Finding resolved flag: `db/models/redteam.py:46` and `db/models/commercial.py:93` (boolean)
- Import item: `db/models/knowledge.py:61` - `pending, extracted, needs_review, approved, rejected, failed`
- Import batch (batch 6): the status the API **returns** is computed from
  the batch's items, `domain/services/knowledge_service.py:1211-1240` -
  `queued, processing, needs_review, completed, failed` - not the stored
  column (`db/models/knowledge.py:34`). "completed" includes batches with
  some failed items; only an all-failed batch is "failed".
- Knowledge item (batch 6): `db/models/knowledge.py:103` - `active, expired, archived, unverified`
  (only `active` counts as verified evidence, `domain/services/compliance_service.py:79`)
- Employee availability (batch 6): `db/models/knowledge.py:174` - `available, partially_available, unavailable, unknown`
- Employee profile review (batch 6): `db/models/knowledge.py:178` - `needs_review, verified, stale, rejected, archived`
  (only `needs_review`, the default, and `verified`, `knowledge_service.py:1156`, have a writer)

| Display state | Gate | Run | Task | Parse | Severity | Grounding | Compliance | Other |
|---|---|---|---|---|---|---|---|---|
| Ready (green) | `approved` | `completed` | `completed` | `parsed` | - | `grounded` | `fully_compliant` | finding resolved = true; readiness `ready: true`; import item `approved` |
| Running (slate) | - | `queued`, `running` | `pending`, `running` | `uploaded`, `queued`, `parsing` | - | - | - | graph projection `running`; import item `pending`, `extracted` |
| Blocked (violet) | `pending`, `changes_requested` | `waiting_for_human` | `blocked` | - | - | `needs_review` | `clarification_required` | readiness `ready: false`; import item `needs_review` |
| Failed (red) | `rejected` | `failed` | `failed` | `failed` | `critical` | `unsupported` | `non_compliant` | import item `failed`, `rejected`; graph contradiction (polish pass) |
| Warning (yellow) | - | - | - | - | `major` | - | `partially_compliant`, `evidence_unavailable` | graph stale; live updates unavailable; in-scope evidence not cited |
| Neutral (ink-2, no hue; ink tint since the polish pass) | - | `cancelled` | `skipped` | - | `minor` | `not_required` | `not_reviewed` | - |

- **Gates - DECIDED (was TODO(human) 1, option a):** `rejected` = Failed,
  `changes_requested` = Blocked. This mirrors the run's own coarse mapping
  (`core/enums.py:53-76`: `rejected` -> `failed`, `changes_requested` ->
  `waiting_for_human`), so a gate badge never disagrees with its run.
- **Severity applies to open findings only.** A resolved finding shows the
  Ready treatment with the label "Resolved", whatever its severity; an open
  one shows its severity badge. This matches what readiness counts as
  blocking: open `critical` and `major` only
  (`domain/services/readiness_service.py:44-45`).
- **`evidence_unavailable` is Warning, not Failed**, because it says the
  system could not find support, not that the bid fails the requirement;
  `non_compliant` is the only compliance value that asserts failure.

### Label map (visible text)

Token names stay `status-ready`, `status-failed` and so on. **Visible text
does not reuse the token name.** One shared map turns each backend value
into words:

- Gate `approved` -> **"Approved"**; `rejected` -> "Rejected";
  `changes_requested` -> "Changes requested"; `pending` -> "Awaiting
  decision".
- Run and task `completed` -> **"Done"**; `running` -> "Running"; `queued`
  and task `pending` -> "Queued"; `waiting_for_human` -> "Waiting for
  review"; `failed` -> "Failed"; `cancelled` -> "Cancelled"; `skipped` ->
  "Skipped"; task `blocked` -> "Blocked".
- **"Ready" is reserved for the readiness verdict** (`ready: true` ->
  "Ready", `false` -> "Not ready"). Nothing else on any screen says
  "Ready", so the word always means the one thing an approver signs off on.
  (The readiness page's E2E reads that exact `<strong>` text,
  `apps/web/e2e/full-flow.spec.ts:1452`.) **Since batch 5 the verdict
  shows only for a report generated in this visit**: a stored report
  loaded on mount may predate later changes to the tender, so it is shown
  as "Last stored report: state version N, generated {date}, ..." with no
  `<strong>` and never the word "Ready" (`Readiness.tsx`; see "Batch 5: as
  built"). The final-readiness gate panel's request-time badge is left as
  is, labelled "At request time".
- Severity: "Critical", "Major", "Minor". Grounding: "Grounded",
  "Unsupported", "Needs review", "Not required". Compliance: "Compliant",
  "Partly compliant", "Not compliant", "Clarification needed", "No
  evidence", "Not reviewed". Finding resolved: "Resolved" / "Open".
- **Batch 6 families (chosen at plan review, `status/statusMap.ts`):**
  - Import batch: queued Running "Queued", processing Running
    "Processing", needs_review Blocked "Needs review", failed Failed
    "Failed"; completed is Ready **"Done" only when `failed_items` is 0**,
    otherwise Warning **"Done with failures"** (`describeImportBatch`,
    derived from the same response).
  - Knowledge item: active Ready "Active", expired Failed "Expired" (as
    validity's expired), unverified **Warning** "Unverified" (data
    compliance does not count as verified, not a step waiting on a
    person), archived Neutral "Archived".
  - Availability: available Ready "Available", partially_available
    Warning "Partly available", unavailable **Blocked** "Unavailable"
    (Neutral would read milder than partly available), unknown Neutral
    "Unknown".
  - Profile review: needs_review Blocked "Needs review", verified Ready
    "Verified", stale Warning "Stale", rejected Failed "Rejected",
    archived Neutral "Archived".
- **Polish pass (chosen, not in this file before; in code):**
  - Readiness blocker categories (`domain/services/readiness_service.py:33-40`)
    through a hand-written map in `Readiness.tsx`, `humanize` for one the
    backend adds later: e.g. `critical_redteam_finding` "Critical red-team
    finding" (as the nav says "Red team", not "redteam"),
    `role_with_no_selected_candidate` "Role without a selected candidate".
  - Graph node property values through the families above
    (`routes/graphExplorerValues.ts`): `validity_status`,
    `grounding_status`, `availability_status`, `profile_review_status` by
    key; a bare `status` by node label - Compliance (`projector.py:339`),
    ProposalSection (`:366`), and Employee, Certification, Project and
    Capability, whose `status` is the knowledge item's (`:194-201`); any
    other status humanised. The labels match the ones the projector writes
    (`graph_projection/cypher/nodes.py:71`, `:98`, `:108`, `:117`, `:126`,
    `:135`).

### Named Rules

**The Reserved Amber Rule.** Amber marks what is *yours to act on or where
you are*: links, the primary action, focus, the current nav item. It never
marks a state. If something amber is not clickable or focused, it is wrong.

**The Three Signals Rule.** A status is shown by hue, icon and a text label
together. Remove the colour and the screen must still read correctly
(grayscale check in each batch).

**The No Raw Enum Rule.** `fully_compliant`, `waiting_for_human` and
friends are never shown as-is; one shared label map turns them into words.
(Since batch 4 the compliance select shows mapped labels; its option
*values* stay the backend words, which `full-flow.spec.ts:714` and `:736`
read - `apps/web/src/routes/ComplianceMatrix.tsx`.)

### Theming hook (decided: dark only for this restyle)

Tokens will be defined under `:root[data-theme="dark"]`, with `index.html`'s
`<html>` carrying `data-theme="dark"` and `color-scheme: dark`, and a plain
`:root` fallback holding the same values. Components only ever read semantic
names (`--surface`, `--ink-2`, `--status-failed`), never raw hex. A light set
can later be added as `:root[data-theme="light"]` without touching a
component. **No light values are designed or tested in this phase**; see
Deferred.

## Typography

**Display Font:** Newsreader (with Georgia, "Times New Roman", serif)
**Body Font:** Archivo (with system-ui, -apple-system, "Segoe UI", sans-serif)

**Character:** Archivo is a grotesque with enough width to stay legible at
12-13px in dense tables; Newsreader is the landing's voice, used once per
screen so the app is recognisably the same product without the serif
competing with data.

**Decided: Newsreader appears only in page titles (`h1`) and empty-state
headings.** Tables, forms, labels, buttons, numbers, section titles: Archivo.

### Hierarchy (fixed rem, ratio about 1.15 between body steps - Operate mode)
- **Page title** (Newsreader 300, 1.75rem / 28px, 1.15): one per screen.
- **Section title** (Archivo 600, 1.125rem / 18px, 1.3): `h2`.
- **Subsection** (Archivo 600, 1rem / 16px, 1.35): `h3`.
- **Body** (Archivo 400, 0.875rem / 14px, 1.5): prose, notices, form help.
  Prose measure capped at 72ch.
- **Data** (Archivo 400, 0.8125rem / 13px, 1.4, tabular numerals): table
  cells, counts, money, dates.
- **Label** (Archivo 500, 0.75rem / 12px, 1.3, sentence case): field
  labels, column headers, badge text. **Not uppercase** (frontend-design's
  all-caps-label tell; the landing does not use it either).

### Numerals (measured)

Archivo **has** a `tnum` OpenType feature, but its default figures are
**proportional**: digit advances 521-574 units, measured with fontTools on
the Google-served Archivo Regular TTF (GSUB features present: `ccmp dnom
frac liga locl numr pnum tnum`). So every table, count, money value and date
must set `font-variant-numeric: tabular-nums` or columns will not align.
Newsreader's default figures are already tabular (all 1133 units, measured).
Both fonts include the rupee sign U+20B9 (measured; neither includes
Devanagari, which this UI does not need).

### Font loading (decided: self-hosted woff2 under `apps/web/public/`)

- **How the landing does it today:** a runtime request to Google Fonts,
  `apps/landing/index.html:11-14` (preconnect to `fonts.googleapis.com` and
  `fonts.gstatic.com`, then one `css2` stylesheet for Newsreader 200/300/400
  plus italics and Archivo 400-700, `display=swap`).
- **What apps/web will do:** `@font-face` rules in the token stylesheet
  pointing at woff2 files in `apps/web/public/fonts/`, `font-display: swap`,
  Latin subset plus U+20B9. Weights: Archivo 400/500/600, Newsreader 300.
  Both families are SIL Open Font License; the OFL text ships next to the
  files.
- **Why:** no third-party request at runtime from a logged-in tool, no
  dependency on a font host's availability, no new npm dependency.
- **Batch 1 caveat:** the woff2 files are **binary**, and commits are
  text-only until they are approved. Adding them is part of restyle batch 1
  and needs its own approval. Batch 1 must also re-run the numerals check
  above against the *self-hosted* files: a subsetter can silently drop
  `tnum` or U+20B9.

### Named Rules

**The One Serif Rule.** One Newsreader line per screen, plus an empty
state's heading. A second serif line means something is a page title that
should not be.

**The Tabular Rule.** Any column of numbers is tabular and right-aligned.

## Layout

**App shell (built, batch 2).** A 48px top bar (wordmark "TenderOS" in
Archivo 600 at 16px, linking to the dashboard; "Bids" and "Talent" links;
the signed-in email; a Log out button) over a content column
(`apps/web/src/shell/AppShell.tsx`).
The landing sets its wordmark in Newsreader
(`apps/landing/src/styles/chrome.css:8`); the app does not, because
decision 4 limits Newsreader to page titles and empty-state headings. The
top bar shows the **email, not an org name**: `/auth/me` carries no
organisation name (`MeMembership` is `organization_id` + `role`,
`apps/web/src/api/client.ts:180-183`). There is **no account menu**.

**Log out (built, plan item 39).** A quiet `Button` beside the email, on every
signed-in screen. It awaits `logout()`, which can take up to its 5 s bound, and
while that runs it is disabled, `aria-busy`, and reads "Logging out...": there
was no existing participle to reuse, and a quiet button at full opacity would
otherwise show no change at all for 5 s. `RequireAuth` does the landing on
`/login`, not the button. A deliberate log out carries no `from`, so the next
login starts at the dashboard; an expired session still returns to its page.
The email shrinks first at phone width. Below 360px the account area takes a
second line, the bar's one exception to 48px, because the busy label can't fit
on the first.

Inside a tender, the **workflow nav**
(built) gives every tender screen a way back to the dashboard (the
wordmark and "Bids") and to every other step. The processing screen's old
dot-separated link row was removed in batch 3. The **tender bar** carries
the workflow nav and, since batch 3, the **run status badge** (next
paragraph); the tender **name and client** are still not built (deferred,
below).

**The shell sits outside `<main>`.** Top bar, tender header and workflow nav
are siblings of `<main>`, never inside it, and contain no `<strong>`, no
heading whose text could contain a screen's own heading text, and no link
whose name contains a tender name on the dashboard. The constraints these
protect are listed under Restyle batches.

**Tender header status badge - only where the run is already fetched.**
`tenders.status` cannot drive it: that column is written only by
`PATCH /tenders/{id}`
(`packages/tenderos_backend/src/tenderos_backend/db/repositories/tenders.py:60`),
the workflow never updates it, and all 47 tenders in the live database read
`created` (measured). So the badge appears only on the six screens that
already fetch the latest run today - processing, requirements, compliance,
team, commercial, readiness (measured: the files that call
`listWorkflowRuns` or `getWorkflowRun`) - and is omitted, not faked, on
knowledge selections, proposal, red team and graph. No restyle batch adds a
run fetch to get it. See Decisions, item 9.

**Built in batch 3** (`apps/web/src/shell/runStatus.ts`,
`apps/web/src/shell/TenderLayout.tsx`): `TenderLayout` owns the value and
renders "Run" plus a `StatusBadge` (a `<span>`, outside `<main>`) at the
right of the tender bar - below 600px, on its own line under the nav, so
the nav keeps the full width (batch 4 fix: at 390px the badge had left the
nav 189px, one tab fully visible; now 358px, measured); a screen reports a status it already has with
`useReportRunStatus(status)`. `null` or no report shows nothing; the hook's
cleanup withdraws the status when the screen unmounts, so navigating to a
step that does not report clears it. Wired in batch 3 on the processing
screen (the streamed run's status, else the last run's) and the
requirement register (the first caller: `runs[0].status` from the
`listWorkflowRuns` call it already made for the gate). Batch 4 wires
compliance, team and commercial the same way (each clears its badge after a
gate decision); batch 5 wires readiness the same way. The bar updates
from an effect, one React commit after the screen. After a gate decision
the requirement register clears its badge (blank until the next load)
rather than keep a status the decision made stale.

**Deferred from batch 2 (decided at batch 2 plan review):**
- **Run status badge -> built in batch 3** (above), with the requirement
  register as its first caller. Each remaining screen that fetches the run
  keeps only `runs[0].id` today, so wiring it means keeping the status in
  state from the call it already makes; no new fetch.
- **Tender name and client.** Showing them needs `getTender`
  (`apps/web/src/api/client.ts:233`), which no screen calls today, so it
  would be a new fetch inside a restyle. Not built; needs an explicit
  decision before any batch adds it.

**Workflow nav - DECIDED (was TODO(human) 2, option a); built in batch 2**
(`apps/web/src/shell/TenderLayout.tsx`): a horizontal tab strip under the
top bar, in pipeline order: Documents, Requirements, Compliance, Team,
Commercial, Knowledge, Proposal, Red team, Readiness, plus Graph as a
utility after a divider. It scrolls horizontally on its own at 390px (the
page does not). Plain links directly inside `<nav>` - no `<ul>`/`<li>`, see
the selector list under Restyle batches - with `aria-current="page"` set by
React Router's `NavLink`; the E2E reaches tender screens with `page.goto`,
so the strip does not change how the spec navigates.

**Content width.** Tables run full width (max 1440px); forms and prose cap
at 640px / 72ch. Batch 2 replaced Create bid's hardcoded 480 with
`--width-form` (640px, via `.form-column`) and Login's 360 with
`--width-auth` (360px). The page gutter (16px, 24px from 600px up) is the
shell's `.page` column (`apps/web/src/styles/layout.css`).

**Density.** 32px control height, 36px table rows, 8px cell padding
vertical / 12px horizontal. Spacing uses the existing 4px-based scale
(`styles/tokens.css:23-30`), unchanged.

**Responsive (390px).** Structural, not fluid: the workflow nav becomes a
horizontally scrolling tab strip; tables keep their `<table>` markup and sit
in a horizontally scrollable region with a sticky first column (required by
the E2E's structural selectors - see Restyle batches); row actions stay
reachable without horizontal scroll by sitting in the first or last column
as decided per screen; the gate panel stacks. 16px side gutter. No page-level
horizontal scroll.

## Elevation & Depth

Flat by default, as on the landing. Depth comes from the three surface tones
(`surface-sunk` < `surface` < `surface-raise`) and `--rule` lines. One
shadow exists: the landing's `--cshadow`, used only for the traceability
drawer when it overlays content. No shadow on cards, tables or buttons.

**The Tone-Not-Shadow Rule.** If two regions need separating, change the
surface tone or draw a rule; never add a shadow to a resting element.

## Shapes

Small, precise corners, taken from the landing's own radii (2px and 3-4px
dominate: `border-radius` counts in `apps/landing/src/styles/*.css` were
2px x5, 3px x5, 4px x3, measured). `rounded.sm` 2px for badges and focus
outlines, `rounded.md` 4px for buttons, inputs, panels. `rounded.pill` only
for count chips. Borders 1px. No colored side-stripes on cards or alerts
(craft-floor ban).

## Components

Inventory from the pre-restyle survey (historical: all in `apps/web/src`,
all unstyled browser defaults then; 0 `className` uses, 30 inline
`style={{}}` objects, measured by grep). Each component now exists once
and is reused; after the polish pass, screen code has 0 inline
`style={{}}` and 0 bare `<button>` (measured).

### Buttons
- **Before the restyle (historical):** bare `<button>`, 31 in source across 16 files (measured;
  some render once per row), no variants. Primary and
  destructive actions look identical (e.g. Approve / Request changes / Reject,
  `GateDecisionPanel.tsx:134-141`; Confirm / Reject / Re-verify per row,
  `RequirementRegister.tsx:130-138`). Login uses `disabled` as a tab
  selector (`Login.tsx:69-74`).
- **Will be:** `primary` (amber fill, one per region), `secondary`
  (raise fill, control border), `quiet` (text only, for row actions),
  `danger` (failed-hue text and border, never fill). Height 32px.
- **States:** default, hover (`--rule-2` overlay or 8% lighter amber),
  focus-visible (amber ring), active, disabled (ink-3 text, no fill change
  beyond 50% opacity; **except primary**, see below), busy (label changes to
  the present participle that exists today - "Starting...", "Generating..."
  - plus `aria-busy`).
  **Deviation (batch 2), disabled primary:** ink-3 text on the 50% amber
  fill measured 1.10:1, an unreadable label. `--on-accent` text at 50%
  measured 2.49:1, at 60% 3.06:1 (measured; WCAG 2.x formula, the whole
  control composited over `surface`). So a disabled primary uses
  `--on-accent` text, its amber fill, and 0.6 opacity. Disabled secondary,
  quiet and danger follow the rule above unchanged.

- **Built (batch 2):** `apps/web/src/components/ui/Button.tsx` - variants
  primary, secondary, quiet, danger; one size (32px; this file specifies no
  other); `busy` sets `aria-busy`, swaps in the existing participle and
  disables the button (the E2E waits on `toBeEnabled()` after long actions).
  Busy keeps its variant's colours at full opacity: dimmed like disabled, a
  busy primary measured 1.10:1. Disabled primary uses `--on-accent` text at
  0.6 opacity (label 3.06:1, measured), not ink-3 at 0.5 (1.10:1) - the
  deviation recorded under States. Labels never wrap (`white-space: nowrap`
  on `.btn` and on the base `button` rule, so bare "Re-verify" no longer
  breaks at the hyphen). `buttonClassName()` gives a `<Link>` the
  same look. Login's mode switch is now two toggle buttons with
  `aria-pressed`, not `disabled`. Migrated: Login and Create bid (4 bare
  buttons); 27 bare `<button>`s in 14 files remain for their own batches
  (measured after batch 2). Adjacent actions use the shared `.action-row`
  gap.
- **Built (batch 3):** the gate panel (Approve primary, Request changes
  secondary, Reject danger; the clicked one `aria-busy`, all three
  disabled while submitting, labels unchanged since no participle
  existed), requirement row actions (Confirm and Re-verify quiet, Reject
  danger), Retry (quiet) and Start processing (primary, "Starting...").
  6 bare buttons migrated; 21 remain (measured after batch 3). Buttons and
  inputs share `--control-pad-y` (renamed from `--button-pad-y`, same 6px).
- **Built (batch 4):** row actions quiet (compliance Approve, View
  candidates, Select/Deselect, Re-validate, Resolve, Preview impact); the
  knowledge "Confirm select/deselect" primary. Labels unchanged. Re-validate,
  Preview impact and Confirm carry `aria-busy` on the one in flight (no busy
  label: no participle existed). 7 bare buttons migrated; 14 remain
  (measured after batch 4).
- **Built (batch 5, group A):** Generate proposal and Run red team primary
  with their existing participles ("Generating...", "Running..."); Save
  edits secondary; Inspect trace, Resolve and the drawer's Close quiet.
  Labels unchanged. 6 bare buttons migrated; 8 remain (measured after
  group A).
- **Built (batch 5, group B):** Generate readiness report and the graph's
  Load primary, Generate with its existing "Generating..."; Explore quiet.
  3 bare buttons migrated; 5 remain (measured after batch 5), all in the
  talent screens (batch 6).
- **Built (batch 6, group A):** import queue - Review items and Approve
  quiet, Reject danger; the clicked Approve or Reject carries `aria-busy`
  and both of that row's buttons are disabled while it runs (no busy
  label: no participle existed). Labels unchanged. 3 bare buttons
  migrated; 2 remain, both on the employee profile (measured).
- **Built (batch 6, group B):** employee profile - Verify **primary** (the
  page's one), title Save secondary; each `busy` while its request runs
  (no busy label). Exactly one button name contains "verify"
  (`full-flow.spec.ts:497` is a strict substring match). 2 bare buttons
  migrated; **0 remain** in screen code (measured: the only `<button>` left
  is `Button.tsx`'s own).
- **Built (polish pass):** **quiet buttons carry a 1px `--control-border`
  on a transparent fill** (`components/ui/Button.css`), amending "quiet
  (text only)" above. Before, a quiet row action read as plain text - on
  the import review row, "Approve" beside the outlined danger "Reject".
  The border is the control boundary inputs and secondary buttons use;
  quiet still has no fill, so it stays lighter than secondary. Border
  contrast (WCAG 1.4.11, 3:1), measured from computed styles on every
  screen: 3.44:1 on the page, 3.41:1 raised, 3.37:1 on a hovered row,
  3.41:1 on a sunk well (computed; since plan item 39 the top bar's Log out
  sits on `--surface-sunk`: border 3.41:1, label 9.10:1).

### Status badge
- **Before the restyle (historical):** none. Status is a raw string in a cell (`BidDashboard.tsx:70`,
  `UploadAndProcessingStatus.tsx:218`, `TalentImportReview.tsx:180`),
  "yes"/"no" (`RequirementRegister.tsx:124-126`), or a `[type / status]`
  prefix (`ProposalWorkspace.tsx:115`, `Readiness.tsx:105`).
- **Will be:** icon + label in the status hue on a 14% tint of that hue,
  2px radius, 12px label type. Booleans that are states (verified,
  approved, resolved, selected) become badges; booleans that are facts
  (mandatory) become a plain word.

- **Built (batch 2):** `apps/web/src/components/ui/StatusBadge.tsx`
  (tone + label; a `<span>`, never `<strong>`), icons in
  `apps/web/src/components/ui/icons.tsx` (authored SVG, `aria-hidden`,
  `focusable="false"`; neutral is a hollow circle), and the shared label
  map `apps/web/src/status/statusMap.ts`, which quotes this file's line for
  every row it implements and marks the rows it chose ("chosen, not in
  DESIGN": parse and import-item labels, the unknown-value fallback). Not
  yet placed on any screen: the dashboard ships without a Status column
  (decision 9); the first badges arrive in batch 3.
- **Built (batch 3):** first badges on real screens - gate status, run
  status (tender bar and "Last run"), task status, document parse status,
  requirement Verified. **Spec-forced deviation:** the requirement
  register's Reviewed column stays the plain word "yes"/"no", although it is
  a state: `full-flow.spec.ts:653` reads that cell (td index 5) and expects
  exactly "yes". Mandatory is a fact and stays a plain word by this file's
  own rule.
- **Built (batch 4):** compliance status (badge beside the override
  select), compliance Approved, candidate and knowledge Selected, and the
  finding status (`describeFinding`). New, chosen, not in this file
  (`status/statusMap.ts`): `describeApproved` (Ready "Approved" / Neutral
  "Not approved") and `describeSelected` (Ready "Selected" / Neutral "Not
  selected"); neither can return "Ready" (`statusMap.test.ts`).
- **Built (batch 5, group A):** proposal section status, claim grounding,
  evidence verified and validity, red-team severity. New families, chosen,
  not in this file (`status/statusMap.ts`): `proposalSection` (draft
  Neutral "Draft", review Blocked "In review", approved Ready "Approved")
  and `validity` (valid Ready, invalid Failed, expired **Failed** - the
  backend treats expired exactly like invalid: only verified and `valid`
  evidence grounds a claim, `domain/services/proposal_service.py:154`,
  `:279`, or counts for readiness, `readiness_service.py:219` - unknown
  Neutral). **Spec-forced (raw word in the DOM):** where a label is just
  the backend word capitalised, a badge the spec reads as text keeps that
  word and CSS capitalises it (`describeStatusKeepingRawWord`,
  `.badge-raw-word` in `styles/layout.css`) - the proposal claim tag
  (`full-flow.spec.ts:971`, `:974` find "/ unsupported]") and the red-team
  severity cell (`:1410`, `:1416` read "critical"). grounded, unsupported
  and every severity qualify; needs_review and not_required show their
  mapped labels. **Spec-forced deviation:** the red-team Resolved column
  stays the plain word "yes"/"no" (`:1382`, `:1395` wait for "no" in td
  index 3), alongside the requirement register's Reviewed column.
- **Built (batch 6, group A):** import batch status (`describeImportBatch`)
  and import item status on the import queue - the import item family's
  first screen. The batch status cell is read by the spec only as text it
  puts in a failure message (`full-flow.spec.ts:350`), so it takes the
  mapped label.
- **Built (batch 6, group B):** first callers of three families - knowledge
  item status in the library (badge beside the override select, as
  compliance), availability and profile review on the employee profile.
  **Spec-forced (raw word in the DOM):** Bid eligible is a badge whose DOM
  text is "yes"/"no", drawn "Yes"/"No" by `.badge-raw-word` - Ready when
  eligible, Neutral when not (as `describeSelected`'s negative).
  `full-flow.spec.ts:498` waits for exactly one element inside a `<dl>`
  whose text is exactly "yes"; measured with the spec's own locator, it
  lands on the badge `<span>` (0 before Verify, 1 after), and no other
  `<dl>` value reads "yes".
- **Built (polish pass):** **the neutral badge has a tint** -
  `background: var(--rule-2)`, ink at 7%, laid **translucent** over
  whatever is behind it (`components/ui/StatusBadge.css`). This amends
  "neutral sets no background" (front matter and Status mapping): without
  a box, a neutral badge's label looked indented by its 8px padding beside
  tinted ones. 7%, not the 8% first proposed, because `--rule-2` is the
  existing token; translucent, unlike the hued tints' opaque base, because
  over an opaque base it would equal a hovered row's own 7% tint and vanish
  on hover. ink-2 text on it, measured: 7.56:1 on the page, 6.26:1 on a
  hovered row, 4.78:1 inside the drawer's warning panel (the lowest text
  pair in the app).

### Gate panel
- **Before the restyle (historical):** `GateDecisionPanel.tsx` - a `<ul>` of counts, a textarea, three
  identical buttons, error in danger colour. Decided gates show
  `Decision: <strong>approved</strong> on <ISO timestamp>` (`:89-92`).
- **Will be:** a `surface-raise` panel with a header row (gate name +
  status badge), the summary as a definition grid with tabular counts,
  blockers listed with the blocked icon, notes field, then actions with
  Approve as primary, Request changes as secondary, Reject as danger.
  Decided state shows who/when in human date format.
- **Summary honesty:** the summary counts are the gate's snapshot from when
  it was requested, and before batch 3 nothing said so - an approved compliance gate
  reads "Pending approval: 2" next to two rows marked approved, and the
  resource gate reads "Roles: 1 ... Unselected roles: 4" (screenshots 06,
  07, fake-provider data). The panel will label the summary "At request
  time" and show the request date.
- **States:** not yet opened ("No open ... gate" - still a bare `<p>` on
  five screens after the polish pass, measured: `CommercialFindings.tsx:174`,
  `ComplianceMatrix.tsx:140`, `Readiness.tsx:307`,
  `RequirementRegister.tsx:136`, `TeamWorkspace.tsx:163`), pending (blocked), approved (ready), rejected, changes
  requested, submitting, error (409 message kept verbatim in meaning).
- **Built (batch 3):** `apps/web/src/components/GateDecisionPanel.tsx`. Root
  still a `<section>` (the screens' `<h2>` + `<section>` sibling pair that
  `full-flow.spec.ts:56` finds); header badge; "At request time ·
  {date}" above the summary; the summary is a `<dl>` grid with tabular
  values; final readiness shows the readiness badge and the blockers list
  with the blocked icon; notes field labelled "Notes (optional)"; decided
  state is "Decided {date}" through `formatDate` - no `<strong>`, no raw
  ISO, and no "who" (`decided_by` is a user id; a name needs a fetch). Used
  unchanged on compliance, team, commercial (batch 4) and readiness. On the
  requirement register, and since batch 4 on compliance, team and
  commercial, the heading and panel move together above the table while
  the gate is pending (Screen patterns, "Gates"). **Readiness deviation
  (batch 5):** its gate stays below the report even while pending - the
  panel's request-time snapshot must not sit above the report being read.
  Heading and panel remain adjacent siblings (`full-flow.spec.ts:56`).

### Tables
- **Before the restyle (historical):** 14 bare `<table>`s in 11 files (measured), no header styling,
  no numeric alignment. Raw float output is visible: compliance confidence
  renders as `0.8999999761581421` (`ComplianceMatrix.tsx:118`, fake-provider
  data), and the requirement column shows a UUID instead of the display code
  (`ComplianceMatrix.tsx:104`).
- **Will be:** `surface-sunk` header band, label type for headers, data type
  for cells, `--rule` row dividers, row hover `--rule-2`, numbers
  right-aligned and tabular, rejected rows dimmed (before the restyle an inline
  `opacity: 0.5`, `RequirementRegister.tsx:120`).

- **Built (batch 2):** `TableScroll` (`apps/web/src/components/ui/`), a
  named, focusable horizontal scroll region with a sticky first column,
  used on the Bid dashboard. Other tables get it in their own batch; the
  requirements table still overflows at 390px (579px wide, measured after
  batch 2) until batch 3 wraps it.
- **Built (batch 3):** `TableScroll` on the documents and requirements
  tables; an optional `stickyLastColumn` pins the requirements table's row
  actions to the right edge (they cannot move to the first column: the
  spec reads cells by position). Both screens measure `scrollWidth` 390 at
  390px. The region is now `position: relative`, the containing block for
  anything absolutely positioned inside its table: a visually-hidden
  header label otherwise escaped it and widened the page to 417px. Rejected requirement rows are dimmed **by colour, not opacity**
  (deviation): text in `ink-3`, and a badge in that row drops its hue and
  tint. At the old 0.5 opacity the Verified badge in a rejected row
  measured 2.62:1; `ink-3` measures 5.47:1.
- **Built (batch 4):** `TableScroll` on all six tables of compliance, team
  (roles, candidates), commercial (documents, findings) and knowledge
  selections; confidence and score as whole percents (`format/percent.ts`:
  "100%" only for exactly 1, "0%" only for exactly 0; chosen, not in this
  file); numbers right-aligned through the shared `.cell-num`, UUID cells
  wrapping through `.cell-id` (`styles/layout.css`). The compliance
  requirement column and the candidate column still show raw UUIDs: the
  display code and the employee name each need a fetch those screens do not
  make, and `full-flow.spec.ts:855` reads the candidate id verbatim.
  **`stickyLastColumn` only where it fits (measured at 390px):** kept on
  roles, pricing documents and knowledge items, whose actions column always
  holds a button; dropped on compliance, candidates and findings, where
  pinning it left less room between the two sticky columns than one content
  column needs (compliance Status 181px into 126; candidates 125 into 124;
  findings Note 256 into 175, and 253 even with every finding resolved), so
  that column could never be seen whole. Those three tables' row actions
  scroll with the table instead.
- **Built (batch 6, group A):** `TableScroll` on the import batches and
  items tables. **No sticky last column on either** (measured at 390px):
  batches left 126px for a 152px column with Review items pinned; items
  159px for the 224px Detail column (239 for 224 with every row's actions
  empty, but the column then pins nothing). Both scroll their actions with
  the table. Batch counts through `.cell-num`. The items' Detail cell is a
  labelled grid (stacked below 600px); a résumé file name in the sticky
  Source column breaks after its underscores (`<wbr>`; a local copy of the
  red-team helper at batch 6, shared since the polish pass). The
  open batch's row takes the `surface-raise` tone on every cell, sticky
  ones included, hovered or not (measured).
- **Built (batch 6, group B):** `TableScroll` on the library, Title sticky
  (wrapping, 8-16rem). No actions column, so no sticky last column to
  measure; at 390px the table scrolls inside its region (443px in 358,
  eight fixture rows) and the page stays 390. Columns: Title (an
  employee's one link), Status (badge and select), Type (humanised),
  Valid until (`formatDay`, "Not set" when null - since the polish pass,
  blank where it does not apply; see below).
- **Built (polish pass):**
  - **Body cells align on their first line's baseline** (`index.css`,
    `td { vertical-align: baseline }`); header cells stay `top`; the import
    items table keeps `top` with its own rule (its rows are tall blocks).
    Measured spread of first-line baselines in rows mixing text, a badge
    and a control: 6.9px with `top` on processing, requirements, team,
    commercial, knowledge and graph, 0.2px or less after (0 on graph);
    compliance and library about 4px (a `<select>`'s text position can only
    be estimated); import items unchanged at 6.9px by design.
  - **Shared `.cell-actions`** (`styles/layout.css`): an actions column
    (header and cells) at `width: 1%` and `nowrap`, so a table's spare
    width goes to its prose column instead of gaps between every column;
    used on red team, commercial (both tables), import (both tables) and
    team (both tables). Inside it an `.action-row` stays on one line **from
    600px up only**: kept on one line at 390px, red team's Resolve sat 200px
    and import's Approve/Reject 76px further right in their scrolling tables
    than before the polish pass (measured); with the breakpoint both are
    back exactly where they were (555px and 564px from the table's left
    edge). Red team's own design already wrapped its note field and Resolve
    at phone width (`RedteamFindings.css`).
  - **Shared `BreakAfterUnderscores`** (`components/BreakAfterUnderscores.tsx`)
    replaces the two local copies (red team, import); `<wbr>` adds no text,
    so `full-flow.spec.ts:1380` and `:1409` still match the category
    exactly.
  - **Library Valid until** is blank where it does not apply and "Not set"
    only for a certification without a date: only certifications ever carry
    a `valid_until` (`db/repositories/knowledge.py:367-379`). A date the
    backend sends for another type is still shown.

### Forms and inputs
- **Before the restyle (historical):** labels wrap inputs (good for accessibility, keep it), no
  layout, no help text, `<br />` for spacing (`TalentImportReview.tsx:148`,
  `:153`), file inputs unstyled.
- **Will be:** label above control, `surface-sunk` field with
  `--control-border`, amber focus, error text in failed hue with icon below
  the field. File inputs get a styled button and the chosen filename.

- **Built (batch 2):** `apps/web/src/components/ui/Field.tsx` - label text
  above the control inside the same wrapping `<label>`, so accessible names
  (and the E2E's `getByLabel`) are unchanged; hint text sits outside the
  label. Used on Login and Create bid. Error-below-field and the styled file
  input are not built yet.
- **Built (batch 3):** `Field` labels the gate notes ("Notes (optional)",
  replacing a placeholder) and the processing screen's file input ("Upload
  a document"); the input stays a native file input.
- **Built (batch 6, group A):** the import queue's three uploads are stacked
  `Field`s (no `<br />`), native file inputs, **labels verbatim, colons
  included**: `full-flow.spec.ts:317` finds the résumé input by the
  substring "Resumes (multiple files):" and `:329` waits for it to be
  enabled again, so it stays disabled while an upload is in flight; the
  other two keep their colons so siblings read alike (decided at plan
  review). A hint below says the page does not refresh itself.
- **Built (batch 6, group B):** library - the capability statement upload
  is a `Field` ("Capability statement", hint: found items appear once
  processed, reload to see them); the status select is named "Status of
  {title}" (it had no name), option values stay the backend words, option
  text is mapped. Profile - "Correct title" `Field` with Save beside it
  (the old title input had no accessible name), "Eligible for bid" stays a
  wrapping label on its checkbox, the resume input is a `Field` reading
  "Replace resume" when one is on file and "Upload a resume" when none is
  (so it never claims a file that is not there).
- **Built (polish pass):** `Field`'s own 16px bottom margin moves out of
  any row that aligns its controls to the bottom (`align-items: flex-end`):
  the graph's Load sat 16px below its fields (measured), as the profile's
  Save had in batch 6. Fixed per screen (`GraphExplorer.css`,
  `EmployeeProfile.css`); `Field` itself is unchanged.

### Notices (error, warning, info)
- **Before the restyle (historical):** `<p role="alert" style={{ color: "var(--color-danger)" }}>`
  repeated 22 times (measured), plus one warning variant
  (`UploadAndProcessingStatus.tsx:245`) and two bordered boxes
  (`GraphExplorer.tsx:103-115`, `TraceabilityDrawer.tsx:102-121`).
- **Will be:** one notice component: icon + message on a tint, variants
  failed / warning / info. Keeps `role="alert"` only for errors that
  appear after an action.

- **Built (batch 2):** `apps/web/src/components/ui/Notice.tsx`, used for the
  errors on Login, Bid dashboard and Create bid (3 of the
  `--color-danger` inline readers removed; 18 remain, measured).
- **Built (batch 3):** errors on the processing screen, requirement register
  and gate panel; the "Live updates are currently unavailable" notice is the
  `warning` variant. Remaining inline readers after batch 3 (measured by
  grep, comment mentions excluded): `--color-danger` 15, `--color-warning`
  2, `--color-border` 1 - no alias can be deleted yet.
- **Built (batch 4):** errors on compliance, team, commercial and knowledge
  selections (`failed`, `alert`, as each had `role="alert"`). Remaining
  inline readers after batch 4 (measured, same method): `--color-danger`
  11, `--color-warning` 2, `--color-border` 1 - still none deletable.
- **Built (batch 5, group A):** errors on proposal, red team and the
  traceability drawer. The drawer's two bordered boxes became classes, so
  `--color-warning` and `--color-border` lost their last readers and are
  **deleted** from `styles/tokens.css` (0 references in `src` or the built
  CSS, measured). `--color-danger` has 8 code readers left (measured).
- **Built (batch 5, group B):** errors on the graph explorer and readiness;
  the graph's contradictions box became a warning panel class. Remaining
  inline readers after batch 5 (measured): `--color-danger` 4, all in the
  talent screens - it can be deleted in batch 6.
- **Built (batch 6, group A):** the import queue's error is a `failed`
  notice with `alert`. `--color-danger` readers: 3, all on the library and
  employee profile (measured); the alias goes in group B with its last
  reader.
- **Built (batch 6, group B):** errors on the library and employee profile
  are `failed` notices with `alert`. `--color-danger` had **0** readers
  left in `src` code, then 0 in the built CSS and JS (measured), and is
  **deleted** from `styles/tokens.css` with its comment. No `--color-*`
  alias remains; no inline `style={{}}` remains in screen code.
- **Built (polish pass):** the graph's contradictions panel is now a
  **failed**-tone panel (failed icon, border and tint), not warning
  (decided): a contradiction is the defect the red team reports as critical
  (`deselected_fact_in_use`), so the two screens give it one hue
  (`GraphExplorer.tsx`, `GraphExplorer.css`).

### Empty, loading and error states
- **Before the restyle (historical):** a "Loading..." paragraph in 8 places across 7 files (measured:
  `BidDashboard.tsx:52`, `RequireAuth.tsx:10`, `KnowledgeSelections.tsx:160`,
  `TalentLibrary.tsx:87`, `TalentImportReview.tsx:162`, `:199`,
  `EmployeeProfile.tsx:90`, `TraceabilityDrawer.tsx:65`), some screens
  show nothing while loading (RequirementRegister, ComplianceMatrix,
  TeamWorkspace and others render an empty table), empty copy varies ("No
  findings.", "Nothing in the library yet.", "No bids yet.").
- **Will be:** loading = skeleton rows inside the real table (no spinner in
  content); empty = Newsreader heading + one sentence that says what will
  fill this and the action that does it; error = notice with the failed
  request named and a retry where one exists.

- **Built (batch 2):** `SkeletonRows` and `EmptyState`
  (`apps/web/src/components/ui/`) on the Bid dashboard. The `EmptyState`
  heading is a styled `<p>`, not a heading element, so no extra heading
  enters a screen's `<main>` for the E2E's heading locators to hit.
  `RequireAuth.tsx`'s app-level "Loading..." is unchanged.
- **Built (batch 3):** the processing screen's "No documents uploaded yet"
  is an `EmptyState` that waits for the first response (a loaded flag set
  from the existing `listDocuments` call; before, it showed while still
  loading). **No skeleton rows in batch 3** (deviation): the requirement
  register's E2E waits for the table's first `<tr>` as its "data has
  loaded" signal (`full-flow.spec.ts:629-638`), which placeholder rows would
  satisfy early, and neither screen has a loading flag for its table.
- **Built (batch 4):** each of the six tables gets a loaded flag from the
  fetch it already makes, `aria-busy` while loading, and an `EmptyState`
  **after** the table once the response is empty. The table itself always
  renders: the spec reaches these tables by position (`table.first()`,
  `:696`, `:780`, `:1182`; `table.nth(1)`, `:922`, `:1058`). Still no
  skeleton rows: `:701`, `:785` and `:1182` count `tbody tr` as "loaded"
  and `:922` counts it once. The team screen's candidate list shows
  "Loading candidates..." while its fetch is in flight instead of claiming
  "no candidates" before the response says so (display state only). The
  knowledge preview's "Loading..." stays a paragraph (no table there).
- **Built (batch 6, group A):** the import queue's two "Loading..."
  paragraphs are gone: both tables always render once shown, `aria-busy`
  while their fetch is in flight, **no skeleton rows** (`full-flow.spec.ts`
  `:344` expects exactly one batch row and `:397` counts item rows as
  "loaded"), and an `EmptyState` after the table once the response is
  empty. Selecting another batch clears the previous batch's items first,
  so they never show under the new batch's name (display state only).
- **Built (batch 6, group B):** the library table always renders,
  `aria-busy` while loading, no skeleton rows, an `EmptyState` after it
  once the response is empty. The employee profile has no table: while
  loading it shows a page title "Employee profile" and "Loading the
  profile..." under an `aria-busy` `<main>`, and a failed load shows the
  notice under that title. Its checkbox and Verify button exist only once
  the profile has loaded (`full-flow.spec.ts:496`: the load sets the box,
  so one ticked earlier would be reset). Two "Loading..." paragraphs are
  left, both unchanged: `RequireAuth.tsx`'s app-level one and the
  knowledge impact preview's (batch 4, no table there).
- **Built (polish pass):** the import items `EmptyState` now says "Select
  Review items again to check." instead of "Reload the page to see them.",
  because "Review items" on the open batch now refetches its items (a
  behaviour fix, decided; before, re-selecting the same batch did nothing).
  After the polish pass the same two "Loading..." paragraphs remain
  (`RequireAuth.tsx:10`, `KnowledgeSelections.tsx:237`, measured).

### Navigation
- **Before the restyle (historical):** no `<nav>`, no `<header>` (0 each, measured); dashboard links
  joined with " · " (`BidDashboard.tsx:44`); no route back to the dashboard
  from any screen.
- **Will be:** the app shell and workflow nav above; current item marked
  with amber text and a 2px amber underline plus `aria-current="page"`.

- **Built (batch 2):** `<header>` top bar and `<nav aria-label="Primary">`
  (`AppShell.tsx`), `<nav aria-label="Tender workflow">`
  (`TenderLayout.tsx`), a skip link to `#content`, current item amber with
  `aria-current="page"` (and the 2px underline in the workflow nav).

### Traceability drawer (signature component)
- **Before the restyle (historical):** a bordered `<aside>` appended at the bottom of the proposal
  page (`TraceabilityDrawer.tsx:47-54`), so on a long proposal it opens
  off-screen.
- **Built (batch 5, group A):** `apps/web/src/components/TraceabilityDrawer.tsx`
  is a **modal** side panel - `role="dialog"`, `aria-modal="true"`,
  labelled "Claim trace", over a scrim; right edge, `min(32rem, 100vw)`
  wide, so full screen at 390px. Focus moves to Close on open, Tab and
  Shift+Tab stay inside, Escape closes, and focus returns to the "Inspect
  trace" button that opened it. Modal is safe for the E2E: after the drawer
  opens, every locator in `full-flow.spec.ts:980-991` is scoped to its
  `<aside>`, and the next action on that page is a navigation (`:1029`).
  Cited and "in scope but not cited" evidence are two lists; each entry
  shows the excerpt in a sunk well, the source (humanised kind and title)
  and verified and validity badges. Kept for the spec: the only `<aside>`
  (`:980`), the "Claim trace" heading only once the trace has loaded
  (`:981` waits for it and `:985-987` read the result straight after), the
  uncited panel `role="alert"` containing "not cited" (`:982`), and "No
  additional in-scope evidence." verbatim (`:983`).
- **Built (polish pass):**
  - **"Claim trace" shares the top bar with Close** - heading left, Close
    right - instead of sitting under a bar holding Close alone. It still
    renders only once the trace has loaded (`:981`) and stays inside the
    `<aside>` (`:980`).
  - **The page behind does not scroll while the drawer is open**
    (a behaviour fix, decided): `overflow: hidden` on `<html>` while it is mounted, restored
    exactly on close, and right padding equal to the page scrollbar's
    measured width so hiding the scrollbar does not shift the page. Measured
    with a real 15px scrollbar: 0px shift; a wheel over the scrim moves the
    page 0px while open and 800px after close. Where nothing is laid out
    (`clientWidth` 0) no padding is added.

## Do's and Don'ts

### Do:
- **Do** read only semantic tokens (`--surface`, `--ink-2`,
  `--status-failed`) in component CSS.
- **Do** pair every status hue with its icon and label.
- **Do** set `font-variant-numeric: tabular-nums` on every number column.
- **Do** keep real `<table>`, `<section>`, heading-then-sibling structure;
  the E2E depends on it.
- **Do** show money through the shared INR formatter only.

### Don't:
- **Don't** use amber for a state, a badge or decoration.
- **Don't** use Newsreader in tables, forms, labels, buttons or numbers.
- **Don't** uppercase labels or add eyebrow text above headings.
- **Don't** add shadows to resting elements.
- **Don't** render a raw enum value or `JSON.stringify` output to an
  operator.
- **Don't** show GBP, USD or a bare number for money.

## Screen patterns

- **Register tables** (requirements, compliance, findings, talent): status
  column first after the identifier; row actions as quiet buttons in the
  last column; a count line above the table ("94 requirements, 12
  unverified mandatory") from data already in the response. Since the
  polish pass every register uses the one shared `.count-line`
  (`styles/layout.css`, 12px below it - at 8px it sat tight on the header
  band; RequirementRegister's own copy is gone), and actions columns are
  the shared `.cell-actions` (Tables).
- **Gates:** the gate panel sits at the top of its screen when the gate is
  pending (before the restyle it was at the bottom of every screen, e.g.
  `RequirementRegister.tsx:147-152`), so the decision an operator came for
  is the first thing they see; it moves below the table once decided.
  (Not on readiness - see Gate panel, batch 5.)
- **Evidence view:** excerpt in a `surface-sunk` well, source title and
  page/sheet as metadata in ink-2, verified and validity as badges. Never a
  raw `source_kind:` prefix (`TraceabilityDrawer.tsx:132`).
- **Findings lists** (commercial, red team): severity badge, finding text,
  resolution control in one row; resolved rows collapse to one line.
  **Red team deviation (batch 5, spec-forced):** its columns and cell text
  are fixed by `full-flow.spec.ts` - raw category first (`:1380`, `:1409`),
  raw severity second (`:1410`), plain "yes"/"no" fourth (`:1382`) - so it
  does not use `describeFinding`'s "Resolved" badge. A resolved row is
  carried by its treatment instead: ink-2 text, the severity badge without
  hue or tint, the finding collapsed to one line (kept, decided at the
  polish plan). **Since the polish pass** a resolved row shows its
  resolution note (fetched before, never shown) in the actions cell, td 4,
  which the spec does not read; the note wraps at 16rem (12rem below
  600px), so a long note does not widen the column and an all-resolved
  table does not shrink it (measured: 302px and 280px at 1280, 216px at
  390). The "Resolve" button count is unchanged (`:1083`, `:1088`).
- **Impact list** (knowledge selections): each affected fact as a row with
  its node type and the requirement or claim it feeds, replacing
  `JSON.stringify(row)` (`KnowledgeSelections.tsx:165`); the confirm action
  states what will change ("Deselect and mark 3 facts for review"), using
  only counts the response actually carries.
  **Batch 4 deviation (spec-forced):** the rows stay one `<li>` each whose
  text is exactly `JSON.stringify(row)` - `full-flow.spec.ts:1253` counts
  every `<li>` on the page and `:1258` `JSON.parse`s every `ul li` - shown
  as a sunk well of wrapped data text inside `<code>` (no added text). The
  readable part is a summary line above the list, outside it ("3
  downstream facts affected: Compliance 1, Proposal claim 1, Requirement
  1", `format/impactSummary.ts`). The confirm label stays exactly "Confirm
  select" / "Confirm deselect" (`:1323` regex, `:1325` exact text).
  **Since the polish pass** the previewed item is named under the heading
  ("For Dana Whitfield") in a `<p>` - the heading's text, every `<li>`
  count and the `ul li` parse are untouched.
- **Processing:** task list as a vertical sequence with running/ready/failed
  badges, and the existing "Live updates are currently unavailable" notice
  (`UploadAndProcessingStatus.tsx:245-248`) as a warning notice.
- **Empty / loading / error:** as under Components; every screen gets all
  three.

## Accessibility

- **Contrast targets:** 4.5:1 for all text at the sizes used here (all
  below 18px except page titles), 3:1 for control boundaries, focus ring
  and icons. Every token pair above is measured against these.
- **Focus ring:** 2px solid amber, 2px offset, on `:focus-visible` only;
  the landing's own rule (`apps/landing/src/styles/tokens.css:37`) except
  the offset, which is 3px there. 6.45:1 on
  `surface`, 5.84:1 on `surface-raise` (measured). Before the restyle
  apps/web had no focus styling at all (0 `:focus`/`outline` rules); after
  the polish pass it has 8 `:focus-visible` rules (measured).
- **Keyboard order:** DOM order equals visual order; skip link to main
  content; workflow nav reachable by Tab, arrow keys not required; the
  drawer traps focus while open and returns it on close; no `tabIndex`
  above 0. Before the restyle there were 0 `tabIndex` and 0 `div` click
  handlers (measured) - everything clickable a native button or link - and
  that stayed true. Two `tabIndex` values were added: 0 on the
  `TableScroll` region (`TableScroll.tsx:33`, so a keyboard user can scroll
  it) and -1 on the skip-link target (`AppShell.tsx:59`, focusable without
  being a Tab stop); each batch's keyboard pass and the polish pass
  measured no `tabIndex` above 0.
- **Reduced motion:** all transitions sit behind
  `@media (prefers-reduced-motion: no-preference)`; under `reduce`, state
  changes are instant. Before the restyle there was no motion and no media
  query (0, measured); now 2 `prefers-reduced-motion` blocks guard every
  transition (measured).
- **Motion durations:** `--dur-fast` 120ms (hover, press), `--dur-base`
  180ms (badge/notice appear, row expand), `--dur-panel` 240ms (drawer).
  Ease-out only. No page-load sequences.
- **Live regions:** processing task updates announced politely
  (`aria-live="polite"` on the task list); errors keep `role="alert"`.
- **Labels:** every control keeps an accessible name equal to its visible
  label (also what the E2E queries by).

## Copy rules

- **Never claim more than the code does.** This repo has a standing
  KNOWN_LIMITATIONS pattern for prose asserting a wiring that does not
  exist ("a comment asserting a wiring that doesn't exist is invisible to
  grep"); UI copy is the user-facing version of the same failure. A label,
  empty state or success message may only describe what the response
  actually confirms. Examples: "Graph is fresh" only when `status.fresh`
  is true; no "Saved" toast for a request that has not returned; no
  "AI-verified" wording anywhere, since verification is a human action in
  this product.
- **Sentence case, plain verbs, the action keeps its name** (frontend-design):
  the button "Resolve" produces "Resolved", not "Done".
- **Errors name what failed and what to do.** Keep the status code for
  operators ("Upload failed (413): file is larger than the limit") where
  the code is the useful part.
- **Money is INR, through one shared formatter** (decided), built on
  `Intl.NumberFormat('en-IN')`, no new dependency, two named modes:
  - `compact` for tables and cards: `₹1.20 Cr`, `₹45.0 L`, below one lakh
    the full figure.
  - `full` for detail views and anywhere an exact amount matters:
    `₹1,20,00,000` (Indian digit grouping).
  - **Before the restyle apps/web formatted no money itself** (measured:
    no `Intl`, `toLocaleString`, `₹`, `INR`, `crore` or `lakh` in `src`).
    After the polish pass `Intl` is used for dates, years and the unused
    `formatInr` only - still no amount is formatted on any screen.
    `PricingFindingResponse.delta` is fetched but never shown
    (`api/client.ts:805`), and the only currency column is the raw
    `currency` string (`CommercialFindings.tsx:141`, rendering "-" for both
    fake-provider pricing documents). **But money does reach the screen,
    unformatted, inside backend-written prose:** commercial notes and
    red-team finding text read "Grand total is 44820.0, but the line items
    sum to 57000.0" and "'Cross-Border Logistics' is priced in 'EUR', but
    the sheet's other line items use 'USD'" (screenshots 08 and 12,
    fake-provider data; the currencies come from the synthetic fixture). A
    frontend formatter cannot reach an amount embedded in a backend string.
    Decided: see Decisions, item 8.
  - **Batch 4: `formatInr` still has no caller.** The only structured
    amount on these screens, `delta`, is `actual - expected` in the pricing
    sheet's own currency (`domain/services/pricing_checks.py:265`, `:373`,
    `:501`), and the fixture's sheets are USD and EUR with `currency` null
    on both pricing documents. Labelling it "₹" would claim INR for a
    non-INR figure; a bare number breaks "Don't show ... a bare number for
    money". So `delta` stays unshown, and the commercial screen's Currency
    column shows the stored code or "Not stated". The first caller waits for
    the INR demo data (decision 8, Phase 4 note).
  - **Batch 5: still no caller.** None of the proposal, drawer, red team,
    graph or readiness screens shows a structured amount: readiness counts
    are counts, and amounts appear only inside backend prose (proposal
    text, red-team findings), which stays as written.
  - **Batch 6: still no caller.** No talent model has a rate, salary,
    cost or currency (`db/models/knowledge.py`); `years_experience` is a
    count of years and an import payload's `confidence` a 0-1 score.
  - **Polish pass: still no caller.** No screen shows a structured amount;
    graph `confidence` is a percent, readiness counts are counts.
  - Note: the landing's copy spells the unit out ("₹68.4 crore",
    `apps/landing/src/data/tender.ts:11`) as hand-written strings. The app's
    compact mode uses "Cr" as decided. Not changed here; flagged so the
    difference is known.
- **Dates:** `10 Oct 2026, 14:05` (en-IN), never a raw ISO string. The gate
  panel's raw ISO decided date was replaced in batch 3 (`formatDate`,
  viewer's local zone). en-IN abbreviates September as "Sept" (measured in
  Node and Chromium).
  **Date-only values (batch 6):** `formatDay` (`format/date.ts`) shows a
  backend `date` as `31 Mar 2025`, no clock time. It reads the three
  numbers and formats in UTC: `formatDate("2025-03-31")` treats the value
  as midnight UTC and shows the 30th in any zone west of UTC (tested).
  Audit at batch 6 (grep): every existing `formatDate` caller passes a
  timestamp (`GateDecisionPanel.tsx:123`, `:138`; `GraphExplorer.tsx:123`;
  `Readiness.tsx:206`, `:212`); the only date-only value on any screen is
  the library's raw `valid_until` (`TalentLibrary.tsx:121` before batch
  6). Group B gives `formatDay` its screen callers: the library's Valid
  until and the profile's "Availability as of"; the profile's "Last
  verified" timestamp goes through `formatDate`. (Line numbers above are as
  at batch 6.)
  **Polish pass:** graph node properties in words go through them too -
  date-only values through `formatDay`, `*_at` timestamps through
  `formatDate` (`routes/graphExplorerValues.ts`). `formatDate` callers after
  the polish pass (measured): `GateDecisionPanel.tsx:123`, `:138`;
  `GraphExplorer.tsx:128`; `Readiness.tsx:249`, `:255`;
  `EmployeeProfile.tsx:189`; `TalentImportReview.tsx:290`;
  `graphExplorerValues.ts:164`. `formatDay` callers: `TalentLibrary.tsx:165`,
  `EmployeeProfile.tsx:175`, `talentImportValues.ts:106`,
  `graphExplorerValues.ts:162`.

## What the app will not do

- No WebGL, Three.js, GSAP or canvas effects in apps/web unless the owner
  says so.
- No Tailwind, no component library, no CSS-in-JS. Plain CSS files with
  custom properties.
- No new runtime dependency without asking (fonts are files, not packages;
  icons are authored inline SVG, not an icon package).
- No TanStack Query adoption; data fetching is untouched by the restyle.
- No light theme in this phase.
- No behaviour changes smuggled into a restyle batch: if a batch finds a
  bug, it is recorded, not fixed in the same diff.
- No orchestrated page-load animation, no decorative motion.

## Current state before the restyle: what the screenshots showed (historical)

Captured 2026-10-10 from the fake-provider stack (`scripts/fake-stack.ps1`,
all three app containers confirmed `LLM_PROVIDER=fake`; live graph intact at
3,585 nodes / 47 tenders, measured), at 1280 and 390px, with the default
light colour scheme. Not committed. All data is **fake-provider**.

- **Every screen is browser-default HTML** - the OS UI font,
  default blue links, grey OS buttons, no spacing, content flush against the
  left edge at 1280px (no gutter: `index.css:6-8` zeroes `body` margin and
  nothing replaces it).
- **No orientation.** No header, no "back to bids", no indication which
  tender you are in; nine dot-separated links only on the processing screen.
- **Status cannot be read at a glance.** Raw enums (`waiting_for_human`,
  `fully_compliant`, `deselected_fact_in_use`), "yes"/"no" columns, and
  `[category]` prefixes; nothing distinguishes a pending gate from a decided
  one except the presence of buttons.
- **The four states, as they render today:**
  - *Blocked* (tender `4c8a4a6b`, resource gate pending): the gate panel
    sits under an empty, header-only table ("Roles: 0, Candidates: 0") with
    Approve enabled; nothing says "waiting for you".
  - *Running* (tender `e4e0aa97`): run status `running` since 2026-09-04 with
    every task `pending` - shown exactly like a live run; no age, no "since",
    so a stuck run is indistinguishable from an active one.
  - *Failed* (tender `baf702f6`): "Last run: failed." and a Start button; the
    failed task and its error are not shown (the run has 1 failed task,
    measured).
  - *Ready*: **no tender in the live data reaches it** - no run is
    `completed` and no readiness report has `ready: true` (measured). The
    only "ready" visuals available are approved gates (`5b19f88a`'s first
    four). The ready treatment is therefore specified here but unseen.
  - The processing screen for `5b19f88a` lists four tasks "completed" while
    the run is `waiting_for_human` at the finding-resolution gate; the
    screen never says the run is waiting on a person.
- **Readiness** repeats one blocker three times verbatim (three distinct
  findings, identical message) and shows `snake_case` count keys.
- **Proposal**: section text in a monospace textarea; the traceability
  drawer opens below the last section, off-screen on a long proposal; page
  is 6px wider than the viewport at 1280 (`width: 100%` textarea plus
  border, `ProposalWorkspace.tsx:97`).
- **Graph explorer** is blank until a raw node id is typed: status and
  contradictions are fetched only after a node loads
  (`GraphExplorer.tsx:77`). The graph itself is intact (measured above).
- **Horizontal overflow at 390px** (document `scrollWidth`, measured):
  requirements 521, compliance 500, commercial 407, proposal 396, red team
  721, talent import 420. Login, dashboard, create bid, processing, team,
  knowledge, graph, readiness, talent library and employee profile fit.
- **Login at 390px**: labels and inputs flow inline and wrap mid-pair
  ("Email[field]Password / [field][Log in]").
- **Rate-limit errors are mislabelled.** Pacing the capture was necessary:
  the API's limiter (100 requests and 5 logins per 60s per IP,
  `apps/api/src/tenderos_api/rate_limit.py:32-35`) returned 429 on a first
  unpaced attempt, and the login form showed "Could not reach the server.
  Please try again." - false: the server was reached and refused. That text
  is what `formatErrorDetail.ts:12-13` returns for any non-`ApiError`.
  Cause, measured afterwards with curl: the limiter's 429 carries no
  `access-control-*` header (the 401s before it do), because `_rate_limit`
  is the outermost middleware (`main.py:182`, outside `CORSMiddleware` at
  `main.py:70`), so the browser surfaces it as a network failure. Details
  in KNOWN_LIMITATIONS.md. A backend fix, not a restyle fix; until it
  lands, the notice copy for a non-`ApiError` must not assert the server
  was unreachable ("The request did not complete. Check your connection
  or try again in a minute." is accurate for both causes).

## Restyle batches

Six batches, in dependency order. Each ends with the fake E2E,
`scripts/e2e.ps1` (fake provider), as its check, plus `npm run lint`,
`npm run build` (which runs `tsc -b`) and `npm test` (vitest, 37 tests at
baseline) in `apps/web`.

**E2E constraint (measured from `apps/web/e2e/full-flow.spec.ts`):** 41
`getByRole`, 6 `getByLabel`, 3 `getByText` and 63 `locator()` calls,
including structural selectors (`tbody tr`, `td:nth-child(2)`/`(3)`,
`locator("strong")`, and two `xpath=following-sibling` hops from a heading,
`:56` and `:1455`) and exact raw-enum text matches
(`/^deselected_fact_in_use$/`, `:1409`). Vitest adds 12 `getByRole` and 28
`getByText` (measured). A batch that changes a label, humanises an enum, or
moves a heading relative to its section updates the affected selectors **in
the same batch**, listed in that batch's plan, and the E2E must still pass
6/6. No `data-testid` exists today; adding them is a test change and is
proposed per batch, not assumed.

**Selectors the shell can break (read from the spec, measured).** The
shell (top bar, tender header, workflow nav) sits outside `<main>`. Batch
2's plan must list these lines and state, for each, that the shell does
not affect it:
- `full-flow.spec.ts:53-57`, `gatePanel()`: `getByRole("heading", { name })`
  then `xpath=following-sibling::section[1]`. Requires the gate `<h2>` and
  the panel's `<section>` to stay **siblings under one parent**. Wrapping a
  `<table>` in a scroll `<div>` does **not** break it (the table is not on
  the axis). Wrapping the panel's `<section>` in anything, or moving the
  panel above its `<h2>`, **does**. `name` is a substring match by default,
  so no shell heading may contain a gate heading's text ("Requirement
  review gate", etc.). The "gate panel at the top when pending" pattern
  (Screen patterns) therefore moves the `<h2>` *and* its `<section>`
  together.
- `full-flow.spec.ts:1453-1456`: `locator("h2", { hasText: "Blockers" })`
  then `xpath=following-sibling::ul[1]/li`. Requires the blockers to stay a
  `<ul>` that is a sibling of that `<h2>`. A table wrapper elsewhere does
  not break it; turning the blocker list into a table or wrapping the `<ul>`
  does.
- `full-flow.spec.ts:1451-1452`: `page.locator("strong").first()` is
  page-wide, so **no `<strong>` anywhere in the shell** (it would precede
  the readiness verdict in DOM order). **Stricter than `.first()` suggests:**
  `:1451` is `expect(page.locator("strong")).toBeVisible()`, a strict-mode
  assertion, so a *second* `<strong>` anywhere on the readiness page fails
  it, wherever it sits. StatusBadge is a `<span>` for the same reason.
- `full-flow.spec.ts:1253` and `:1258` (added at batch 2): the impact-preview
  poll counts `page.locator("li")` page-wide and then parses every
  `page.locator("ul li")` text as JSON. A `<ul>`/`<li>` in the shell would
  make the poll pass before the preview renders and add nav labels to the
  parsed list - a silent change, not a clear failure. **The shell uses no
  `<ul>` or `<li>`**; links sit directly in `<nav>`.
- Also page-wide, and kept out of the shell for that reason (batch 2
  audit): `locator("table")` (15 sites, e.g. `:344`), `locator("aside")`
  (`:980`), `locator("section", { has: heading })` (`:389`, `:827`),
  `input[type="file"]` (`:531`), and page-wide `getByRole("button")` names
  (`:511` "Create bid", `:1083` "Resolve", and others). The shell contains
  no `table`, `aside`, `section`, heading, `button` or `input`;
  `apps/web/src/shell/AppShell.test.tsx` asserts this.
- `full-flow.spec.ts:513`: `getByRole("link", { name: tenderName })` on the
  dashboard, substring match - the dashboard must not show the tender name
  as a second link (no breadcrumb or "recent tenders" in the top bar).
- `getByText`: three calls. `:498` is scoped to `dl` and `:983` to the
  drawer; `:1254` ("No downstream facts would be affected.") is page-wide
  but cannot match nav text. None is at risk from the nav.

**Every status gets a render test (no live data for Ready).** No tender in
the live data reaches Ready (no completed run, no `ready: true` report,
measured), and screenshots cannot show it. So each of the five statuses
(plus Neutral) gets a vitest render test with fixture props in the batch
that introduces its component - icon present, label text from the label
map, the matching status class - using the existing vitest and Testing
Library setup, **no new dependency**.

1. **Foundation (built).** The token stylesheet is rewritten to semantic
   names under the `[data-theme]` hook; self-hosted fonts (binary files,
   separate approval) with the numerals re-check; base element styles (body,
   headings, links, tables, forms, buttons, focus ring, selection,
   scrollbars); reduced-motion guard. Screens change only through inherited
   element styles.
2. **Shell and shared components (built).** App shell and workflow nav
   (tender header deferred, see Layout); button variants, status badge with
   icon set and label map, notice, empty state, skeleton, form field, table
   scroll region, INR and date formatters, with the render tests above.
   Applied to Login, Bid dashboard (shipped **without** its Status column,
   decision 9) and Create bid. Details under "Batch 2: as built".
3. **Intake and requirements (built).** Upload and processing status (task
   list, live-updates notice), requirement register, and the gate panel
   (first used here, then reused unchanged). Details under "Batch 3: as
   built".
4. **Analysis gates (built).** Compliance matrix, team workspace,
   commercial findings, knowledge selections with the impact summary. No
   INR use (no INR amount to show). Details under "Batch 4: as built".
5. **Proposal, evidence and readiness (built).** Proposal workspace,
   traceability drawer, red-team findings, graph explorer, readiness.
   Details under "Batch 5: as built".
6. **Talent (built).** Talent library, import and review queue, employee
   profile. Details under "Batch 6: as built".
7. **Polish pass (built).** Three groups after batch 6: shared fixes
   (buttons, badges, cell alignment, count line, actions column, a shared
   underscore-break helper), screen fixes (eight screens, two of the
   changes behaviour fixes: "Review items" refetches the open import
   batch, and the drawer locks page scroll while open), and this file's
   consistency pass. Details under
   "Polish pass: as built".

### Batch 2: as built

- **Routes.** `apps/web/src/App.tsx` keeps the same 16 paths (15 protected
  plus `/login`) and screens. One `RequireAuth` now wraps the shell as a
  layout route instead of one per route; tender routes nest under
  `/tenders/:tenderId` (`TenderLayout`). `apps/web/src/AppRoutes.test.tsx`
  renders the real route tree and pins that an unauthenticated deep link
  goes to `/login` and returns to the same link after sign-in.
- **Checks at batch 2 (measured):** fake E2E (`scripts/e2e.ps1`) 1 passed -
  the spec is a single test, so "6/6" above does not describe it; vitest
  114 (37 at baseline). Contrast: 50 new foreground/background pairs, all
  at or above target (closest: failed badge on `surface-raise`, 4.58:1).
  Under simulated protanopia the failed badge's text on its tint measures
  4.29:1 (Machado 2009, the method in Colors); its icon and label still
  carry the state.
- **Every new export and the batch that is its first caller:**

| Export | File (under `apps/web/src/`) | First caller |
|---|---|---|
| `Button` | `components/ui/Button.tsx` | batch 2 (Login, Create bid) |
| `buttonClassName`, `ButtonVariant` | `components/ui/buttonClassName.ts` | batch 2 (Button, Bid dashboard) |
| `Notice` | `components/ui/Notice.tsx` | batch 2 (Login, Bid dashboard, Create bid) |
| `Field` | `components/ui/Field.tsx` | batch 2 (Login, Create bid) |
| `EmptyState` | `components/ui/EmptyState.tsx` | batch 2 (Bid dashboard) |
| `SkeletonRows` | `components/ui/SkeletonRows.tsx` | batch 2 (Bid dashboard) |
| `TableScroll` | `components/ui/TableScroll.tsx` | batch 2 (Bid dashboard) |
| `StatusIcon`, `InfoIcon` | `components/ui/icons.tsx` | batch 2 (StatusBadge, Notice) |
| `StatusBadge` | `components/ui/StatusBadge.tsx` | batch 3 - **wired** (tender bar, gate panel, processing, requirements) |
| `describeStatus`, `STATUS_MAP` | `status/statusMap.ts` | batch 3 - **wired** (gate, run, task, parse badges; `STATUS_MAP` read by the parse-label test) |
| `describeReadiness` | `status/statusMap.ts` | batch 3 - **wired** (gate panel's final-readiness summary) |
| `describeFinding` | `status/statusMap.ts` | batch 4 - **wired** (commercial findings); **not** used on red team (batch 5: the spec fixes that table's cell text, see Screen patterns, "Findings lists") |
| `StatusTone`, `StatusDescriptor`, `StatusFamily` (types) | `status/statusMap.ts` | batch 2 (StatusBadge, icons) |
| `formatDate` | `format/date.ts` | batch 3 - **wired** (gate panel: request and decided dates) |
| `formatInr`, `pickCompactUnit`, `LAKH`, `CRORE`, `InrMode`, `CompactUnit` | `format/inr.ts` | **still unused after batch 5** - no INR amount on any screen (Copy rules, "Money"); waits for the INR demo data (decision 8, Phase 4) |
| `AppShell`, `PageContent` | `shell/AppShell.tsx` | batch 2 (App.tsx) |
| `TenderLayout` | `shell/TenderLayout.tsx` | batch 2 (App.tsx) |
| `AppRoutes` | `App.tsx` | batch 2 (App, AppRoutes.test.tsx) |

### Batch 3: as built

- **Screens:** `UploadAndProcessingStatus.tsx`, `RequirementRegister.tsx`,
  and the shared `GateDecisionPanel.tsx` (see Components for each).
- **Spec-forced choices** (`apps/web/e2e/full-flow.spec.ts` is unchanged,
  byte-identical to main):
  - **Parse status text** (`:558-566` compares each cell's `textContent`,
    read with `allTextContents()`, to `"parsed"`). The parse badge's DOM
    text is the backend word and CSS `text-transform: capitalize` draws it
    as "Parsed"; `textContent` is unaffected by CSS. Every parse label in
    `statusMap.ts` is that capitalised word, and a vitest enforces it.
  - **Reviewed stays "yes"/"no"** (`:653`, td index 5, exact text); no
    column is added before it.
  - **No skeleton rows** (`:629-638`, see Empty states).
- **Chosen, not in this file** (marked in code): `describeVerified`
  (`status/statusMap.ts`) - verified -> Ready "Verified"; unverified and
  mandatory -> Warning "Not verified" (what the requirement review gate
  counts as "Unverified mandatory"); unverified and optional -> Neutral
  "Not verified". `humanize` (`format/humanize.ts`) turns `task_type` into
  words ("requirement_extraction" -> "Requirement extraction"). Requirement
  `category` is still shown as the backend writes it (lowercase words; no
  label map).
- **`<li>` audit for the gate summary's `<ul>` -> `<dl>`** (measured on
  fake-provider tenders, before and after): the pages whose spec lines read
  `li` page-wide render no gate panel - proposal (`:971`, `:974`) 5 `li`
  before and after, 3 with "/ unsupported]" before and after; knowledge
  selections (`:1253`, `:1258`) 0 and 0. Only the four gate pages lost
  their summary `<li>`s, and no spec line reads `li` there.
- **Run state seen after a decision** (measured): a Reject left the run
  `waiting_for_human` for a few seconds before the worker moved it to
  `failed`; the screens show what the API returns at load.
- **Checks at batch 3 (measured):** fake E2E (`scripts/e2e.ps1`) passed;
  vitest 146 (114 at batch 2). Contrast: 40 new pairs, all at or above
  target after the rejected-row fix (closest: failed badge on
  `surface-raise`, 4.58:1). Greyscale, deuteranopia and protanopia passes
  on the real screens: every badge reads by icon and label; under
  deuteranopia Verified and Rejected/Failed collapse toward one hue as the
  Colors section predicts.
- **New exports and their first caller:**

| Export | File (under `apps/web/src/`) | First caller |
|---|---|---|
| `RunStatusContext`, `useReportRunStatus` | `shell/runStatus.ts` | batch 3 (TenderLayout; requirement register, processing) |
| `describeVerified` | `status/statusMap.ts` | batch 3 (requirement register) |
| `humanize` | `format/humanize.ts` | batch 3 (processing task list) |
| `TableScroll` `stickyLastColumn` prop | `components/ui/TableScroll.tsx` | batch 3 (requirement register) |

### Batch 4: as built

- **Screens:** `ComplianceMatrix.tsx`, `TeamWorkspace.tsx`,
  `CommercialFindings.tsx`, `KnowledgeSelections.tsx` (see Components and
  Screen patterns for each). Run status badge wired on compliance, team
  and commercial from the run each already fetched; none on knowledge
  selections (no run fetch). Pending gates sit above the tables.
- **Spec-forced choices** (`apps/web/e2e/full-flow.spec.ts` unchanged,
  byte-identical to main):
  - **Compliance option values stay backend words** (`:714` reads
    `inputValue()`, `:736` selects by value); only the option text is
    mapped. The row's Approve is removed, not disabled, once approved
    (`:728`, `:737`).
  - **Candidate id stays raw and alone in its cell** (`:855`, exact
    `toHaveText`); "Select"/"Deselect" labels unchanged (`:877`, exact).
  - **Both commercial tables always render, documents first** (`:922`,
    `:1058` use `table.nth(1)`).
  - **Impact rows stay raw JSON `<li>`s; confirm labels unchanged** (see
    Screen patterns, "Impact list").
  - **No skeleton rows** on any of the six tables (Empty states).
- **Chosen, not in this file** (marked in code): `describeApproved`,
  `describeSelected` (Status badge); `formatPercent` (Tables);
  `summarizeImpact` rules - the total is the number of rows (= `<li>`s);
  the per-label breakdown counts only rows with a string `labels` array,
  once per distinct label, so it can exceed the total; unrecognised rows
  count in the total only; nothing renders for zero rows. Finding type and
  knowledge item type through `humanize`; Neo4j labels re-cased only
  ("ProposalClaim" -> "Proposal claim"). Null texts: "Not scored"
  (confidence, score), "None stated" (minimum experience), "Not stated"
  (currency). Resolved findings collapse to one line (CSS clamp; full text
  stays in the DOM).
- **`<li>` audit** (measured with fixture data in the browser): knowledge
  selections 0 `li` / 0 `ul li` with no preview open, 3 / 3 (all 3
  `JSON.parse`) for a 3-row preview, 0 / 0 for an empty one; compliance,
  team and commercial render none (the gate panel's `<ul>` is only for
  `final_readiness`).
- **Checks at batch 4 (measured):** vitest 183 (146 at batch 3), stable
  over three runs; fake E2E (`scripts/e2e.ps1`) 1 passed on the final code.
  One earlier fake run failed at step 13 (`:1466`, readiness read "Ready")
  for a reason outside these screens: when the worker has already opened
  the `final_readiness` gate before step 14, that gate's readiness report
  (pre-deselection, ready) is what `Readiness.tsx` shows on mount, and
  `:1451` passes on it before the clicked report replaces it; recorded for
  KNOWN_LIMITATIONS. Page `scrollWidth` 390 at 390px on all four screens in
  every state. Contrast: 23 new pairs, 22 at or above target as first
  built; the failed badge on a hovered row measured 4.36:1 (the badge tint
  was translucent, so the row-hover overlay showed through; the same
  pairing existed on the batch 3 documents table). Fixed in the batch 4
  fixes: `StatusBadge.css` lays each tint over an opaque `--surface` base,
  so every tone measures its own-tint ratio on the page, the raised panel
  and a hovered row alike (lowest: failed, 5.17:1; neutral, which has no
  tint, 7.56:1 on a hovered row; the rejected-row treatment, 4.69:1
  hovered - all measured). Greyscale, deuteranopia and protanopia
  passes: every badge reads by icon and label; under deuteranopia Critical
  and Resolved share one hue, and the amber Approve sits close to Major's
  yellow, as Colors predicts. Keyboard: a ring on every stop, no `tabIndex`
  above 0. Bare buttons 14 remain (21 at batch 3).
- **New exports and their first caller:**

| Export | File (under `apps/web/src/`) | First caller |
|---|---|---|
| `formatPercent` | `format/percent.ts` | batch 4 (compliance confidence, team score) |
| `summarizeImpact`, `labelWords`, `ImpactSummary` | `format/impactSummary.ts` | batch 4 (knowledge selections) |
| `describeApproved` | `status/statusMap.ts` | batch 4 (compliance) |
| `describeSelected` | `status/statusMap.ts` | batch 4 (team candidates, knowledge selections) |
| `.count-line`, `.cell-id`, `.cell-num` (classes) | `styles/layout.css` | batch 4 (the four screens); `RequirementRegister`'s own `.requirements__count` is the same rule, left for the polish pass |

### Batch 5: as built

- **Screens:** `ProposalWorkspace.tsx`, `TraceabilityDrawer.tsx`,
  `RedteamFindings.tsx` (group A); `GraphExplorer.tsx`, `Readiness.tsx`
  (group B). Run status badge wired on readiness from the run it already
  fetched; none on proposal, red team or graph (no run fetch).
- **Spec-forced choices** (`apps/web/e2e/full-flow.spec.ts` unchanged,
  byte-identical to main):
  - **Proposal sections stay direct children of `<main>`** (`:952`); the
    empty state is not a `<section>`.
  - **Claim tag keeps "/ unsupported]"** (`:971`, `:974`) - the raw-word
    badge (Status badge, batch 5).
  - **Drawer:** the only `<aside>`; heading only once the trace has loaded;
    uncited panel `role="alert"` with "not cited"; empty sentence verbatim
    (`:980-987`). Modal, because nothing outside the `<aside>` is touched
    while it is open (Traceability drawer).
  - **Red team cells fixed** (Screen patterns, "Findings lists"): raw
    category with `<wbr>` after each underscore (no added text), raw
    severity, plain "yes"/"no".
  - **Readiness verdict only for a report generated in this visit** (Label
    map): the `<strong>` text is unchanged ("Ready"/"Not ready", `:1452`,
    `:1466`), and the "Blockers" `<h2>` keeps its sibling `<ul>` of `<li>`
    holding each message (`:1454-1456`, `:1479`). No locator on /readiness
    reads a table or the counts (grep of `:1436-1494`), so the counts are a
    `<dl>`.
- **Readiness: stored vs current (follow-up, not built).** `GET /readiness`
  returns the latest stored report (`readiness_service.py:450-459`), and
  no response this screen fetches carries the tender's current state
  version, so the screen cannot tell a stored report is current. A mount
  `GET` that answers after Generate is dropped, so a stored report never
  replaces the one just generated. The
  follow-up is a **backend change**: `GET /readiness` adds
  `current_state_version` (or `is_current`), so the comparison is one
  atomic request. Rejected: comparing against `getTender` (two requests,
  which can race a state change between them) and generating on mount
  (writes a report on every visit).
- **Chosen, not in this file** (marked in code): `proposalSection` and
  `validity` families, `describeStatusKeepingRawWord`,
  `describeGraphFreshness` (Status badge); readiness count labels - a
  hand-written map of the 11 keys (`Readiness.tsx`, `COUNT_LABELS`; e.g.
  `critical_major_redteam_findings` -> "Open critical or major red-team
  findings", since it counts both, `readiness_service.py:45`, `:248`),
  `humanize` for unknown keys; graph values (`routes/graphExplorerValues.ts`)
  - plain values as text, booleans "Yes"/"No", null "Not set", arrays
  joined, objects as their JSON in `<code>` (never "[object Object]"),
  backend words in summaries humanised; graph status "Fresh"/"Stale".
  Node names come from the properties `graph_projection/projector.py`
  actually writes: ProposalClaim (`:376-381`, no `claim_text` projected)
  and Evidence (`:319-324`, no title) carry no name, so they read "{claim
  type} claim" and "{source kind} evidence" rather than a raw UUID; every
  other label has a name or title.
- **Tables (measured at 390px, actions populated and empty):** red team -
  no sticky last column (it would leave 38px for a 192px column); graph
  neighbours - sticky last column kept (166px for 128 with Explore, 241
  for 138 without).
- **`<li>` / `<strong>` / `<table>` audit** (fixture data, measured in the
  browser): proposal 4 `li` (3 claims + "No claims"), 1 matching "/
  unsupported]", 0 `strong`; red team 1 table, 0 `li`; readiness 0
  `strong` for no report or a stored one, exactly 1 ("Not ready") after
  Generate, blockers `<ul>` 3 `li`, 0 tables; graph 0 `strong`, 1 table.
- **Checks at batch 5 (measured):** vitest 238 (183 at batch 4), stable
  over three runs; fake E2E (`scripts/e2e.ps1`) passed on group A's build
  and on group B's final build. The group B run hit the batch 4 failure
  condition - a stored version-8 "Ready" report with no blockers existed
  before step 14 - and passed: the stored report showed no `<strong>`, so
  `:1451` waited for the generated version-13 "Not ready" report. Page
  `scrollWidth` 390 at 390px on all five screens in every state. Contrast:
  27 new pairs in group A and 25 in group B, all at or above target
  (lowest 5.17:1, failed badge). Greyscale, deuteranopia and protanopia
  passes on proposal, drawer, red team, readiness and graph. Keyboard: a
  ring on every stop, no `tabIndex` above 0; the drawer moves focus to
  Close, keeps Tab and Shift+Tab inside, closes on Escape or a scrim click,
  and returns focus to the exact "Inspect trace" button. Bare buttons 5
  remain, inline styles 4, `--color-danger` readers 4 (all talent screens).
- **New exports and their first caller:**

| Export | File (under `apps/web/src/`) | First caller |
|---|---|---|
| `describeStatusKeepingRawWord` | `status/statusMap.ts` | batch 5 (proposal claim tag, red-team severity) |
| `describeGraphFreshness` | `status/statusMap.ts` | batch 5 (graph explorer) |
| `proposalSection`, `validity` families | `status/statusMap.ts` | batch 5 (proposal, traceability drawer) |
| `graphValue`, `nodeSummary`, `contradictionParts` | `routes/graphExplorerValues.ts` | batch 5 (graph explorer) |
| `.badge-raw-word` (class) | `styles/layout.css` | batch 5 (proposal, red team) |
| `describeFinding` | `status/statusMap.ts` | unchanged: commercial only; **not** on red team (spec-forced) |
| `formatInr` | `format/inr.ts` | **still unused** - no INR amount on any screen |

### Batch 6: as built

**Group A - import and review queue** (`TalentImportReview.tsx`; the
library and employee profile are group B).

- **Spec-forced choices** (`apps/web/e2e/full-flow.spec.ts` unchanged,
  byte-identical to main):
  - **Upload labels verbatim, colons included** (`:317` substring match,
    `:329` waits for the input to be enabled) - Forms and inputs.
  - **The batches table is the page's first `<table>`; its row's `<td>`s
    are read by index** (`:344-353`): Type, Status, Total, Completed,
    Failed in that order, the Type cell a `<td>` (a `<th>` shifts every
    index), and the three number cells hold only digits (`Number()`).
  - **The Items `<section>` is a direct child of `<main>`** (`:388-390`):
    "`section` that has a heading 'Items'" also matches any ancestor
    section, so a wrapper would add the batches' rows to the `:402` count.
    A vitest pins one such section, not nested.
  - **One row per item, Approve inside it, labels unchanged** (`:402-424`).
  - **No skeleton rows** in either table (`:344`, `:397`).
- **Chosen, not in this file** (marked in code): the batch 6 status
  families (Label map); import type labels - `manual` reads "Capability
  statement", its only writer being that upload
  (`apps/api/src/tenderos_api/routes/knowledge.py:222`); source labels -
  `row_N` "Row N" (the real sheet row), `project_N` / `capability_N`
  counted from 1, `_batch_parse_failure` "Whole file", file names as
  written; the Detail cell shows the backend's reason **and** the parsed
  fields (before, the reason replaced them), in a fixed key order because
  JSONB does not keep one, null as "Not set", an empty mention list as
  "None", `confidence` as "Extraction confidence" through `formatPercent`,
  years as "1 year" / "12.5 years", unknown keys humanised with a `<code>`
  JSON fallback (`routes/talentImportValues.ts`); count lines ("5
  batches, 1 needs review").
- **Recorded, not fixed (pre-existing):** "Review items" on the batch that
  is already open does not refetch its items - the effect is keyed on the
  selected id, which does not change - although the screen's own comment
  says re-selecting shows progress. The hint and the empty state therefore
  say "reload". Behaviour fix for a later block.
- **Measured (fake fixture data, real Vite dev server, page.route):** page
  `scrollWidth` 390 at 390px in eight states (empty, loading, load error,
  populated, items open, items loading, items empty, items with no row
  actions). The spec's own locators run in strict mode on the restyled DOM
  with a spec-like fixture: one résumé input, disabled during upload and
  enabled after; one batch row whose cells parse as integers; four item
  rows; Approve clicked and hidden. Contrast: 557 text pairs from computed
  styles with exact compositing (rows, hovered rows, the selected row,
  the Detail grid, notice, badges), 0 below 4.5:1, lowest 5.17:1 (failed
  badge); the ink-3 chunk id 5.47:1 at rest, 4.69:1 on a hovered row.
  Greyscale, deuteranopia and protanopia: every badge reads by icon and
  label. Keyboard: 12 stops, a ring on each, no `tabIndex` above 0.
- **Checks at group A (measured):** vitest 280 (238 at batch 5), stable
  over three runs; fake E2E (`scripts/e2e.ps1`, all three app containers
  `LLM_PROVIDER=fake`, web image rebuilt and checked for the new code
  before the run) 1 passed. Bare buttons 2 remain, inline styles 3,
  `--color-danger` readers 3 (library and employee profile).
- **New exports and their first caller:**

| Export | File (under `apps/web/src/`) | First caller |
|---|---|---|
| `importBatch`, `knowledgeItem`, `availability`, `profileReview` families | `status/statusMap.ts` | batch 6 A (import queue: `importBatch`); the other three in group B |
| `describeImportBatch` | `status/statusMap.ts` | batch 6 A (import queue) |
| `formatDay` | `format/date.ts` | batch 6 A (certification dates in the import Detail cell) |
| `importTypeLabel`, `sourceRefLabel`, `payloadFields`, `formatYears`, `NOT_SET` | `routes/talentImportValues.ts` | batch 6 A (import queue) |
| `formatInr` | `format/inr.ts` | **still unused** - no money in any talent response |

**Group B - talent library and employee profile** (`TalentLibrary.tsx`,
`EmployeeProfile.tsx`; `styles/tokens.css`).

- **Spec-forced choices** (`full-flow.spec.ts` unchanged, byte-identical to
  main):
  - **One link per employee** (`:476-492` count every
    `a[href^="/talent/employees/"]` and follow each): an employee row's
    title is its only link, href shape unchanged; nothing else on the page
    links there.
  - **The checkbox and Verify exist only after load** (`:496`) - Empty,
    loading and error states.
  - **One "verify" button, one "yes" in a `<dl>`** (`:497`, `:498`) -
    Buttons and Status badge.
- **Chosen, not in this file** (marked in code): library count line ("8
  items, 4 employees"); profile fields beyond the five shown before -
  employee code, location, experience ("1 year", "7.5 years"),
  availability as of, last verified - with "Not set" for each null, and
  not `profile_verified_by` (a user id; a name needs a fetch); the profile
  hint "Only a verified profile marked eligible is offered as a candidate."
  (`db/repositories/knowledge.py:314` requires verified and eligible); the
  resume label by whether one is on file.
- **Alias deletion:** `--color-danger` deleted (Notices). With it, every
  `--color-*` alias is gone, and screen code holds no inline style and no
  bare `<button>` (measured by grep). `JSON.stringify` output reaches the
  screen only inside `<code>` fallbacks (graph values, import payload
  values, the knowledge impact `<li>`s - spec-forced, batch 4) and in
  `api/formatErrorDetail.ts:25`, which stringifies an API error item it
  does not recognise into the error text (not a restyle surface; left as
  is).
- **Measured (fake fixture data, real Vite dev server, page.route):** the
  spec's own locators on the restyled DOM - 4 employee links for 4
  employees, hrefs unchanged; 0 checkboxes and 0 Verify buttons while the
  profile loads; 1 button matching `/verify/i`; `dl getByText("yes",
  {exact: true})` 0 before Verify and exactly 1 after (the badge
  `<span>`, drawn "Yes"), Verify sending `eligible_for_bid: true`. Page
  `scrollWidth` 390 at 390px in nine states (library empty, loading, load
  error, populated; profile empty, loading, load error, populated,
  verified). Contrast: 200 text pairs from computed styles, a hovered
  library row included, 0 below 4.5:1, lowest 5.17:1 (failed badge, an
  expired item); the field hint (ink-3) 5.47:1. Greyscale, deuteranopia
  and protanopia: every badge reads by icon and label. Keyboard: library
  15 stops, profile 5, a ring on each, no `tabIndex` above 0. The title
  Save measured 14px below its input (Field's own bottom margin) and was
  aligned in the profile's CSS; now flush at 1280 and 390.
- **Checks at group B (measured):** vitest 294 (280 at group A), stable
  over three runs; fake E2E (`scripts/e2e.ps1`, all three app containers
  `LLM_PROVIDER=fake`, web image rebuilt and checked for the new code
  before the run) 1 passed. Bare buttons 0, inline styles 0,
  `--color-danger` 0 in `src` code and in the built CSS and JS.
  `scripts/check-secrets.ps1` now refuses to run (its tracked-file list,
  31,146 characters, is over its own 30,000 guard since batch 5's files
  were committed) and needs batching - a `scripts/` change, not made here;
  the same `detect-secrets-hook` run in three chunks on a throwaway copy
  exited 0 on each, and `.secrets.baseline` is unchanged.
- **New exports:** none. First screen callers: `knowledgeItem`,
  `availability`, `profileReview` families; `formatDay` (library,
  profile); `formatYears` and `NOT_SET` (profile, library).

### Polish pass: as built

Three groups after batch 6, each verified and approved before the next:
**1/3 shared fixes**, **2/3 screen fixes**, **3/3 this consistency pass**
(DESIGN.md only). `apps/web/e2e/full-flow.spec.ts` unchanged throughout,
byte-identical to main.

- **Group 1 - shared** (`Button.css`, `StatusBadge.css`, `index.css`,
  `styles/layout.css`, new `components/BreakAfterUnderscores.tsx`; the
  screens that take `.cell-actions`): quiet-button border (Buttons),
  neutral badge tint (Status badge), baseline cell alignment, shared
  `.count-line`, shared `.cell-actions`, shared `BreakAfterUnderscores`
  (Tables, Screen patterns). Two CSS-source tests pin the button and badge
  rules (vitest applies no stylesheet).
- **Group 2 - screens:**
  - Team: a "Roles" heading above the roles table - a plain `<h2>` in
    `<main>`, so the spec's "section with heading Candidates" (`:827`) and
    the gate's heading-then-section pair (`:56`) are unaffected.
  - Knowledge selections: the previewed item named (Screen patterns).
  - Red team: resolution notes on resolved rows (Screen patterns).
  - Proposal: tag and claim text as one block with "Inspect trace" on its
    own line below (the `<li>` still holds "/ unsupported]" and the button,
    `:971-974`); half the badge's side padding inside the claim tag, so "]"
    sits by the word; **"Save edits" only while a section's draft differs
    from its stored text** (the spec never reads it - 0 matches) and gone
    again after a save; each section capped at the text width (72ch), so
    its rule no longer runs past the text.
  - Drawer: heading in the bar; **page scroll locked while open**
    (behaviour fix, decided; Traceability drawer).
  - Graph: Load aligned (Forms); **properties in words, ids last** -
    status-like values through their family, date-only values through
    `formatDay`, `*_at` through `formatDate`, `confidence` as a percent,
    and `id` / `*_id` still shown (nothing that was shown is hidden) in
    ink-3 after everything else; contradictions in the failed tone
    (Notices).
  - Readiness: **blockers grouped** - identical category and message as
    one `<li>` with "(3 blockers)" after the verbatim message (`:1459`
    needs at least one `<li>`, `:1464` the D6 message inside one); the
    count never splits across lines (`.readiness__blocker-count`,
    `nowrap`, the space before it outside it); category labels by hand
    (Label map). The stored-report line still counts every blocker.
  - Import queue: `aria-current="true"` on the open batch's row; **"Review
    items" on the open batch refetches its items** (behaviour fix,
    decided), and the items empty state says so (Empty states).
  - Library: Valid until blank where it does not apply (Tables).
- **Checks (measured; fake fixture data in the browser harness, fake
  provider in the E2E):**
  - Group 1: vitest 299 (294 at batch 6), three runs; the spec's own
    locators on 14 screens identical before and after; 1,233 text pairs
    on 17 screens and a hovered row each, 0 below 4.5:1 (lowest 4.78:1);
    quiet borders 3.37:1 or better; page `scrollWidth` equal to the
    viewport at 390 and 1280 on every screen; keyboard pass on 17 screens;
    fake E2E 1 passed.
  - Group 2: vitest 311, three runs; spec locators identical before and
    after except readiness, which is the intended grouping and still meets
    `:1459` and `:1464`; 1,326 text pairs, 0 below 4.5:1 (lowest 4.78:1);
    36 screen states fit at 390 and 1280; keyboard pass on 18 states;
    greyscale, deuteranopia and protanopia on the ten changed screens;
    the readiness count measured on one line at 1280, 390 and 320; fake
    E2E 1 passed (and again after the count fix).
  - Credential scan. `scripts/check-secrets.ps1` refuses to run (see
    Deferred), so CI's `detect-secrets-hook` ran in three chunks on a
    throwaway copy of the tracked files instead; each chunk exited 0, and
    `.secrets.baseline` is unchanged.
- **Counts after the polish pass (measured):** vitest 311; 0 bare
  `<button>` and 0 inline `style={{}}` in screen code; 0 `--color-*`
  aliases; 2 "Loading..." paragraphs (`RequireAuth.tsx:10`,
  `KnowledgeSelections.tsx:237`); 19 `<table>`s in 13 files, 11 of them
  using `TableScroll`; `formatInr` still unused.
- **New exports:** `BreakAfterUnderscores`
  (`components/BreakAfterUnderscores.tsx`; red team, import),
  `propertyValue`, `propertyEntries`, `isIdKey`, `PropertyValue`
  (`routes/graphExplorerValues.ts`; graph explorer).

## Deferred

- **Light theme.** Tokens are hooked for it; values, contrast and testing
  are not part of this phase. The landing's light values
  (`apps/landing/src/styles/tokens.css:17-26`) are the starting point when
  it is picked up.
- **`.impeccable/design.json` sidecar.** Impeccable's `document` flow also
  writes a machine-readable sidecar; not written in this phase (scope was
  DESIGN.md only).
- **PRODUCT.md.** Impeccable's context loader reported none; offered, not
  created.

**From the polish pass** (decided at the polish plan, or found during it):

- **S2, a scroll hint on `TableScroll`.** At 390px the import items
  table's Approve/Reject and red team's Resolve sit in the part of a
  scrolling table that needs a sideways scroll to reach, with nothing on
  screen saying the table scrolls. A shared edge fade on `TableScroll` (a
  small scroll/resize listener and CSS) would cover every table; it changes
  every table screen, so it waits.
- **Badge-plus-select restyle** (compliance status, library status): the
  badge and the select say the same word in one cell. Left as is
  (decided); the compliance select must keep showing the current value
  (`full-flow.spec.ts:714` reads it, `:736` selects by value).
- **Width policy beyond `.cell-actions`:** narrow tables still stretch to
  full width at 1280 (DESIGN's own "tables run full width"); the 640px gate
  panel and profile grid follow "forms 640px".
- **Graph neighbours hint:** neighbour names scroll under the pinned
  Explore column at 390 - the S2 hint would cover it.
- **Duplicate `formatYears`:** `TeamWorkspace.tsx:25` (null-aware) and
  `routes/talentImportValues.ts:55`; one shared formatter belongs in
  `format/`.
- **"Save edits" layout jump:** the button appears on the first typed
  character, pushing the claims below it down one row.
- **Tall resolved red-team row at 390:** with the note in the actions cell,
  a resolved row can be taller than its one-line clamped finding text
  suggests.
- **Red-team "Actions" header:** the visually hidden header says "Actions"
  while a resolved row's cell holds a note, not an action.
- **`scripts/check-secrets.ps1` batching:** its tracked-file list (31,146
  characters, measured) is over its own 30,000 guard since batch 5's files
  were committed, so it refuses to run. The fix belongs on main, not in
  this restyle branch.

## Decisions (recorded)

1. **Fonts - DECIDED:** self-hosted woff2 in `apps/web/public/` (OFL).
   Reason: no runtime third-party request; no new dependency. Binary files
   are added in batch 1 under their own approval.
2. **Light theme - DECIDED:** dark only for this restyle; semantic tokens
   under a `[data-theme]` hook; light deferred, not designed or tested.
3. **Accent vs warning - DECIDED:** amber (`#C98A47`) for brand, links,
   primary actions and focus only; warning `#D4CC63` (hue 39 degrees from
   amber, measured); every status carries icon + text label + colour.
4. **Newsreader scope - DECIDED:** page titles and empty-state headings
   only; Archivo elsewhere. Archivo has `tnum` but defaults to proportional
   figures (measured), so tabular-nums is mandatory on data.
5. **INR - DECIDED:** one shared formatter on `Intl.NumberFormat('en-IN')`
   with `compact` (`₹1.20 Cr`) and `full` (`₹1,20,00,000`) modes; no new
   dependency; no existing money formatting found.
6. **Rejected and changes-requested gates - DECIDED (TODO(human) 1, option
   a):** `rejected` = Failed, `changes_requested` = Blocked. Reason: mirrors
   the run's coarse mapping
   (`packages/tenderos_backend/src/tenderos_backend/core/enums.py:53-76`), so
   a gate badge never disagrees with its run. Rejected alternatives: both
   Blocked (hides that a rejected final gate ends the run); a sixth
   "Returned" state (a new hue and concept for two values).
7. **Workflow nav - DECIDED (TODO(human) 2, option a):** horizontal tab
   strip under the tender header, scrolling at 390px. Reason: no layout grid
   change, full-width tables kept, and the E2E navigates by `page.goto`.
   Rejected alternatives: a left rail with per-step status (new data wiring
   inside a restyle); the styled link row (still no way back).
8. **Money inside backend-written text - DECIDED (TODO(human) 3, option
   a):** the restyle formats structured amounts only (`delta` once
   surfaced); backend prose such as "Grand total is 44820.0" stays as the
   backend writes it, and this file says so rather than hiding it. Reason:
   no backend change inside a frontend restyle. Structured amount fields on
   findings remain a possible separate backend block.
   **Phase 4 note (before any public deployment):** seeded demo data must
   be INR. The currency mismatch is itself a seeded red-team defect
   ("'Cross-Border Logistics' is priced in 'EUR', but the sheet's other line
   items use 'USD'"), so the fix changes the *base* currency to INR and
   keeps the odd line item in a different currency - the defect must still
   be detectable after the change.
9. **Dashboard status - DECIDED (TODO(human) 4, as recommended):** batch 2
   ships the bid dashboard **without a Status column**, and the tender-header
   status badge appears only on the six screens that already fetch the run
   (Layout). Reason: the only status the list endpoint carries today is
   `tenders.status`, which reads `created` for all 47 tenders (measured)
   because nothing in the workflow writes it; showing it would be a UI
   claiming more than the code does. **The real fix is a separate backend
   block** - either the workflow writes `tenders.status` from the coarse
   run status, or the tender list endpoint returns the latest run's status
   - and it must land before any batch wants the column back. Recorded in
   KNOWN_LIMITATIONS.md.

No open TODO(human) items remain in this file. Not updating the Impeccable
skill (installed v4.2.1, v4.5.2 available) until after Phase 1, by decision.
