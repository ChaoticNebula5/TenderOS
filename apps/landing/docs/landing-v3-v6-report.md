# Landing v3, phase V6 report

Phase: V6, hardening (checklist items 69 to 76 in `landing-v3-v0-report.md`) plus extras 8 to 12.
Branch `landing-v3`. Measurements on the production build (`vite build`, `vite preview`) unless
stated otherwise. Chromium 154 driven by Playwright over CDP.

## 0. Decisions needed

Three requirements cannot be met without changing what the page shows, so per the instruction they
were not changed. Each is measured below with a proposal.

1. **Theme change costs more than one style recalculation** (item 69). The toggle itself costs one,
   but the reference's `body { transition: background-color .45s, color .45s }` then restyles the
   whole tree on every frame of the fade. Section 1.
2. **Nav links to pinned chapters land 64 px before rest** (item 73), from the reference's
   `scroll-padding-top`. Section 4.
3. **8 of 68 text/background pairs are under WCAG AA** (item 74), all from reference colours.
   Section 5.

## 1. Light theme (item 69)

Code: the header button calls `toggleTheme()` (`src/world/store.ts`), which sets `data-theme` on the
root, rewrites the button label, and sets `theme.light`, which the loop reads for the depth fade.
No React state, no loop restart. The loop no longer reads `data-theme` from the DOM each frame.

| Check | Result |
|---|---|
| Label | "Light", then "Dark", then "Light", the same as the reference |
| `data-theme` | `light` / `dark` on the root, the same as the reference |
| Depth fade | All 41 document opacities identical to the reference in dark (0.72) and in light (0.4); back to the dark values after toggling back |
| Loop | 0 `cancelAnimationFrame` during the toggle, 1 pending callback after it |
| React commits | 0 (the toggle is not React state) |
| Style recalculations, 700 ms after the click, reduced motion on | V6 7, reference 7 (2 full-document recalcs, 5 tiny) |
| Style recalculations, 700 ms after the click, motion on | V6 172, reference 181 |

**Why not one.** The click causes one full recalculation (about 1,234 elements). The rest are the
reference's CSS transitions: `body` fades `background-color` and `color` over 0.45 s, and because
`color` is inherited, every frame of that fade restyles about 1,251 elements; the `.lb` label colour
transitions add small ones. Getting to one recalculation means changing the theme fade, for example:

- (a) keep as is (reference behaviour);
- (b) fade only `body`'s `background-color` and let text colour switch at once (one element restyled
  per frame instead of the whole tree; the text no longer fades);
- (c) no transition at all (the switch is instant).

(b) and (c) change pixels during the 0.45 s fade, so neither was applied.

Screenshots: `docs/screens/v6/<size>-light-<state>-reference.png` and `-v6.png` (section 10).

## 2. Layout at many sizes (item 70)

At each size, at the hero and at the pack (V6 screenshots `docs/screens/v6/<size>-hero-v6.png` and
`-pack-v6.png`):

| Size | Horizontal overflow | Largest on-screen scale | Clipped text | World framing (pack, at rest) | Pinned columns between header and bottom edge |
|---|---|---|---|---|---|
| 1920x1080 | none | 0.56 | none | inside | pack 294..834, proof 201..927, impact 254..874 |
| 1440x900 | none | 0.45 | none | inside | 231..717, 115..833, 168..780 |
| 1280x720 (short desktop) | none | 0.36 | none | inside | 164..594, 68..690, 88..670 |
| 1024x768 | none | 0.33 | none | inside | 192..614, 86..720, 108..698 |
| 768x1024 | none | 0.24 | none | inside (0 px, pixel-identical to the reference) | 265..807, 120..953, 124..948 |
| 390x844 | none | 0.21 | none | 7 px (reference 6) | not pinned |
| 360x640 | none | 0.15 | none | 5 px (reference 5) | not pinned |
| 320x568 | none | 0.13 | none | 5 px (reference 5) | not pinned |

