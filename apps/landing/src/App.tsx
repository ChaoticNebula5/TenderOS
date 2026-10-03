import { useMemo, useRef } from "react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { useGSAP } from "@gsap/react";
import { Header } from "./components/Header";
import { Chapter } from "./components/Chapter";
import { Hero } from "./components/Hero";
import { Pack } from "./components/Pack";
import { Reading } from "./components/Reading";
import { PlaceholderChapter, type PlaceholderChapterProps } from "./components/PlaceholderChapter";
import { World } from "./world/World";
import { CHAPTERS } from "./data/chapters";
import { createReadingDomRefs } from "./world/readingDom";
import proseStyles from "./styles/prose.module.css";
import readingStyles from "./components/Reading.module.css";
import styles from "./App.module.css";

gsap.registerPlugin(ScrollTrigger);

/** Eyebrow/heading copy for the chapters P1 doesn't build a real component
 *  for - see PlaceholderChapter.tsx. Ported from reference-landing.html's
 *  per-chapter <p class="eyebrow">/<h2> markup (454-543); close has no
 *  eyebrow in the reference either. */
const PLACEHOLDER_COPY: Record<string, PlaceholderChapterProps> = {
  comply: { eyebrowNo: "03", eyebrow: "Compliance", heading: "A status has to survive a check." },
  draft: { eyebrowNo: "04", eyebrow: "Drafting", heading: "Every sentence opens its page." },
  red: { eyebrowNo: "05", eyebrow: "Red team", heading: "It argues with the draft before the buyer does." },
  impact: { eyebrowNo: "06", eyebrow: "Impact", heading: "Change one fact. See what it breaks." },
  price: { eyebrowNo: "07", eyebrow: "Pricing", heading: "Arithmetic the model never does." },
  close: { heading: "Win the bid you can actually defend." },
};

/**
 * App root: mounts the world once, the header once, and the nine chapters
 * in document order, plus the scroll-reveal animation shared by every
 * chapter's text. Ported from reference-landing.html:414-545 (body markup)
 * and 559-561 (the `.rev` reveal wiring, `MODE==='world'` branch only).
 */
export function App() {
  const scopeRef = useRef<HTMLDivElement>(null);
  const headerRef = useRef<HTMLElement>(null);
  const hotClauseIndex = useRef(-1);
  const isLightTheme = useRef(false);
  const readingRefs = useMemo(createReadingDomRefs, []);

  useGSAP(
    () => {
      const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      gsap.utils.toArray<HTMLElement>(`.${proseStyles.rev}`).forEach((el) => {
        gsap.to(el, {
          opacity: 1,
          y: 0,
          duration: reduceMotion ? 0.001 : 0.85,
          ease: "power2.out",
          scrollTrigger: { trigger: el, start: "top 88%" },
        });
      });
    },
    { scope: scopeRef },
  );

  return (
    <div ref={scopeRef}>
      <World
        headerRef={headerRef}
        scopeRef={scopeRef}
        hotClauseIndex={hotClauseIndex}
        isLightTheme={isLightTheme}
        readingRefs={readingRefs}
      />
      <div className={styles.vignette} aria-hidden="true" />
      <Header headerRef={headerRef} scopeRef={scopeRef} isLightTheme={isLightTheme} />
      <main className={styles.main}>
        {CHAPTERS.map((chapter) => (
          <Chapter
            key={chapter.id}
            id={chapter.id}
            side={chapter.side}
            wide={chapter.wide}
            className={chapter.id === "read" ? readingStyles.read : undefined}
          >
            {chapter.id === "hero" ? (
              <Hero />
            ) : chapter.id === "pack" ? (
              <Pack hotClauseIndex={hotClauseIndex} />
            ) : chapter.id === "read" ? (
              <Reading domRefs={readingRefs} />
            ) : (
              <PlaceholderChapter {...PLACEHOLDER_COPY[chapter.id]} />
            )}
          </Chapter>
        ))}
      </main>
      <footer className={styles.footer}>
        <span>TenderOS</span>
        <span>Evidence-grounded bid operations</span>
      </footer>
    </div>
  );
}
