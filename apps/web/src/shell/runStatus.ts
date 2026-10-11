import { createContext, useContext, useEffect } from "react";

// The tender bar's run status badge (DESIGN.md Layout, "Tender header status
// badge"; deferred from batch 2 to batch 3). TenderLayout owns the value and
// renders the badge; a tender screen that ALREADY has its run's status from
// a fetch it makes anyway reports it with useReportRunStatus. Nothing here
// fetches: the badge only ever shows what a screen already knows.
//
// Own file, not TenderLayout.tsx: react-refresh/only-export-components
// (eslint.config.js) warns when a component file also exports a hook or a
// context object.

type SetRunStatus = (status: string | null) => void;

// null outside a TenderLayout: a screen rendered on its own (as in a unit
// test) reports into nothing instead of throwing.
export const RunStatusContext = createContext<SetRunStatus | null>(null);

// Reports `status` while the calling screen is mounted. null means "not
// known" and shows no badge. Clearing happens in the effect's cleanup: when
// the screen unmounts (navigating to another step) or reports a different
// value, the previous one is withdrawn first, so a status never outlives
// the screen that knew it.
export function useReportRunStatus(status: string | null): void {
  const setRunStatus = useContext(RunStatusContext);
  useEffect(() => {
    if (!setRunStatus) return;
    setRunStatus(status);
    return () => setRunStatus(null);
  }, [setRunStatus, status]);
}
