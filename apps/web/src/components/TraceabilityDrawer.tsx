import { useEffect, useState } from "react";
import { ApiError, traceProposalClaim } from "../api/client";
import type { ClaimEvidenceTraceResponse, ProposalClaimTraceResponse } from "../api/client";

// Screen 16 (Part J). Rendered inline by whichever screen has a claim_id
// to open (screen 13, currently; screen 9's compliance matrix gets its
// own compliance-trace path in §7 step 10, once view_compliance_trace
// lands - not this component, since ComplianceTraceResponse has no
// currently_in_scope_evidence equivalent, see KNOWN_LIMITATIONS.md's
// [B13] compliance-trace entry). Not a route of its own - claimId is the
// only thing the caller needs to know.
//
// This is the first UI in the project able to show a human, for one
// specific claim, whether supporting evidence existed and simply wasn't
// cited - see proposal_service.py's ProposalClaimTrace docstring for the
// mechanism (currently_in_scope_evidence re-runs the drafting node's own
// scoping logic at view time and excludes anything already in `evidence`,
// so the two groups below never overlap).
interface TraceabilityDrawerProps {
  claimId: string;
  onClose: () => void;
}

export function TraceabilityDrawer({ claimId, onClose }: TraceabilityDrawerProps) {
  const [trace, setTrace] = useState<ProposalClaimTraceResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setTrace(null);
    setError(null);
    traceProposalClaim(claimId)
      .then((result) => {
        if (!cancelled) setTrace(result);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof ApiError ? `Trace failed (${err.status})` : "Trace failed");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [claimId]);

  return (
    <aside
      style={{
        border: "1px solid var(--color-border)",
        borderRadius: "4px",
        padding: "var(--space-4)",
        marginTop: "var(--space-5)",
      }}
    >
      <button type="button" onClick={onClose}>
        Close
      </button>

      {error && (
        <p role="alert" style={{ color: "var(--color-danger)" }}>
          {error}
        </p>
      )}

      {trace === null && !error && <p>Loading trace...</p>}

      {trace !== null && (
        <>
          <h2>Claim trace</h2>
          <p>{trace.claim_text}</p>
          <p>
            Type: {trace.claim_type} · Grounding: {trace.grounding_status} · Section:{" "}
            {trace.section_name}
          </p>

          <h3>Cited</h3>
          {trace.evidence.length === 0 ? (
            <p>No evidence cited.</p>
          ) : (
            <ul>
              {trace.evidence.map((entry) => (
                <EvidenceEntry key={entry.evidence_link_id} entry={entry} />
              ))}
            </ul>
          )}

          {!trace.section_is_data_gated ? (
            <p>This section isn't data-gated.</p>
          ) : trace.currently_in_scope_evidence.length === 0 ? (
            <p>No additional in-scope evidence.</p>
          ) : (
            // Deliberately louder than the "Cited" list above: a non-empty
            // result here means the drafting model had this evidence
            // available for this exact section and chose not to cite it -
            // PRD's own citation-correctness investigation named this the
            // project's largest open quality gap, so it gets a bordered,
            // color-flagged box rather than a plain <ul>, and the count is
            // stated up front instead of left for a human to count rows.
            // var(--color-warning), not var(--color-danger): this is a
            // signal worth a reviewer's attention, not proof of an error -
            // the model may have had good reason to prefer other sources.
            <div
              role="alert"
              style={{
                border: "1px solid var(--color-warning)",
                borderRadius: "4px",
                padding: "var(--space-3)",
                marginTop: "var(--space-2)",
              }}
            >
              <h3 style={{ color: "var(--color-warning)", margin: 0 }}>
                {trace.currently_in_scope_evidence.length} piece
                {trace.currently_in_scope_evidence.length === 1 ? "" : "s"} of in-scope evidence not
                cited
              </h3>
              <ul>
                {trace.currently_in_scope_evidence.map((entry) => (
                  <EvidenceEntry key={entry.evidence_link_id} entry={entry} />
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </aside>
  );
}

function EvidenceEntry({ entry }: { entry: ClaimEvidenceTraceResponse }) {
  return (
    <li>
      {entry.source_kind}: {entry.excerpt ?? "(no excerpt)"} —{" "}
      {entry.knowledge_item_title ?? entry.document_filename ?? "unknown source"} (
      {entry.verified ? "verified" : "unverified"}, {entry.validity_status})
    </li>
  );
}
