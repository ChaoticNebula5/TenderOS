import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import {
  ApiError,
  cancelWorkflowRun,
  createWorkflowRun,
  getWorkflowRun,
  listDocuments,
  listGates,
  listWorkflowRuns,
  listWorkflowTasks,
  reprocessDocument,
  uploadDocument,
} from "../api/client";
import type { DocumentResponse, WorkflowTaskResponse } from "../api/client";
import { formatErrorDetail, readEnvelopeCode, readEnvelopeDetail } from "../api/formatErrorDetail";
import { Button } from "../components/ui/Button";
import { EmptyState } from "../components/ui/EmptyState";
import { Field } from "../components/ui/Field";
import { Notice } from "../components/ui/Notice";
import { StatusBadge } from "../components/ui/StatusBadge";
import { TableScroll } from "../components/ui/TableScroll";
import { humanize } from "../format/humanize";
import { useEventStream } from "../hooks/useEventStream";
import { useListReads } from "../hooks/useListReads";
import { useReportRunStatus } from "../shell/runStatus";
import { describeStatus } from "../status/statusMap";
import "./UploadAndProcessingStatus.css";

// Module-level, not inside the component: this Set never changes, so
// creating it once here (instead of on every render) is both cheaper and
// what fixes the useEffect dependency warning below - a value declared
// inside the component is a "new" value to React on every render, even
// with identical contents, so React can't tell it's safe to skip.
const NON_TERMINAL_STATUSES = new Set(["queued", "running", "waiting_for_human"]);

// Plan item 19: the sentence under "Last run" for each reason the backend
// records on a failed run (WorkflowRunResponse.error_code). Each says only
// what the code knows (DESIGN.md, Copy rules): the reaper knows the job is
// gone, not why; DISPATCH_FAILED covers a run that never started and a
// decided gate that was never resumed; a timeout is one step's, not the
// run's. A new run always starts from the beginning (no resume route).
const RUN_ERROR_SENTENCES: Record<string, string> = {
  WORKER_LOST:
    "The last run stopped because the background job processing it was lost. Start processing again to run it from the beginning.",
  DISPATCH_FAILED:
    "The last run stopped before its next step could start. Start processing again to run it from the beginning.",
  JOB_TIMEOUT:
    "The last run stopped because one step ran past its time limit. Start processing again to run it from the beginning.",
};

// Plan item 71: the gate whose Request changes ended the last run, read
// from that run's gates (GET /workflow-runs/{id}/gates; the notes live on
// the gate's decision_payload, not on the run). The newest such gate, in
// case a run ever records more than one.
interface ChangesRequestedAt {
  gateType: string;
  notes: string | null;
}

async function findChangesRequestedGate(runId: string): Promise<ChangesRequestedAt | null> {
  const gates = await listGates(runId);
  const ended = gates.filter((gate) => gate.status === "changes_requested").at(-1);
  if (!ended) return null;
  const notes = ended.decision_payload?.notes;
  return {
    gateType: ended.gate_type,
    notes: typeof notes === "string" && notes.trim() !== "" ? notes : null,
  };
}

