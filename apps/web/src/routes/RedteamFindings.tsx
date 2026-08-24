import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import {
  ApiError,
  listRedteamFindings,
  resolveRedteamFinding,
  triggerRedTeam,
} from "../api/client";
import type { RedteamFindingResponse } from "../api/client";

// Screen 14 (Part J). resolve_redteam_finding is the one action granted
// to all 7 organizational roles (rbac.py) - no per-role gating logic
// needed on this screen, unlike compliance/talent/commercial.
export function RedteamFindings() {
  const { tenderId } = useParams<{ tenderId: string }>();
  const [findings, setFindings] = useState<RedteamFindingResponse[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [resolutionNotes, setResolutionNotes] = useState<Record<string, string>>({});

  async function refresh() {
    if (!tenderId) return;
    setFindings(await listRedteamFindings(tenderId));
  }

  useEffect(() => {
    refresh().catch(() => setError("Failed to load red-team findings"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenderId]);

  async function handleRun() {
    if (!tenderId) return;
    setError(null);
    setRunning(true);
    try {
      await triggerRedTeam(tenderId);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? `Run failed (${err.status})` : "Run failed");
    } finally {
      setRunning(false);
    }
  }

  async function handleResolve(findingId: string) {
    setError(null);
    try {
      const updated = await resolveRedteamFinding(findingId, resolutionNotes[findingId] ?? null);
      setFindings((prev) => prev.map((f) => (f.id === findingId ? updated : f)));
    } catch (err) {
      setError(err instanceof ApiError ? `Resolve failed (${err.status})` : "Resolve failed");
    }
  }

  return (
    <main>
      <h1>Red-team findings</h1>

      {error && (
        <p role="alert" style={{ color: "var(--color-danger)" }}>
          {error}
        </p>
      )}

      <button type="button" onClick={handleRun} disabled={running}>
        {running ? "Running..." : "Run red team"}
      </button>

      {findings.length === 0 && <p>No findings.</p>}

      <table>
        <thead>
          <tr>
            <th>Category</th>
            <th>Severity</th>
            <th>Finding</th>
            <th>Resolved</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {findings.map((finding) => (
            <tr key={finding.id}>
              <td>{finding.finding_category}</td>
              <td>{finding.severity}</td>
              <td>{finding.finding_text}</td>
              <td>{finding.resolved ? "yes" : "no"}</td>
              <td>
                {!finding.resolved && (
                  <>
                    <input
                      type="text"
                      placeholder="Resolution note"
                      value={resolutionNotes[finding.id] ?? ""}
                      onChange={(e) =>
                        setResolutionNotes((prev) => ({
                          ...prev,
                          [finding.id]: e.target.value,
                        }))
                      }
                    />
                    <button type="button" onClick={() => handleResolve(finding.id)}>
                      Resolve
                    </button>
                  </>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </main>
  );
}
