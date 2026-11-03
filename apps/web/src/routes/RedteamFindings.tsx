import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import {
  ApiError,
  listRedteamFindings,
  resolveRedteamFinding,
  triggerRedTeam,
} from "../api/client";
import type { OperationResponse, RedteamFindingResponse } from "../api/client";
import { BreakAfterUnderscores } from "../components/BreakAfterUnderscores";
import { Button } from "../components/ui/Button";
import { EmptyState } from "../components/ui/EmptyState";
import { Notice } from "../components/ui/Notice";
import { StatusBadge } from "../components/ui/StatusBadge";
import { TableScroll } from "../components/ui/TableScroll";
import { useListReads } from "../hooks/useListReads";
import { useTenderOperation } from "../hooks/useTenderOperation";
import { describeStatusKeepingRawWord } from "../status/statusMap";
import "./RedteamFindings.css";

// Screen 14 (Part J). resolve_redteam_finding is the one action granted
// to all 7 organizational roles (rbac.py) - no per-role gating logic
// needed on this screen, unlike compliance/talent/commercial.
//
// E2E contract (full-flow.spec.ts) - the column order and cell text are
// fixed, so this screen deviates from DESIGN.md's findings pattern:
// - the findings table is the page's first table, always rendered, tbody
//   holding only data rows (step 14, knowledge selections);
// - cell 0 is the raw finding_category (step 14, knowledge selections compares it trimmed to
//   "deselected_fact_in_use"; step 14, knowledge selections matches /^deselected_fact_in_use$/);
// - cell 1's text is the raw severity word (step 14, knowledge selections look for "critical"),
//   so its badge keeps that word in the DOM and CSS capitalises it
//   (.badge-raw-word) - every severity label is the capitalised word;
// - cell 3 is exactly "no" or "yes" (step 14, knowledge selections wait for "no"), so
//   resolution is that plain word, not describeFinding's "Resolved" badge;
// - "Resolve" buttons are counted page-wide and must disappear one by one
//   (step 12, resolve findings), so a resolved row has none and no other button's name
//   contains "Resolve".
export function RedteamFindings() {
  const { tenderId } = useParams<{ tenderId: string }>();
  const [findings, setFindings] = useState<RedteamFindingResponse[]>([]);
  // Set once the list answers, so the empty state only claims "no
  // findings" after the response says so (DESIGN.md Copy rules).
  const [findingsLoaded, setFindingsLoaded] = useState(false);
  // A resolve can land while the re-read after an operation is in flight;
  // that read's answer must not undo it (useListReads.ts).
  const findingReads = useListReads();
  const [error, setError] = useState<string | null>(null);
  const [resolutionNotes, setResolutionNotes] = useState<Record<string, string>>({});

  // The tender on screen now, kept fresh on every render, so a read that
  // answers after the user moved to another tender can tell (the same guard
  // as CommercialFindings).
  const tenderIdRef = useRef(tenderId);
  tenderIdRef.current = tenderId;

  // isCancelled: true once this read's result no longer belongs on screen
  // (the user moved to another tender); a late answer is dropped instead of
  // painting the previous tender's findings.
  async function refresh(isCancelled: () => boolean) {
    if (!tenderId) return;
    const startedAt = findingReads.start();
    const result = await listRedteamFindings(tenderId);
    if (isCancelled()) return;
    setFindings((current) => findingReads.merge(startedAt, result, current));
    setFindingsLoaded(true);
  }

  useEffect(() => {
    let cancelled = false;
    setFindingsLoaded(false);
    refresh(() => cancelled).catch(() => {
      if (!cancelled) setError("Failed to load red-team findings");
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenderId]);

  // Phase 4: the POST returns 202 and the worker runs the red team. The
  // button stays busy until the run is finished and the findings it wrote
  // have been re-read (useTenderOperation), and a run started before a
  // reload is picked up again on load. On failure, the operation's own
  // message (LLM timeout, worker lost, ...).
  async function handleFinished(operation: OperationResponse) {
    const finishedForTenderId = tenderId;
    const isCancelled = () => tenderIdRef.current !== finishedForTenderId;
    if (operation.status === "failed") {
      setError(operation.error_message ?? "Run failed");
    }
    await refresh(isCancelled).catch(() => {
      if (!isCancelled()) setError("Failed to load red-team findings");
    });
  }
  const { busy: running, start } = useTenderOperation(tenderId, "redteam_run", handleFinished);

  async function handleRun() {
    if (!tenderId) return;
    setError(null);
    try {
      await start(() => triggerRedTeam(tenderId));
    } catch (err) {
      setError(err instanceof ApiError ? `Run failed (${err.status})` : "Run failed");
    }
  }

  async function handleResolve(findingId: string) {
    setError(null);
    try {
      const updated = await resolveRedteamFinding(findingId, resolutionNotes[findingId] ?? null);
      findingReads.changed(findingId);
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
              <th className="cell-actions">
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
                  <td className="cell-actions">
                    {/* Polish pass (P3): a resolved row shows the note it was
                        resolved with (fetched before, never shown). td 4 is
                        not read by the spec, and the note adds no button, so
                        the page-wide "Resolve" count (step 12, resolve findings) is
                        unchanged. */}
                    {finding.resolved && finding.resolution_note && (
                      <p className="redteam__note">{finding.resolution_note}</p>
                    )}
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
