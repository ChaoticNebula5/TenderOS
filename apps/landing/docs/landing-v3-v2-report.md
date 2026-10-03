# Landing v3, phase V2 report

Phase: V2, tender analysis and eligibility check (checklist items 25 to 40 in
`landing-v3-v0-report.md`). Branch `landing-v3`. All measurements on the production build
(`vite build`, `vite preview`) unless stated otherwise.

## 1. V1 fixes (amended into the V1 commit, now `d011434`)

- Screenshots untracked (`git rm -r --cached apps/landing/docs/screens`) and ignored by a new
  `apps/landing/.gitignore` (`docs/screens/`). The repo-root `.gitignore` was not touched. Files kept on disk.
- Header background is fully opaque `var(--bg)` in both themes, no `backdrop-filter`; border,
  height and layout unchanged.
- Touch check moved to V5, with touch throwing, using CDP touch events.
- V1 report open points updated to match.

## 2. What was built

| File | Change |
|---|---|
| `src/data/tender.ts` | Added `REQ`, `EVROW`, `ST`, `SHORT` |
| `src/data/chapters.ts` | Added `SIDE = [0, 1, 0, 1, 0, 1]` (right, left, right, left, right, left) |
| `src/world/store.ts` | Added refs (scrims, wires SVG, index buttons), `ctx.y1`, `ctx.hoff`, screen arrays `scr`, `HF`, hover state `idxHover`, `hoverCard`, `hoffT` |
| `src/world/arrangements.ts` | `pack` (helix), `match`, `helixBase()`; table is now `[pile, pack, match, park, park, park]` |
| `src/world/layout.ts` | `measure()` sets `ctx.y1`, fits with `hoff` zeroed, sets the wires `viewBox` |
| `src/world/actions.ts` | New: `bringToFront()` with nearest wrap |
| `src/world/labels.ts` | New: label definitions and `placeLabels()` with change-only writes (class, transform, width, opacity) |
| `src/world/wires.ts` | New: `setD`, `setO`, `curve`, anchors, eligibility wires and the gap stub, `placeWires()` |
| `src/components/Labels.tsx`, `Wires.tsx` | New: render labels and wire paths once, register refs |
| `src/components/WorldLayers.tsx` | Scrim refs; uses `Labels` and `Wires` |
| `src/chapters/Analysis.tsx` | Index rows: hover, focus, blur and click handlers that write to the store only |
| `src/world/manipulation.ts` | `pick()`; hover and click on mandatory helix pages, `point` cursor |
| `src/world/frame.ts` | Side alternation and scrims, `hoff` easing, screen arrays, document states, `.hot`, index row marker, labels, wires |

`pick()` landed in V2, not V5, because hovering and clicking helix pages (items 30, 31) need it.
The frame still performs no layout reads; V3's claim position will be the first, read at the top
of the frame.

## 3. Checklist status (items 25 to 40)

| # | Item | Status |
|---|---|---|
| 25 | `pack` helix: two seamless turns, seen from above, travels with scroll, parallax, ends faded | Done; matches the reference at pin start, pin middle and the transition |
| 26 | Mandatory pages at least 0.82 opacity, all six lit | Done; all six `data-s="lit"` in the pack chapter |
| 27 | Helix labels on the outer side relative to the world centre | Done |
| 28 | Index list from the mandatory pages | Done (real buttons, keyboard focusable, Tab moves row to row) |
| 29 | Row hover or focus lights page (`.hot`) and label (`.on`) | Done, verified with mouse and with keyboard focus |
| 30 | Hovering a mandatory page lights its row; pointer cursor | Done, verified |
| 31 | Click on row or page spins it to the front, nearest wrap | Done, verified by click on a page and by Enter on a focused row |
| 32 | With no hover, the row of the page facing the reader is marked | Done |
| 33 | `match` arrangement, portrait grid | Done |
| 34 | States: met amber, partial slate, not met alarm with red glow | Done |
| 35 | Requirement labels above with coloured status | Done |
| 36 | Evidence labels below | Done |
| 37 | Wires: solid amber met, dashed slate partial, dashed alarm stub under p. 214 | Done |
| 38 | Labels and wires fade with chapter weight; no labels in portrait | Done; in portrait 0 labels visible. Wires still draw in portrait, as in the reference (it hides labels there, not wires) |
| 39 | Tally copy | Done (static since V1) |
| 40 | Side alternation, scrims, `.r` columns; none in portrait | Done; measured at chapters 1, 2, 3: scrims 1/0, 0/1, 1/0, column on the right in chapter 2; portrait scrims `display: none` |

## 4. Screenshots

