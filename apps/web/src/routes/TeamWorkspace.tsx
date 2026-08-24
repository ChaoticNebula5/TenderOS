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
  const [selectedRoleId, setSelectedRoleId] = useState<string | null>(null);
  const [matches, setMatches] = useState<RoleMatchResponse[]>([]);
  const [gate, setGate] = useState<GateResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The last role a candidate fetch was actually requested for - lets
  // handleSelectRole tell whether ITS OWN listMatches response is still
  // the current one by the time it resolves, or whether the user has
  // since clicked a different role.
  const latestRequestedRoleId = useRef<string | null>(null);

  useEffect(() => {
    if (!tenderId) return;
    let cancelled = false;
    listRoles(tenderId)
      .then((result) => {
        if (!cancelled) setRoles(result);
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
    listWorkflowRuns(tenderId)
      .then(async (runs) => {
        if (cancelled || runs.length === 0) return;
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

  async function handleSelectRole(roleId: string) {
    setSelectedRoleId(roleId);
    setMatches([]);
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

  return (
    <main>
      <h1>Team workspace</h1>

      {error && (
        <p role="alert" style={{ color: "var(--color-danger)" }}>
          {error}
        </p>
      )}

      <table>
        <thead>
          <tr>
            <th>Role</th>
            <th>Min experience</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {roles.map((role) => (
            <tr key={role.id}>
              <td>{role.role_name}</td>
              <td>{role.min_experience_years ?? "-"}</td>
              <td>
                <button type="button" onClick={() => handleSelectRole(role.id)}>
                  View candidates
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {selectedRoleId && (
        <section>
          <h2>Candidates</h2>
          {matches.length === 0 && <p>No candidates found for this role yet.</p>}
          {matches.length > 0 && (
            <table>
              <thead>
                <tr>
                  <th>Candidate</th>
                  <th>Score</th>
                  <th>Selected</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {matches.map((match) => (
                  <tr key={match.id}>
                    <td>{match.employee_knowledge_item_id}</td>
                    <td>{match.score ?? "-"}</td>
                    <td>{match.human_selected ? "yes" : "no"}</td>
                    <td>
                      <button
                        type="button"
                        onClick={() => handleSelectCandidate(match, !match.human_selected)}
                      >
                        {match.human_selected ? "Deselect" : "Select"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}

      <h2>Resource selection gate</h2>
      {gate ? (
        <GateDecisionPanel gate={gate} onDecided={setGate} />
      ) : (
        <p>No open resource selection gate for this tender yet.</p>
      )}
    </main>
  );
}
