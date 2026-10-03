import type { RefObject } from "react";
import type { KbDoc } from "../data/kb";
import { REQUIREMENT_CODES, REQUIREMENT_GAPS, STATUS_LABEL } from "../data/requirements";

/**
 * DOM refs the reading chapter's lens card, hud counters and requirement
 * grid are written through - owned by `Reading.tsx` (which creates the real
 * elements), written by `Scene.tsx`'s per-frame loop (which has the only
 * render loop in the app, so this is how WebGL-driven state reaches DOM
 * text/classes without a second loop or a React re-render per frame -
 * matches how `headerRef` already lets Scene read *out* of the DOM; this is
 * the same channel used in the other direction).
 */
export interface ReadingDomRefs {
  lensCard: RefObject<HTMLDivElement | null>;
  hRead: RefObject<HTMLElement | null>;
  hCov: RefObject<HTMLElement | null>;
  requirementCells: RefObject<Record<string, HTMLSpanElement | null>>;
}

export function createReadingDomRefs(): ReadingDomRefs {
  return {
    lensCard: { current: null },
    hRead: { current: null },
    hCov: { current: null },
    requirementCells: { current: {} },
  };
}

/**
 * The lens card's inner content is built as an HTML string (`innerHTML`),
 * not JSX - the only way to replicate the reference's per-word stagger
 * (`--i` custom property per `<span>`, reference-landing.html:1183) without
 * rendering 40 documents' worth of per-word React elements that only ever
 * apply to whichever one is currently shown. That has one consequence
 * worth flagging: CSS Modules only rewrites class names it can see
 * statically in JSX/`styles.x` property access - a class name baked into a
 * template-literal string is invisible to it. Every class used inside
 * `fillLensCard`'s template below is therefore taken from this object
 * (Reading.module.css's real scoped names, read as plain strings) rather
 * than hard-coded, so it still resolves to the correct scoped selector.
 *
 * Typed as a plain string-keyed record, not a named-key interface: a CSS
 * Module's generated type (Vite's `CSSModuleClasses`) is itself a string
 * index signature, and TypeScript won't treat that as satisfying a fixed
 * set of named properties without an explicit (and needlessly risky) cast -
 * `Record<string, string>` is what the import actually is, and property
 * access like `cx.lk` still type-checks fine against it.
 */
export type LensCardClassNames = Record<string, string>;

/**
 * Fills the lens card with one document's fact, source, requirement and
 * status, and restarts the word-by-word reveal animation. Ported from
 * reference-landing.html:1178-1188 (`fillLens`).
 */
export function fillLensCard(refs: ReadingDomRefs, doc: KbDoc, index: number, total: number, cx: LensCardClassNames): void {
  const el = refs.lensCard.current;
  if (!el) return;

  const words = doc.fact
    .split(" ")
    .map((word, i) => `<span style="--i:${i}">${word}</span>`)
    .join(" ");
  const groundsNote =
    doc.claimIndex >= 0
      ? `<div style="font-size:11.5px;color:var(--amber);margin-top:9px">Grounds a sentence in Section 4</div>`
      : "";
  const statusClass = cx[doc.status];

  // doc's fields are fixed literal strings from data/kb.ts, never user
  // input or fetched data - not an XSS surface, same reasoning as
  // Pack.tsx's dangerouslySetInnerHTML.
  el.innerHTML = `
    <div class="${cx.lk}"><span class="${cx.ln}">${String(index + 1).padStart(2, "0")} / ${total}</span>
      <span>${doc.type} · ${doc.page} page${doc.page > 1 ? "s" : ""}</span></div>
    <div class="${cx.lf}">${doc.filename}</div>
    <p class="${cx.lx}">${words}</p>
    <div class="${cx.lr}"><span class="${cx.rq}">${doc.requirementCode} ${doc.requirementName}</span><span class="${cx.st} ${statusClass}">${STATUS_LABEL[doc.status]}</span></div>
    ${groundsNote}`;

  // Reference's reflow-forcing restart trick: removing then re-adding the
  // class alone wouldn't restart the CSS animation, since the browser
  // coalesces the two class changes into one style recalculation unless
  // something reads a layout property (`offsetWidth`) in between, forcing
  // it to actually flush the "removed" state before the "added" state is
  // applied.
  el.classList.remove(cx.sw);
  void el.offsetWidth;
  el.classList.add(cx.sw);
}

/**
 * Updates the "N of 40 read" / "N of 23 requirements covered" counters and
 * toggles each requirement cell's covered/gap/hit state. Ported from
 * reference-landing.html:1189-1198 (`readingHud`).
 *
 * Unlike `fillLensCard`, the requirement cells this toggles are real JSX
 * elements (`Reading.tsx` renders one `<span className={styles.rc}>` per
 * requirement code) - so `classNames.cov`/`gap`/`hit` are just passed
 * straight to `classList.toggle`, already-resolved scoped strings, no
 * template-string caveat here.
 *
 * @param coveredCodes requirement codes at least one read document answers
 * @param allRead whether every document has been read
 * @param lensRequirementCode the current lens document's requirement code,
 *   or null if nothing is in the lens
 * @param lensSettled whether the lens has finished flying in (reference's
 *   `lensT` window, `>1.02 && <1.75` - "hit" only pulses briefly once
 *   arrived, not for the whole time a document is in the lens)
 */
export function updateReadingHud(
  refs: ReadingDomRefs,
  readCount: number,
  coveredCodes: ReadonlySet<string>,
  allRead: boolean,
  lensRequirementCode: string | null,
  lensSettled: boolean,
  classNames: { cov: string; gap: string; hit: string },
): void {
  if (refs.hRead.current) refs.hRead.current.textContent = String(readCount).padStart(2, "0");
  if (refs.hCov.current) refs.hCov.current.textContent = String(coveredCodes.size);

  const cells = refs.requirementCells.current;
  REQUIREMENT_CODES.forEach((code) => {
    const cell = cells[code];
    if (!cell) return;
    cell.classList.toggle(classNames.cov, coveredCodes.has(code));
    cell.classList.toggle(classNames.gap, allRead && REQUIREMENT_GAPS.includes(code));
    cell.classList.toggle(classNames.hit, lensSettled && lensRequirementCode === code);
  });
}
