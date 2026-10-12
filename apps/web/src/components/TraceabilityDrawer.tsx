import { useEffect, useRef, useState } from "react";
import { ApiError, traceProposalClaim } from "../api/client";
import type { ClaimEvidenceTraceResponse, ProposalClaimTraceResponse } from "../api/client";
import { humanize } from "../format/humanize";
import { describeStatus, describeVerified } from "../status/statusMap";
import { Button } from "./ui/Button";
import { StatusIcon } from "./ui/icons";
import { Notice } from "./ui/Notice";
import { StatusBadge } from "./ui/StatusBadge";
import "./TraceabilityDrawer.css";

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
//
// Restyle batch 5 (DESIGN.md "Traceability drawer"): a modal side panel -
// right edge on wide screens, full screen at phone width - with a scrim.
// Focus moves to Close on open, Tab and Shift+Tab stay inside, Escape or a
// click on the scrim closes, and focus returns to `returnFocusTo` (the "Inspect trace" button
// that opened it). Modal is safe for the E2E: after opening, every locator
// in full-flow.spec.ts:980-991 is scoped to this <aside>, and the next
// action on that page is a navigation (:1029) - nothing outside it is
// touched while it is open.
//
// E2E contract: the only <aside> on the page (:980); the "Claim trace"
// heading renders only once the trace has loaded (:981 waits for it, then
// :985-987 read the in-scope result immediately - a heading shown during
// loading would let that read run before the result exists); the in-scope
// warning stays role="alert" containing "not cited" (:982); the empty
// sentence stays verbatim (:983).
interface TraceabilityDrawerProps {
  claimId: string;
  onClose: () => void;
  returnFocusTo?: HTMLElement | null;
}

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function TraceabilityDrawer({ claimId, onClose, returnFocusTo }: TraceabilityDrawerProps) {
  const [trace, setTrace] = useState<ProposalClaimTraceResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const panelRef = useRef<HTMLElement>(null);
  // The latest onClose, read by the document listener below, so a parent
  // re-render with a new callback never leaves the listener calling a
  // stale one.
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

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

  // Focus in on open; back to the opener on close. The opener is captured
  // once, at open, so a later re-render cannot redirect it.
  useEffect(() => {
    const opener = returnFocusTo ?? (document.activeElement as HTMLElement | null);
    panelRef.current?.querySelector<HTMLElement>(".trace-drawer__close")?.focus();
    return () => {
      if (opener && opener.isConnected) opener.focus();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Escape and the Tab trap listen on the document, not on the panel: a
  // click on the scrim (or anywhere outside) moves focus to <body>, and a
  // panel-level handler would then never hear Escape and let Tab walk into
  // the page behind. Focus outside the panel is pulled back in (first
  // element on Tab, last on Shift+Tab); inside, Tab wraps at the ends.
  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      const panel = panelRef.current;
      if (event.key !== "Tab" || !panel) return;
      const focusable = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (!active || !panel.contains(active)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      } else if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, []);

  return (
    <>
      {/* Visual only for assistive tech (aria-hidden); a pointer click on it
          closes the drawer, as on any modal backdrop. */}
      <div className="trace-drawer__scrim" aria-hidden="true" onClick={onClose} />
      <aside
        ref={panelRef}
        className="trace-drawer"
        role="dialog"
        aria-modal="true"
        aria-label="Claim trace"
      >
        <div className="trace-drawer__bar">
          <Button variant="quiet" className="trace-drawer__close" onClick={onClose}>
            Close
          </Button>
        </div>

        {error && (
          <Notice variant="failed" alert>
            {error}
          </Notice>
        )}

        {trace === null && !error && <p className="trace-drawer__loading">Loading trace...</p>}

        {trace !== null && (
          <>
            <h2>Claim trace</h2>
            <p className="trace-drawer__claim">{trace.claim_text}</p>
            <dl className="trace-drawer__meta">
              <div>
                <dt>Type</dt>
                <dd>{humanize(trace.claim_type)}</dd>
              </div>
              <div>
                <dt>Grounding</dt>
                <dd>
                  <StatusBadge {...describeStatus("grounding", trace.grounding_status)} />
                </dd>
              </div>
              <div>
                <dt>Section</dt>
                <dd>{trace.section_name}</dd>
              </div>
            </dl>

            <h3>Cited</h3>
            {trace.evidence.length === 0 ? (
              <p className="trace-drawer__none">No evidence cited.</p>
            ) : (
              <ul className="evidence-list">
                {trace.evidence.map((entry) => (
                  <EvidenceEntry key={entry.evidence_link_id} entry={entry} />
                ))}
              </ul>
            )}

            {!trace.section_is_data_gated ? (
              <p className="trace-drawer__none">This section isn't data-gated.</p>
            ) : trace.currently_in_scope_evidence.length === 0 ? (
              <p className="trace-drawer__none">No additional in-scope evidence.</p>
            ) : (
              // Deliberately louder than the "Cited" list above: a non-empty
              // result here means the drafting model had this evidence
              // available for this exact section and chose not to cite it -
              // PRD's own citation-correctness investigation named this the
              // project's largest open quality gap, so it gets a flagged
              // panel rather than a plain list, and the count is stated up
              // front instead of left for a human to count rows. Warning,
              // not failed: a signal worth a reviewer's attention, not
              // proof of an error - the model may have had good reason to
              // prefer other sources.
              <div role="alert" className="trace-drawer__uncited">
                <h3>
                  <StatusIcon tone="warning" className="trace-drawer__uncited-icon" />
                  {trace.currently_in_scope_evidence.length} piece
                  {trace.currently_in_scope_evidence.length === 1 ? "" : "s"} of in-scope evidence
                  not cited
                </h3>
                <ul className="evidence-list">
                  {trace.currently_in_scope_evidence.map((entry) => (
                    <EvidenceEntry key={entry.evidence_link_id} entry={entry} />
                  ))}
                </ul>
              </div>
            )}
          </>
        )}
      </aside>
    </>
  );
}

// DESIGN.md Screen patterns, "Evidence view": the excerpt in a sunk well,
// the source as ink-2 metadata, verified and validity as badges - never a
// raw `source_kind:` prefix.
function EvidenceEntry({ entry }: { entry: ClaimEvidenceTraceResponse }) {
  const source = entry.knowledge_item_title ?? entry.document_filename ?? "Unknown source";
  return (
    <li className="evidence">
      <p className="evidence__excerpt">{entry.excerpt ?? "No excerpt recorded."}</p>
      <p className="evidence__source">
        {humanize(entry.source_kind)} · {source}
      </p>
      <p className="evidence__badges">
        <StatusBadge {...describeVerified(entry.verified, false)} />
        <StatusBadge {...describeStatus("validity", entry.validity_status)} />
      </p>
    </li>
  );
}