// Screen 7 (Part J). Functional requirement 5: progress via SSE
// (useEventStream, B14) - was usePolling; that hook's own docstring
// promised this swap wouldn't need this component to change, and it
// didn't, beyond the import and the two call sites below.
export function UploadAndProcessingStatus() {
  const { tenderId } = useParams<{ tenderId: string }>();
  const [documents, setDocuments] = useState<DocumentResponse[]>([]);
  // documents starts as [] - the same value as "this tender has none". The
  // empty state waits for the first response so it never claims "no
  // documents" while the list is still loading. Set from the existing
  // listDocuments call below; no extra fetch.
  const [documentsLoaded, setDocumentsLoaded] = useState(false);
  // The file input works before the first documents read has answered, so
  // an upload can land while that read is in flight; its late answer must
  // not remove the uploaded row (useListReads.ts, CI run 39983426118).
  const documentReads = useListReads();
  const tenderIdRef = useRef(tenderId);
  tenderIdRef.current = tenderId;
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [startingRun, setStartingRun] = useState(false);

  const [workflowRunId, setWorkflowRunId] = useState<string | null>(null);
  // Set only when the newest run is terminal (completed/failed/cancelled)
  // and there's no active run to poll instead - the "here's what happened
  // last time" note, shown next to the "Start processing" button rather
  // than instead of it.
  const [lastRunStatus, setLastRunStatus] = useState<string | null>(null);
  // Plan item 19: why that last run failed, when the backend recorded a
  // reason (RUN_ERROR_SENTENCES); null for any other outcome.
  const [lastRunErrorCode, setLastRunErrorCode] = useState<string | null>(null);
  // Plan item 71: the last run's id, so a changes_requested outcome can
  // name its gate and show the reviewer's notes (findChangesRequestedGate).
  const [lastRunId, setLastRunId] = useState<string | null>(null);
  const [changesRequestedAt, setChangesRequestedAt] = useState<ChangesRequestedAt | null>(null);
  // Plan item 72: the in-page confirmation for cancelling the active run.
  const [confirmingCancel, setConfirmingCancel] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);
  const cancelRegionRef = useRef<HTMLDivElement>(null);
  const cancelButtonRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // workflowRunId/lastRunStatus otherwise carry over from whichever
    // tender was previously showing - React Router keeps this component
    // instance mounted across a tenderId-only route change, and the
    // lookup effect below skips itself whenever workflowRunId is
    // already set. Without this reset, navigating tender A -> tender B
    // would keep polling and displaying tender A's run.
    setWorkflowRunId(null);
    setLastRunStatus(null);
    setLastRunErrorCode(null);
    setLastRunId(null);
  }, [tenderId]);

  useEffect(() => {
    if (!tenderId || workflowRunId !== null) return;
    let cancelled = false;
    listWorkflowRuns(tenderId)
      .then((runs) => {
        if (cancelled) return;
        // Newest-first (the API's own ordering) - find the first one that's
        // still in progress, if any. A cancelled run followed by a real
        // retry has its retry earlier in this list than the cancelled run,
        // so this picks the retry, not the dead run behind it.
        const active = runs.find((run) => NON_TERMINAL_STATUSES.has(run.status));
        if (active) {
          setWorkflowRunId(active.id);
        } else if (runs.length > 0) {
          // Every run so far is terminal - runs[0] is the most recent
          // outcome (completed/failed/cancelled), worth showing, but not
          // worth polling since nothing about it will change.
          setLastRunStatus(runs[0].status);
          setLastRunErrorCode(runs[0].error_code ?? null);
          setLastRunId(runs[0].id);
        }
      })
      .catch(() => {
        if (!cancelled) setError("Failed to load workflow run status");
      });
    return () => {
      cancelled = true;
    };
  }, [tenderId, workflowRunId]);

  useEffect(() => {
    if (!tenderId) return;
    let cancelled = false;
    setDocumentsLoaded(false);
    const startedAt = documentReads.start();
    listDocuments(tenderId)
      .then((result) => {
        if (cancelled) return;
        setDocuments((current) => documentReads.merge(startedAt, result, current));
        setDocumentsLoaded(true);
      })
      .catch(() => {
        if (!cancelled) setError("Failed to load documents");
      });
    return () => {
      cancelled = true;
    };
  }, [tenderId, documentReads]);

  // After a change: the server's list, so the screen does not rest on the
  // upload's own answer alone. Dropped if the user has moved to another
  // tender meanwhile; a failure keeps the rows already shown.
  function rereadDocuments(forTenderId: string) {
    const startedAt = documentReads.start();
    listDocuments(forTenderId)
      .then((result) => {
        if (tenderIdRef.current !== forTenderId) return;
        setDocuments((current) => documentReads.merge(startedAt, result, current));
      })
      .catch(() => {
        if (tenderIdRef.current === forTenderId) setError("Failed to load documents");
      });
  }

  async function handleUpload(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file || !tenderId) return;
    setError(null);
    setUploading(true);
    try {
      const uploaded = await uploadDocument(tenderId, file);
      documentReads.changed(uploaded.id);
      setDocuments((prev) => {
        const withoutDuplicate = prev.filter((d) => d.id !== uploaded.id);
        return [...withoutDuplicate, uploaded];
      });
      rereadDocuments(tenderId);
    } catch (err) {
      setError(err instanceof ApiError ? `Upload failed (${err.status})` : "Upload failed");
    } finally {
      setUploading(false);
      event.target.value = "";
    }
  }

  async function handleReprocess(documentId: string) {
    setError(null);
    try {
      const updated = await reprocessDocument(documentId);
      documentReads.changed(documentId);
      setDocuments((prev) => prev.map((d) => (d.id === documentId ? updated : d)));
    } catch (err) {
      setError(err instanceof ApiError ? `Reprocess failed (${err.status})` : "Reprocess failed");
    }
  }

  async function handleStartProcessing() {
    if (!tenderId) return;
    setError(null);
    setStartingRun(true);
    try {
      const run = await createWorkflowRun(tenderId, crypto.randomUUID());
      setLastRunStatus(null);
      setLastRunErrorCode(null);
      setLastRunId(null);
      setWorkflowRunId(run.id);
    } catch (err) {
      // Phase 4 plan item 43: another tab (or an earlier click) already
      // started a run for this tender. Show that run as if this click had
      // started it: the streams below follow whatever run id is set.
      const activeRunId =
        err instanceof ApiError && readEnvelopeCode(err.body) === "WORKFLOW_RUN_ACTIVE"
          ? readEnvelopeDetail(err.body, "active_run_id")
          : null;
      if (activeRunId !== null) {
        setLastRunStatus(null);
        setLastRunErrorCode(null);
        setWorkflowRunId(activeRunId);
      } else if (err instanceof ApiError && err.status === 409) {
        setError("All documents must finish parsing before processing can start.");
      } else {
        setError("Failed to start processing.");
      }
    } finally {
      setStartingRun(false);
    }
  }

  const { data: tasks, error: tasksStreamError } = useEventStream<WorkflowTaskResponse[]>(
    () => listWorkflowTasks(workflowRunId as string),
    workflowRunId as string,
    workflowRunId !== null,
  );
  // Watches the RUN itself (not just its tasks) so the screen can notice
  // when it finishes - nothing previously watched for that, so
  // workflowRunId stayed set forever once a run started, leaving this
  // screen stuck on the task list with no way back to "Start
  // processing" after the run actually completed, failed, or was
  // cancelled.
  const { data: activeRun, error: runStreamError } = useEventStream(
    () => getWorkflowRun(workflowRunId as string),
    workflowRunId as string,
    workflowRunId !== null,
  );
  // Both streams' errors surfaced, not just their data - useEventStream.ts
  // already computes a real terminal error (a 401 on ticket-minting, or
  // mint/reconnect retries exhausted), but until now this was the only
  // consumer and it destructured just { data }, so a dead stream left the
  // task list frozen with no visible sign it had stopped updating. Kept
  // separate from `tasks`/`activeRun` being possibly stale (not cleared on
  // error) - the last-known state stays on screen, with a visible note that
  // it may no longer be live, rather than the screen blanking entirely.
  const streamError = tasksStreamError ?? runStreamError;

  // The tender bar's run badge (useReportRunStatus): the live run's status
  // from the stream above while one is active, otherwise the last run's
  // from the listWorkflowRuns call at the top. Both are fetched already for
  // this screen's own use; nothing extra is requested.
  useReportRunStatus(workflowRunId !== null ? (activeRun?.status ?? null) : lastRunStatus);

  useEffect(() => {
    if (activeRun && !NON_TERMINAL_STATUSES.has(activeRun.status)) {
      setLastRunStatus(activeRun.status);
      setLastRunErrorCode(activeRun.error_code ?? null);
      setLastRunId(activeRun.id);
      setWorkflowRunId(null);
    }
  }, [activeRun]);

  // Plan item 72: any open confirmation was about the run being followed;
  // when that changes (it ended, or another tender), the confirmation goes.
  useEffect(() => {
    setConfirmingCancel(false);
    setCancelError(null);
  }, [workflowRunId]);

  // Plan item 71: only a run that ended with changes requested needs its
  // gates; every other outcome says what it needs from the run itself.
  useEffect(() => {
    setChangesRequestedAt(null);
    if (lastRunStatus !== "changes_requested" || lastRunId === null) return;
    let cancelled = false;
    findChangesRequestedGate(lastRunId)
      .then((found) => {
        if (!cancelled) setChangesRequestedAt(found);
      })
      .catch(() => {
        // The badge and the general sentence still show; only the gate's
        // name and the notes are missing.
      });
    return () => {
      cancelled = true;
    };
  }, [lastRunStatus, lastRunId]);

  // Focus follows the confirmation: into it on open, on the safe choice
  // ("Keep running").
  useEffect(() => {
    if (confirmingCancel) {
      cancelRegionRef.current?.querySelector<HTMLButtonElement>(".processing__keep")?.focus();
    }
  }, [confirmingCancel]);

  function closeCancelConfirmation() {
    setConfirmingCancel(false);
    setCancelError(null);
    // Back to "Cancel run", once React has rendered it in the region's place.
    requestAnimationFrame(() => cancelButtonRef.current?.querySelector("button")?.focus());
  }

  async function handleCancelRun() {
    if (workflowRunId === null) return;
    setCancelError(null);
    setCancelling(true);
    try {
      const run = await cancelWorkflowRun(workflowRunId);
      // The answer is the run's status now: "cancelled", or the terminal
      // status it reached first (a cancel on an ended run is a no-op).
      if (!NON_TERMINAL_STATUSES.has(run.status)) {
        setLastRunStatus(run.status);
        setLastRunErrorCode(run.error_code ?? null);
        setLastRunId(run.id);
        setWorkflowRunId(null);
      }
      setConfirmingCancel(false);
    } catch (err) {
      setCancelError(
        err instanceof ApiError && err.status === 403
          ? "Your role can't cancel runs."
          : "Failed to cancel the run.",
      );
    } finally {
      setCancelling(false);
    }
  }

  // The dot-separated link row that sat under the heading is gone: the
  // tender bar's workflow nav (shell/TenderLayout.tsx) links every step
  // from every tender screen. No E2E locator or vitest used those links.
  return (
    <main>
      <h1>Upload and processing status</h1>

      {error && (
        <Notice variant="failed" alert>
          {error}
        </Notice>
      )}

      <section>
        <h2>Documents</h2>
        {/* Still the page's first file input: full-flow.spec.ts (step 3, upload) finds it
            by type, not by this label. */}
        <Field label="Upload a document">
          <input type="file" onChange={handleUpload} disabled={uploading} />
        </Field>
        {documentsLoaded && documents.length === 0 && (
          <EmptyState title="No documents uploaded yet">
            Upload the tender&apos;s documents above. Processing can start once every document is
            parsed.
          </EmptyState>
        )}
        {documents.length > 0 && (
          <TableScroll label="Documents">
            <table>
              <thead>
                <tr>
                  <th>Filename</th>
                  <th>Type</th>
                  <th>Status</th>
                  <th>
                    <span className="visually-hidden">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {documents.map((doc) => (
                  <tr key={doc.id}>
                    <td>{doc.filename}</td>
                    <td>{doc.doc_type}</td>
                    {/* Spec-forced: full-flow.spec.ts (step 3, upload) reads this
                        cell's textContent and requires exactly "parsed". So
                        the badge's DOM text is the backend word and CSS
                        capitalises it (.parse-status); every parse label in
                        statusMap.ts is that capitalised word, which
                        statusMap.test.ts enforces. Third column, as before. */}
                    <td>
                      <span className="parse-status">
                        <StatusBadge
                          tone={describeStatus("parse", doc.parse_status).tone}
                          label={doc.parse_status}
                        />
                      </span>
                    </td>
                    <td>
                      {doc.parse_status === "failed" && (
                        <Button variant="quiet" onClick={() => handleReprocess(doc.id)}>
                          Retry
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableScroll>
        )}
      </section>

      <section>
        <h2>Processing</h2>
        {workflowRunId === null ? (
          <div className="processing__start">
            {lastRunStatus && (
              <p className="processing__last-run">
                Last run <StatusBadge {...describeStatus("run", lastRunStatus)} />
              </p>
            )}
            {lastRunStatus === "failed" &&
              lastRunErrorCode &&
              RUN_ERROR_SENTENCES[lastRunErrorCode] && (
                <p className="processing__last-run-reason">
                  {RUN_ERROR_SENTENCES[lastRunErrorCode]}
                </p>
              )}
            {lastRunStatus === "changes_requested" && (
              <div className="processing__last-run-reason">
                <p>
                  {changesRequestedAt
                    ? `Changes were requested at the ${humanize(changesRequestedAt.gateType)} gate.`
                    : "Changes were requested at one of the run's review gates."}
                </p>
                {changesRequestedAt?.notes && (
                  <p className="processing__last-run-notes">
                    <span className="processing__last-run-notes-label">Reviewer&apos;s notes:</span>{" "}
                    {changesRequestedAt.notes}
                  </p>
                )}
                <p>
                  Make the changes, then start processing again. The new run starts from the
                  beginning.
                </p>
              </div>
            )}
            <Button
              variant="primary"
              onClick={handleStartProcessing}
              busy={startingRun}
              busyLabel="Starting..."
            >
              Start processing
            </Button>
          </div>
        ) : (
          <>
            {streamError && (
              <Notice variant="warning" alert>
                Live updates are currently unavailable ({formatErrorDetail(streamError)}). The list
                below may be out of date.
              </Notice>
            )}
            {/* Announced politely as tasks change (DESIGN.md Accessibility,
                "Live regions"). Task types have no hand-written labels, so
                humanize() re-cases the backend's own word. */}
            <ul className="task-list" aria-live="polite">
              {(tasks ?? []).map((task) => (
                <li key={task.id} className="task-list__item">
                  <span className="task-list__type">{humanize(task.task_type)}</span>
                  <StatusBadge {...describeStatus("task", task.status)} />
                </li>
              ))}
              {tasks !== null && tasks.length === 0 && <li>Waiting for tasks to start...</li>}
            </ul>
            {/* Plan item 72: cancel the active run, confirmed here rather than
                in a browser dialog. Shown to every role, like the gate
                buttons: the web app has no role data, and the API answers
                403 for a role that may not cancel. */}
            <div className="processing__cancel">
              {confirmingCancel ? (
                <div
                  ref={cancelRegionRef}
                  role="group"
                  aria-labelledby="processing-cancel-title"
                  className="processing__cancel-confirm"
                  onKeyDown={(event) => {
                    if (event.key === "Escape" && !cancelling) closeCancelConfirmation();
                  }}
                >
                  <p id="processing-cancel-title" className="processing__cancel-title">
                    Cancel this run?
                  </p>
                  <p>
                    Work already done is kept. A cancelled run can&apos;t be resumed; start
                    processing again to run it from the beginning.
                  </p>
                  {cancelError && (
                    <Notice variant="failed" alert>
                      {cancelError}
                    </Notice>
                  )}
                  <div className="action-row">
                    <Button
                      variant="danger"
                      onClick={handleCancelRun}
                      busy={cancelling}
                      busyLabel="Cancelling..."
                    >
                      Yes, cancel the run
                    </Button>
                    <Button
                      variant="quiet"
                      className="processing__keep"
                      onClick={closeCancelConfirmation}
                      disabled={cancelling}
                    >
                      Keep running
                    </Button>
                  </div>
                </div>
              ) : (
                <div ref={cancelButtonRef}>
                  <Button variant="secondary" onClick={() => setConfirmingCancel(true)}>
                    Cancel run
                  </Button>
                </div>
              )}
            </div>
          </>
        )}
      </section>
    </main>
  );
}
