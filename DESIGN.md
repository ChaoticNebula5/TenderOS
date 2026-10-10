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
  status-badge-neutral:
    textColor: "{colors.ink-2}"
    rounded: "{rounded.sm}"
    padding: "2px 8px"
---

# Design System: TenderOS operator app

> **Status: proposal, nothing in this file is built yet.** It describes what
> the apps/web restyle *will* do. Every statement about the UI as it is
> today cites a file and line; everything else is future tense. When a batch
> lands, its section moves from "will" to "does", in the same commit as the
> code - never ahead of it (see Copy rules).
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
   (today: `KnowledgeSelections.tsx:165`, `GraphExplorer.tsx:112`,
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

| Display state | Gate | Run | Task | Parse | Severity | Grounding | Compliance | Other |
|---|---|---|---|---|---|---|---|---|
| Ready (green) | `approved` | `completed` | `completed` | `parsed` | - | `grounded` | `fully_compliant` | finding resolved = true; readiness `ready: true`; import item `approved` |
| Running (slate) | - | `queued`, `running` | `pending`, `running` | `uploaded`, `queued`, `parsing` | - | - | - | graph projection `running`; import item `pending`, `extracted` |
| Blocked (violet) | `pending`, `changes_requested` | `waiting_for_human` | `blocked` | - | - | `needs_review` | `clarification_required` | readiness `ready: false`; import item `needs_review` |
| Failed (red) | `rejected` | `failed` | `failed` | `failed` | `critical` | `unsupported` | `non_compliant` | import item `failed`, `rejected` |
| Warning (yellow) | - | - | - | - | `major` | - | `partially_compliant`, `evidence_unavailable` | graph stale; live updates unavailable; in-scope evidence not cited |
| Neutral (ink-2, no hue) | - | `cancelled` | `skipped` | - | `minor` | `not_required` | `not_reviewed` | - |

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
  `apps/web/e2e/full-flow.spec.ts:1452`.)
- Severity: "Critical", "Major", "Minor". Grounding: "Grounded",
  "Unsupported", "Needs review", "Not required". Compliance: "Compliant",
  "Partly compliant", "Not compliant", "Clarification needed", "No
  evidence", "Not reviewed". Finding resolved: "Resolved" / "Open".

### Named Rules

**The Reserved Amber Rule.** Amber marks what is *yours to act on or where
you are*: links, the primary action, focus, the current nav item. It never
marks a state. If something amber is not clickable or focused, it is wrong.

**The Three Signals Rule.** A status is shown by hue, icon and a text label
together. Remove the colour and the screen must still read correctly
(grayscale check in each batch).

**The No Raw Enum Rule.** `fully_compliant`, `waiting_for_human` and
friends are never shown as-is; one shared label map turns them into words.
(Today the compliance select shows raw values, `ComplianceMatrix.tsx:110-115`.)

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

**App shell.** A 48px top bar (wordmark "TenderOS" in Archivo 600 at 16px;
org name; account menu) over a content column. The landing sets its wordmark
in Newsreader (`apps/landing/src/styles/chrome.css:8`); the app does not,
because decision 4 limits Newsreader to page titles and empty-state
headings. Inside a tender, a **tender header** (name, client, and - on some
screens only - a run status badge) and a **workflow nav** replace today's
dot-separated link row (`UploadAndProcessingStatus.tsx:180-190`) and give
every tender screen a way back - today only the processing screen links
onward, and no screen links back to the dashboard.

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

**Workflow nav - DECIDED (was TODO(human) 2, option a):** a horizontal tab
strip under the tender header, in pipeline order: Documents, Requirements,
Compliance, Team, Commercial, Knowledge, Proposal, Red team, Readiness, plus
Graph as a utility at the end. It scrolls horizontally at 390px. Plain links
with `aria-current="page"`; the E2E reaches tender screens with
`page.goto`, so the strip does not change how the spec navigates.

**Content width.** Tables run full width (max 1440px); forms and prose cap
at 640px / 72ch. Today `CreateBid.tsx:41` and `Login.tsx:66` hardcode
480 and 360; those become tokens.

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

