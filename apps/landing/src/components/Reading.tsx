import { REQUIREMENTS, REQUIREMENT_CODES } from "../data/requirements";
import type { ReadingDomRefs } from "../world/readingDom";
import chapterStyles from "./Chapter.module.css";
import proseStyles from "../styles/prose.module.css";
import styles from "./Reading.module.css";

export interface ReadingProps {
  domRefs: ReadingDomRefs;
}

/**
 * The reading chapter's text column: eyebrow/heading/lead (no scroll-reveal
 * fade here - see below), the lens card, and the read-count/requirement-
 * coverage hud. Ported from reference-landing.html:454-466.
 *
 * Unlike every other chapter, this one's heading/lead aren't `.rev`-tagged
 * in the reference - the whole chapter pins instead of scrolling normally,
 * so a one-time "fade in as it enters the viewport" doesn't apply the same
 * way once you're already pinned inside it.
 *
 * The lens card's content and the hud/requirement-grid's covered/gap/hit
 * state are written imperatively by `world/Scene.tsx` through `domRefs`
 * (`world/readingDom.ts`), not by this component re-rendering - this
 * component only creates the DOM nodes and hands their refs upward.
 */
export function Reading({ domRefs }: ReadingProps) {
  const cellRefCallback = (code: string) => (el: HTMLSpanElement | null) => {
    domRefs.requirementCells.current[code] = el;
  };

  return (
    <div className={styles.stagebox}>
      <div className={`${chapterStyles.col} ${styles.readCol}`}>
        <p className={proseStyles.eyebrow}>
          <span className={proseStyles.eyebrowNo}>02</span>Reading
        </p>
        <h2>
          Every page read. <i>Every fact kept.</i>
        </h2>
        <p className={proseStyles.lead} style={{ fontSize: "14.5px" }}>
          Scroll to read the knowledge base one document at a time. The highlighted line becomes a fact, and the fact
          lands on the requirement it answers.
        </p>

        <div ref={domRefs.lensCard} className={styles.lenscard}>
          <div className={styles.lk}>
            <span className={styles.ln}>00 / 40</span>
            <span>Waiting for the first page</span>
          </div>
          <p className={styles.lx} style={{ color: "var(--ink-3)" }}>
            Nothing read yet.
          </p>
        </div>

        <div className={styles.hud}>
          <div className={styles.hrow}>
            <span>
              <b ref={domRefs.hRead}>00</b> of 40 read
            </span>
            <span>
              <b ref={domRefs.hCov}>0</b> of {REQUIREMENT_CODES.length} requirements covered
            </span>
          </div>
          <div className={styles.hg}>
            {REQUIREMENT_CODES.map((code) => (
              <span key={code} ref={cellRefCallback(code)} className={styles.rc} title={`${code} ${REQUIREMENTS[code]}`}>
                {code.slice(2)}
              </span>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
