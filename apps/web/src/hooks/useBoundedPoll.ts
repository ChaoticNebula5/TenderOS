import { useCallback, useEffect, useRef, useState } from "react";

// Plan item 78: a page with no event stream for its data (a document's
// parse status, an import batch's progress) re-reads it on its own, but
// only within bounds (user decision 2026-11-14):
// - only while the page shows something unfinished (`active`);
// - first after POLL_FIRST_DELAY_MS, then twice as long each time up to
//   POLL_MAX_DELAY_MS, and back to the first delay whenever what the page
//   shows changes (`fingerprint`), so progress is followed closely and a
//   long wait costs at most two reads a minute (the API allows 100 a
//   minute per address, rate_limit.py);
// - not while the tab is hidden; a tab shown again starts afresh;
// - after POLL_IDLE_LIMIT_MS with no change it stops and reports
//   `stopped`, so a page can say so and offer `checkAgain`. Nothing ends a
//   parse whose job was lost, so without this a stuck row would be read
//   for as long as the tab stays open.
//
// The hook owns no data. `read` is the page's own re-read, which applies
// its answer through the page's usual merge (useListReads.ts); its errors
// are the page's to show, and the poll carries on after one.
export const POLL_FIRST_DELAY_MS = 2_000;
export const POLL_MAX_DELAY_MS = 30_000;
export const POLL_IDLE_LIMIT_MS = 15 * 60_000;

export function useBoundedPoll(
  read: () => Promise<void>,
  active: boolean,
  fingerprint: string,
): { stopped: boolean; checkAgain: () => void } {
  const readRef = useRef(read);
  readRef.current = read;
  const delay = useRef(POLL_FIRST_DELAY_MS);
  const lastChangeAt = useRef(Date.now());
  const [stopped, setStopped] = useState(false);
  const [visible, setVisible] = useState(() => document.visibilityState !== "hidden");
  // Bumped after each read that changed nothing, to schedule the next one.
  const [readsDone, setReadsDone] = useState(0);

  // Declared before the scheduling effect, so in a commit where both run
  // this one resets the delay first. A change is progress: watch closely
  // again, and the idle clock starts over.
  useEffect(() => {
    delay.current = POLL_FIRST_DELAY_MS;
    lastChangeAt.current = Date.now();
    setStopped(false);
  }, [fingerprint]);

  useEffect(() => {
    function onVisibilityChange() {
      const nowVisible = document.visibilityState !== "hidden";
      if (nowVisible) {
        // Time spent hidden is not time spent watching.
        delay.current = POLL_FIRST_DELAY_MS;
        lastChangeAt.current = Date.now();
      }
      setVisible(nowVisible);
    }
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => document.removeEventListener("visibilitychange", onVisibilityChange);
  }, []);

  useEffect(() => {
    if (!active || stopped || !visible) return;
    let cancelled = false;
    const timer = setTimeout(async () => {
      if (Date.now() - lastChangeAt.current >= POLL_IDLE_LIMIT_MS) {
        setStopped(true);
        return;
      }
      try {
        await readRef.current();
      } catch {
        // The page's read reports its own failure.
      }
      // A read whose answer changed the fingerprint re-ran this effect
      // already, from the first delay; this one is then stale.
      if (cancelled) return;
      delay.current = Math.min(delay.current * 2, POLL_MAX_DELAY_MS);
      setReadsDone((n) => n + 1);
    }, delay.current);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [active, stopped, visible, readsDone, fingerprint]);

  const checkAgain = useCallback(() => {
    delay.current = POLL_FIRST_DELAY_MS;
    lastChangeAt.current = Date.now();
    setStopped(false);
    void readRef.current().catch(() => {
      // As above: the page's read reports its own failure.
    });
  }, []);

  // Nothing unfinished: nothing to have stopped watching.
  return { stopped: stopped && active, checkAgain };
}