Inventory from the survey (all in `apps/web/src`, all unstyled browser
defaults today; 0 `className` uses, 30 inline `style={{}}` objects,
measured by grep). Each component will exist once and be reused.

### Buttons
- **Today:** bare `<button>`, 31 in source across 16 files (measured;
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
  beyond 50% opacity), busy (label changes to the present participle that
  exists today - "Starting...", "Generating..." - plus `aria-busy`).

### Status badge
- **Today:** none. Status is a raw string in a cell (`BidDashboard.tsx:70`,
  `UploadAndProcessingStatus.tsx:218`, `TalentImportReview.tsx:180`),
  "yes"/"no" (`RequirementRegister.tsx:124-126`), or a `[type / status]`
  prefix (`ProposalWorkspace.tsx:115`, `Readiness.tsx:105`).
- **Will be:** icon + label in the status hue on a 14% tint of that hue,
  2px radius, 12px label type. Booleans that are states (verified,
  approved, resolved, selected) become badges; booleans that are facts
  (mandatory) become a plain word.

### Gate panel
- **Today:** `GateDecisionPanel.tsx` - a `<ul>` of counts, a textarea, three
  identical buttons, error in danger colour. Decided gates show
  `Decision: <strong>approved</strong> on <ISO timestamp>` (`:89-92`).
- **Will be:** a `surface-raise` panel with a header row (gate name +
  status badge), the summary as a definition grid with tabular counts,
  blockers listed with the blocked icon, notes field, then actions with
  Approve as primary, Request changes as secondary, Reject as danger.
  Decided state shows who/when in human date format.
- **Summary honesty:** the summary counts are the gate's snapshot from when
  it was requested, and today nothing says so - an approved compliance gate
  reads "Pending approval: 2" next to two rows marked approved, and the
  resource gate reads "Roles: 1 ... Unselected roles: 4" (screenshots 06,
  07, fake-provider data). The panel will label the summary "At request
  time" and show the request date.
- **States:** not yet opened ("No open ... gate" - today a bare `<p>` on
  five screens, measured), pending (blocked), approved (ready), rejected, changes
  requested, submitting, error (409 message kept verbatim in meaning).

### Tables
- **Today:** 14 bare `<table>`s in 11 files (measured), no header styling,
  no numeric alignment. Raw float output is visible: compliance confidence
  renders as `0.8999999761581421` (`ComplianceMatrix.tsx:118`, fake-provider
  data), and the requirement column shows a UUID instead of the display code
  (`ComplianceMatrix.tsx:104`).
- **Will be:** `surface-sunk` header band, label type for headers, data type
  for cells, `--rule` row dividers, row hover `--rule-2`, numbers
  right-aligned and tabular, rejected rows dimmed (today an inline
  `opacity: 0.5`, `RequirementRegister.tsx:120`).

### Forms and inputs
- **Today:** labels wrap inputs (good for accessibility, keep it), no
  layout, no help text, `<br />` for spacing (`TalentImportReview.tsx:148`,
  `:153`), file inputs unstyled.
- **Will be:** label above control, `surface-sunk` field with
  `--control-border`, amber focus, error text in failed hue with icon below
  the field. File inputs get a styled button and the chosen filename.

### Notices (error, warning, info)
- **Today:** `<p role="alert" style={{ color: "var(--color-danger)" }}>`
  repeated 22 times (measured), plus one warning variant
  (`UploadAndProcessingStatus.tsx:245`) and two bordered boxes
  (`GraphExplorer.tsx:103-115`, `TraceabilityDrawer.tsx:102-121`).
- **Will be:** one notice component: icon + message on a tint, variants
  failed / warning / info. Keeps `role="alert"` only for errors that
  appear after an action.

