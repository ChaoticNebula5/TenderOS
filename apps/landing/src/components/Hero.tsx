import chapterStyles from "./Chapter.module.css";
import proseStyles from "../styles/prose.module.css";
import styles from "./Hero.module.css";

/**
 * The hero chapter's text column - copy ported verbatim from
 * reference-landing.html:430-441. Its own file (rather than inlined in
 * App.tsx) because P2-P5 will each get one of these per chapter, and this
 * is the first of nine; keeping "what chapter 0 says" separate from "how
 * chapters are laid out" (Chapter.tsx) and "what the world behind it draws"
 * (world/formations.ts's `sphere`) is the same three-way split rule 1
 * draws between DOM, WebGL and SVG, just applied one level up.
 *
 * The reference's three amber connector threads from each <mark> to its
 * source sheet (reference-landing.html:1741-1749) are not built here - P1's
 * scope is the world/camera/blend core, not the SVG overlay layer; see the
 * phase report for why that's a deliberate deferral rather than an
 * oversight.
 */
export function Hero() {
  return (
    <div className={chapterStyles.col}>
      <h1 className={proseStyles.rev}>
        Nothing enters the bid <i>without a source.</i>
      </h1>
      <p className={`${proseStyles.lead} ${proseStyles.rev}`}>
        TenderOS reads the tender pack and your own evidence, drafts only what that evidence supports, and shows you
        what breaks when a fact changes.
      </p>
      <div className={`${proseStyles.row} ${proseStyles.rev}`}>
        <a className={`${proseStyles.btn} ${proseStyles.btnPrimary}`} href="#pack">
          See how it reads
        </a>
        <a className={`${proseStyles.btn} ${proseStyles.btnSecondary}`} href="#close">
          Start a tender
        </a>
      </div>
      <div className={`${styles.quote} ${proseStyles.rev}`}>
        <div className={styles.quoteLabel}>A sentence from a live bid, and the three pages behind it</div>
        <p>
          <mark>A. Mehta</mark> leads delivery, under <mark>₹100 crore</mark> of professional indemnity cover, on a
          team that <mark>resignalled the Blue Line</mark>.
        </p>
      </div>
    </div>
  );
}
