# Landing v3, phase V1 report

Phase: V1, world core and overview (checklist items 1 to 24 in `landing-v3-v0-report.md`).
Branch `landing-v3`. All measurements on the production build (`vite build`, `vite preview`) unless
stated otherwise.

## 1. Housekeeping before V1

- V0 committed as `29b7b22` "V0: remove earlier landing build, scaffold v3 foundation" (apps/landing only).
- The 26 root screenshots were deleted after confirming: exactly 26 `*.png` at the repo root, each
  one untracked (`?? <name>` per file), and no file referencing any of them other than the V0
  report's own inventory row. Nothing else outside `apps/landing` was touched.

## 2. What was built

| File | Role |
|---|---|
| `src/world/math.ts` | Constants (`CW`, `CH`, `F`, `RM`) and pure helpers |
| `src/data/tender.ts` | Pages, evidence, drafts, card-id scheme |
| `src/data/claims.ts` | The five draft statements (static sheet now, tracing in V3) |
| `src/data/chapters.ts` | Chapter names for `#where` (V2 adds `SIDE`) |
| `src/documents/parts.tsx` | `Bars`, `Rows`, `Top`, `Sig`, `Foot` |
| `src/documents/TenderPage.tsx`, `Evidence.tsx`, `Draft.tsx` | Document templates as JSX |
| `src/documents/Doc.tsx` | The three layers `.cw` > `.sc` (+ glows) > `.card`, refs registered into the store |
| `src/world/store.ts` | Mutable state shared by loop, layout, input and refs; `resetStore()` |
| `src/world/arrangements.ts` | `sphere()`, `rot()`, `park`, `pile`, arrangement table `L`, stagger `DL` |
| `src/world/layout.ts` | `measure()` (world rects, holds, per-arrangement fit), `pOf()` |
| `src/world/manipulation.ts` | Drag and swipe to rotate, inertia, cursors |
| `src/world/frame.ts` | `QSTEP`, the frame loop, `startWorld()` / stop |
| `src/world/useWorld.ts` | One effect: `useEffect(() => startWorld(), [])` |
| `src/components/WorldLayers.tsx`, `Header.tsx`, `Footer.tsx` | Fixed layers and chrome |
| `src/chapters/*.tsx` | All six chapters, static markup with the reference's exact copy |
| `src/styles/world.css`, `documents.css`, `chrome.css`, `chapters.css`, `responsive.css` | The rest of the reference CSS, by banner; every line from 12 to 288 is in exactly one file |

Deviations from the V0 module map: `NAMES` lives in `data/chapters.ts` instead of `data/impact.ts`
(chapter-level, not change-impact data). Pointer listeners are attached by `startWorld()` through
`attachPointer(signal)` instead of by `useWorld()`, so the one stop function tears down everything.

### V1 boundary

The arrangement table is `[pile, park, park, park, park, park]`. Chapters 1 to 5 hold their documents
in the reference's park shell (far, dim, slowly turning) until V2 to V5 replace each entry with its
own arrangement. Scroll mapping, holds, fit and the morph already run over all six chapters, so
later phases add arrangements without changing the mechanism. The world stays on the right in every
chapter until side alternation lands in V2. The labels, wires and flying layers are present and empty.

All six chapters render their final static markup (index list, tally, claim sheet, reader and trace
containers, cause buttons, summary idle line) with no behaviour attached, so section heights and
scroll holds are final.

## 3. Checklist status (items 1 to 24)

| # | Item | Status |
|---|---|---|
| 1 | 41 documents rendered once with exact copy | Done |
| 2 | Three nested layers, glows inside `.sc` | Done |
| 3 | All document templates | Done |
| 4 | Projection and on-screen scale cap at 1 | Done |
| 5 | Per-arrangement fit `Gs[k]` | Done |
| 6 | Scroll holds | Done |
| 7 | `pOf()` with smoothed scroll | Done |
| 8 | Morph with per-document stagger | Done |
| 9 | Coarse/fine scale split, change-only writes, hide under 0.004 | Done |
| 10 | Depth fade (0.72 dark, 0.4 light, read from `data-theme`) | Done; light path exercised in V6 with the toggle |
| 11 | `#atmo` by transform, written only on change | Done (compares the whole transform string, so a height-only resize also updates it) |
| 12 | One rAF loop, dt clamp, idle when hidden | Done, verified at runtime (section 5) |
| 13 | World rect desktop and portrait | Done |
| 14 | `measure()` on load, resize, `ResizeObserver`, `fonts.ready` | Done |
| 15 | `pile` sphere | Done |
| 16 | `park` shell | Done |
| 17 | Mouse parallax | Done |
| 18 | Drag to rotate with inertia | Done, verified at runtime (section 5) |
| 19 | Touch: sideways swipe rotates, otherwise scroll | Ported as in the reference; not exercised with real touch input in this phase |
| 20 | Cursors | Done, verified at runtime |
| 21 | Header with `#where` | Done, verified at runtime; theme button rendered, toggle is V6 |
| 22 | All six chapters with final markup | Done |
| 23 | Hero copy, buttons, mouse/touch note | Done |
| 24 | Grain, footer | Done |

