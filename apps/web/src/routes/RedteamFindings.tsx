import { Fragment, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import {
  ApiError,
  listRedteamFindings,
  resolveRedteamFinding,
  triggerRedTeam,
} from "../api/client";
import type { RedteamFindingResponse } from "../api/client";
import { Button } from "../components/ui/Button";
import { EmptyState } from "../components/ui/EmptyState";
import { Notice } from "../components/ui/Notice";
import { StatusBadge } from "../components/ui/StatusBadge";
import { TableScroll } from "../components/ui/TableScroll";
import { describeStatusKeepingRawWord } from "../status/statusMap";
import "./RedteamFindings.css";

// Screen 14 (Part J). resolve_redteam_finding is the one action granted
// to all 7 organizational roles (rbac.py) - no per-role gating logic
// needed on this screen, unlike compliance/talent/commercial.
//
// E2E contract (full-flow.spec.ts) - the column order and cell text are
// fixed, so this screen deviates from DESIGN.md's findings pattern:
// - the findings table is the page's first table, always rendered, tbody
//   holding only data rows (:1375, :1405);
// - cell 0 is the raw finding_category (:1380 compares it trimmed to
//   "deselected_fact_in_use"; :1409 matches /^deselected_fact_in_use$/);
// - cell 1's text is the raw severity word (:1410-1416 look for "critical"),
//   so its badge keeps that word in the DOM and CSS capitalises it
//   (.badge-raw-word) - every severity label is the capitalised word;
// - cell 3 is exactly "no" or "yes" (:1382, :1395 wait for "no"), so
//   resolution is that plain word, not describeFinding's "Resolved" badge;
// - "Resolve" buttons are counted page-wide and must disappear one by one
//   (:1083-1101), so a resolved row has none and no other button's name
//   contains "Resolve".
export function RedteamFindings() {
  const { tenderId } = useParams<{ tenderId: string }>();
  const [findings, setFindings] = useState<RedteamFindingResponse[]>([]);
  // Set once the list answers, so the empty state only claims "no
  // findings" after the response says so (DESIGN.md Copy rules).
  const [findingsLoaded, setFindingsLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [resolutionNotes, setResolutionNotes] = useState<Record<string, string>>({});

  async function refresh() {
    if (!tenderId) return;
    setFindings(await listRedteamFindings(tenderId));
    setFindingsLoaded(true);
  }

  useEffect(() => {
    setFindingsLoaded(false);
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

  const openCount = findings.filter((f) => !f.resolved).length;

  return (
    <main>
      <h1>Red-team findings</h1>

      {error && (
        <Notice variant="failed" alert>
          {error}
        </Notice>
      )}

      <div className="action-row redteam__actions">
        <Button variant="primary" onClick={handleRun} busy={running} busyLabel="Running...">
          Run red team
        </Button>
      </div>

      {findings.length > 0 && (
        <p className="count-line">
          {findings.length} {findings.length === 1 ? "finding" : "findings"}, {openCount} open
        </p>
      )}

      <TableScroll label="Red-team findings">
        <table className="redteam" aria-busy={!findingsLoaded || undefined}>
          <thead>
            <tr>
              <th>Category</th>
              <th>Severity</th>
              <th>Finding</th>
              <th>Resolved</th>
              <th>
                <span className="visually-hidden">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {findings.map((finding) => {
              const severity = describeStatusKeepingRawWord("severity", finding.severity);
              return (
                <tr
                  key={finding.id}
                  className={finding.resolved ? "redteam__row--resolved" : undefined}
                >
                  <td>
                    <BreakAfterUnderscores text={finding.finding_category} />
                  </td>
                  <td>
                    <span className={severity.keepsRawWord ? "badge-raw-word" : undefined}>
                      <StatusBadge tone={severity.tone} label={severity.label} />
                    </span>
                  </td>
                  <td className="redteam__text">
                    <span className="redteam__text-inner">{finding.finding_text}</span>
                  </td>
                  <td>{finding.resolved ? "yes" : "no"}</td>
                  <td>
                    {!finding.resolved && (
                      <div className="action-row redteam__resolve">
                        <input
                          type="text"
                          aria-label="Resolution note"
                          placeholder="Resolution note"
                          value={resolutionNotes[finding.id] ?? ""}
                          onChange={(e) =>
                            setResolutionNotes((prev) => ({
                              ...prev,
                              [finding.id]: e.target.value,
                            }))
                          }
                        />
                        <Button variant="quiet" onClick={() => handleResolve(finding.id)}>
                          Resolve
                        </Button>
                      </div>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </TableScroll>

      {findingsLoaded && findings.length === 0 && (
        <EmptyState title="No red-team findings yet">
          Run red team checks the drafted proposal and the analysis behind it, and lists what it
          finds here.
        </EmptyState>
      )}
    </main>
  );
}

// The raw category with a line-break opportunity after each underscore
// ("deselected_ / fact_ / in_use" at phone width, not a break mid-word).
// <wbr> adds no text, so the cell's textContent stays exactly the backend
// word that full-flow.spec.ts:1380 and :1409 compare.
function BreakAfterUnderscores({ text }: { text: string }) {
  const parts = text.split("_");
  return (
    <>
      {parts.map((part, i) => (
        <Fragment key={i}>
          {part}
          {i < parts.length - 1 && (
            <>
              _<wbr />
            </>
          )}
        </Fragment>
      ))}
    </>
  );
}
