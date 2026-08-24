import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import {
  ApiError,
  generateReadiness,
  getReadiness,
  listGates,
  listWorkflowRuns,
} from "../api/client";
import type { GateResponse, ReadinessReportResponse } from "../api/client";
import { GateDecisionPanel } from "../components/GateDecisionPanel";

// Screen 15 (Part J). Same "most recent workflow run -> its gate" lookup
// shape as screens 8/9/11/12, this time for the final_readiness gate -
// the Approver's actual decision point. The readiness report itself
// (counts/blockers) is fetched separately from GET /readiness, not
// derived from the gate - the gate only carries a ready/blockers
// snapshot as of when it opened; this screen shows the current report.
export function Readiness() {
  const { tenderId } = useParams<{ tenderId: string }>();
  const [report, setReport] = useState<ReadinessReportResponse | null>(null);
  const [gate, setGate] = useState<GateResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);

  async function refreshReport() {
    if (!tenderId) return;
    try {
      setReport(await getReadiness(tenderId));
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setReport(null);
      } else {
        throw err;
      }
    }
  }

  useEffect(() => {
    refreshReport().catch(() => setError("Failed to load the readiness report"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenderId]);

  useEffect(() => {
    if (!tenderId) return;
    let cancelled = false;
    listWorkflowRuns(tenderId)
      .then(async (runs) => {
        if (cancelled || runs.length === 0) return;
        const gates = await listGates(runs[0].id);
        const readinessGate = gates.find((g) => g.gate_type === "final_readiness");
        if (!cancelled && readinessGate) setGate(readinessGate);
      })
      .catch(() => {
        if (!cancelled) setError("Failed to load the final readiness gate");
      });
    return () => {
      cancelled = true;
    };
  }, [tenderId]);

  async function handleGenerate() {
    if (!tenderId) return;
    setError(null);
    setGenerating(true);
    try {
      setReport(await generateReadiness(tenderId));
    } catch (err) {
      setError(err instanceof ApiError ? `Generate failed (${err.status})` : "Generate failed");
    } finally {
      setGenerating(false);
    }
  }

  return (
    <main>
      <h1>Readiness</h1>

      {error && (
        <p role="alert" style={{ color: "var(--color-danger)" }}>
          {error}
        </p>
      )}

      <button type="button" onClick={handleGenerate} disabled={generating}>
        {generating ? "Generating..." : "Generate readiness report"}
      </button>

      {report === null && <p>No readiness report has been generated yet.</p>}

      {report !== null && (
        <>
          <p>
            <strong>{report.ready ? "Ready" : "Not ready"}</strong> (as of state version{" "}
            {report.tender_state_version})
          </p>

          <h2>Blockers</h2>
          {report.blockers.length === 0 ? (
            <p>No blockers.</p>
          ) : (
            <ul>
              {report.blockers.map((blocker, i) => (
                <li key={i}>
                  [{blocker.category}] {blocker.message}
                </li>
              ))}
            </ul>
          )}

          <h2>Counts</h2>
          <table>
            <tbody>
              {Object.entries(report.counts).map(([key, value]) => (
                <tr key={key}>
                  <td>{key}</td>
                  <td>{value}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      <h2>Final readiness gate</h2>
      {gate ? (
        <GateDecisionPanel gate={gate} onDecided={setGate} />
      ) : (
        <p>No open final readiness gate for this tender yet.</p>
      )}
    </main>
  );
}
