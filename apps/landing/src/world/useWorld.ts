import { useEffect } from "react";
import { startWorld } from "./frame";

/**
 * Runs the world for as long as the calling component is mounted. Call it from the
 * component that renders the documents and chapters, so its effect runs after all
 * of them are in the DOM. StrictMode's mount, unmount, mount in development is safe:
 * the stop returned by startWorld() tears everything down before the second start.
 */
export function useWorld() {
  useEffect(() => startWorld(), []);
}
