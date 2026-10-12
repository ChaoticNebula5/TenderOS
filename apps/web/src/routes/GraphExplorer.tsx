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
import { Button } from "../components/ui/Button";
import { EmptyState } from "../components/ui/EmptyState";
import { Field } from "../components/ui/Field";
import { StatusIcon } from "../components/ui/icons";
import { Notice } from "../components/ui/Notice";
import { StatusBadge } from "../components/ui/StatusBadge";
import { TableScroll } from "../components/ui/TableScroll";
import { formatDate } from "../format/date";
import { humanize } from "../format/humanize";
import { labelWords } from "../format/impactSummary";
import { describeGraphFreshness } from "../status/statusMap";
import { contradictionParts, graphValue, nodeSummary } from "./graphExplorerValues";
import "./GraphExplorer.css";

// Screen 17 (Part J). Click-to-expand: clicking a neighbor re-queries
// graph/trace for that node, using its own node_labels (now returned by
// the fixed Cypher - KNOWN_LIMITATIONS.md's updated [B10] entry) to know
// what to ask for next. No route lists graph nodes to browse from
// scratch, so the entry point is a manual node_label + node_id pair -
// realistically arrived at from a link elsewhere (a requirement, a
// compliance entry) rather than typed cold, but supported directly here
// too since no other screen passes one in yet.
//
// Restyle batch 5: no JSON.stringify output to an operator (DESIGN.md
// Do's and Don'ts) - the node is a definition list, neighbours a table,
// contradictions named rows (graphExplorerValues.ts); only a value with no
// flat text form falls back to its JSON in <code>. Fetches are unchanged:
// status and contradictions still load only after a node does. The spec
// never visits this screen.
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
        <Notice variant="failed" alert>
          {error}
        </Notice>
      )}

      {/* "Fresh" only when the response says fresh (DESIGN.md Copy rules). */}
      {status && (
        <p className="graph__status">
          <StatusBadge {...describeGraphFreshness(status.fresh)} />
          <span>
            State version {status.current_state_version} · projected{" "}
            {status.projected_state_version ?? "never"}
            {status.last_projected_at && (
              <> · last projected {formatDate(status.last_projected_at)}</>
            )}
          </span>
        </p>
      )}

      {contradictions !== null && contradictions.length > 0 && (
        <div role="alert" className="graph__contradictions">
          <h2>
            <StatusIcon tone="warning" className="graph__contradictions-icon" />
            {contradictions.length} graph contradiction{contradictions.length === 1 ? "" : "s"}
          </h2>
          <ul className="graph__contradiction-list">
            {contradictions.map((row, i) => (
              <li key={i}>
                <span className="graph__contradiction-category">
                  {humanize(String(row.category ?? "contradiction"))}
                </span>
                {contradictionParts(row).map((part) => (
                  <span key={part.key} className="graph__contradiction-part">
                    {humanize(part.key)}: {part.text}
                  </span>
                ))}
              </li>
            ))}
          </ul>
        </div>
      )}

      <h2>Look up a node</h2>
      <div className="graph__lookup">
        <Field label="Node type">
          <select value={nodeLabel} onChange={(e) => setNodeLabel(e.target.value)}>
            {[...TENDER_SCOPED_LABELS, ...SHARED_LABELS].map((label) => (
              <option key={label} value={label}>
                {labelWords(label)}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Node id">
          <input
            type="text"
            className="graph__id-input"
            value={nodeId}
            onChange={(e) => setNodeId(e.target.value)}
          />
        </Field>
        <Button
          variant="primary"
          onClick={() => loadNode(nodeLabel, nodeId)}
          busy={busy}
          disabled={!nodeId}
        >
          Load
        </Button>
      </div>

      {history.length > 1 && (
        <p className="graph__path">
          Path:{" "}
          {history.map((step, i) => (
            <span key={i}>
              {i > 0 && " → "}
              {labelWords(step.label)}
            </span>
          ))}
        </p>
      )}

      {trace === null && (
        <EmptyState title="No node loaded yet">
          Choose a node type, paste its id from a requirement, compliance entry or claim, and load
          it to see its properties and neighbours.
        </EmptyState>
      )}

      {trace !== null && !trace.n && (
        <p className="graph__none">No node with that id in this tender&apos;s graph.</p>
      )}

      {trace && trace.n && (
        <section className="graph__node">
          <h2>{trace.node_labels.map(labelWords).join(", ")}</h2>
          <dl className="graph__props">
            {Object.entries(trace.n).map(([key, value]) => {
              const shown = graphValue(value);
              return (
                <div key={key}>
                  <dt>{humanize(key)}</dt>
                  <dd>{shown.kind === "json" ? <code>{shown.text}</code> : shown.text}</dd>
                </div>
              );
            })}
          </dl>

          <h3>Neighbors</h3>
          {trace.neighbors.length === 0 ? (
            <p className="graph__none">No neighbors.</p>
          ) : (
            // stickyLastColumn measured at 390px (batch 5): with Explore in
            // every row and with no Explore at all, the widest middle column
            // still fits between the two pinned columns.
            <TableScroll label="Neighbors" stickyLastColumn>
              <table className="graph__neighbors">
                <thead>
                  <tr>
                    <th>Relationship</th>
                    <th>Node type</th>
                    <th>Node</th>
                    <th>
                      <span className="visually-hidden">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {trace.neighbors.map((edge, i) => {
                    const id = graphNodeIdFor(edge.neighbor_labels, edge.neighbor);
                    return (
                      <tr key={i}>
                        <td>{humanize(edge.relationship)}</td>
                        <td>{edge.neighbor_labels.map(labelWords).join(", ")}</td>
                        <td className="cell-id">{nodeSummary(edge.neighbor) || "Unnamed"}</td>
                        <td>
                          {id && (
                            <Button
                              variant="quiet"
                              onClick={() => loadNode(edge.neighbor_labels[0], id)}
                              disabled={busy}
                            >
                              Explore
                            </Button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </TableScroll>
          )}
        </section>
      )}
    </main>
  );
}