`scrollWidth` is 15 px under `innerWidth` at every size (the scrollbar). The proof column was
measured with claim 3 selected (its long explanation) and the impact column with a full summary.
At phone sizes the helix is fitted with an unbounded height factor by design and its edge pages are
tilted, so a few pixels past the band are expected; V6 matches the reference within 1 px.

**Live resize across 760 px.**

| Step | Result |
|---|---|
| 1440, claim 1 selected, thread drawing | Thread drawn, CV on the reading plane (z 9000, lit), reader empty |
| Resize to 390 | Reader appears (436 px), thread cleared, CV back on the wall (z 3500, still lit), scrims hidden, no labels |
| Back to 1440 | Reader gone, thread and focus back |
| 1440, cause 1, 1 s into the wave | Horizontal edges, 12 labels |
| Resize to 390 | Wave continues to completion (7 hit, gate blocked), vertical edges, no labels |
| Back to 1440 | Horizontal edges, 12 labels |

Nothing in `#flying` and no `.held` at any step.

## 3. Reduced motion (item 72)

Code: `motion.reduced` (`src/world/math.ts`) follows `matchMedia('(prefers-reduced-motion: reduce)')`
through a `change` listener on the world's `AbortSignal`. When it switches on, the parallax target is
zeroed (a page loaded with it on never sets one). Off, every branch reads the same value as before.

Toggled live with CDP media emulation in the same session:

| Check | Result |
|---|---|
| Off, then on | Auto-spin stops |
| Parallax | None on mouse move |
| Raw scroll | World final within two frames of a scroll |
| Morphs snap at the halfway point | At 40% of match to proof the match pages hold their exact match pose; at 60% they have left |
| Helix travel | Same pose at pin start and pin middle |
| `bringToFront` | Lands on the next frame |
| Thread | Fully drawn after two frames (dash offset 0); focus jumps (z 9000) |
| Wave | Complete within two frames (gate blocked) |
| Swell | None (largest wrapper scale 1.017, the normal fine-scale remainder) |
| Summary animation | `animation-name: none`, no delay |
| Transitions | 0.000001 s |
| On, then off | Spin resumes; body transition back to 0.45 s |

**V5 open point, the held page under reduced motion** (sphere static), pixels over 24/255 below the
header: **0 at 1440x900, 0 at 390x844**. Screenshots `docs/screens/v6/<size>-rm-overview-held-*.png`.

## 4. Keyboard (item 73)

**Tab order, 1440x900** (24 of 24 rendered controls): TenderOS, Analysis, Evidence, Impact, Light,
Start a tender, See how it works, the six index rows (p. 18, 41, 67, 122, 173, 214), the five
statements, Reset, the three causes, Start a tender, Read the architecture, then focus leaves the page.

**Tab order, 390x844** (21 of 21): the same without the three nav links, which are hidden at phone
width.

No stop is off screen or inside an `aria-hidden` layer, and there is no trap. Every stop matches
`:focus-visible` in both themes; the ring is amber in each (`rgb(201,138,71)` dark,
`rgb(138,90,43)` light). It computes to 1.6 px in this harness (the CSS is the reference's 2 px; the
browser window appears to run at 80% zoom).

Enter and Space: index rows (the row is marked after its page spins to the front), statements
(`aria-pressed` true), Reset ("0 of 5 statements reviewed"), causes (`aria-pressed` true). All pass.

**Anchor landings, 1440x900:**

| Link | Lands | At rest | Heading |
|---|---|---|---|
| TenderOS, both closing buttons | `#top`, scrollY 0 | yes | clear (247) |
| Hero "Start a tender" | `#bid`, 6326 | yes (hold from 6282) | clear (400) |
| Nav Analysis, hero "See how it works" | `#pack`, 836 | **64 px before** the hold (900) | clear (325) |
| Nav Evidence | `#proof`, 3266 | **64 px before** (3330) | clear (209) |
| Nav Impact | `#stress`, 4796 | **64 px before** (4860) | clear (262) |

