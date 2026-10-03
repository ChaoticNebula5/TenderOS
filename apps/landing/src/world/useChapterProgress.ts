import { useRef, type RefObject } from "react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { useGSAP } from "@gsap/react";
import { CHAPTERS } from "../data/chapters";
import { KB } from "../data/kb";

gsap.registerPlugin(ScrollTrigger);

/** Share of the reading chapter's pinned scroll spent morphing sheets onto
 *  the helix before the "which document is at the front" travel begins -
 *  reference's `PM` (reference-landing.html:811). */
export const READING_MORPH_SHARE = 0.2;

export interface ChapterProgressState {
  /** Per-chapter scroll progress in [0,1], index-aligned with CHAPTERS.
   *  progress[0] (hero) is always 0 - it's the baseline the world starts
   *  from, not something scrolled "into". */
  progress: number[];
  /** How far the pack has opened, in [0, 1.35] - can overshoot 1 so the
   *  stack finishes opening slightly before the clause list finishes
   *  scrolling past, matching the reference's feel exactly. */
  packRaw: number;
  /** Raw [0,1] progress through the reading chapter's own pinned scroll -
   *  reference's `readP`. Distinct from `progress[2]` (the "read" chapter's
   *  entry into the blend, same as every other chapter) - this is what
   *  happens *while* pinned inside it. */
  readP: number;
}

/**
 * Wires one GSAP ScrollTrigger per chapter (skipping the hero), the pack
 * chapter's second trigger, and - new in P2 - the reading chapter's pin.
 * Ported from reference-landing.html:1016-1022 (per-chapter/pack) and
 * 1071-1081 (the pin, desktop and mobile branches).
 *
 * The pin is the one trigger in this file spec §9 calls out by name: it
 * inserts extra scroll length into the page (`end:'+=300%'`), which shifts
 * every trigger positioned below it. `refreshPriority: 1` tells ScrollTrigger
 * to measure the pin's own start/end *before* lower-priority triggers during
 * a refresh, so those triggers account for the inserted length instead of
 * being measured against a page that hasn't grown yet - which is exactly
 * the "chapters after it start early" bug the spec's own comment there
 * describes. `ScrollTrigger.sort()` (called once, after every trigger in
 * this hook exists) is the reference's own fix for the same problem,
 * applied before both refresh passes below - it doesn't matter that
 * Header's and App's *own* triggers are created later, in separate
 * components: `refresh()` re-measures the *global* trigger registry, not
 * just the ones this hook made.
 */
export function useChapterProgress(scopeRef: RefObject<HTMLElement | null>, isMobile: boolean): RefObject<ChapterProgressState> {
  const state = useRef<ChapterProgressState>({ progress: CHAPTERS.map(() => 0), packRaw: 0, readP: 0 });

  useGSAP(
    () => {
      CHAPTERS.forEach((chapter, k) => {
        if (k === 0) return;
        const el = document.getElementById(chapter.id);
        if (!el) return;
        ScrollTrigger.create({
          trigger: el,
          start: "top 88%",
          end: "top 34%",
          scrub: true,
          onUpdate: (self) => {
            state.current.progress[k] = self.progress;
          },
        });
      });

      const packEl = document.getElementById("pack");
      if (packEl) {
        ScrollTrigger.create({
          trigger: packEl,
          start: "top 55%",
          end: "bottom 70%",
          scrub: true,
          onUpdate: (self) => {
            state.current.packRaw = self.progress * 1.35;
          },
        });
      }

      const readEl = document.getElementById("read");
      if (readEl) {
        if (!isMobile) {
          ScrollTrigger.create({
            trigger: readEl,
            start: "top top",
            end: "+=300%",
            pin: true,
            anticipatePin: 1,
            refreshPriority: 1,
            onUpdate: (self) => {
              state.current.readP = self.progress;
            },
            snap: {
              snapTo: (v) =>
                v < READING_MORPH_SHARE * 0.98
                  ? v
                  : READING_MORPH_SHARE +
                    (Math.round(((v - READING_MORPH_SHARE) / (1 - READING_MORPH_SHARE)) * (KB.length - 1)) / (KB.length - 1)) *
                      (1 - READING_MORPH_SHARE),
              duration: { min: 0.15, max: 0.45 },
              delay: 0.12,
              ease: "power1.inOut",
            },
          });
        } else {
          // Mobile gets no pin (reference: `if(!MOB){...pin...}else{...scrub...}`)
          // - scrolling the reading chapter just scrubs readP directly,
          // same as any other chapter's progress trigger.
          ScrollTrigger.create({
            trigger: readEl,
            start: "top 70%",
            end: "bottom 30%",
            scrub: true,
            onUpdate: (self) => {
              state.current.readP = self.progress;
            },
          });
        }
      }

      ScrollTrigger.sort();
      ScrollTrigger.refresh();

      // Newsreader/Archivo load async (index.html's Google Fonts <link>).
      // The refresh above measures trigger start/end against whatever font
      // is showing at that instant - almost always a fallback, since fonts
      // haven't finished loading yet on a fresh visit. When the real font
      // swaps in, every heading and paragraph reflows to its actual line
      // height, silently invalidating every trigger boundary just measured.
      // That's a scroll-position-dependent, network-timing-dependent
      // misalignment - "sometimes" is exactly what a race reads like from
      // outside. Re-measuring once fonts are actually ready closes it, and
      // doubles as a second correctly-ordered pass now that Header's and
      // App's own triggers (created in later-running effects) also exist.
      document.fonts.ready.then(() => {
        ScrollTrigger.sort();
        ScrollTrigger.refresh();
      });
    },
    { scope: scopeRef, dependencies: [isMobile] },
  );

  return state;
}

/**
 * Reduces the per-chapter progress array to the continuous cross-chapter
 * float `F` from spec §4 ("Blending"): floor(F) and floor(F)+1 are the two
 * formations in play, F - floor(F) is the blend amount between them.
 * Reference: `for(let k=1;k<chapters.length;k++)F+=prog[k]` (line 1563).
 */
export function chapterFloat(progress: number[]): number {
  let total = 0;
  for (let k = 1; k < progress.length; k++) total += progress[k];
  return total;
}