In `docs/screens/v2/` (gitignored, on disk only), reference next to V2. File names:
`<viewport>-<position>-reference.png` and `<viewport>-<position>-v2.png`.

| Position | 1440x900 scroll y | 390x844 scroll y |
|---|---|---|
| `y0` | 0 | 0 |
| `y450` | 450 | 450 |
| `pack-start` (start of the pack pin; hold start in portrait, where the chapter is not pinned) | 900 | 665 |
| `pack-mid-hover` (middle of the pack hold, index row 3 "Similar works experience" hovered) | 1215 | 936 |
| `pack-to-match` (midpoint of the transition from the pack hold to the match hold) | 1899 | 1492 |
| `match-mid` (middle of the match hold) | 2430 | 1930 |

Positions were computed from each page's own section geometry with the same hold formula as
`measure()`; both pages produced identical values.

Findings, from the pairs reviewed by eye (1440x900: pack start, pack middle with hover,
transition, match middle; 390x844: pack middle with hover, match middle) and V2 at 1440x900 y 450:
every reviewed pair matches the reference, with the same helix pose, lit pages, label sides, marked
row, wires and states. At y 450 the world now slides left and forms the helix, which closes the two
differences V1 had at that position. The remaining files were captured but not individually reviewed.

## 5. Portrait framing check (390x844)

Measured with `getBoundingClientRect` on every document at opacity 0.35 or more, against the world
band (header bottom 60 px to 56% of the viewport, 473 px) and the top of the active text column.

| Position | Visible | Lit | Lit outside band | Lit under the column | Any under the column |
|---|---|---|---|---|---|
| Pack middle, reference | 15 | 5 | none | page 14, bottom 21 px | pages 14, 15 |
| Pack middle, V2 | 15 | 5 | none | page 14, bottom 21 px | pages 14, 15 |
| Match middle, reference | 14 | 6 | none | none | evidence 24 to 27, bottom 30 px |
| Match middle, V2 | 14 | 6 | none | none | evidence 24 to 27, bottom 30 px |

The helix and the eligibility grid are framed inside the top 56%. They are not entirely clear of
the text column: in portrait these chapters are unpinned (`mfree`), so by the middle of the
chapter the column has scrolled up into the world band (its top at 384 px and 410 px, against 473),
covering the bottom edge of a few cards. V2 is identical to the reference here; see open point 1.

## 6. Runtime checks

| Server | Pending rAF (min, max) | rAF callbacks per second | `cancelAnimationFrame` at load |
|---|---|---|---|
| Dev (`vite`, StrictMode) | 1, 1 | 180 | 1 (first world torn down before the second starts) |
| Preview (production) | 1, 1 | 180 | 0 |

180 per second is this display's refresh rate (it matches the 180 frames per second in the paint
traces). One loop: never more than one callback pending.

Console: only the `favicon.ico` 404 the reference also has.

## 7. Perf check

Production build, Chromium 154 via Playwright, `deviceScaleFactor: 2`, 1440x900, CDP traces.
Same harness on the reference.

| Check | Target | V2 | Reference |
|---|---|---|---|
| Raster, 3 s idle on the hero | under about 50 ms | 0.3 ms | 0.3 ms |
| Raster, 3 s idle at the pack middle | under about 50 ms | 0 ms | 0 ms |
| Long tasks (over 50 ms), idle hero and pack | none | 0 | 0 |
| Main-thread tasks over 16 ms, wheel-scrolling from the top through chapters 1 to 3 (to y 3330) and back | none | 0 (max task 5.05 ms) | 0 (max task 5.06 ms) |
| Paint lifecycle, 3 s idle hero / pack (for comparison) | | 346 / 257 ms | 334 / 255 ms |
| Paint lifecycle during that scroll (for comparison) | | 670 ms | 616 ms |
| Raster during that scroll (for comparison) | | 65 ms | 64 ms |
| React commits while idle, hero and pack | zero | 0 and 0 (1 at load) | |
| React commits during an index row hover and click | (extra) | 0 | |
| `requestAnimationFrame` loops | exactly one | One, at runtime in dev and preview | |

Paint during the scroll is about 9% above the reference. Not a gate failure; noted to watch as
labels and wires grow in V3 and V4.

## 8. Open points for review

1. **Portrait: cards partly under the text column** (section 5). Identical to the reference, so
   left as is. If you want it clear, the change would be to fit portrait arrangements to the space
   above the column rather than the fixed 56% band, which departs from the reference and needs a
   per-frame read of the column position; I would not do it without your go-ahead.
2. **Correction to the V1 summary:** in chat I described V1's loop as about 60 callbacks per
   second. That was a misreading of the sampling window; the rate is the display's 180 Hz. The V1
   report gave only the raw count, and the single-loop result is unaffected.