The 64 px is the reference's `html { scroll-padding-top: calc(env(safe-area-inset-top) + 64px) }`.
On a pinned chapter that stops the jump 64 px before the pin starts: about 92% through the morph,
99.8% after easing. **Proposed fix, not applied** (it changes where a nav click lands compared with
the reference): `.pin { scroll-margin-top: calc(0px - env(safe-area-inset-top, 0px) - 64px) }`,
which lands pinned chapters exactly at the pin start; their headings sit 209 to 325 px down, clear of
the header.

## 5. Accessibility (item 74)

**axe-core 4.10.2** (loaded from cdnjs into the production page, so not a dependency), all rules:

| | Dark | Light |
|---|---|---|
| 1440x900 | 0 violations | `color-contrast`: `#716759` on `#E9E2D4` = 4.3, 25 nodes (`#where` name, chapter labels, index rows, notes, Reset) |
| 390x844 | 0 violations | `color-contrast`: the same pair, 2 nodes (footer) |

A first light-theme run also flagged `.mark` at 1.09:1. That was the theme fade caught mid-way (the
renderer was producing frames slowly, so the 0.45 s fade took up to 3 s); re-run after waiting for
the final colour, it does not appear. axe reports 321 to 377 contrast checks per run as "incomplete"
(text on gradients and in the transformed world); those pairs were computed by hand below.

Structure: `lang="en"`; headings H1 then five H2, in order; one each of `header`, `nav`, `main`,
`footer`; world layers `aria-hidden` (world, both scrims, labels, wires, flying, the claim boxes,
the reader) with no focusable element inside them.

