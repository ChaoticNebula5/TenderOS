// The workflow run statuses that are still in progress: a run in one of
// these can change, so a screen follows it; any other status is final.
// The web app's only copy of the backend's ACTIVE_RUN_STATUSES
// (packages/tenderos_backend/src/tenderos_backend/db/models/workflow.py),
// which TypeScript cannot import.
//
// Plan item 78: moved here from UploadAndProcessingStatus.tsx (it was
// NON_TERMINAL_STATUSES) once the gate screens needed it too.
// tests/unit/test_web_active_run_statuses.py reads the line below and fails
// if it differs from the backend's set, so keep it one array literal of
// double-quoted strings under this name.
export const ACTIVE_RUN_STATUSES = ["queued", "running", "waiting_for_human"] as const;

const ACTIVE = new Set<string>(ACTIVE_RUN_STATUSES);

export function isActiveRunStatus(status: string): boolean {
  return ACTIVE.has(status);
}