### Empty, loading and error states
- **Today:** a "Loading..." paragraph in 8 places across 7 files (measured:
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

### Navigation
- **Today:** no `<nav>`, no `<header>` (0 each, measured); dashboard links
  joined with " · " (`BidDashboard.tsx:44`); no route back to the dashboard
  from any screen.
- **Will be:** the app shell and workflow nav above; current item marked
  with amber text and a 2px amber underline plus `aria-current="page"`.

### Traceability drawer (signature component)
- **Today:** a bordered `<aside>` appended at the bottom of the proposal
  page (`TraceabilityDrawer.tsx:47-54`), so on a long proposal it opens
  off-screen.
- **Will be:** a right-side panel on wide screens and a full-height sheet at
  390px, focus moved into it on open and back to the "Inspect trace" button
  on close, Escape closes. Cited evidence and "in scope but not cited"
  evidence as two labelled lists; source, excerpt, verified badge and
  validity badge per entry.

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
  unverified mandatory") from data already in the response.
- **Gates:** the gate panel sits at the top of its screen when the gate is
  pending (today it is at the bottom of every screen, e.g.
  `RequirementRegister.tsx:147-152`), so the decision an operator came for
  is the first thing they see; it moves below the table once decided.
- **Evidence view:** excerpt in a `surface-sunk` well, source title and
  page/sheet as metadata in ink-2, verified and validity as badges. Never a
  raw `source_kind:` prefix (`TraceabilityDrawer.tsx:132`).
- **Findings lists** (commercial, red team): severity badge, finding text,
  resolution control in one row; resolved rows collapse to one line.
- **Impact list** (knowledge selections): each affected fact as a row with
  its node type and the requirement or claim it feeds, replacing
  `JSON.stringify(row)` (`KnowledgeSelections.tsx:165`); the confirm action
  states what will change ("Deselect and mark 3 facts for review"), using
  only counts the response actually carries.
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
  the landing's own rule, `apps/landing/src/styles/tokens.css:37`. 6.45:1 on
  `surface`, 5.84:1 on `surface-raise` (measured). Today apps/web has no
  focus styling at all (0 `:focus`/`outline` rules, measured).
- **Keyboard order:** DOM order equals visual order; skip link to main
  content; workflow nav reachable by Tab, arrow keys not required; the
  drawer traps focus while open and returns it on close; no `tabIndex`
  above 0. Today there are 0 `tabIndex` and 0 `div` click handlers
  (measured) - everything clickable is already a native button or link, and
  that stays true.
- **Reduced motion:** all transitions sit behind
  `@media (prefers-reduced-motion: no-preference)`; under `reduce`, state
  changes are instant. Today there is no motion and no media query (0,
  measured).
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
  - **Today apps/web formats no money itself** (measured: no `Intl`,
    `toLocaleString`, `₹`, `INR`, `crore` or `lakh` in `src`).
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
  - Note: the landing's copy spells the unit out ("₹68.4 crore",
    `apps/landing/src/data/tender.ts:11`) as hand-written strings. The app's
    compact mode uses "Cr" as decided. Not changed here; flagged so the
    difference is known.
- **Dates:** `10 Oct 2026, 14:05` (en-IN), never a raw ISO string (today:
  `GateDecisionPanel.tsx:91`).

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

## Current state: what the screenshots show

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
  the readiness verdict in DOM order).
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

1. **Foundation.** Token stylesheet rewritten to semantic names under the
   `[data-theme]` hook; self-hosted fonts (binary files, separate approval)
   with the numerals re-check; base element styles (body, headings, links,
   tables, forms, buttons, focus ring, selection, scrollbars); reduced-motion
   guard. Screens change only through inherited element styles.
2. **Shell and shared components.** App shell, tender header and workflow
   nav; button variants, status badge with icon set and label map, notice,
   empty state, skeleton, INR and date formatters, with the render tests
   above. Applied to Login, Bid dashboard (shipped **without** its Status
   column, decision 9) and Create bid.
3. **Intake and requirements.** Upload and processing status (task list,
   live-updates notice), requirement register, and the gate panel (first
   used here, then reused unchanged).
4. **Analysis gates.** Compliance matrix, team workspace, commercial
   findings (first INR use if amounts are surfaced), knowledge selections
   with the impact list.
5. **Proposal, evidence and readiness.** Proposal workspace, traceability
   drawer, red-team findings, graph explorer, readiness.
6. **Talent.** Talent library, import and review queue, employee profile.

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
