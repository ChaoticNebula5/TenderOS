# Landing v3, phase V5 report

Phase: V5, submission stack and direct manipulation (checklist items 61 to 68 in
`landing-v3-v0-report.md`). Branch `landing-v3`. All measurements on the production build
(`vite build`, `vite preview`) unless stated otherwise.

## 1. What was built

| File | Change |
|---|---|
| `src/data/tender.ts` | `STACK`: the reference's 17-page starting order, bottom to top |
| `src/world/store.ts` | Full gesture record (`drag`), typed `Overlay` and `ov[]`, live `stack` order and index, `els.cards`, `els.flying`; all reset when the world starts |
| `src/world/arrangements.ts` | `submission` (loose stack); the table is complete: `[pile, pack, match, proof, impact, submission]` |
| `src/world/manipulation.ts` | Rewritten: `grab`, `launch`, `flick`, `toBottom`, `stepOv` (flight physics), `lift`/`land`, `setBack`, `abortDrag`, `landAll`; extended pointer handlers; blur and visibility handlers |
| `src/world/frame.ts` | Overlays applied per document before the coarse/fine split; `stop()` lands any airborne page |
| `src/components/WorldLayers.tsx` | Refs for `#cards` and `#flying` |

Constants are the reference's: 220 ms hold, lift to `min(1, s * 1.3 + 0.05)`, tilt `clamp(vx * 0.012, ±16)`,
380 px/s throw threshold, 90 ms still-before-release, 280 ms / 8 px quick click, gravity 560, drag
`exp(-0.35 dt)`, spin `vx * 0.22 + random ±80` decaying at `exp(-0.4 dt)`, exit bounds, 2.8 s limit,
return after 1.6 s (overview, 1.1 s ease) or 0.25 s (stack, 0.8 s ease, slid in from the side it left).

### Decisions beyond the reference (no visual change)

1. **Reparenting happens at mode switches, not per frame.** The reference checks `parentNode` every
   frame in its return branch. V5 moves a page into `#flying` once (`lift`, at grab or fling) and back
   once (`land`, when the return starts, which also removes `.held`).
2. **A quick click on the stack converts the held page straight into a toss.** The reference moves it
   back to `#cards` and then into `#flying` again; V5 keeps it airborne. Same motion and values.
3. **`pointercancel` never flings.** The reference routes it through the release handler, where a quick
   tap could fling. V5 routes it, window `blur`, `visibilitychange` to hidden, and a mouse move with
   `buttons === 0` to `abortDrag()`: a held page goes back to its place, a rotate stops, nothing is thrown.
4. **`stop()` lands every airborne page** before teardown, so React's tree under `#cards` is never left
   inconsistent by a world restart (HMR, remount).
5. **No allocation in `pointermove`.** The `pick()` filters are module-level functions, not new closures.

All listeners (pointer, `blur`, `visibilitychange`) use the same `AbortSignal` as before; the hold
timer is cleared on abort.

## 2. Checklist status (items 61 to 68)

| # | Item | Status |
|---|---|---|
| 61 | `submission` stack: 17 pages, executive summary on top, offsets and tilts | Done; matches the reference in the reviewed 1440 page-end pair (stack at rest beside the footer) and the 390 held pair; the `stack-rest` pairs were captured but not individually reviewed |
| 62 | Overview click flings outward; shrinks, fades, refiles after 1.6 s | Done; speed and direction distribution matches the reference (section 4) |
| 63 | Overview hold 220 ms lifts; follows and tilts; thrown above 380 px/s, else settles back | Done; held-page screenshots match the reference; trajectories within 2 px with identical input (section 4) |
| 64 | Stack: drag-throw or click-toss alternating; to the bottom; slides back from the side it left | Done; 20 of 20 tosses alternate `LRLR...` as in the reference; top after one toss "Capability and experience", after five "ISO/IEC 27001:2022" |
| 65 | Flight physics | Done; constants as listed above; dt clamped at 0.05 by the frame |
| 66 | Held and thrown pages in `#flying`, back to `#cards` on return; `.held` while held | Done; integrity checks below show every page back in `#cards` with no `.held` after every test |
| 67 | Touch: tap throws, sideways swipe rotates | Done; CDP touch tests in section 5 |
| 68 | Grab cursor over the stack | Done; `grab` over the overview world and over stack pages, `grabbing` while dragging, none over the text column, header, footer, links or buttons (`overUI`) |

## 3. Screenshots

In `docs/screens/v5/` (gitignored, on disk only).

