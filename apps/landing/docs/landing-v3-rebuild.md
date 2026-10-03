# Landing page v3 rebuild

This plan replaces the earlier landing rebuild plan (phases P0 to P6). That earlier build is deleted in phase V0 and rebuilt from the reference. Phases here are named V0 to V6. Each phase ends at a hard stop: report, screenshots, perf numbers, then wait.

You write all of the code yourself, in every phase. Do not use Learning mode. Do not leave `TODO(human)` markers, stubs, placeholder bodies or numbered-pseudocode scaffolds. Every phase ships complete, working code.

## Source of truth

`apps/landing/reference/tenderos-v3.html` is a working, single-file implementation of the finished page. It is the spec for layout, copy, motion and interaction. Port it; do not redesign it. Where an installed skill (Impeccable, frontend-design, GSAP, Three.js, canvas) suggests something different, the reference wins. Copy is final as written in the reference.

Sections of the reference, by the comment banners in its script:

| Banner | What it holds |
|---|---|
| DATA | Tender pages, evidence, drafts, eligibility (`REQ`), claims, impact graph (`NODES`, `EDGES`), stack order, `SIDE` per chapter |
| DOCUMENTS | HTML templates for every document type |
| ARRANGEMENTS | `sphere()` relaxation, `rot()`, `park()`, and the six arrangement functions in `L` |
| LAYOUT | `measure()` (world rects, scroll holds, per-arrangement fit), `pOf()` (scroll to chapter progress) |
| LABELS & WIRES | Label placement, SVG wires, `setD`/`setO` change-only writers |
| INTERACTION | Index, claims, `select()`, phone reader, `seed()` impact wave, theme |
| direct manipulation | `pick()`, `grab()`, `flick()`, `launch()`, `stepOv()` physics, pointer handlers |
| FRAME | `QSTEP`, `desired()` states, the single `frame()` loop |

## What changes from the current apps/landing

- The earlier landing build is removed completely, including everything from its phases P0 to P6: components, the R3F world core, GSAP and ScrollTrigger code, old pages and sections, tests, stories, assets, and the old landing plan and report docs. It is not ported, adapted or kept for reference. Git history and a tag are the only record.
- No WebGL. The world is real HTML documents positioned by our own projection math. three.js, `@react-three/*`, GSAP and their type packages leave the landing app's dependencies.
- Scroll is mapped to chapter progress by `pOf()`; all motion runs in one loop.
- Tokens come from the reference's CSS (the `:root` and light-theme blocks). Do not carry over the earlier token files.
- What stays: the app shell the repo needs to run (package.json, build and TypeScript config, lint, test and CI wiring), unless it only exists to serve the old build.

## Architecture rules

These came out of profiling the reference. Breaking any of them brings the lag back.

1. All text is DOM. Nothing that must be read is drawn into a canvas or texture.
2. One `requestAnimationFrame` loop drives the whole world. It advances by elapsed seconds, clamped at 0.05. A reduced-motion path snaps instead of animating.
3. React renders the document DOM once. The loop mutates refs and writes styles directly. No per-frame `setState`, no per-frame re-render. The React Profiler must show zero commits while the page idles.
4. Every document is three nested layers, as in the reference:
   - **Wrapper `.cw`**: carries `translate3d`, `rotate` and a fine scale of at most a few percent, with `will-change: transform`.
   - **Inner `.sc`**: carries a coarse scale, quantized as `exp(round(ln(s) * QSTEP) / QSTEP)` with `QSTEP = 24`.
   - **Page `.card`**: the content itself, never scaled directly.

   A continuously changing scale on the layer that holds text re-renders that text every frame. That was the original lag.
5. Final on-screen scale is capped at 1. The natural card size (320 x 436) is the reading size, so a document being read lands exactly on scale 1 and stays sharp.
6. Nothing inside a document animates a paint property (box-shadow, background, border colour). State rings switch instantly. Glows are separate layers (`.glow.ga`, `.glow.gr`) that fade by opacity and are `visibility: hidden` when inactive, so their blur never enlarges the document's layer.
7. No per-frame writes to root CSS custom properties. The atmosphere layer moves by transform.
8. Write only what changed. Cache transform, opacity, z-index, label and SVG path strings, as the reference does.
9. Read layout (`getBoundingClientRect`) once at the start of a frame, before any writes.
10. No `mix-blend-mode` or `backdrop-filter` over the world. Grain lives in the page background.
11. Framing is computed from geometry and the live aspect (`measure()` fits each arrangement), never hand-tuned distances.
12. All money in INR, Indian context throughout, no em dashes in any copy.

## Phases

Every phase ends at a hard stop. At each stop, report what was built, screenshots at 1440x900 and 390x844 next to the reference at the same scroll positions, and the perf check below. Then wait.

**V0. Remove the old build and set up the new foundation.**
1. Inventory first. List every file and dependency that belongs only to the earlier landing build, and every one that is shared with other apps or packages. Save the list to `docs/landing-v3-v0-report.md` before deleting anything.
2. Create a recovery point: `git tag landing-v2-final` on the current commit. Work on a new branch, `landing-v3`.
3. Delete the earlier landing build as described above, including its docs. The boundary is strict: only `apps/landing` and files that exist solely for it. Do not touch the backend, other apps, or shared packages. If something shared looks landing-specific, list it in the report and leave it alone.
4. Remove the landing app's dependencies that only served the old build (three.js, R3F, drei, GSAP and their types) and any config for them.
5. Scaffold the new foundation: a clean app that builds, lints and type-checks, with the reference file copied in as `apps/landing/reference/tenderos-v3.html`.
6. Add to the report: a module map for the port, and a numbered checklist of every behaviour in the reference, grouped by V1 to V6.
7. Confirm the app builds and type-checks, then stop.

**V1. World core and overview.**
- Data, document components, the three-layer document, projection and per-arrangement fit, scroll mapping, the coarse/fine scale split, the frame loop.
- `pile` arrangement and the hero chapter, with drag-to-rotate and inertia. No pulling or throwing yet.

**V2. Tender analysis and eligibility check.**
- Helix with all six pages lit and labelled.
- Two-way index: hover lights page and row, click spins the page to the front via `bringToFront()`.
- Eligibility rows, wires and labels.
- Side alternation, with the world switching sides per `SIDE` and two scrims.

**V3. Evidence tracing.**
- Evidence wall and focus at the reading plane.
- Thread from the live claim position to the exact cited line, using the `CT` offsets measured in `measure()`.
- Dead-end thread for unsupported and flagged claims.
- Phone reader slot.

**V4. Change impact.**
- Graph layout.
- Impact propagation (breadth first over the real edges).
- Wave: edges draw along their length with a pulse at the head, documents swell as the wave reaches them, and the summary lines appear in step with the wave.

**V5. Submission and direct manipulation.**
- Stack arrangement.
- Throw physics.
- On the overview: click a page to fling it away, hold to lift it and throw it, and it files itself back.
- On the stack: drag or click the top page to throw it, and it slides back under the stack.
- Thrown pages fly in a layer above the content.
- On touch: tap to throw, sideways swipe to rotate.

**V6. Hardening.**
- Light theme, 390px, reduced motion, keyboard focus.
- Perf check on every chapter.
- Remove dead code and unused dependencies.

## Perf check at every stop

- Chrome Performance panel, device pixel ratio 2, three seconds idle on the hero: Raster under about 50 ms in total, no long tasks.
- Scroll through each finished chapter: no frames over 16 ms on the main thread.
- React Profiler while idle: zero commits.
- Exactly one `requestAnimationFrame` loop in the codebase.
