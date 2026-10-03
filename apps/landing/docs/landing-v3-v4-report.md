# Landing v3, phase V4 report

Phase: V4, change impact (checklist items 52 to 60 in `landing-v3-v0-report.md`), plus the claim 4
screenshots carried over from V3. Branch `landing-v3`. All measurements on the production build
(`vite build`, `vite preview`) unless stated otherwise.

## 1. What was built

| File | Change |
|---|---|
| `src/data/impact.ts` | New: `NODES` (as objects: card, column, label lead and text, summary name), `EDGES`, `CAUSES` (button copy, source node, verb), `GATE`, `WAVE`, `UNREACHED`, derived `nodeOf`, `colCount`, `rowOf` |
| `src/world/store.ts` | `input.cause`, `causeNode`, `hop`, `wave` |
| `src/world/actions.ts` | `seedImpact(k)`: breadth-first search over `EDGES` from the cause, once per click; writes the store and returns the hops |
| `src/world/arrangements.ts` | `impact` (four columns, rows in portrait); table is now `[pile, pack, match, proof, impact, park]` |
| `src/world/labels.ts` | Twelve impact labels below the nodes; per-label class flag now also drives `hit` |
| `src/world/wires.ts` | `curveCtl()`, `cubicD()`, `cubicAtLen()` (point by arc-length fraction); impact edges with base wire, hot wire and pulse, change-only caches, `placeImpactEdges()` |
| `src/components/Wires.tsx` | 16 base edges in `#wg`, hot wires in `#wh`, pulse dots in `#wd` |
| `src/chapters/Impact.tsx` | Cause buttons with `aria-pressed`; summary, total and idle prompt as React state in a live region; staggered delays; remount per click so the entrance replays |
| `src/world/frame.ts` | `reached()`; `src`/`hit` states; wave advance; swell on the wrapper's fine scale; gate stamp; impact labels and edges |

Notes:

- **Hop search.** The reference relaxes the edges six times; V4 runs a real breadth-first search, as
  requested. Results are identical on this graph.
- **Pulse position.** `cubicAt(t)` takes the curve parameter, which runs faster on the straighter
  middle of these S-curves than at the ends, so it would put the pulse ahead of or behind the dash
  head. `cubicAtLen()` walks the same 32-segment polyline as `cubicLen()` to the dash's arc length.
  No `getTotalLength()` or `getPointAtLength()` anywhere in `src/`.
- **Fine scale and rule 4.** The 7% swell rides on the wrapper's fine scale, as specified. At the peak
  of a swell the wrapper carries up to about 9% (7% plus the at most ±2% quantization remainder), for
  0.6 s; the coarse step is unaffected (measured below).

## 2. Checklist status (items 52 to 60)

| # | Item | Status |
|---|---|---|
| 52 | `impact` arrangement: four columns; rows in portrait | Done; pinned on desktop with the world on the right; in portrait all 12 nodes inside the top band (93 to 440 px of 60 to 473) |
| 53 | Node labels below each document | Done; hidden in portrait (0 visible), as in the reference |
| 54 | Edges as curves, horizontal on desktop, vertical in portrait | Done; first portrait edge `M136 162C136 172.75 106.5 172.75 106.5 183.5` (vertical tangents) |
| 55 | Cause buttons with `aria-pressed`; hops breadth first | Done; `aria-pressed` `true,false,false` after cause 1 |
| 56 | Wave at 0.9 hops per second | Done; dt clamped at 0.05 by the frame |
| 57 | Edges draw along their length with a pulse at the head; retract when the cause changes | Done; on switching cause, the first edge's dash offset eases 0 to 153 over about 36 frames, no jump |
| 58 | `src`/`hit` as the wave arrives; 7% swell over 0.6 s; labels turn alarm | Done; measured on p. 173: wrapper scale 0.9816 at rest, 1.0503 at peak (x1.070), coarse step constant at `scale(0.23262)` throughout, about 0.67 s; no re-trigger once settled; 7 labels `hit` for cause 1 |
| 59 | Gate stamp switches to Blocked when the wave reaches it | Done; `.blocked` set, stamp shows "Blocked", `transition-duration: 0s` |
| 60 | Summary in step with the wave; plural forms; total; idle prompt | Done; reference delays (0.05 s, 1/0.9, 2/0.9, 3/0.9, 3/0.9 + 0.35 s); "2 requirements lose", "1 requirement loses", "3 proposal sections contain" all as in the reference |

Reduced motion (emulated): within 120 ms of the click the gate is blocked and all six dependents are
`hit`; largest wrapper scale 1.017 (no swell); summary lines have no delay and `animation: none`.

## 3. Screenshots

