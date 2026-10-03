# TenderOS landing page: build spec

This is the source of truth for rebuilding the landing page as React components.
The reference implementation is `reference-landing.html`, a single self-contained file that
already does everything described here. When this document and the reference disagree,
**the reference wins for behaviour, this document wins for structure.**

Open the reference in a browser before writing any code.

---

## 1. What the page is

One WebGL scene stays fixed behind the whole page. Nine chapters scroll over it as normal
document flow. The same 40 documents rearrange for each chapter. Only the reading chapter pins.

The product claim the page makes: **the model cannot claim what the code has not checked.**
Every chapter is one instance of that claim.

| # | Chapter | Formation | What the reader does |
|---|---------|-----------|----------------------|
| 0 | Hero | `sphere` | Drags the sphere. Three threads run from a sentence to its source pages |
| 1 | Pack | `pack` | Scrolls: the stack opens, six mandatory pages step out, hover links a clause to its page |
| 2 | Reading (pinned) | `helix` | Scrolls one document at a time. A real HTML page opens, a line highlights, the fact lands on a requirement |
| 3 | Compliance | `retrieve` | Picks a requirement. Candidates rank, rerank, then the gate cites or drops each one |
| 4 | Drafting | `draft` | Clicks a sentence. Its source page opens, or a retrieval receipt explains why there is none |
| 5 | Red team | `redteam` | Watches five checks run, then the sentence narrows in place against the record that caps it |
| 6 | Impact | `recede` | Breaks a fact. A pulse travels the graph, hop by hop |
| 7 | Pricing | `crossdoc` | Types into any rate or total. Checks re-run. The cited rate card answers back |
| 8 | Close | `bid` | Sees the bound pack, a readable cover sheet, and the seal land |

---

## 2. Stack

- **Vite + React + TypeScript**, in the monorepo beside `apps/web`.
- **React Three Fiber + drei**, on a **current** three.js, pinned exactly in `package.json`.
  The reference is on r128 UMD, which is five years old; do not carry that forward.
- **GSAP + ScrollTrigger** via `useGSAP`.
- No CSS framework. Port the tokens from the reference `:root` block into CSS variables.

### Version pinning is the first task
Pin three, @react-three/fiber, @react-three/drei, gsap. Record the versions in the PR.
Morph targets, colour management and several material flags changed after r128, so the sheet
geometry in §4 must be re-verified on the pinned version, not assumed.

---

## 3. Architecture rules

These are not style preferences. Each one is a bug that has already cost time.

1. **Text that must be read is never inside a WebGL texture.** Text lives in the DOM, objects
   live in WebGL, connectors live in SVG. This now also bans drei's `Text` and `Text3D`.
   Document artwork may contain decorative lettering that nobody needs to read.
2. **Every animation advances by elapsed seconds, clamped at 0.05.** In R3F that is the
   `delta` from `useFrame`. Never a fixed amount per frame.
3. **Nothing renders off screen.** Control R3F's `frameloop`, and gate the impact canvas with
   an IntersectionObserver.
4. **Camera framing is computed from geometry and the live viewport.** Never hand-tuned distances.
5. `setDrawRange` on indexed geometry counts indices, not vertices.
6. `Object3D.position` is read-only. Use `.position.set()`.
7. `Object.assign(obj.userData, {...})`, never `obj.userData = {...}`.
8. Every canvas needs an explicit CSS size.
9. No scroll-scrubbed transforms on content columns.
10. Before handing over a build: syntax check, exactly one render loop per canvas, and no stale
    references to deleted code.

### Two rules earned the hard way in this page
11. **Class names collide.** `.bar` hit a panel title bar and silently emptied the score bars;
    `.g2` hit a two-column grid and squeezed a card to 120px. Namespace component CSS (CSS
    modules or a prefix). Both bugs looked like layout bugs and were neither.
12. **An overlay belongs to the chapter whose copy is on screen, not to a formation weight.**
    Formation weight completes long before a chapter's text arrives, which is why pages and the
    seal used to appear over the previous chapter. See §6.

---

## 4. The world

### Sheet geometry
One `PlaneGeometry(SW, SH, 18, 1)` with `SW = 2.6`, `SH = SW * 1.36`, plus a **morph target**
that wraps it onto the helix cylinder. Front mesh carries the document texture, back mesh is
`BackSide` in paper colour. Instancing is worth trying, but the morph target and per-sheet
opacity must survive it; if they don't, 40 meshes is fine.

### Formations
A formation is a pure function `(doc, out, t) => void` writing `position`, `quaternion`,
`bend`, `scale`, `opacity`, `emissive`, plus a static `{halfW, halfH, front}` used for framing.
Ported as-is from the reference: `sphere`, `pack`, `helix`, `retrieve`, `draft`, `redteam`,
`recede`, `crossdoc`, `bid`.

