import { useEffect, useMemo, useRef, type RefObject } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import { BackSide, Color, FogExp2, Group, Mesh, MeshStandardMaterial, PerspectiveCamera, Vector3 } from "three";
import { KB } from "../data/kb";
import { CHAPTERS } from "../data/chapters";
import { buildSheetGeometry, helixLayout } from "./sheetGeometry";
import { generateDocumentTexture } from "./textures";
import { createEnvironmentTexture } from "./environment";
import { createFormationOutput, resolveFormation, SHEET_RADIUS_SPHERE, type FormationInput, type SheetUnit } from "./formations";
import { distanceForFormation, measureRegion } from "./cameraFraming";
import { approach, clamp01, clampedDelta, ease } from "./mathUtils";
import { chapterFloat, READING_MORPH_SHARE, type ChapterProgressState } from "./useChapterProgress";
import type { HeroDragAngles } from "./useHeroDrag";
import { fillLensCard, updateReadingHud, type ReadingDomRefs } from "./readingDom";
import readingStyles from "../components/Reading.module.css";

const CAMERA_FOV = 44;
const N = KB.length;
const EMISSIVE_COLOR = new Color("#C98A47");

// Theme-reactive palette. Ported from reference-landing.html:1847-1851
// (`window.onThemeChange`) - "paper has to read darker than the page in
// light mode," per the reference's own comment there.
const FOG_COLOR = { dark: "#0E0B08", light: "#CFC7B7" };
const BACK_COLOR_BY_THEME = { dark: "#E2D9C6", light: "#B6AA92" };
const FRONT_TINT_BY_THEME = { dark: "#FFFFFF", light: "#BEB39B" };
const EXPOSURE_BY_THEME = { dark: 1.05, light: 0.74 };

interface SheetRefs {
  group: Group;
  front: Mesh;
  back: Mesh;
  frontMaterial: MeshStandardMaterial;
}

export interface SceneProps {
  chapterProgress: RefObject<ChapterProgressState>;
  heroDrag: RefObject<HeroDragAngles>;
  headerRef: RefObject<HTMLElement | null>;
  hotClauseIndex: RefObject<number>;
  isMobile: boolean;
  /** Weight of the "sphere" formation right now, written every frame so
   *  `useHeroDrag`'s pointer handlers can gate dragging without this
   *  component re-rendering on every scroll tick. */
  sphereWeight: RefObject<number>;
  isLightTheme: RefObject<boolean>;
  /** DOM refs for the reading chapter's lens card and hud - see
   *  world/readingDom.ts for why Scene writes these directly instead of
   *  through React state. */
  readingRefs: ReadingDomRefs;
}

/**
 * The world's content: lights, environment, fog, the 40 document sheets,
 * and the one per-frame loop that blends formations and derives the camera.
 * Ported from reference-landing.html:813-838 (renderer/lights/environment),
 * 869-884 (per-document mesh setup) and 1582-1725 (the render loop's world-
 * relevant parts - the reading/compliance/red-team/pricing overlay logic in
 * that same loop is P2-P5 scope and isn't ported here).
 *
 * Sheets are built with the three.js constructor called directly
 * (`args={[geometry, material]}` below), not left to React-three-fiber's
 * default of constructing an empty `Mesh` and assigning `.geometry`/
 * `.material` afterward. That default matters here specifically: `Mesh`
 * only populates `morphTargetInfluences` once, inside its constructor,
 * from whatever geometry it was constructed with (three.js's own
 * `updateMorphTargets()`, confirmed against the installed
 * node_modules/three source rather than assumed). Constructed empty first,
 * a sheet's morph target would silently never exist - the bend that's
 * supposed to wrap it onto the helix cylinder from P2 onward would just do
 * nothing, with no error. Passing the real geometry and material as
 * constructor `args` is what makes morph targets work at all.
 */
