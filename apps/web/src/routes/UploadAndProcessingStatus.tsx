import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ApiError,
  createWorkflowRun,
  getWorkflowRun,
  listDocuments,
  listWorkflowRuns,
  listWorkflowTasks,
  reprocessDocument,
  uploadDocument,
} from "../api/client";
import type { DocumentResponse, WorkflowTaskResponse } from "../api/client";
import { useEventStream } from "../hooks/useEventStream";

// Module-level, not inside the component: this Set never changes, so
// creating it once here (instead of on every render) is both cheaper and
// what fixes the useEffect dependency warning below - a value declared
// inside the component is a "new" value to React on every render, even
// with identical contents, so React can't tell it's safe to skip.
const NON_TERMINAL_STATUSES = new Set(["queued", "running", "waiting_for_human"]);

// Screen 7 (Part J). Functional requirement 5: progress via SSE
// (useEventStream, B14) - was usePolling; that hook's own docstring
// promised this swap wouldn't need this component to change, and it
// didn't, beyond the import and the two call sites below.
export function UploadAndProcessingStatus() {
  const { tenderId } = useParams<{ tenderId: string }>();
  const [documents, setDocuments] = useState<DocumentResponse[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [startingRun, setStartingRun] = useState(false);

  const [workflowRunId, setWorkflowRunId] = useState<string | null>(null);
  // Set only when the newest run is terminal (completed/failed/cancelled)
  // and there's no active run to poll instead - the "here's what happened
  // last time" note, shown next to the "Start processing" button rather
  // than instead of it.
  const [lastRunStatus, setLastRunStatus] = useState<string | null>(null);

  useEffect(() => {
    // workflowRunId/lastRunStatus otherwise carry over from whichever
    // tender was previously showing - React Router keeps this component
    // instance mounted across a tenderId-only route change, and the
    // lookup effect below skips itself whenever workflowRunId is
    // already set. Without this reset, navigating tender A -> tender B
    // would keep polling and displaying tender A's run.
    setWorkflowRunId(null);
    setLastRunStatus(null);
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
    listDocuments(tenderId)
      .then((result) => {
        if (!cancelled) setDocuments(result);
      })
      .catch(() => {
        if (!cancelled) setError("Failed to load documents");
      });
    return () => {
      cancelled = true;
    };
  }, [tenderId]);

  async function handleUpload(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file || !tenderId) return;
    setError(null);
    setUploading(true);
    try {
      const uploaded = await uploadDocument(tenderId, file);
      setDocuments((prev) => {
        const withoutDuplicate = prev.filter((d) => d.id !== uploaded.id);
        return [...withoutDuplicate, uploaded];
      });
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
      setWorkflowRunId(run.id);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setError("All documents must finish parsing before processing can start.");
      } else {
        setError("Failed to start processing.");
      }
    } finally {
      setStartingRun(false);
    }
  }

  const { data: tasks } = useEventStream<WorkflowTaskResponse[]>(
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
  const { data: activeRun } = useEventStream(
    () => getWorkflowRun(workflowRunId as string),
    workflowRunId as string,
    workflowRunId !== null,
  );

  useEffect(() => {
    if (activeRun && !NON_TERMINAL_STATUSES.has(activeRun.status)) {
      setLastRunStatus(activeRun.status);
      setWorkflowRunId(null);
    }
  }, [activeRun]);

  return (
    <main>
      <h1>Upload and processing status</h1>
      <p>
        <Link to={`/tenders/${tenderId}/requirements`}>Requirements</Link> ·{" "}
        <Link to={`/tenders/${tenderId}/compliance`}>Compliance</Link> ·{" "}
        <Link to={`/tenders/${tenderId}/team`}>Team</Link> ·{" "}
        <Link to={`/tenders/${tenderId}/commercial`}>Commercial</Link> ·{" "}
        <Link to={`/tenders/${tenderId}/knowledge-selections`}>Knowledge selections</Link> ·{" "}
        <Link to={`/tenders/${tenderId}/proposal`}>Proposal</Link> ·{" "}
        <Link to={`/tenders/${tenderId}/redteam`}>Red team</Link> ·{" "}
        <Link to={`/tenders/${tenderId}/graph`}>Graph</Link> ·{" "}
        <Link to={`/tenders/${tenderId}/readiness`}>Readiness</Link>
      </p>

      {error && (
        <p role="alert" style={{ color: "var(--color-danger)" }}>
          {error}
        </p>
      )}

      <section>
        <h2>Documents</h2>
        <input type="file" onChange={handleUpload} disabled={uploading} />
        {documents.length === 0 ? (
          <p>No documents uploaded yet.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Filename</th>
                <th>Type</th>
                <th>Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {documents.map((doc) => (
                <tr key={doc.id}>
                  <td>{doc.filename}</td>
                  <td>{doc.doc_type}</td>
                  <td>{doc.parse_status}</td>
                  <td>
                    {doc.parse_status === "failed" && (
                      <button type="button" onClick={() => handleReprocess(doc.id)}>
                        Retry
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section>
        <h2>Processing</h2>
        {workflowRunId === null ? (
          <>
            {lastRunStatus && <p>Last run: {lastRunStatus}.</p>}
            <button type="button" onClick={handleStartProcessing} disabled={startingRun}>
              {startingRun ? "Starting..." : "Start processing"}
            </button>
          </>
        ) : (
          <ul>
            {(tasks ?? []).map((task) => (
              <li key={task.id}>
                {task.task_type}: {task.status}
              </li>
            ))}
            {tasks !== null && tasks.length === 0 && <li>Waiting for tasks to start...</li>}
          </ul>
        )}
      </section>
    </main>
  );
}