### Blending
Chapter progress gives a float `F` across chapters. Evaluate formation `floor(F)` and
`floor(F)+1`, then lerp per sheet with a stagger of `0.3 * (i / N)`. Quaternions slerp.

### The readable page
The centrepiece. A document flies to a fixed depth computed so it projects to a target pixel
height, and a real HTML page is positioned over its projected box. Used by four chapters
(reading, drafting, pricing, close). drei's `Html` with `transform` may replace the manual
projection; verify it tracks during the fly-in before committing to it.

---

## 5. Data

Extract from the reference into typed modules. None of it should live in components.

- `data/kb.ts` — 40 documents: type, pages, filename, the one extracted fact, requirement code,
  status (`ok` / `exp` / `old`), which proposal claim it grounds, and artwork args.
- `data/requirements.ts` — 23 requirement codes and names. R-08 has no document: that gap is
  computed, never hard-coded.
- `data/clauses.ts` — six mandatory clauses with page, section and cell provenance.
- `data/scenarios.ts` — the three compliance traces with retrieval and rerank scores.
- `data/redteam.ts` — three claims, the failing check, the narrowed sentence, the records that cap them.
- `data/pricing.ts` — five workbook rows, the cited rate card rate, the USD rate.
- `content/excerpts.tsx` — page bodies per document type.

Everything is Indian: INR with `toLocaleString('en-IN')`, crore and lakh, DMRC / CMRL / NCRTC,
RDSO competency, CERT-In audit, DPDP Act, EPF and ESI, MSME, GST. No pound or dollar amounts
except the one deliberate USD line in the pricing workbook.

---

## 6. Chapter ownership rule

```
const col  = chapter.querySelector('.col').getBoundingClientRect()
const owns = col.top <= H * 0.62 && col.bottom >= H * 0.38
```

A chapter owns the page, the receipt card, the seal and its connectors only while `owns` is
true, smoothed over a few frames. The page also rides vertically with its column so it enters
from below with the text instead of waiting in the middle of the viewport.

---

## 7. Phases, with a hard stop after each

Do not start a phase before the previous one is reviewed.

**P0 · Scaffold.** Vite, React, R3F, pinned versions, tokens, nine empty chapters in normal
flow, header with scrollspy, light and dark themes.
*Accept when:* the page scrolls, themes switch, no WebGL yet.

**P1 · World core.** Renderer, sheet geometry with morph target, `sphere` and `pack`, camera
framing, chapter blending, hero drag.
*Accept when:* hero and pack match the reference at 1440×900 and at 1280×720, and the camera
distance is derived, not typed.

**P2 · Reading.** Pin, helix, lens, readable page, requirement grid, the three connectors, snap.
*Accept when:* every document can be read at any window size and the snap settles on one document.

**P3 · Compliance and drafting.** `retrieve`, the trace panel, `draft`, source pages, the
retrieval receipt for claims with no page.
*Accept when:* all three requirement scenarios and all five sentences behave as in the reference.

**P4 · Red team and impact.** Five-check sweep, in-place narrowing, the ceiling formation, the
graph with travelling pulses.
*Accept when:* the sweep finishes under a second and the graph is keyboard reachable (see §8).

**P5 · Pricing and close.** Editable cells, the four checks, the reactive rate card page, the
bound stack, the cover sheet, the seal.
*Accept when:* typing any number in any cell produces correct findings and a correct gate state.

**P6 · Finish.** Mobile, accessibility, reduced motion, performance, cross-browser.
*Accept when:* §8 is closed out.

---

## 8. Known gaps, to close in the rebuild

- **Mobile is reduced, not designed.** The reference hides the page, the seal and every
  connector below 900px and halves the document count. Design the mobile version properly:
  the page belongs inline under the copy, not as an overlay.
- **The impact graph is keyboard-inaccessible.** Nodes are canvas hit-tests. Give each node a
  real focusable element, or render the graph as SVG.
- **Pricing cells are `contenteditable` with only digit stripping.** Use real inputs with
  validation and an undo path.
- **The light theme is a compromise.** Paper is darkened to read against a cream background.
  Consider a different document tint per theme rather than one colour scaled.
- **No tests.** At minimum: the pricing checks are pure functions and should be unit tested,
  and the gap computation (R-08) should be tested against the data, not asserted in prose.

---

## 9. Where to be careful

- The reading chapter's pin adds scroll length. Triggers created before it and positioned below
  it need `ScrollTrigger.sort()` plus `refreshPriority` on the pin, or the chapters after it
  start early. This already bit once.
- Formation weight is not chapter visibility. See §6.
- Sheets on the helix are translucent until read. Transparency plus morph targets is the most
  fragile combination in this page; check it first on the pinned three.js version.
