import { useState, type RefObject } from "react";
import { CLAUSES } from "../data/clauses";
import chapterStyles from "./Chapter.module.css";
import proseStyles from "../styles/prose.module.css";
import styles from "./Pack.module.css";

export interface PackProps {
  /** Written on hover, read every frame by the pack formation (see
   *  `world/formations.ts`'s `hotClauseIndex` input) to light up the
   *  hovered clause's sheet in the stack - App.tsx owns the ref so both
   *  this component and `<World>` can reach it without a module global. */
  hotClauseIndex: RefObject<number>;
}

/**
 * The pack chapter's text column: eyebrow, heading, lead copy, and the six
 * mandatory clauses. Ported from reference-landing.html:443-452 (markup)
 * and 1162-1168 (hover wiring).
 */
export function Pack({ hotClauseIndex }: PackProps) {
  // Local state purely to re-render the hovered <li>'s own class for the
  // background highlight - a per-clause visual, unrelated to the ref above
  // which exists so the *world* can read it every frame without a re-render.
  const [hoveredIndex, setHoveredIndex] = useState(-1);

  return (
    <div className={chapterStyles.col}>
      <p className={`${proseStyles.eyebrow} ${proseStyles.rev}`}>
        <span className={proseStyles.eyebrowNo}>01</span>The pack
      </p>
      <h2 className={proseStyles.rev}>
        The clause that sinks you <i>is on page 214.</i>
      </h2>
      <p className={`${proseStyles.lead} ${proseStyles.rev}`}>
        PDF, DOCX and XLSX are parsed down to the page, the sheet and the cell, with OCR for scanned pages. Mandatory
        language is caught by rule before a model sees it, so a <i>shall</i> is never read as a <i>should</i>.
      </p>
      <ul className={`${styles.clauses} ${proseStyles.rev}`}>
        {CLAUSES.map((clause, i) => (
          <li
            key={clause.sourceFilename + clause.location}
            className={i === hoveredIndex ? styles.hot : undefined}
            onMouseEnter={() => {
              hotClauseIndex.current = i;
              setHoveredIndex(i);
            }}
            onMouseLeave={() => {
              if (hotClauseIndex.current === i) hotClauseIndex.current = -1;
              setHoveredIndex((current) => (current === i ? -1 : current));
            }}
          >
            {/* clause.html is a fixed literal string authored in data/clauses.ts,
                never user input or anything fetched at runtime - the <b> tags
                are how the mandatory-language word is emphasised, so this isn't
                an XSS surface the way rendering untrusted content would be. */}
            <div className={styles.clauseText} dangerouslySetInnerHTML={{ __html: clause.html }} />
            <div className={styles.clauseProvenance}>
              <span>{clause.sourceFilename}</span> &middot; {clause.location}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
