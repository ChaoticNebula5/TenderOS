import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import {
  ApiError,
  listGates,
  listPricingDocuments,
  listPricingFindings,
  listWorkflowRuns,
  resolvePricingFinding,
  validatePricingDocument,
} from "../api/client";
import type {
  GateResponse,
  OperationResponse,
  PricingDocumentResponse,
  PricingFindingResponse,
} from "../api/client";
import { GateDecisionPanel } from "../components/GateDecisionPanel";
import { refreshPendingGate } from "../gates/refreshGate";
import { Button } from "../components/ui/Button";
import { EmptyState } from "../components/ui/EmptyState";
import { Notice } from "../components/ui/Notice";
import { StatusBadge } from "../components/ui/StatusBadge";
import { TableScroll } from "../components/ui/TableScroll";
import { humanize } from "../format/humanize";
import { useListReads } from "../hooks/useListReads";
import { useTenderOperation } from "../hooks/useTenderOperation";
import { useReportRunStatus } from "../shell/runStatus";
import { describeFinding } from "../status/statusMap";
import "./CommercialFindings.css";

// Screen 12 (Part J). Same gate-lookup shape as screens 8/9: most recent
// workflow run -> its commercial_approval gate. validate_pricing_document
// is the manual re-validation trigger (G9) - re-runs the check across
// the whole tender, not just one document.
//
// Money (restyle batch 4): a finding's `delta` is the only structured
// amount, and it is in the pricing sheet's own currency (pricing_checks.py),
// not INR - the fixture's sheets are USD and EUR. It stays unshown rather
// than be labelled with a currency it is not in (DESIGN.md Copy rules,
// decision 8). Amounts inside `note` are backend prose and stay as written.
export function CommercialFindings() {
  const { tenderId } = useParams<{ tenderId: string }>();
  const [documents, setDocuments] = useState<PricingDocumentResponse[]>([]);
  const [findings, setFindings] = useState<PricingFindingResponse[]>([]);
  // Set once each list answers, so an empty state only claims "none" after
  // the response says so (DESIGN.md Copy rules).
  const [documentsLoaded, setDocumentsLoaded] = useState(false);
  const [findingsLoaded, setFindingsLoaded] = useState(false);
  // A resolve can land while the re-read after an operation is in flight;
  // that read's answer must not undo it (useListReads.ts).
  const findingReads = useListReads();
  const [gate, setGate] = useState<GateResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The latest run's status, from the listWorkflowRuns call this screen
  // already makes for the gate - reported to the tender bar. No extra fetch.
  const [runStatus, setRunStatus] = useState<string | null>(null);
  useReportRunStatus(runStatus);
  // handleValidate's own guard, not the mount effect's - see below.
  const tenderIdRef = useRef(tenderId);
  tenderIdRef.current = tenderId;

  async function refreshFindings(isCancelled: () => boolean) {
    if (!tenderId) return;
    const startedAt = findingReads.start();
    try {
      const result = await listPricingFindings(tenderId);
      if (!isCancelled()) {
        setFindings((current) => findingReads.merge(startedAt, result, current));
        setFindingsLoaded(true);
      }
    } catch {
      if (!isCancelled()) setError("Failed to load pricing findings");
    }
  }

  useEffect(() => {
    if (!tenderId) return;
    let cancelled = false;
    setDocumentsLoaded(false);
    setFindingsLoaded(false);
    listPricingDocuments(tenderId)
      .then((result) => {
        if (cancelled) return;
        setDocuments(result);
        setDocumentsLoaded(true);
      })
      .catch(() => {
        if (!cancelled) setError("Failed to load pricing documents");
      });
    void refreshFindings(() => cancelled);
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenderId]);

  useEffect(() => {
    if (!tenderId) return;
    let cancelled = false;
    // A stale gate from the PREVIOUS tender must not stay visible (and
    // actionable) while this tender's own lookup is still in flight, or
    // if this tender turns out to have no matching gate at all.
    setGate(null);
    // Same for the run status shown in the tender bar.
    setRunStatus(null);
    listWorkflowRuns(tenderId)
      .then(async (runs) => {
        if (cancelled || runs.length === 0) return;
        setRunStatus(runs[0].status);
        const gates = await listGates(runs[0].id);
        const commercialGate = gates.find((g) => g.gate_type === "commercial_approval");
        if (!cancelled && commercialGate) setGate(commercialGate);
      })
      .catch(() => {
        if (!cancelled) setError("Failed to load the commercial approval gate");
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

  // Once the operation is finished, and before the buttons are released:
  // re-read the findings, and on failure show the operation's own message.
  // useTenderOperation only calls this for the tender on screen; the
  // findings read itself is guarded the same way as the mount effect's,
  // against tenderIdRef.current (kept fresh by the render-time assignment
  // above), in case the user moves to another tender while it is in flight.
  async function handleFinished(operation: OperationResponse) {
    const finishedForTenderId = tenderId;
    if (operation.status === "failed") {
      setError(operation.error_message ?? "Validation failed");
    }
    await refreshFindings(() => tenderIdRef.current !== finishedForTenderId);
    // Plan item 73: the gate's live counts follow this change.
    void refreshPendingGate(gate, setGate);
  }
  // Phase 4 Stage 2: one re-validation is one tender-wide operation. It
  // re-checks every pricing document of the tender together (cross-document
  // checks need every sheet), whichever row's Re-validate started it, and a
  // click on another row while it runs joins it (observed on the fake stack,
  // 2026-10-16: two documents' POSTs, one operation, findings on both). So
  // every row shows busy while it runs, after a click and after a reload
  // alike.
  const { busy: validating, start } = useTenderOperation(
    tenderId,
    "commercial_validation",
    handleFinished,
  );

  async function handleValidate(pricingDocumentId: string) {
    setError(null);
    // Captured now, compared against tenderIdRef.current once the POST
    // resolves: a rejection arriving after the user has navigated to a
    // different tender must not paint THIS tender's failure onto that
    // other tender's screen. (useTenderOperation applies the same check
    // before following the operation the POST returns.)
    const startedForTenderId = tenderId;
    try {
      await start(() => validatePricingDocument(pricingDocumentId));
    } catch (err) {
      if (tenderIdRef.current === startedForTenderId) {
        setError(
          err instanceof ApiError ? `Validation failed (${err.status})` : "Validation failed",
        );
      }
    }
  }

  async function handleResolve(findingId: string) {
    setError(null);
    try {
      const updated = await resolvePricingFinding(findingId);
      findingReads.changed(findingId);
      setFindings((prev) => prev.map((f) => (f.id === findingId ? updated : f)));
      // Plan item 73: the gate's live counts follow this change.
      void refreshPendingGate(gate, setGate);
    } catch (err) {
      setError(err instanceof ApiError ? `Resolve failed (${err.status})` : "Resolve failed");
    }
  }

  const openCount = findings.filter((f) => !f.resolved).length;

  // Heading and panel always render together, as siblings
  // (full-flow.spec.ts (gatePanel())). Above the tables while pending (DESIGN.md
  // Screen patterns, "Gates"), below them otherwise. The panel holds no
  // <table>, so the findings table stays table index 1 either way.
  const gateBlock = (
    <>
      <h2>Commercial approval gate</h2>
      {gate ? (
        <GateDecisionPanel gate={gate} onDecided={handleGateDecided} />
      ) : (
        <p>No open commercial approval gate for this tender yet.</p>
      )}
    </>
  );
  const gateFirst = gate?.status === "pending";

  return (
    <main>
      <h1>Commercial findings</h1>

      {error && (
        <Notice variant="failed" alert>
          {error}
        </Notice>
      )}

      {gateFirst && gateBlock}

      {/* E2E contract: BOTH tables always render, documents first, even when
          empty - full-flow.spec.ts (step 8, pricing findings) and step 12, resolve findings reach the findings table by
          position, page.locator("table").nth(1). An empty state replaces
          neither table; it sits after it. */}
      <section>
        <h2>Pricing documents</h2>
        <TableScroll label="Pricing documents" stickyLastColumn>
          <table className="pricing-documents" aria-busy={!documentsLoaded || undefined}>
            <thead>
              <tr>
                <th>Sheet</th>
                <th>Currency</th>
                <th className="cell-actions">
                  <span className="visually-hidden">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {documents.map((doc) => (
                <tr key={doc.id}>
                  <td>{doc.sheet_name}</td>
                  <td>{doc.currency ?? "Not stated"}</td>
                  <td className="cell-actions">
                    <Button
                      variant="quiet"
                      onClick={() => handleValidate(doc.id)}
                      busy={validating}
                      busyLabel="Re-validating..."
                    >
                      Re-validate
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
        {documentsLoaded && documents.length === 0 && (
          <EmptyState title="No pricing documents">
            Pricing documents appear here when an uploaded pricing schedule is parsed.
          </EmptyState>
        )}
      </section>

      {/* step 8, pricing findings counts this tbody's rows one-shot, so it holds data rows only;
          :1059-1076 find Resolve inside it and wait for each to be REMOVED
          (count goes down by one), so a resolved row has no button at all. */}
      <section>
        <h2>Findings</h2>
        {findings.length > 0 && (
          <p className="count-line">
            {findings.length} {findings.length === 1 ? "finding" : "findings"}, {openCount} open
          </p>
        )}
        {/* No stickyLastColumn (measured at 390px, batch 4): pinning the
            actions column left less room between the two sticky columns than
            the Note column needs, even with every finding resolved, so it
            could never be seen whole. The actions scroll instead. */}
        <TableScroll label="Findings">
          <table className="findings" aria-busy={!findingsLoaded || undefined}>
            <thead>
              <tr>
                <th>Type</th>
                <th>Status</th>
                <th>Note</th>
                <th className="cell-actions">
                  <span className="visually-hidden">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {findings.map((finding) => (
                <tr
                  key={finding.id}
                  className={finding.resolved ? "findings__row--resolved" : undefined}
                >
                  <td>{humanize(finding.finding_type)}</td>
                  <td>
                    {/* DESIGN.md:296-299: resolved shows "Resolved" whatever
                        the severity; open shows its severity badge. */}
                    <StatusBadge {...describeFinding(finding.resolved, finding.severity)} />
                  </td>
                  <td className="findings__note">
                    <span className="findings__note-text">{finding.note}</span>
                  </td>
                  <td className="cell-actions">
                    {!finding.resolved && (
                      <Button variant="quiet" onClick={() => handleResolve(finding.id)}>
                        Resolve
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
        {findingsLoaded && findings.length === 0 && (
          <EmptyState title="No pricing findings">
            Findings appear here when the pricing checks run on this tender's pricing documents.
          </EmptyState>
        )}
      </section>

      {!gateFirst && gateBlock}
    </main>
  );
}
