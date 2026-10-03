import chapterStyles from "./Chapter.module.css";
import proseStyles from "../styles/prose.module.css";

export interface PlaceholderChapterProps {
  eyebrowNo?: string;
  eyebrow?: string;
  heading: string;
}

/**
 * Stand-in body for the six chapters P1 doesn't build (read, comply, draft,
 * red, impact, price, close - minus pack and hero, which have their own
 * real components). Only the eyebrow/heading are ported from the
 * reference, so the page has something real to scroll through and the
 * header's scrollspy + world's chapter-progress tracking have real DOM
 * targets to measure - the lead copy, lists and interactive panels each of
 * these chapters needs are P2-P5 scope (spec §7).
 */
export function PlaceholderChapter({ eyebrowNo, eyebrow, heading }: PlaceholderChapterProps) {
  return (
    <div className={chapterStyles.col}>
      {eyebrow && (
        <p className={`${proseStyles.eyebrow} ${proseStyles.rev}`}>
          {eyebrowNo && <span className={proseStyles.eyebrowNo}>{eyebrowNo}</span>}
          {eyebrow}
        </p>
      )}
      <h2 className={proseStyles.rev}>{heading}</h2>
    </div>
  );
}