Reference next to V5 (deterministic states), at 1440x900 and 390x844, same scripted mouse actions:

| State | How |
|---|---|
| `stack-rest` | submission hold start (y 6282 at 1440, 4860 at 390), 2.5 s settle |
| `page-end` | scrolled to the bottom (footer) |
| `overview-held` | mouse down at a fixed point on the sphere 1.5 s after load, held still 800 ms |
| `stack-held` | mouse down on the top page's centre, held still 800 ms |

Reviewed by eye: 1440 `stack-held`, `overview-held` and `page-end`, and 390 `stack-held`, against the
reference. All match (the same page lifted on the overview: p. 34, pre-bid meeting).

V5 only (flights are random in the reference): `1440x900-overview-midflight`, `-overview-returning`
(0.45 s into the return), `-stack-midflight`, `-stack-after-toss`, `-stack-after-five-tosses`;
`390x844-touch-stack-tap` and `390x844-touch-hero-swipe`.

## 4. Throws compared with the reference

**Drag-and-release, position of the thrown page's centre after release (px).** Identical scripted
path: press, 8 moves of (+30, -12) at 16 ms, release (overview: held 400 ms first).

First run, Playwright mouse with real timing:

| | 0.1 s | 0.25 s | 0.5 s | 1.0 s | Max deviation |
|---|---|---|---|---|---|
| Stack, reference / V5 | 806,314 / 803,315 | 940,274 / 933,277 | 1149,239 / 1134,246 | 1516,276 / 1486,289 | 33 |
| Overview, reference / V5 | 1397,390 / 1416,383 | 1529,352 / 1575,333 | 1585,339 / 1591,329 | (hidden off screen) | 50 |

That deviation is input timing, not the port: both pages compute release speed from real time gaps
between pointer moves, and Playwright's waits jitter by a few ms around 16. Second run, with each
page's `performance.now()` scripted during the gesture and the events dispatched synchronously at
exactly 16 ms, so both pages receive identical input:

| | 0.1 s | 0.25 s | 0.5 s | 1.0 s | Max deviation |
|---|---|---|---|---|---|
| Stack, reference / V5 | 906,274 / 901,276 | 1166,185 / 1160,187 | 1570,73 / 1565,74 | 1596,462 / 1598,462 | **6 px** |
| Overview, reference / V5 | 1485,355 / 1485,355 | 1600,314 / 1598,314 | 1600,314 / 1598,314 | 1600,314 / 1598,314 | **2 px** |

The remaining few pixels are frame phase (at about 1,800 px/s one 5.5 ms frame is about 10 px). The
overview page leaves the screen before 0.5 s; its last written position is then frozen while hidden.

**Click flings (random speed).** 20 clicks on a fixed 5x4 grid over the overview, on each page;
17 of 20 hit a page on both (the other 3 hit gaps on both). Launch velocity from the first frames:

| | Reference | V5 |
|---|---|---|
| Speed range | 1125 to 2506 px/s | 1136 to 2430 px/s |
| Mean speed | 1757 px/s | 1727 px/s |
| Direction range | -175° to 142° | -175° to 140° |

**Stack tosses (random speed), 20 on each page:**

| | Reference | V5 |
|---|---|---|
| Directions | `LRLRLRLRLRLRLRLRLRLR` | `LRLRLRLRLRLRLRLRLRLR` |
| Horizontal speed | 1537 to 2339 px/s | 1550 to 2210 px/s |
| Vertical speed | -747 to -401 px/s | -690 to -432 px/s |

(The formula gives 1500 to 2100 horizontally and -420 to -680 vertically; the measured spread includes
frame-sampling noise.) A first attempt at this measurement tracked the wrong element (a previous page
still off screen in `#flying`) and showed zero speeds on both pages; it was a probe error and was
re-run tracking `#flying`'s last child.

## 5. Robustness

Integrity after each test: pages in `#cards` / unique / in `#flying` / with `.held`.

