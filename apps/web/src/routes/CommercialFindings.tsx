import { useEffect, useState } from "react";
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
import type { GateResponse, PricingDocumentResponse, PricingFindingResponse } from "../api/client";
import { GateDecisionPanel } from "../components/GateDecisionPanel";

// Screen 12 (Part J). Same gate-lookup shape as screens 8/9: most recent
// workflow run -> its commercial_approval gate. validate_pricing_document
// is the manual re-validation trigger (G9) - re-runs the check across
// the whole tender, not just one document.
export function CommercialFindings() {
  const { tenderId } = useParams<{ tenderId: string }>();
  const [documents, setDocuments] = useState<PricingDocumentResponse[]>([]);
  const [findings, setFindings] = useState<PricingFindingResponse[]>([]);
  const [gate, setGate] = useState<GateResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [validating, setValidating] = useState(false);

  async function refreshFindings(isCancelled: () => boolean) {
    if (!tenderId) return;
    try {
      const result = await listPricingFindings(tenderId);
      if (!isCancelled()) setFindings(result);
    } catch {
      if (!isCancelled()) setError("Failed to load pricing findings");
    }
  }

  useEffect(() => {
    if (!tenderId) return;
    let cancelled = false;
    listPricingDocuments(tenderId)
      .then((result) => {
        if (!cancelled) setDocuments(result);
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
    listWorkflowRuns(tenderId)
      .then(async (runs) => {
        if (cancelled || runs.length === 0) return;
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

  async function handleValidate(pricingDocumentId: string) {
    setError(null);
    setValidating(true);
    try {
      await validatePricingDocument(pricingDocumentId);
      await refreshFindings(() => false);
    } catch (err) {
      setError(err instanceof ApiError ? `Validation failed (${err.status})` : "Validation failed");
    } finally {
      setValidating(false);
    }
  }

  async function handleResolve(findingId: string) {
    setError(null);
    try {
      const updated = await resolvePricingFinding(findingId);
      setFindings((prev) => prev.map((f) => (f.id === findingId ? updated : f)));
    } catch (err) {
      setError(err instanceof ApiError ? `Resolve failed (${err.status})` : "Resolve failed");
    }
  }

  return (
    <main>
      <h1>Commercial findings</h1>

      {error && (
        <p role="alert" style={{ color: "var(--color-danger)" }}>
          {error}
        </p>
      )}

      <section>
        <h2>Pricing documents</h2>
        <table>
          <thead>
            <tr>
              <th>Sheet</th>
              <th>Currency</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {documents.map((doc) => (
              <tr key={doc.id}>
                <td>{doc.sheet_name}</td>
                <td>{doc.currency ?? "-"}</td>
                <td>
                  <button
                    type="button"
                    onClick={() => handleValidate(doc.id)}
                    disabled={validating}
                  >
                    Re-validate
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section>
        <h2>Findings</h2>
        <table>
          <thead>
            <tr>
              <th>Type</th>
              <th>Severity</th>
              <th>Note</th>
              <th>Resolved</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {findings.map((finding) => (
              <tr key={finding.id}>
                <td>{finding.finding_type}</td>
                <td>{finding.severity}</td>
                <td>{finding.note}</td>
                <td>{finding.resolved ? "yes" : "no"}</td>
                <td>
                  {!finding.resolved && (
                    <button type="button" onClick={() => handleResolve(finding.id)}>
                      Resolve
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <h2>Commercial approval gate</h2>
      {gate ? (
        <GateDecisionPanel gate={gate} onDecided={setGate} />
      ) : (
        <p>No open commercial approval gate for this tender yet.</p>
      )}
    </main>
  );
}
