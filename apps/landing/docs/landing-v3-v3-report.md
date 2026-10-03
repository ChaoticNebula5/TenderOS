# Landing v3, phase V3 report

Phase: V3, evidence tracing (checklist items 41 to 51 in `landing-v3-v0-report.md`). Branch
`landing-v3`. All measurements on the production build (`vite build`, `vite preview`) unless
stated otherwise.

## 1. What was built

| File | Change |
|---|---|
| `src/world/store.ts` | `input.sel`, `input.threadP` (the loop's copy of the selection), `CT` offsets, `els.claimBtn` refs |
| `src/world/arrangements.ts` | `proof` (evidence wall); table is now `[pile, pack, match, proof, park, park]` |
| `src/world/layout.ts` | `measure()` records each document's cited-line offset `CT` (`offsetTop`, at natural size) |
| `src/world/actions.ts` | `selectClaim(i)`, `clearClaims()` |
| `src/world/wires.ts` | `cubicAt()`, `cubicLen()`: Bézier point and length computed from control points |
| `src/world/thread.ts` | New: `placeThread()`, the evidence thread with change-only writes of path, glow path, dash, offset, opacity, end-ring position and colour |
| `src/components/Wires.tsx` | Thread, glow and end-ring refs |
| `src/chapters/Evidence.tsx` | React state for selection and reviewed set; claim buttons with `aria-pressed` and kept verdict marks; trace panel; counter and summary; Reset; phone reader as JSX |
| `src/world/frame.ts` | Claim rect read at the top of the frame; focus overlay to the reading plane; wall dimming; `lit` state for the cited document; thread drawing |

### Design decisions beyond the reference

1. **No `getTotalLength()`.** The reference reads the thread's length from the DOM whenever its path
   changes, which is every frame while the document glides or the page scrolls, and after that
   frame's writes. V3 computes it from the four control points (32-segment polyline, padded by 0.2%
   plus 1 px so the dash always covers the true length). The only layout read in the frame is the
   statement's `getBoundingClientRect`, at the top.
2. **Reading scale on a coarse step.** The reference's `FS = min(1, W.w*.56/CW, W.h*.84/CH)` is exact
   only when it clamps to 1. V3 floors it onto the QSTEP grid, `exp(floor(ln FS * 24) / 24)`, so at
   rest the coarse layer equals the final scale and the wrapper's fine scale is exactly 1.
3. **Counter is a live region.** `#cstat` has `aria-live="polite"` as requested; in the reference only
   `#trace` has it.

## 2. Checklist status (items 41 to 51)

| # | Item | Status |
|---|---|---|
| 41 | `proof` evidence wall, two staggered rows curving away; portrait grid | Done; matches the reference at the proof pin start and the transition |
| 42 | Claims as buttons; selection marks `.on`, `aria-pressed`, keeps the verdict mark | Done; `aria-pressed` verified `true,false,false,false,false` after selecting claim 1; marks kept across selections (all-reviewed screenshot) |
| 43 | Trace panel per verdict; counter; final summary sentence | Done; React state, changes on click only |
| 44 | Reset clears selection, verdicts, trace and counter | Done, verified: trace empty, counter "0 of 5", all buttons back to `cl`, thread cleared, cited document back on the wall (unlit, z 2850) |
| 45 | Cited document to the reading plane, upright, on top, natural size | Done; at 1440x900 coarse `scale(1)`, wrapper scale 1, z 9000. At 900x560 coarse `scale(0.8825)` (the floored step) with wrapper scale 1 |
| 46 | Other evidence dims while a claim is selected | Done |
| 47 | Cited line highlighted (`.ct` under `lit`) | Done; seen in screenshots on the CV row (claim 1) and the CA certificate's three-year average row (claim 2). Claim 4 (ISO certificate) was not among the captured states |
| 48 | Thread from the live sentence to the cited line, drawn at 1.5/s, glow when supported, colour per verdict | Done; leaves the sentence's left edge and lands on the document's right edge in this chapter (world on the left) |
| 49 | Dead-end thread with end ring for unsupported and flagged | Done; alarm for claim 3, slate for claim 5 |
| 50 | Sentence rect read once at the top of the frame, before writes | Done; measured exactly 1 `getBoundingClientRect` per frame while drawing, 0 with no selection |
| 51 | Phone reader inline below the sheet, full column width, refreshed on resize; no thread in portrait | Done; resizing 1440 to 390 mid-selection shows the reader (436 px) and clears the thread; resizing back removes the reader and restores the thread |

