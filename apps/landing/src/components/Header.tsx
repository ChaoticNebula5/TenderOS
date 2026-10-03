import { useState, type RefObject } from "react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { useGSAP } from "@gsap/react";
import styles from "./Header.module.css";

gsap.registerPlugin(ScrollTrigger);

/** Chapters with a nav link, in nav order - matches reference-landing.html
 *  424-425 exactly: neither hero nor close is linked. */
const NAV_LINKS: { id: string; label: string }[] = [
  { id: "pack", label: "Pack" },
  { id: "read", label: "Reading" },
  { id: "comply", label: "Compliance" },
  { id: "draft", label: "Drafting" },
  { id: "red", label: "Red team" },
  { id: "impact", label: "Impact" },
  { id: "price", label: "Pricing" },
];

export interface HeaderProps {
  headerRef: RefObject<HTMLElement | null>;
  scopeRef: RefObject<HTMLElement | null>;
  /** Written on toggle, read every frame by the world (fog colour, exposure,
   *  paper tint - reference-landing.html:1847-1851) - same ref-not-state
   *  reasoning as `hotClauseIndex`. */
  isLightTheme: RefObject<boolean>;
}

/**
 * Fixed nav bar: scrollspy-highlighted links plus the light/dark toggle.
 * Ported from reference-landing.html:422-427 (markup), 563-567 (theme
 * toggle) and 1082-1085 (scrollspy).
 *
 * Its own file rather than folded into App.tsx because it owns two things
 * nothing else needs: the theme-flip side effect on `<html data-theme>`,
 * and its own `useGSAP` scan of every chapter for the currently-active one
 * - logic that has nothing to do with laying out chapters themselves.
 */
export function Header({ headerRef, scopeRef, isLightTheme }: HeaderProps) {
  const [activeId, setActiveId] = useState<string | null>(null);
  const [isLight, setIsLight] = useState(false);

  useGSAP(
    () => {
      NAV_LINKS.forEach(({ id }) => {
        const el = document.getElementById(id);
        if (!el) return;
        ScrollTrigger.create({
          trigger: el,
          start: "top 55%",
          end: "bottom 45%",
          onToggle: (self) => {
            // Reference: `link.classList.toggle('on', s.isActive)` - sets
            // AND clears. Only guarding the true case here would leave a
            // chapter's link stuck highlighted after scrolling back past
            // it, since nothing else would ever turn it off.
            setActiveId((current) => {
              if (self.isActive) return id;
              return current === id ? null : current;
            });
          },
        });
      });
    },
    { scope: scopeRef },
  );

  const toggleTheme = () => {
    const next = !isLight;
    setIsLight(next);
    isLightTheme.current = next;
    document.documentElement.dataset.theme = next ? "light" : "dark";
  };

  return (
    <header ref={headerRef} className={styles.header}>
      <div className={styles.mark}>
        Tender<span>OS</span>
      </div>
      <nav className={styles.nav}>
        {NAV_LINKS.map(({ id, label }) => (
          <a
            key={id}
            href={`#${id}`}
            className={id === activeId ? `${styles.navLink} ${styles.on}` : styles.navLink}
          >
            {label}
          </a>
        ))}
        <button type="button" className={styles.themeToggle} onClick={toggleTheme}>
          {isLight ? "Dark" : "Light"}
        </button>
      </nav>
    </header>
  );
}
