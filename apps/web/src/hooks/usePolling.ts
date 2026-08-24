import { useEffect, useRef, useState } from "react";

// Functional requirement 5 (Part J): "Progress updates through polling
// initially; SSE by B14." Callers depend only on { data, error }, so B14
// can replace the interval below with an SSE subscription without any
// consuming component changing.
export function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalMs: number,
  enabled: boolean,
): { data: T | null; error: unknown } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    let timeoutId: ReturnType<typeof setTimeout> | undefined;

    async function tick() {
      try {
        const result = await fetcherRef.current();
        if (!cancelled) {
          setData(result);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err);
      } finally {
        // Chained, not setInterval - a fetch slower than intervalMs
        // would otherwise overlap with the next tick, and a slower
        // request resolving after a faster later one can overwrite
        // fresher data with stale data. Scheduling the next tick only
        // once this one is fully settled makes that ordering impossible.
        if (!cancelled) timeoutId = setTimeout(() => void tick(), intervalMs);
      }
    }

    void tick();
    return () => {
      cancelled = true;
      if (timeoutId !== undefined) clearTimeout(timeoutId);
    };
  }, [intervalMs, enabled]);

  return { data, error };
}