## 3. Screenshots

In `docs/screens/v3/` (gitignored, on disk only), `<viewport>-<state>-reference.png` and
`<viewport>-<state>-v3.png`. Both pages were driven by the same script: claims activated with
`button.click()` in order, mouse parked in the corner, 2 s settle after the last click.

| State | 1440x900 (scroll y) | 390x844 (scroll y) |
|---|---|---|
| `proof-start`, no selection | pin start, 3330 | hold middle, 2993 (not pinned at phone width) |
| `claim1` (supported) | 3330 | 2993 |
| `claim2` (supported) | 3330 | 2993 |
| `claim3-unsupported` (dead end) | 3330 | 2993 |
| `claim5-flagged` (commitment) | 3330 | 2993 |
| `all-reviewed` (all five, last is claim 5) | 3330 | 2993 |
| `match-to-proof` (transition midpoint) | 2961 | 2360 |
| `claim1-reader` (inline reader) | | reader scrolled to just under the header |

Reviewed by eye: 1440x900 claim 1, claim 3, all reviewed and the transition against the reference;
V3's claim 2 and claim 5; the 390 inline reader against the reference. Every reviewed pair matches.
The remaining files were captured but not individually reviewed.

## 4. Runtime checks

| Server | Pending rAF (min, max), sampled at the proof chapter while a thread draws | rAF callbacks per second | `cancelAnimationFrame` |
|---|---|---|---|
| Dev (StrictMode) | 1, 1 | 180 | 1 (first world torn down before the second starts) |
| Preview | 1, 1 | 180 | 0 |

## 5. Perf check

Production build, Chromium 154 via Playwright, `deviceScaleFactor: 2`, 1440x900, CDP traces, the
same harness on the reference.

| Check | Target | V3 | Reference |
|---|---|---|---|
| Raster, 3 s idle at the proof chapter with claim 1 selected and drawn | under about 50 ms | 0 ms | 14.6 ms |
| Raster, 3 s idle on the hero | under about 50 ms | 0 ms | 0 ms |
| Long tasks (over 50 ms) | none | 0 in every trace | 0 |
| Main-thread tasks over 16 ms, wheel-scrolling chapters 2 to 4 (pack pin start to the end of the proof chapter) and back | none | 0 (max task 5.04 ms) | 0 (max 5.07 ms) |
| Main-thread tasks over 16 ms, during the selection click and 2 s of thread drawing and settling | none | 0 (max task 6.45 ms) | 0 (max 6.34 ms) |
| **Paint lifecycle during that scroll** | within about 15% of the reference | **690.7 ms** | **685.1 ms** (+0.8%) |
| Paint lifecycle, selection and drawing / idle at proof / idle hero | (for comparison) | 197 / 287 / 318 ms | 217 / 319 / 326 ms |
| React commits while idle, hero and proof with a claim selected and drawn | zero | 0 and 0 | |
| React commits while the thread draws | zero | 0 | |
| React commits per selection | (for information) | first selection on a page: 2; every later selection and Reset: 1. See open point 1 | |
| `requestAnimationFrame` loops | exactly one | One, at runtime in dev and preview | |

Paint during the scroll is within 1% of the reference, well inside the 15% check.

## 6. Open points for review

1. **The first selection on a page produces two React commits, later ones one.** Measured with real
   mouse clicks in dev and preview: the first selection commits once at sync priority (Evidence and
   Trace render) and then once more at normal priority, about 6 ms later, rendering no component.
   Second and later selections, programmatic clicks after the first, and Reset each commit exactly
   once. Neither commit happens while the thread draws or while idle. I have not identified what
   React schedules the empty commit for; it is once per page load and renders nothing.
2. **Servers.** Every dev, preview and reference server started for this phase was stopped and the
   ports (4173, 5173, 4180) confirmed free before committing.