export function Scene({
  chapterProgress,
  heroDrag,
  headerRef,
  hotClauseIndex,
  isMobile,
  sphereWeight,
  isLightTheme,
  readingRefs,
}: SceneProps) {
  const { gl, scene } = useThree();

  const sheetGeometry = useMemo(() => {
    const { helixRadius } = helixLayout(N);
    return buildSheetGeometry(helixRadius, isMobile ? 8 : 18);
  }, [isMobile]);

  const backMaterial = useMemo(
    () =>
      new MeshStandardMaterial({
        color: BACK_COLOR_BY_THEME.dark,
        roughness: 0.86,
        metalness: 0.02,
        envMapIntensity: 0.9,
        side: BackSide,
      }),
    [],
  );

  const frontMaterials = useMemo(
    () =>
      KB.map(
        (doc) =>
          new MeshStandardMaterial({
            map: generateDocumentTexture(doc.texture),
            roughness: 0.78,
            metalness: 0.02,
            envMapIntensity: 1,
            transparent: true,
            opacity: 0.92,
            emissive: EMISSIVE_COLOR,
            emissiveIntensity: 0,
          }),
      ),
    [],
  );

  const units: SheetUnit[] = useMemo(
    () => KB.map((doc, index) => ({ index, phase: Math.random() * 7, claimIndex: doc.claimIndex, rd: 0, lens: 0 })),
    [],
  );

  const sheetRefs = useRef<(SheetRefs | null)[]>(KB.map(() => null));
  const outputA = useMemo(createFormationOutput, []);
  const outputB = useMemo(createFormationOutput, []);
  const clock = useRef(0);
  const camDistance = useRef(40);
  const packOpen = useRef(0);
  // null (not a boolean) so the first frame always applies the palette
  // once, rather than silently matching the material defaults by
  // coincidence - dark mode's values happen to equal the constructor
  // defaults, but that shouldn't be why they're correct on load.
  const lastAppliedTheme = useRef<boolean | null>(null);

  // Reading-chapter bookkeeping. Reference: `readUpTo=-1,lensJ=-1,lensT=0`
  // (reference-landing.html:888) plus `lensShown=-1` (line 1177) - same
  // starting values, same meaning ("nothing read, nothing in the lens yet").
  const readUpTo = useRef(-1);
  const lensJ = useRef(-1);
  const lensShown = useRef(-1);
  const lensT = useRef(0);
  /**
   * TODO(human): world-space point the lens document flies to. Stays at the
   * origin until the depth calculation is written - see the numbered
   * pseudocode below this component and formations.ts's `FormationInput.
   * pageAnchor` for what's already wired to consume it. Once you compute a
   * real value here each frame, the reading chapter's "document comes
   * forward" motion (already live, using this placeholder) starts landing
   * in the right place with no other code changes.
   */
  const pageAnchor = useRef(new Vector3(0, 0, 0));

  useEffect(() => {
    scene.environment = createEnvironmentTexture(gl);
    // Not disposed on unmount: the world mounts once for the page's whole
    // life (one fixed canvas behind every chapter, per spec §1) and never
    // unmounts before a full page navigation, which reclaims it anyway.
  }, [gl, scene]);

  useFrame((state, rawDelta) => {
    const dt = clampedDelta(rawDelta);
    clock.current += dt;
    const t = clock.current;

    // ── theme-reactive palette (reference: window.onThemeChange) - only
    //    touched when the theme actually changed, not every frame ──
    const light = isLightTheme.current;
    if (lastAppliedTheme.current !== light) {
      lastAppliedTheme.current = light;
      const key = light ? "light" : "dark";
      gl.toneMappingExposure = EXPOSURE_BY_THEME[key];
      backMaterial.color.set(BACK_COLOR_BY_THEME[key]);
      frontMaterials.forEach((m) => m.color.set(FRONT_TINT_BY_THEME[key]));
      if (state.scene.fog instanceof FogExp2) state.scene.fog.color.set(FOG_COLOR[key]);
    }

    const progress = chapterProgress.current.progress;
    const F = chapterFloat(progress);
    const kRaw = Math.floor(F);
    const blendT = Math.min(1, F - kRaw);
    const k = Math.min(CHAPTERS.length - 1, kRaw);
    const kb = Math.min(CHAPTERS.length - 1, k + 1);
    const chapterA = CHAPTERS[k];
    const chapterB = CHAPTERS[kb];
    const formationA = resolveFormation(chapterA.form);
    const formationB = resolveFormation(chapterB.form);

    sphereWeight.current = (chapterA.form === "sphere" ? 1 - blendT : 0) + (chapterB.form === "sphere" ? blendT : 0);
    const helixWeight = (chapterA.form === "helix" ? 1 - blendT : 0) + (chapterB.form === "helix" ? blendT : 0);

    packOpen.current += (clamp01(chapterProgress.current.packRaw) - packOpen.current) * approach(4, dt);

    // ── reading progress: which document is at the front of the helix,
    //    which one is "the lens" (the reader's current document), and how
    //    many have been scrolled past. Ported from
    //    reference-landing.html:1606-1611. ──
    const readP = chapterProgress.current.readP;
    const readTravel = clamp01((readP - READING_MORPH_SHARE) / (1 - READING_MORPH_SHARE)) * (N - 1);
    const isReading = readP >= READING_MORPH_SHARE * 0.98 && helixWeight > 0.5;
    if (isReading) {
      readUpTo.current = Math.max(readUpTo.current, Math.round(readTravel));
      lensJ.current = Math.round(readTravel);
    } else if (helixWeight < 0.2) {
      lensJ.current = -1;
    }

    if (lensJ.current >= 0 && lensShown.current !== lensJ.current) {
      fillLensCard(readingRefs, KB[lensJ.current], lensJ.current, N, readingStyles);
      lensShown.current = lensJ.current;
      lensT.current = 0;
    }
    if (lensJ.current >= 0 && units[lensJ.current].lens > 0.8) {
      lensT.current += dt;
    }

    const input: FormationInput = {
      heroYaw: heroDrag.current.yaw,
      heroPitch: heroDrag.current.pitch,
      packOpen: packOpen.current,
      hotClauseIndex: hotClauseIndex.current,
      readP,
      readTravel,
      pageAnchor: pageAnchor.current,
    };

    // ── camera framing: region + distance derived from geometry and the
    //    live viewport (rule 4), never a typed constant ──
    const { width: W, height: H } = state.size;
    const headerBottom = headerRef.current?.getBoundingClientRect().bottom ?? 0;
    const topInset = Math.max(0, headerBottom);
    const ra = measureRegion(chapterA.side, W, H, topInset, !!chapterA.wide, isMobile);
    const rb = measureRegion(chapterB.side, W, H, topInset, !!chapterB.wide, isMobile);
    const region = { cx: ra.cx + (rb.cx - ra.cx) * blendT, cy: ra.cy, w: ra.w + (rb.w - ra.w) * blendT, h: ra.h };

    const camera = state.camera as PerspectiveCamera;
    camera.setViewOffset(W, H, W / 2 - region.cx, H / 2 - region.cy, W, H);
    const da = distanceForFormation(formationA, region, CAMERA_FOV, H);
    const db = distanceForFormation(formationB, region, CAMERA_FOV, H);
    const camWant = (da + (db - da) * blendT) * 1.08;
    camDistance.current += (camWant - camDistance.current) * approach(6, dt);
    camera.position.set(0, 0, camDistance.current);
    camera.updateMatrixWorld();

    if (state.scene.fog instanceof FogExp2) {
      state.scene.fog.density = 0.7 / (camDistance.current + SHEET_RADIUS_SPHERE);
    }

    // ── per-sheet formation blend (rule 2: elapsed seconds, clamped dt) ──
    for (let i = 0; i < N; i++) {
      const refs = sheetRefs.current[i];
      if (!refs) continue;
      const unit = units[i];

      // Per-sheet reading state, updated before the formations read it
      // (reference-landing.html:1699/1706 - the same ordering, values
      // written for this sheet, then read by fa.f/fb.f in the next lines).
      const lensTarget = unit.index === lensJ.current && isReading ? 1 : 0;
      unit.lens += (lensTarget * helixWeight - unit.lens) * approach(7, dt);
      unit.rd += ((unit.index <= readUpTo.current ? 1 : 0) * helixWeight - unit.rd) * approach(4, dt);

      formationA.f(unit, outputA, t, input);
      formationB.f(unit, outputB, t, input);

      const stagger = ease(clamp01((blendT - (unit.index / N) * 0.3) / 0.7));
      refs.group.position.lerpVectors(outputA.position, outputB.position, stagger);
      refs.group.quaternion.copy(outputA.quaternion).slerp(outputB.quaternion, stagger);
      const bend = outputA.bend + (outputB.bend - outputA.bend) * stagger;
      const scale = outputA.scale + (outputB.scale - outputA.scale) * stagger;
      refs.group.scale.setScalar(scale);

      refs.frontMaterial.opacity = outputA.opacity + (outputB.opacity - outputA.opacity) * stagger;
      refs.frontMaterial.emissiveIntensity = (outputA.emissive + (outputB.emissive - outputA.emissive) * stagger) * 0.34;

      if (refs.front.morphTargetInfluences) refs.front.morphTargetInfluences[0] = bend;
      if (refs.back.morphTargetInfluences) refs.back.morphTargetInfluences[0] = bend;

      refs.group.visible = refs.frontMaterial.opacity > 0.012 && (!isMobile || unit.index % 2 === 0);
    }

    // ── reading hud: counters + requirement grid, every frame (reference
    //    calls readingHud() unconditionally too, reference-landing.html:1843;
    //    cheap DOM class toggles, and updateReadingHud no-ops before
    //    Reading.tsx has mounted its refs) ──
    const readCount = readUpTo.current + 1;
    const covered = new Set<string>();
    for (let j = 0; j <= readUpTo.current; j++) {
      if (j !== lensJ.current || lensT.current > 1.02) covered.add(KB[j].requirementCode);
    }
    const allRead = readCount >= N && lensT.current > 1.02;
    const lensSettled = lensT.current > 1.02 && lensT.current < 1.75;
    updateReadingHud(
      readingRefs,
      Math.max(0, readCount),
      covered,
      allRead,
      lensJ.current >= 0 ? KB[lensJ.current].requirementCode : null,
      lensSettled,
      { cov: readingStyles.cov, gap: readingStyles.gap, hit: readingStyles.hit },
    );
  });

  return (
    <>
      <ambientLight color={0xffe9d2} intensity={0.44} />
      <directionalLight color={0xffe0b6} intensity={1.5 * Math.PI} position={[-10, 14, 16]} />
      <directionalLight color={0x9fc2c8} intensity={0.4 * Math.PI} position={[14, -6, 8]} />
      <directionalLight color={0xffd9a8} intensity={0.35 * Math.PI} position={[4, 8, -22]} />
      <fogExp2 attach="fog" args={["#0E0B08", 0.012]} />
      {KB.map((doc, i) => (
        <group
          key={doc.filename}
          ref={(group) => {
            if (!group) {
              sheetRefs.current[i] = null;
              return;
            }
            const front = group.children[0] as Mesh;
            const back = group.children[1] as Mesh;
            sheetRefs.current[i] = { group, front, back, frontMaterial: front.material as MeshStandardMaterial };
          }}
        >
          <mesh args={[sheetGeometry, frontMaterials[i]]} />
          <mesh args={[sheetGeometry, backMaterial]} />
        </group>
      ))}
    </>
  );
}
