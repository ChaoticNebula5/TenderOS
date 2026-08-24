import { useState } from "react";
import { useParams } from "react-router-dom";
import {
  ApiError,
  getGraphContradictions,
  getGraphStatus,
  getGraphTrace,
  graphNodeIdFor,
  triggerGraphProjection,
} from "../api/client";
import type { GraphContradictionRow, GraphStatusResponse, GraphTraceResponse } from "../api/client";

// Screen 17 (Part J). Click-to-expand: clicking a neighbor re-queries
// graph/trace for that node, using its own node_labels (now returned by
// the fixed Cypher - KNOWN_LIMITATIONS.md's updated [B10] entry) to know
// what to ask for next. No route lists graph nodes to browse from
// scratch, so the entry point is a manual node_label + node_id pair -
// realistically arrived at from a link elsewhere (a requirement, a
// compliance entry) rather than typed cold, but supported directly here
// too since no other screen passes one in yet.
const TENDER_SCOPED_LABELS = [
  "Tender",
  "Requirement",
  "Evidence",
  "Compliance",
  "Role",
  "ProposalClaim",
  "ProposalSection",
];
const SHARED_LABELS = ["Employee", "Certification", "Project", "Capability"];

export function GraphExplorer() {
  const { tenderId } = useParams<{ tenderId: string }>();
  const [status, setStatus] = useState<GraphStatusResponse | null>(null);
  const [contradictions, setContradictions] = useState<GraphContradictionRow[] | null>(null);
  const [trace, setTrace] = useState<GraphTraceResponse | null>(null);
  const [nodeLabel, setNodeLabel] = useState(TENDER_SCOPED_LABELS[0]);
  const [nodeId, setNodeId] = useState("");
  const [history, setHistory] = useState<{ label: string; id: string }[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function refreshStatusAndContradictions() {
    if (!tenderId) return;
    try {
      setStatus(await getGraphStatus(tenderId));
      setContradictions(await getGraphContradictions(tenderId));
    } catch (err) {
      if (!(err instanceof ApiError && err.status === 409)) {
        throw err;
      }
    }
  }

  async function loadNode(label: string, id: string) {
    if (!tenderId || !id) return;
    setError(null);
    setBusy(true);
    try {
      let result: GraphTraceResponse;
      try {
        result = await getGraphTrace(tenderId, label, id);
      } catch (err) {
        // Same "graph stale: enqueue projection, retry query once" policy
        // (Part I4) screen 10 already implements for graph/impact.
        if (err instanceof ApiError && err.status === 409) {
          await triggerGraphProjection(tenderId, crypto.randomUUID());
          result = await getGraphTrace(tenderId, label, id);
        } else {
          throw err;
        }
      }
      setTrace(result);
      setHistory((prev) => [...prev, { label, id }]);
      setNodeLabel(label);
      setNodeId(id);
      await refreshStatusAndContradictions();
    } catch (err) {
      setError(err instanceof ApiError ? `Load failed (${err.status})` : "Load failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main>
      <h1>Graph explorer</h1>

      {error && (
        <p role="alert" style={{ color: "var(--color-danger)" }}>
          {error}
        </p>
      )}

      {status && (
        <p>
          Graph is {status.fresh ? "fresh" : "stale"} (state version {status.current_state_version},
          projected {status.projected_state_version ?? "never"}).
        </p>
      )}

      {contradictions !== null && contradictions.length > 0 && (
        <div
          role="alert"
          style={{ border: "1px solid var(--color-danger)", padding: "var(--space-3)" }}
        >
          <h2 style={{ color: "var(--color-danger)" }}>
            {contradictions.length} graph contradiction{contradictions.length === 1 ? "" : "s"}
          </h2>
          <ul>
            {contradictions.map((row, i) => (
              <li key={i}>{JSON.stringify(row)}</li>
            ))}
          </ul>
        </div>
      )}

      <h2>Look up a node</h2>
      <select value={nodeLabel} onChange={(e) => setNodeLabel(e.target.value)}>
        {[...TENDER_SCOPED_LABELS, ...SHARED_LABELS].map((label) => (
          <option key={label} value={label}>
            {label}
          </option>
        ))}
      </select>
      <input
        type="text"
        placeholder="Node id"
        value={nodeId}
        onChange={(e) => setNodeId(e.target.value)}
      />
      <button type="button" onClick={() => loadNode(nodeLabel, nodeId)} disabled={busy || !nodeId}>
        Load
      </button>

      {history.length > 1 && (
        <p>
          Path:{" "}
          {history.map((step, i) => (
            <span key={i}>
              {i > 0 && " -> "}
              {step.label}
            </span>
          ))}
        </p>
      )}

      {trace && trace.n && (
        <section>
          <h2>
            {trace.node_labels.join(", ")} ({JSON.stringify(trace.n)})
          </h2>

          <h3>Neighbors</h3>
          {trace.neighbors.length === 0 ? (
            <p>No neighbors.</p>
          ) : (
            <ul>
              {trace.neighbors.map((edge, i) => {
                const id = graphNodeIdFor(edge.neighbor_labels, edge.neighbor);
                return (
                  <li key={i}>
                    [{edge.relationship}] {edge.neighbor_labels.join(", ")} -{" "}
                    {JSON.stringify(edge.neighbor)}{" "}
                    {id && (
                      <button
                        type="button"
                        onClick={() => loadNode(edge.neighbor_labels[0], id)}
                        disabled={busy}
                      >
                        Explore
                      </button>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      )}
    </main>
  );
}
