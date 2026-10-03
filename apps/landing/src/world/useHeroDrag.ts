import { useEffect, useRef, type RefObject } from "react";

export interface HeroDragAngles {
  yaw: number;
  pitch: number;
}

const PITCH_LIMIT = 0.5;
const YAW_SENSITIVITY = 0.0055;
const PITCH_SENSITIVITY = 0.004;

/**
 * Turns the hero sphere under the pointer. Ported from
 * reference-landing.html:895-909 (the `hand` yaw/pitch tracker).
 *
 * Returns a plain mutable ref rather than React state deliberately: this
 * updates on every `pointermove`, and formations read it inside `useFrame`
 * (up to 60 times a second) - routing it through `useState` would mean a
 * React re-render on every mouse-move frame, which is exactly the kind of
 * per-frame work rule 2 says belongs to elapsed-seconds animation, not to
 * React's render cycle.
 *
 * @param targetRef the element pointer events are read from - the world
 *   canvas, since that's what the reference binds `pointerdown`/`pointermove`
 *   to (`cv.addEventListener(...)`)
 * @param grabbableRef current sphere-formation weight, read fresh on every
 *   pointerdown/pointermove rather than captured at mount time - mirrors the
 *   reference reading the live module-level `worldGrabbable` inside the same
 *   handlers, so dragging is only possible while the hero chapter actually
 *   owns the screen
 */
export function useHeroDrag(
  targetRef: RefObject<HTMLElement | null>,
  grabbableRef: RefObject<number>,
): RefObject<HeroDragAngles> {
  const angles = useRef<HeroDragAngles>({ yaw: 0, pitch: 0 });

  useEffect(() => {
    const target = targetRef.current;
    if (!target) return;

    let grabbing = false;
    let lastX = 0;
    let lastY = 0;

    const onPointerDown = (e: PointerEvent) => {
      if (grabbableRef.current < 0.4) return;
      grabbing = true;
      lastX = e.clientX;
      lastY = e.clientY;
      target.setPointerCapture(e.pointerId);
      target.style.cursor = "grabbing";
    };
    const onPointerUp = () => {
      grabbing = false;
      target.style.cursor = "";
    };
    const onPointerMove = (e: PointerEvent) => {
      if (!grabbing) {
        target.style.cursor = grabbableRef.current > 0.4 ? "grab" : "";
        return;
      }
      const dx = (e.clientX - lastX) * YAW_SENSITIVITY;
      const dy = (e.clientY - lastY) * PITCH_SENSITIVITY;
      angles.current.yaw += dx;
      angles.current.pitch = Math.max(-PITCH_LIMIT, Math.min(PITCH_LIMIT, angles.current.pitch + dy));
      lastX = e.clientX;
      lastY = e.clientY;
    };

    target.style.touchAction = "pan-y";
    target.addEventListener("pointerdown", onPointerDown);
    target.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
    return () => {
      target.removeEventListener("pointerdown", onPointerDown);
      target.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onPointerUp);
    };
  }, [targetRef, grabbableRef]);

  return angles;
}