Live regions: `#where`, `#trace`, `#cstat` and `#fall` are polite. The two in the evidence chapter
never carry the same content: after each selection `#trace` holds the verdict and hops ("Supported.
Traced to source in three steps...") and `#cstat` the count ("1 of 5 statements reviewed") or the
summary sentence.

**WCAG contrast, every text colour on every background it sits on** (both themes, paper colours
inside documents, label chips evaluated over the background and, worst case, over paper). 8 of 68
pairs fail; all are reference colours. Full table in Appendix A.

| Pair | Ratio | Needs |
|---|---|---|
| Light: `--ink-3` #716759 on `--bg` #E9E2D4 (chapter labels, notes, `#where` name, index rows, hop labels, summary step labels, footer, Reset) | 4.30 | 4.5 |
| Dark: status Met, amber #C98A47 on a label chip over paper (#423E38) | 3.65 | 4.5 |
| Dark: status Partially met, slate #7FA5AA on the same | 3.98 | 4.5 |
| Dark: status Not met and hit labels, alarm #CF5F45 on the same | 2.72 | 4.5 |
| Paper: issuer and row labels #7A6B58 on the darker end of the paper gradient #EEE5D3 | 4.12 | 4.5 |
| Paper: `c-ref` and `c-foot` #9A8B78 on #EEE5D3 | 2.65 | 4.5 |
| Paper: the same on the lighter end #F8F2E6 | 2.97 | 4.5 |
| Paper: stamp "Blocked" #B4452F on #EEE5D3 | 4.38 | 4.5 |

Over the plain background the dark label chips pass (4.81 to 7.04). Text inside documents is
decorative in the world (`aria-hidden`) but is read visually, and the phone reader shows it full size.

**Proposed fixes, not applied** (smallest change along the same hue that reaches 4.5:1):

| Colour | Proposal |
|---|---|
| Light `--ink-3` #716759 | #6E6456 (3% darker, 4.50:1) |
| Paper issuer and row labels #7A6B58 | #736553 (5.5% darker, 4.52:1) |
| Paper `c-ref`, `c-foot` #9A8B78 | #706558 (27% darker, 4.55:1) |
| Stamp "Blocked" #B4452F | #B0442E (2% darker, 4.52:1) |
| Dark label chip, 80% `--bg` | 90% `--bg`: amber 5.02, slate 5.48, alarm 3.75; 95%: alarm 4.33 |

## 6. Safe areas (item 75)

Verified with CDP `Emulation.setSafeAreaInsetsOverride` (top 47 px, bottom 34 px, as on a notched
phone), 390x844 mobile:

| | No insets | Insets 47 / 34 |
|---|---|---|
| `viewport` meta | `viewport-fit=cover` | same |
| `html` padding top / bottom | 0 / 0 | 47 / 34 px |
| Header padding top | 15 px | 62 px |
| Footer padding bottom | 26 px | 60 px |
| `scroll-padding-top` | 64 px | 111 px |

Not covered, as in the reference: the world's top edge uses a fixed 60 px header height in
`measure()`, so with a top inset the world starts under the taller header; no fixed layer handles
left or right insets (landscape notches). Not verifiable without a real device: Safari's toolbars and
home indicator, real rotation.

## 7. Dead code (item 76)

| Check | Result |
|---|---|
| knip 5 | No unused files. 13 exports and 5 exported types were used only inside their own module; they are now module-private. 1 unused devDependency, `prettier` (no script or config used it), removed. knip now reports nothing |
| `tsc` with `noUnusedLocals`, `noUnusedParameters` | Clean |
| Commented-out code, `console.*` | None |
| TODO, FIXME, XXX, HACK | None |
| Stale phase comments | Two ("from V5", "from V2") rewritten |
| CSS selectors | All 201 selectors in the production stylesheet match an element in at least one state, driven through every class the loop adds (`lit`, `part`, `gap`, `hit`, `src`, `held`, `hot`, `on`, `blocked`, `grab`, `grabbing`, `point`), the light theme and the phone reader. None removed |

## 8. Extras

### 8. Link destinations

`src/data/links.ts` holds the four outbound destinations; `Overview.tsx` and `Submission.tsx` read
from it. Placeholders (today's values from the reference):

| Button | Constant | Placeholder |
|---|---|---|
| Hero "Start a tender" | `LINKS.heroStart` | `#bid` |
| Hero "See how it works" | `LINKS.heroHowItWorks` | `#pack` |
| Closing "Start a tender" | `LINKS.closingStart` | `#top` |
| Closing "Read the architecture" | `LINKS.closingArchitecture` | `#top` |

### 9. Soak test

90 s scripted on the production build: three full scrolls, 10 overview flings and 10 stack tosses,
every claim, all three causes, five theme toggles, the breakpoint crossed twice. GC forced through
CDP (`HeapProfiler.collectGarbage`) before each reading.

| | JS heap | DOM nodes | Event listeners |
|---|---|---|---|
| After load | 2.09 MB | 1789 | 167 |
| After 90 s | 3.43 MB | 1845 | 180 |

To separate one-time growth from a leak, the same 90 s workload was run three times in a row:

| | JS heap | DOM nodes | Event listeners |
|---|---|---|---|
| After load | 2.20 MB | 1789 | 167 |
| After cycle 1 | 3.42 MB | 1843 | 180 |
| After cycle 2 | 3.50 MB | 1843 | 180 |
| After cycle 3 | 3.54 MB | 1843 | 180 |

No leak: nodes and listeners are flat after the first cycle and the heap creeps by 0.08 then 0.04 MB
(engine warm-up). The one-time growth is accounted for: the 54 nodes are the content the evidence and
impact chapters now hold (trace panel, summary lines); the 13 listeners are Playwright's, installed in
an isolated world on its first `page.click()` (the same +13 appears on a plain `data:` page with no app
code). The app's own listeners stay at 167. After the soak, every page is back in `#cards` with no
`.held`.

### 10. Full regression sweep

Pairs at 1440x900 and 390x844 in dark and light, pixels over 24/255 below the header. Wall-clock
captures cannot match pixel for pixel where motion depends on frame time (the sphere's spin, the slow
park shell, mouse parallax easing): a reference-against-reference control differed by up to 228,625
pixels on the hero. So both pages were driven with a **virtual frame clock** (an init script that
hands every `requestAnimationFrame` callback exactly 1/60 s and freezes at frame 1200), with each
action at the same frame number. Both apps take all motion from those timestamps.

| State | 1440 dark | 1440 light | 390 dark | 390 light |
|---|---|---|---|---|
| Hero | 197,486 (see a) | 51,305 (a) | 48,682 (a) | 9,437 (a) |
| Hero, reduced motion | **0** | **0** | **0** | **0** |
| Pack, row 3 hovered | 0 | 0 | 0 | 0 |
| Match | 0 | 0 | 0 | 0 |
| Proof, claim 1 | 0 | 0 | 0 | 0 |
| Proof, claim 3 | 0 | 0 | 0 | 0 |
| Impact, cause 1 settled | 36 (b) | 43 (b) | 0 | 0 |
| Stack at rest | 0 | 0 | 0 | 0 |
| Page end | 0 | 0 | 0 | 0 |

(a) The sphere's auto-spin angle is time since the loop started. The reference's loop starts during
HTML parsing, V6's after the module loads and React mounts, so at the same virtual frame V6's sphere
is a few frames behind in spin phase. Under reduced motion (no time term in the sphere) the hero is 0
in all four.

(b) Same box in both themes (x 331 to 465, y 575 to 637), where the left scrim fades out and the
faint, slowly turning park shell shows through. The park shell turns with frame time even under
reduced motion (as in the reference) and carries the same start offset. The text there (the summary)
is identical in crops of both pages. Not present at 390, where there is no scrim.

Light pairs saved as `docs/screens/v6/<size>-light-<state>-{reference,v6}.png`.

### 11. README

`apps/landing/README.md`: what the app is, how to run, build and check it, how to compare against the
reference, the twelve rules, where things are, known limitations. No em dashes.

### 12. Bundle and dependencies

| File | Raw | gzip |
|---|---|---|
| JS | 241,076 B | 77,110 B |
| CSS | 16,475 B | 4,659 B |

Dependencies: `react`, `react-dom` (runtime). Dev: `vite`, `@vitejs/plugin-react` (build),
`typescript`, `@types/react`, `@types/react-dom`, `@types/node` (`tsconfig.node.json`), `eslint`,
`@eslint/js`, `typescript-eslint`, `eslint-plugin-react-hooks`, `eslint-plugin-react-refresh`,
`eslint-config-prettier`, `globals` (all imported by `eslint.config.js`). knip confirms each is used.
`prettier` was removed.

## 9. Runtime and perf

Single loop at runtime, sampled after a live reduced-motion toggle and a theme toggle:

| Server | Pending rAF (min, max) | Per second | `cancelAnimationFrame` |
|---|---|---|---|
| Dev (StrictMode) | 1, 1 | 170 | 1 (first world torn down) |
| Preview | 1, 1 | 172 | 0 |

Perf check from the plan, chapters 1 to 6, `deviceScaleFactor: 2`, 1440x900, the same harness on
the reference:

| Check | Target | V6 | Reference |
|---|---|---|---|
| Raster, 3 s idle at each chapter 1 to 6 | under about 50 ms | 1.0, 0, 0, 0, 0, 0 ms | 0.7, 0, 0, 0, 0, 0 ms |
| Long tasks, idle at each chapter | none | 0 | 0 |
| Main-thread tasks over 16 ms, full scroll top to bottom and back | none | 0 (max 5.09 ms) | 0 (max 5.10 ms) |
| Paint lifecycle during that scroll | within about 15% | 806.7 ms (-3.2%) | 833.0 ms |
| Raster during that scroll | (compare) | 94.3 ms | 97.8 ms |
| React commits across the whole run | zero | 0 | |

## Final state

### The twelve architecture rules

| # | Rule | Evidence |
|---|---|---|
| 1 | All text is DOM | Every document is JSX in `src/documents`; no canvas or texture anywhere in `src` |
| 2 | One rAF loop, dt clamped at 0.05, reduced motion snaps | `requestAnimationFrame` appears only in `src/world/frame.ts`; pending callbacks always 1 in dev and preview in every phase; `Math.min(dt, 0.05)`; section 3 |
| 3 | React renders once; zero commits while idle | 0 commits idle in every phase; 0 during the thread, the wave, every throw and the theme toggle; commits only on clicks that change chapter UI (claims, causes) |
| 4 | Three nested layers, quantized coarse scale | `Doc.tsx`; `QSTEP = 24`; focus lands on a step (V3: coarse 0.8825, wrapper 1 at 900x560); swell rides the wrapper only (V4: coarse constant through the swell) |
| 5 | Final scale capped at 1 | `Math.min(1, s * G)`; largest on-screen scale 0.56 across 8 sizes; reading plane at exactly 1 at 1440 |
| 6 | No animated paint properties inside documents | State rings by `data-s` switch instantly; glows fade by opacity; gate stamp `transition: 0s`; `.held` is a static class |
| 7 | No per-frame root custom-property writes | The loop writes no custom properties; the theme is one attribute per click; `#atmo` moves by transform |
| 8 | Write only what changed | Caches for transform, opacity, z-index, visibility, state, labels, wire and thread paths, dashes, offsets; idle raster 0 to 1 ms |
| 9 | Layout read once at the start of a frame | One `getBoundingClientRect` per frame while the thread draws, 0 otherwise (V3); no `getTotalLength`/`getPointAtLength` (lengths and points computed) |
| 10 | No `mix-blend-mode` or `backdrop-filter` over the world | None in the CSS; header made opaque in V1 |
| 11 | Framing from geometry | `measure()` fits each arrangement to the live world rect; framing measured at 8 sizes |
| 12 | INR, Indian context, no em dashes | Copy is the reference's; no em dash in `src`, `index.html` or the README |

### Deliberate deviations from the reference, V0 to V6

| Phase | Deviation | Visual change |
|---|---|---|
| V1 | Header `backdrop-filter` removed; header background opaque `var(--bg)` (rule 10, option c) | Yes: no see-through behind the header |
| V1 | Document templates as JSX instead of `innerHTML` | No |
| V2 | Labels, wires and index rows rendered once by React with refs; label class flags written only on change | No |
| V2 | `pick()` added in V2 rather than V5 | No |
| V3 | Thread length computed from control points (no `getTotalLength`) | No |
| V3 | Reading scale floored onto a coarse step | Only where the reference's scale is below 1 and off-step: the page lands up to 4% smaller and sharp |
| V3 | `#cstat` is a live region | No |
| V4 | Hops by breadth-first search | No (same hops) |
| V4 | Pulse placed by arc length (`cubicAtLen`) | The pulse sits on the dash head; the reference's `getPointAtLength` does the same |
| V4 | Swell peak up to about 9% on the wrapper's fine scale (accepted) | No |
| V5 | Pages reparented at mode switches, not checked per frame | No |
| V5 | Quick stack click converts the held page straight into a toss | No |
| V5 | `pointercancel`, `blur`, hidden tab and a buttons-0 move release a held page without throwing | Only in those cases (the reference could fling or leave a page held) |
| V5 | `stop()` lands airborne pages; no allocation in `pointermove` | No |
| V6 | Reduced motion read live; parallax target zeroed when it switches on | No when the setting is unchanged |
| V6 | Theme flag in the store instead of reading `data-theme` each frame | No |
| V6 | Outbound links in `src/data/links.ts`; internal-only exports made private; `prettier` removed | No |

### Perf summary across phases

Paint during scroll against the reference in the same harness:

| Phase | Scroll measured | Paint V / reference | Raster (measured case) V / reference | Long tasks | React commits idle |
|---|---|---|---|---|---|
| V1 | hero and transition | (idle 351 / 379 ms) | idle hero 0.4 / 0.5 ms | 0 | 0 |
| V2 | chapters 1 to 3 | 670 / 616 ms (+9%) | idle pack 0 / 0 ms | 0 | 0 |
| V3 | chapters 2 to 4 | 690.7 / 685.1 ms (+0.8%) | idle proof, claim selected 0 / 14.6 ms | 0 | 0 |
| V4 | chapters 3 to 5 | 734.2 / 725.7 ms (+1.2%) | during the wave 55.0 / 54.2 ms | 0 | 0 |
| V5 | chapters 4 to 6 | 738.9 / 756.7 ms (-2.4%) | ten tosses 53.7 / 53.5 ms | 0 | 0 |
| V6 | chapters 1 to 6 | 806.7 / 833.0 ms (-3.2%) | full scroll 94.3 / 97.8 ms | 0 | 0 |

No main-thread task over 16 ms in any phase's measured scroll or interaction.

### Known limitations

- Outbound buttons point at in-page anchors (placeholders, section 8).
- Theme switch restyles the whole tree per frame during the 0.45 s fade (section 1, decision needed).
- Nav links to pinned chapters land 64 px before rest (section 4, decision needed).
- 8 text/background pairs under WCAG AA, all reference colours (section 5, decision needed).
- On a phone, unpinned chapters let the text column scroll over the bottom of the world (accepted in V2;
  to check on a real phone).
- The world's top edge ignores a top safe-area inset; left and right insets are not handled.
- Touch, safe areas and phone layout verified by emulation only, not on a device.
- The first claim selection on a page costs two React commits (V3; not chased, as agreed).
- No automated test suite in the app; verification is the phase harness described in the reports.

## Appendix A. Contrast table

| Theme | Where | Text | fg | bg | Ratio | Needs | |
|---|---|---|---|---|---|---|---|
| dark | page | h1, h2, body ink | #F3ECE1 | #15110D | 16.01 | 3 | pass |
| dark | page | headings and body text (ink) | #F3ECE1 | #15110D | 16.01 | 4.5 | pass |
| dark | page | lead, txt, tally, why, nav, secondary buttons (ink-2) | #BCB0A0 | #15110D | 8.82 | 4.5 | pass |
| dark | page | lbl, note, #where name, idx rows, hop labels, fall spans, footer, Reset (ink-3) | #8B8073 | #15110D | 4.86 | 4.5 | pass |
| dark | page | amber: italic headings, mark OS, kick, #where number, idx number | #C98A47 | #15110D | 6.45 | 4.5 | pass |
| dark | page | slate: kick.flag, tally partial | #7FA5AA | #15110D | 7.04 | 4.5 | pass |
| dark | page | alarm: kick.bad, fall bad, tally gap | #CF5F45 | #15110D | 4.81 | 4.5 | pass |
| dark | page | primary button: bg on ink | #15110D | #F3ECE1 | 16.01 | 4.5 | pass |
| dark | page | primary button hover: bg on amber | #15110D | #C98A47 | 6.45 | 4.5 | pass |
| dark | page | cause button pressed: bg on alarm | #15110D | #CF5F45 | 4.81 | 4.5 | pass |
| dark | label chip over background | ink-2 / ink / amber / slate / alarm | | #15110D | 8.82 / 16.01 / 6.45 / 7.04 / 4.81 | 4.5 | pass |
| dark | label chip over paper | ink-2 / ink | | #423E38 | 4.98 / 9.05 | 4.5 | pass |
| dark | label chip over paper | status Met (amber) | #C98A47 | #423E38 | 3.65 | 4.5 | **FAIL** |
| dark | label chip over paper | status Partially met (slate) | #7FA5AA | #423E38 | 3.98 | 4.5 | **FAIL** |
| dark | label chip over paper | status Not met, hit (alarm) | #CF5F45 | #423E38 | 2.72 | 4.5 | **FAIL** |
| light | page | h1, h2, body ink | #231E19 | #E9E2D4 | 12.82 | 4.5 | pass |
| light | page | lead, txt, tally, why, nav, secondary buttons (ink-2) | #4A423A | #E9E2D4 | 7.64 | 4.5 | pass |
| light | page | lbl, note, #where name, idx rows, hop labels, fall spans, footer, Reset (ink-3) | #716759 | #E9E2D4 | 4.30 | 4.5 | **FAIL** |
| light | page | amber | #8A5A2B | #E9E2D4 | 4.55 | 4.5 | pass |
| light | page | slate | #3E6167 | #E9E2D4 | 5.24 | 4.5 | pass |
| light | page | alarm | #9E3B2B | #E9E2D4 | 5.23 | 4.5 | pass |
| light | page | primary button: bg on ink | #E9E2D4 | #231E19 | 12.82 | 4.5 | pass |
| light | page | primary button hover: bg on amber | #E9E2D4 | #8A5A2B | 4.55 | 4.5 | pass |
| light | page | cause button pressed: bg on alarm | #E9E2D4 | #9E3B2B | 5.23 | 4.5 | pass |
| light | label chip over background | ink-2 / ink / amber / slate / alarm | | #E9E2D4 | 7.64 / 12.82 / 4.55 / 5.24 / 5.23 | 4.5 | pass |
| light | label chip over paper | ink-2 / ink / amber / slate / alarm | | #ECE5D8 | 7.87 / 13.19 / 4.69 / 5.39 / 5.38 | 4.5 | pass |
| both | paper, bottom #EEE5D3 | c-t title / c-p, c-row / card text | | #EEE5D3 | 14.33 / 12.52 / 13.61 | 4.5 | pass |
| both | paper, bottom #EEE5D3 | c-top issuer, row labels | #7A6B58 | #EEE5D3 | 4.12 | 4.5 | **FAIL** |
| both | paper, bottom #EEE5D3 | c-top tag, c-sub, c-k, seal | #8A5A2B | #EEE5D3 | 4.69 | 4.5 | pass |
| both | paper, bottom #EEE5D3 | c-ref, c-foot | #9A8B78 | #EEE5D3 | 2.65 | 4.5 | **FAIL** |
| both | paper, bottom #EEE5D3 | c-small | #5E5141 | #EEE5D3 | 6.15 | 4.5 | pass |
| both | paper, bottom #EEE5D3 | stamp Clear to submit | #3E6167 | #EEE5D3 | 5.40 | 4.5 | pass |
| both | paper, bottom #EEE5D3 | stamp Blocked | #B4452F | #EEE5D3 | 4.38 | 4.5 | **FAIL** |
| both | paper, top #F8F2E6 | c-t / c-p / card text / issuer / amber ink / c-small / stamps | | #F8F2E6 | 16.08 / 14.05 / 15.27 / 4.63 / 5.26 / 6.90 / 6.06, 4.91 | 4.5 | pass |
| both | paper, top #F8F2E6 | c-ref, c-foot | #9A8B78 | #F8F2E6 | 2.97 | 4.5 | **FAIL** |
| both | cited line highlight | row text | #2A2218 | #E2C8A6 | 9.73 | 4.5 | pass |
| both | claim sheet | heading / statement / unsupported / flagged | | #F6EFE2 | 5.13 / 15.86 / 5.65 / 7.20 | 4.5 | pass |
| both | claim sheet, selected row | statement | #1A150E | #E8DCCA | 13.41 | 4.5 | pass |
