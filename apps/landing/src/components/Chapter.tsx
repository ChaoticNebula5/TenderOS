import type { ReactNode } from "react";
import type { ChapterSide } from "../world/cameraFraming";
import styles from "./Chapter.module.css";

export interface ChapterProps {
  id: string;
  side: ChapterSide;
  wide?: boolean;
  /** Extra class appended after the base chapter class, for the rare
   *  chapter that needs to override its own section-level layout -
   *  currently only "read": it pins instead of scrolling normally, so it
   *  needs `min-height:100vh;padding:0` in place of the generic chapter's
   *  `112vh`/`110px…` (reference-landing.html:108, `#read{...}`). Kept as a
   *  generic escape hatch rather than a `isReading` boolean so the next
   *  chapter that needs its own override doesn't need this component
   *  touched again.
   */
  className?: string;
  children: ReactNode;
}

/**
 * Layout shell shared by all nine chapters: the fade-to-background gradient
 * behind the text column, and which side of the screen that column sits on.
 * Ported from reference-landing.html:63-76.
 *
 * Its own file because every one of the nine chapters needs exactly this
 * wrapper and nothing else varies at this level - what's *inside* a chapter
 * (Hero, Pack, or a P2-P5 chapter later) is a different component's job;
 * this one only answers "where does the text column sit, and how wide is
 * the world's free region as a result" - the same `side`/`wide` values
 * `world/cameraFraming.ts`'s `measureRegion` reads for the exact same
 * chapter.
 */
export function Chapter({ id, side, wide, className, children }: ChapterProps) {
  const classes = [styles.chapter, wide && styles.wide, className].filter(Boolean).join(" ");
  return (
    <section id={id} data-side={side} className={classes}>
      {children}
    </section>
  );
}