## 4. Screenshots

In `docs/screens/v1/`, each pair taken 2.0 s after load so the auto-spin is at nearly the same angle.

| Viewport, scroll | Reference | V1 |
|---|---|---|
| 1440x900, y 0 | `1440x900-y0-reference.png` | `1440x900-y0-v1.png` |
| 1440x900, y 450 | `1440x900-y450-reference.png` | `1440x900-y450-v1.png` |
| 390x844, y 0 | `390x844-y0-reference.png` | `390x844-y0-v1.png` |
| 390x844, y 450 | `390x844-y450-reference.png` | `390x844-y450-v1.png` |

Findings:

- **Hero, both viewports (y 0): matches.** Same sphere framing, same documents in the same slots,
  same chrome and copy. The remaining differences are the few milliseconds of auto-spin between captures.
- **y 450, expected differences (V2 scope):** the reference is forming the helix and has slid its
  world left of the text; V1 is moving toward the park shell and stays on the right, so it overlaps
  chapter 2's right-aligned text. Side alternation (item 40) and the helix (item 25) fix both.
- **y 450, header (needs your decision):** with no `backdrop-filter`, scrolled text is faintly
  readable through the 82%-opaque header ("reads the tender document..." at 1440, "Every claim in
  your" at 390). The reference blurs it away. See section 7.

## 5. Runtime checks

One rAF loop, by wrapping `requestAnimationFrame` before the page loads and sampling pending
callbacks 40 times over about a second:

| Server | Pending rAF (min, max) | Callbacks in window | `cancelAnimationFrame` calls at load |
|---|---|---|---|
| Dev (`vite`, StrictMode) | 1, 1 | 227 | 1 (StrictMode's first world torn down before the second starts) |
| Preview (production) | 1, 1 | 225 | 0 |

Interaction, on preview at 1440x900:

- Cursor `grab` over the world on the overview, none over the text column, `grabbing` during a drag, cleared on release.
- Under emulated reduced motion (no auto-spin, so any motion is input): the world is still without
  input; a drag starting on the text column is ignored; a fast release keeps turning (inertia); a
  drag held still for 200 ms before release does not coast. Inertia decays at `exp(-2.4 t)` as in
  the reference, so after a fast flick the motion falls below write precision at about 4.5 s, after
  which the write caches stop all style writes.
- `#where` reads "1 Overview" at the top, "4 Evidence tracing" inside the evidence pin, "6 Submission" at the end.
- At the end of the page, 36 documents are visible (pages and evidence in the park shell; drafts hidden).
- Console: only a `favicon.ico` 404, which the reference also has.

## 6. Perf check

Production build, Chromium 154 driven by Playwright, `deviceScaleFactor: 2` at 1440x900, measured
from CDP traces rather than by reading the Performance panel by hand. The same harness was run on
the reference for comparison.

| Check | Target | V1 | Reference |
|---|---|---|---|
| Raster, 3 s idle on the hero (`RasterTask` total) | under about 50 ms | 0.4 ms | 0.5 ms |
| Paint lifecycle, 3 s idle (main thread) | (for comparison) | 351 ms | 379 ms |
| Long tasks (over 50 ms), idle | none | 0 | |
| Main-thread tasks over 16 ms, scrolling the hero and its transition, wheel steps both ways | none | 0 (max task 2.2 ms) | |
| Same, scrolling the whole page (for information) | | 0 (max task 3.6 ms) | |
| React commits while idle for 3 s, counted through the DevTools global hook | zero | 0 (1 commit at load) | |
| `requestAnimationFrame` loops in the codebase | exactly one | One loop in `src/world/frame.ts` (two call sites: the first kick and the reschedule) | |

## 7. Open points for review

1. **Header blur (rule 10 against the reference). Resolved: option (c).** The reference header has
   `backdrop-filter: blur(12px)`; rule 10 forbids `backdrop-filter` over the world. The header
   background is now fully opaque `var(--bg)` in both themes, with no filter; border, height and
   layout unchanged. The y 450 screenshots above were taken before this fix and still show the
   see-through.
2. **Screenshots are gitignored** (`apps/landing/.gitignore`: `docs/screens/`) and live on disk only,
   at the paths listed in section 4. Every phase writes to `docs/screens/<phase>/` and never stages them.
3. **Touch (item 19)** is ported line for line but was not exercised with real touch input. Deferred
   to V5, where touch rotate and touch throw are tested together with CDP touch events.