| Test | Result |
|---|---|
| Held page, then `pointercancel` | Held before (1 in `#flying`, `.held`); drag ended at once; a fast late release threw nothing; 41/41/0/0 |
| Held page, then window `blur` | Same: drag ended, nothing thrown, 41/41/0/0 |
| Held page, then `pointermove` with `buttons: 0` | Same, 41/41/0/0 |
| Held page, then `visibilitychange` to hidden | Opening another tab did not hide this one in this harness, so `document.hidden` was overridden to `true` before dispatching the event (the handler reads exactly that). Drag ended, nothing thrown, 41/41/0/0 |
| Throw on the overview, scroll to chapter 4 at once, scroll back | 41/41/0/0 after 3.5 s |
| Toss on the stack, scroll to the top at once, scroll back | 41/41/0/0 |
| Resize 1440x900 to 1280x800 mid-drag | Page still held and `grabbing` after the resize; the following throw launched; 41/41/0/0 |
| Ten overview clicks 40 ms apart | Peak 10 pages in `#flying`; 41/41/0/0 after 3.5 s |
| Ten stack tosses 60 ms apart | Peak 9 in `#flying`; 41/41/0/0; all 17 stack pages visible at full opacity |
| React commits across all of the above | **0** |

Reduced motion (emulated), observed on both pages, identical behaviour:

| | Reference | V5 |
|---|---|---|
| Sphere idle | static | static |
| Mouse parallax | none | none |
| Click fling (x over 240 ms) | flies, 875 to 674 | flies, 880 to 656 |
| Hold lifts, fast release throws | yes, yes | yes, yes |
| Everything back afterwards | yes | yes |

## 6. Touch (CDP `Input.dispatchTouchEvent`, 390x844, mobile and touch emulation)

Environment: `(hover: none)` matches; `#top` has `touch-action: pan-y`. Gestures: `touchStart`, then
`touchMove` steps 16 ms apart, then `touchEnd`; taps are `touchStart`, 70 ms, `touchEnd`. The
sequence column is what the page received, logged in capture phase.

| Gesture | Pointer events received | Result |
|---|---|---|
| Hero, sideways swipe (100,260) to (300,270), 10 moves | `pointerdown, pointermove x10, pointerup` | Sphere turned (page 0 moved 115 px against 4 px of auto-spin drift in the same time); no scroll; nothing thrown |
| Hero, tap on the front page (231,274) | `pointerdown, pointerup` | The page was flung (1 in `#flying`) |
| Hero, vertical swipe up 220 px starting on a page | `pointerdown, pointermove, pointercancel` | Browser took over and scrolled 205 px; no rotation; nothing thrown. This is the scroll-takeover `pointercancel`: no fling |
| Stack, tap on the top page (221,236) | `pointerdown, pointerup` | Top page tossed; screenshot shows the next page on top and the tossed one sliding back in |
| Stack, vertical swipe up 200 px | `pointerdown, pointermove, pointercancel` | Scrolled 70 px; nothing thrown |
| Stack, sideways drag 150 px | `pointerdown, pointermove, pointercancel` | Nothing thrown, no scroll (dragging does not throw on touch, as in the reference) |

## 7. Runtime checks

| Server | Pending rAF (min, max), sampled during a stack throw | rAF per second | `cancelAnimationFrame` |
|---|---|---|---|
| Dev (StrictMode) | 1, 1 | 180 | 1 (first world torn down) |
| Preview | 1, 1 | 180 | 0 |

## 8. Perf check

Production build, Chromium 154 via Playwright, `deviceScaleFactor: 2`, 1440x900, CDP traces, the
same harness on the reference.

| Check | Target | V5 | Reference |
|---|---|---|---|
| Raster during one drag throw on the stack (2.5 s from press) | (compare) | 7.0 ms | 6.0 ms |
| Raster during a spam of ten tosses (3.6 s) | (compare) | 53.7 ms | 53.5 ms |
| Long tasks (over 50 ms) | none | 0 in every trace | 0 |
| Main-thread tasks over 16 ms, one throw / ten tosses | none | 0 / 0 (max 5.32 / 5.14 ms) | 0 / 0 (max 5.29 / 5.58 ms) |
| Main-thread tasks over 16 ms, wheel-scrolling chapters 4 to 6 and back | none | 0 (max 2.86 ms) | 0 (max 3.38 ms) |
| **Paint lifecycle during that scroll** | within about 15% | **738.9 ms** | **756.7 ms** (-2.4%) |
| React commits during scroll, throw and spam | zero | 0 | |
| `requestAnimationFrame` loops | exactly one | One, at runtime in dev and preview | |

## 9. Open points for review

1. **Drag-throw positions agree within a few pixels only with identical input.** With real Playwright
   mouse timing the deviation was 33 to 50 px, from input jitter in both pages' velocity estimate; with
   scripted timestamps it is 6 px (stack) and 2 px (overview). Both runs are reported above.
2. **Servers.** Every dev, preview and reference server started for this phase was stopped and the
   ports (4173, 5173, 4180) confirmed free before committing.