In `docs/screens/v4/` (gitignored, on disk only), `<viewport>-<state>-reference.png` and
`<viewport>-<state>-v4.png`. Both pages driven by the same script: buttons activated with
`button.click()`, mouse parked in the corner, fixed waits.

| State | 1440x900 (scroll y) | 390x844 (scroll y) | Wait after click |
|---|---|---|---|
| `impact-start`, no cause | pin start, 4860 | hold middle, 4054 (not pinned at phone width) | |
| `cause1-1.5s` (ISO certificate) | 4860 | 4054 | 1.5 s |
| `cause1-6s` | 4860 | 4054 | 6 s |
| `cause2-6s` (A. Mehta) | 4860 | 4054 | 6 s |
| `cause3-6s` (rates) | 4860 | 4054 | 6 s |
| `proof-to-impact` (transition midpoint) | 4410 | 3523 | |
| `claim4` (ISO certificate statement, proof chapter) | 3330 | 2993 | 2 s |
| `claim4-reader` (390 only: inline reader scrolled into view) | | reader under the header | 2 s |

Reviewed by eye: 1440x900 cause 1 at 1.5 s and 6 s, cause 3 and claim 4 against the reference;
V4's cause 2; 390x844 cause 1 at 6 s against the reference; V4's 390 claim 4 and claim 4 reader.
Every reviewed pair matches. The remaining files were captured but not individually reviewed.

**Claim 4 confirmed:** "ISO/IEC 27001:2022" carries the cited-line highlight in all four cases
(1440 and 390, reference and V4): computed background `rgba(201, 138, 71, 0.32)` and the 7 px amber
bar. At 390 the highlight is in the inline reader, captured as `390x844-claim4-reader-*.png` because
the plain `claim4` capture at 390 ends at the claim sheet.

## 4. Runtime checks

| Server | Pending rAF (min, max), sampled while a wave runs | `cancelAnimationFrame` | First cause by real mouse click: commits |
|---|---|---|---|
| Dev (StrictMode) | 1, 1 | 1 (first world torn down) | 1, at 6 ms |
| Preview | 1, 1 | 0 | 1, at 5 ms |

Frame rate: with the tab in front, V4 and the reference both run at 180 fps idle and 180 fps during
the wave (the display rate). One sampling run reported 47 callbacks per second for both builds; that
tab had not been brought to the front, and Chrome paces background tabs. Re-measured in front:
180 for V4 and for the reference, before and after.

## 5. Perf check

Production build, Chromium 154 via Playwright, `deviceScaleFactor: 2`, 1440x900, CDP traces, the
same harness on the reference.

| Check | Target | V4 | Reference |
|---|---|---|---|
| Raster during the wave (5 s from the first cause click) | (compare) | 55.0 ms | 54.2 ms |
| Raster, 3 s idle at the impact chapter, wave settled | under about 50 ms | 0 ms | 0 ms |
| Long tasks (over 50 ms) | none | 0 in every trace | 0 |
| Main-thread tasks over 16 ms while the wave runs | none | 0 (max task 6.56 ms) | 0 (max 6.82 ms) |
| Main-thread tasks over 16 ms, wheel-scrolling chapters 3 to 5 (match start to the end of the impact chapter) and back | none | 0 (max task 5.09 ms) | 0 (max 5.09 ms) |
| **Paint lifecycle during that scroll** | within about 15% of the reference | **734.2 ms** | **725.7 ms** (+1.2%) |
| Paint lifecycle during the wave / idle at impact | (for comparison) | 657 / 336 ms | 668 / 354 ms |
| React commits while idle at the impact chapter | zero | 0 | |
| React commits during the wave (after the click's own commit) | zero | 0 | |
| React commits, first cause click (programmatic / real mouse) | (watch item) | 1 / 1 | |
| React commits, later cause clicks | (watch item) | 1, 1, 1 | |
| `requestAnimationFrame` loops | exactly one | One, at runtime in dev and preview | |

Raster during the wave is real work in both builds: swelling documents and hot wires repaint as the
wave passes. It matches the reference within 2%.

**Watch item (V3 double commit):** cause clicks commit exactly once, the first included, with a real
mouse click and programmatically. The V3 first-selection double commit does not appear in this chapter.

## 6. Open points for review

1. **Portrait, impact chapter:** as in V2's chapters, at the middle of the chapter the text column
   (top at 412 px) covers the bottom 28 px of the lowest row of the graph. All nodes are inside the
   56% band; the overlap comes from the unpinned column scrolling into it, the pattern you accepted
   for checking on a real phone in V6.
2. **Servers.** Every dev, preview and reference server started for this phase was stopped and the
   ports (4173, 5173, 4180) confirmed free before committing.
