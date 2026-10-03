import { useEffect, useRef, useState, type RefObject } from "react";
import { Canvas } from "@react-three/fiber";
import { ACESFilmicToneMapping, SRGBColorSpace } from "three";
import { Scene } from "./Scene";
import { useChapterProgress } from "./useChapterProgress";
import { useHeroDrag } from "./useHeroDrag";
import type { ReadingDomRefs } from "./readingDom";
import styles from "./World.module.css";

export interface WorldProps {
  headerRef: RefObject<HTMLElement | null>;
  /** Root element `useChapterProgress`'s `useGSAP` scopes its ScrollTriggers
   *  to - passed down rather than queried, so there's one definition of
   *  "the scrollable app root" shared with whatever else needs it. */
  scopeRef: RefObject<HTMLElement | null>;
  /** Written by the pack chapter's clause list on hover, read here each
   *  frame - see App.tsx for why this lives above both instead of as a
   *  module-level global (the same reasoning as formations.ts's own note
   *  on why FormationInput is an explicit argument, not a closure). */
  hotClauseIndex: RefObject<number>;
  /** Written by Header's toggle, read here each frame - reference's
   *  `window.onThemeChange` (reference-landing.html:1847-1851), ported as a
   *  ref instead of a global function for the same reason as every other
   *  cross-component input in this app. */
  isLightTheme: RefObject<boolean>;
  /** DOM refs for the reading chapter's lens card/hud - owned by App.tsx,
   *  attached by Reading.tsx, written by Scene.tsx each frame. */
  readingRefs: ReadingDomRefs;
}

/** Evaluated once at mount, matching the reference's own one-time `MOB`
 *  (reference-landing.html:552) - the world doesn't re-layout itself if the
 *  viewport crosses 900px after load. */
function isMobileViewport(): boolean {
  return typeof window !== "undefined" && window.matchMedia("(max-width: 900px)").matches;
}

/**
 * Mounts the one WebGL canvas that stays fixed behind every chapter (spec
 * §1). Owns renderer configuration (rule 3's frameloop control lives here)
 * and the two input hooks (`useChapterProgress`, `useHeroDrag`); `Scene`
 * owns everything that's actually drawn.
 */
export function World({ headerRef, scopeRef, hotClauseIndex, isLightTheme, readingRefs }: WorldProps) {
  const wrapperRef = useRef<HTMLDivElement>(null);
  const [frameloop, setFrameloop] = useState<"always" | "never">("always");
  const [isMobile] = useState(isMobileViewport);

  const chapterProgress = useChapterProgress(scopeRef, isMobile);
  const sphereWeight = useRef(0);
  const heroDrag = useHeroDrag(wrapperRef, sphereWeight);

  useEffect(() => {
    const onVisibilityChange = () => setFrameloop(document.hidden ? "never" : "always");
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => document.removeEventListener("visibilitychange", onVisibilityChange);
  }, []);

  return (
    <div ref={wrapperRef} className={styles.world} aria-hidden="true">
      <Canvas
        frameloop={frameloop}
        dpr={[1, isMobile ? 1.5 : 2]}
        gl={{ antialias: !isMobile, alpha: true }}
        camera={{ fov: 44, near: 0.1, far: 600, position: [0, 0, 40] }}
        onCreated={({ gl }) => {
          gl.toneMapping = ACESFilmicToneMapping;
          gl.toneMappingExposure = 1.05;
          gl.outputColorSpace = SRGBColorSpace;
        }}
      >
        <Scene
          chapterProgress={chapterProgress}
          heroDrag={heroDrag}
          headerRef={headerRef}
          hotClauseIndex={hotClauseIndex}
          isMobile={isMobile}
          sphereWeight={sphereWeight}
          isLightTheme={isLightTheme}
          readingRefs={readingRefs}
        />
      </Canvas>
    </div>
  );
}
