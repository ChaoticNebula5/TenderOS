import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import {
  ApiError,
  listGates,
  listMatches,
  listRoles,
  listWorkflowRuns,
  selectCandidate,
} from "../api/client";
import type { GateResponse, RoleMatchResponse, RoleRequirementResponse } from "../api/client";
import { GateDecisionPanel } from "../components/GateDecisionPanel";
import { Button } from "../components/ui/Button";
import { EmptyState } from "../components/ui/EmptyState";
import { Notice } from "../components/ui/Notice";
import { StatusBadge } from "../components/ui/StatusBadge";
import { TableScroll } from "../components/ui/TableScroll";
import { formatPercent } from "../format/percent";
import { useReportRunStatus } from "../shell/runStatus";
import { describeSelected } from "../status/statusMap";
import "./TeamWorkspace.css";

// "5 years", "1 year". chosen, not in DESIGN: the column showed the bare
// number; null (the role states no minimum) reads "None stated".
function formatYears(years: number | null): string {
  if (years === null) return "None stated";
  return `${years} ${years === 1 ? "year" : "years"}`;
}

// Screen 11 (Part J). Where the resource_selection targets mismatch
// (KNOWN_LIMITATIONS.md) becomes concrete: the resource_selection gate's
// own targets are role_requirement ids, but selecting a candidate needs
// a role_match id (one per candidate, not one per role) - this screen
// gets that id the real way, from GET /roles/{id}/matches, and calls
// select-candidate directly. It never attempts to submit a
// role_requirement id back as a target_update - GateDecisionPanel
// doesn't offer that path for any gate type, and this screen doesn't
// build one either.
export function TeamWorkspace() {
  const { tenderId } = useParams<{ tenderId: string }>();
  const [roles, setRoles] = useState<RoleRequirementResponse[]>([]);
  // Set once listRoles answers, so the empty state only claims "no roles"
  // after the response says so (DESIGN.md Copy rules).
  const [rolesLoaded, setRolesLoaded] = useState(false);
  const [selectedRoleId, setSelectedRoleId] = useState<string | null>(null);
  const [matches, setMatches] = useState<RoleMatchResponse[]>([]);
  // True while the clicked role's listMatches call is in flight. Display
  // state only: without it the screen said "No candidates found" during
  // the fetch, before the response could confirm it.
  const [matchesLoading, setMatchesLoading] = useState(false);
  const [gate, setGate] = useState<GateResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The latest run's status, from the listWorkflowRuns call this screen
  // already makes for the gate - reported to the tender bar. No extra fetch.
  const [runStatus, setRunStatus] = useState<string | null>(null);
  useReportRunStatus(runStatus);
  // The last role a candidate fetch was actually requested for - lets
  // handleSelectRole tell whether ITS OWN listMatches response is still
  // the current one by the time it resolves, or whether the user has
  // since clicked a different role.
  const latestRequestedRoleId = useRef<string | null>(null);

  useEffect(() => {
    if (!tenderId) return;
    let cancelled = false;
    setRolesLoaded(false);
    listRoles(tenderId)
      .then((result) => {
        if (cancelled) return;
        setRoles(result);
        setRolesLoaded(true);
      })
      .catch(() => {
        if (!cancelled) setError("Failed to load roles");
      });
    return () => {
      cancelled = true;
    };
  }, [tenderId]);

  useEffect(() => {
    if (!tenderId) return;
    let cancelled = false;
    // A tender change keeps this component mounted; the previous tender's
    // run status must not show against the new one while it loads.
    setRunStatus(null);
    listWorkflowRuns(tenderId)
      .then(async (runs) => {
        if (cancelled || runs.length === 0) return;
        setRunStatus(runs[0].status);
        const gates = await listGates(runs[0].id);
        const resourceGate = gates.find((g) => g.gate_type === "resource_selection");
        if (!cancelled && resourceGate) setGate(resourceGate);
      })
      .catch(() => {
        if (!cancelled) setError("Failed to load the resource selection gate");
      });
    return () => {
      cancelled = true;
    };
  }, [tenderId]);

  // As RequirementRegister: the decision moves the run on server-side and
  // this screen does not re-fetch it, so the badge goes blank, not stale.
  function handleGateDecided(updated: GateResponse) {
    setGate(updated);
    setRunStatus(null);
  }

  async function handleSelectRole(roleId: string) {
    setSelectedRoleId(roleId);
    setMatches([]);
    setMatchesLoading(true);
    setError(null);
    latestRequestedRoleId.current = roleId;
    try {
      const result = await listMatches(roleId);
      // A slower fetch for a role the user has since clicked away from
      // must not overwrite the matches for whichever role they're
      // looking at now - only apply this response if it's still the
      // most recently requested one.
      if (latestRequestedRoleId.current === roleId) setMatches(result);
    } catch {
      if (latestRequestedRoleId.current === roleId) {
        setError("Failed to load candidates for this role");
      }
    } finally {
      // Same guard: an older role's fetch finishing must not end the
      // loading state of the role now being fetched.
      if (latestRequestedRoleId.current === roleId) setMatchesLoading(false);
    }
  }

  async function handleSelectCandidate(match: RoleMatchResponse, selected: boolean) {
    if (!selectedRoleId) return;
    setError(null);
    try {
      const updated = await selectCandidate(
        selectedRoleId,
        match.employee_knowledge_item_id,
        selected,
        crypto.randomUUID(),
      );
      setMatches((prev) => prev.map((m) => (m.id === updated.id ? updated : m)));
    } catch (err) {
      setError(err instanceof ApiError ? `Selection failed (${err.status})` : "Selection failed");
    }
  }

  const selectedRole = roles.find((r) => r.id === selectedRoleId) ?? null;

  // Heading and panel always render together, as siblings
  // (full-flow.spec.ts:56). Above the tables while pending (DESIGN.md
  // Screen patterns, "Gates"), below them otherwise. Either way the
  // Candidates <section> is never the gate heading's FIRST following
  // section: it comes after the panel, or before the heading.
  const gateBlock = (
    <>
      <h2>Resource selection gate</h2>
      {gate ? (
        <GateDecisionPanel gate={gate} onDecided={handleGateDecided} />
      ) : (
        <p>No open resource selection gate for this tender yet.</p>
      )}
    </>
  );
  const gateFirst = gate?.status === "pending";

  return (
    <main>
      <h1>Team workspace</h1>

      {error && (
        <Notice variant="failed" alert>
          {error}
        </Notice>
      )}

      {gateFirst && gateBlock}

      {/* E2E contract (full-flow.spec.ts:780-795): the page's first table,
          data rows only in tbody (:785 counts them as "loaded"), role name in
          the first cell, a "View candidates" button per row (:825). */}
      <TableScroll label="Roles" stickyLastColumn>
        <table className="roles" aria-busy={!rolesLoaded || undefined}>
          <thead>
            <tr>
              <th>Role</th>
              <th className="cell-num">Min experience</th>
              <th>
                <span className="visually-hidden">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {roles.map((role) => (
              <tr key={role.id}>
                <td>{role.role_name}</td>
                <td className="cell-num">{formatYears(role.min_experience_years)}</td>
                <td>
                  <Button variant="quiet" onClick={() => handleSelectRole(role.id)}>
                    View candidates
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>

      {rolesLoaded && roles.length === 0 && (
        <EmptyState title="No roles extracted yet">
          Roles appear here when talent analysis reads this tender's staffing requirements.
        </EmptyState>
      )}

      {/* E2E contract (full-flow.spec.ts:827-887): a <section> holding the
          "Candidates" heading; tbody rows equal the response's matches
          (:852); the first cell is EXACTLY the raw employee id (:855 -
          toHaveText, so nothing else may sit in that cell); the row's button
          reads exactly "Select", then "Deselect" (:877-887). */}
      {selectedRoleId && (
        <section className="candidates">
          <h2>Candidates</h2>
          {selectedRole && <p className="candidates__role">For {selectedRole.role_name}</p>}
          {matchesLoading && <p className="candidates__loading">Loading candidates...</p>}
          {!matchesLoading && matches.length === 0 && (
            <EmptyState title="No candidates for this role yet">
              Candidates appear when talent analysis scores verified, bid-eligible employees against
              this role.
            </EmptyState>
          )}
          {/* No stickyLastColumn (measured at 390px, batch 4): pinning the
              actions column left less room between the two sticky columns
              than the Score and Selected columns need, so they could never be
              seen whole. The actions scroll instead. */}
          {matches.length > 0 && (
            <TableScroll label="Candidates">
              <table className="candidates__table">
                <thead>
                  <tr>
                    <th>Candidate</th>
                    <th className="cell-num">Score</th>
                    <th>Selected</th>
                    <th>
                      <span className="visually-hidden">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {matches.map((match) => (
                    <tr key={match.id}>
                      {/* The employee's name needs a knowledge-item fetch
                          this screen does not make; the spec also reads
                          this id verbatim (:855). */}
                      <td className="cell-id">{match.employee_knowledge_item_id}</td>
                      <td className="cell-num">{formatPercent(match.score) ?? "Not scored"}</td>
                      <td>
                        <StatusBadge {...describeSelected(match.human_selected)} />
                      </td>
                      <td>
                        <Button
                          variant="quiet"
                          onClick={() => handleSelectCandidate(match, !match.human_selected)}
                        >
                          {match.human_selected ? "Deselect" : "Select"}
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableScroll>
          )}
        </section>
      )}

      {!gateFirst && gateBlock}
    </main>
  );
}
