# TenderOS landing page

The public landing page for TenderOS. Six chapters of text scroll over one fixed "world" of real
HTML documents (tender pages, evidence files, bid drafts) that rearrange per chapter: a turning
sphere, a helix, an eligibility board, an evidence wall with a traced thread, a change-impact graph
and a submission stack you can throw pages from.

It is a port of `reference/tenderos-v3.html`, a working single-file version of the page. That file
is the specification for layout, copy, motion and interaction.

## Run, build, check

```
npm install
npm run dev          # Vite dev server
npm run build        # type-check, then production build to dist/
npm run preview      # serve dist/ (use this for any performance measurement)
npm run lint
npm run typecheck
```

There is no automated test suite in this app. Each phase was verified with a scripted browser
harness (Playwright over the Chrome DevTools Protocol) against the production build; the method and
results are in `docs/landing-v3-v1-report.md` to `docs/landing-v3-v6-report.md`.

## Comparing with the reference

Serve the reference next to the app and open both at the same size and scroll position:

```
cd reference && python -m http.server 4180   # http://localhost:4180/tenderos-v3.html
npm run preview                               # http://localhost:4173/
```

Both pages take their motion from `requestAnimationFrame` timestamps, so for a pixel comparison
drive both with the same actions at the same frame numbers (or emulate reduced motion, which removes
the sphere's spin). The V6 report describes the harness that does this.

## Architecture rules

From `docs/landing-v3-rebuild.md`. Breaking any of them brings the lag back.

1. All text is DOM. Nothing that must be read is drawn into a canvas or texture.
2. One `requestAnimationFrame` loop drives the whole world. It advances by elapsed seconds, clamped
   at 0.05. A reduced-motion path snaps instead of animating.
3. React renders the document DOM once. The loop mutates refs and writes styles directly. No
   per-frame `setState`, no per-frame re-render; zero commits while the page idles.
4. Every document is three nested layers: wrapper `.cw` (translate, rotate, a fine scale of a few
   percent), inner `.sc` (coarse scale quantized with `QSTEP = 24`), page `.card` (never scaled).
5. Final on-screen scale is capped at 1; a document being read lands exactly on scale 1.
6. Nothing inside a document animates a paint property. State rings switch instantly; glows are
   separate layers that fade by opacity.
7. No per-frame writes to root CSS custom properties. The atmosphere layer moves by transform.
8. Write only what changed: transform, opacity, z-index, label and SVG path strings are cached.
9. Read layout (`getBoundingClientRect`) once at the start of a frame, before any writes.
10. No `mix-blend-mode` or `backdrop-filter` over the world. Grain lives in the page background.
11. Framing is computed from geometry and the live aspect, never hand-tuned distances.
12. All money in INR, Indian context throughout, no em dashes in any copy.

## Where things are

The module map is in `docs/landing-v3-v0-report.md`, section 4. In short: `src/data` holds the
copy and data, `src/documents` the document templates, `src/world` the engine (store, arrangements,
layout, labels, wires, thread, manipulation, frame loop), `src/chapters` and `src/components` the
React markup, `src/styles` the reference CSS split by section. Outbound link destinations are in
`src/data/links.ts`.

## Known limitations

- The four outbound buttons point at in-page anchors from the reference (`src/data/links.ts`);
  real destinations are not chosen yet.
- The 0.45 s theme fade (kept from the reference) restyles the whole page on every frame because
  `body` transitions the inherited text colour. At device pixel ratio 2 the longest task per fade
  measured 13.4 to 19.4 ms (median 16.5), against 11.7 to 18.2 ms (median 15.3) on the reference, so
  both cross 16 ms at times. If it stutters on a real device, the documented fix is to fade only
  `body`'s background colour and let text colour switch at once. Details in the V6 report.
- `c-ref` and `c-foot` inside documents (#9A8B78) are under WCAG AA on paper, kept deliberately as
  incidental print on illustrative documents. Every other text colour passes.
- On a phone, unpinned chapters let the text column scroll over the bottom edge of the world.
- Left and right safe-area insets are not handled (landscape notches); the top inset is.
- Safe areas and touch were verified by emulation only, not on a real device.
