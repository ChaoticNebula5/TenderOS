import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import {
  ApiError,
  listKnowledgeSelections,
  listOrganizationKnowledge,
  previewGraphImpact,
  setKnowledgeSelection,
  triggerGraphProjection,
} from "../api/client";
import type {
  GraphImpactRow,
  KnowledgeItemResponse,
  KnowledgeSelectionResponse,
} from "../api/client";
import { useAuth } from "../auth/useAuth";

// Screen 10 (Part J). Built against POST /tenders/{id}/graph/impact, not
// the documented-but-never-built POST .../impact-preview - see
// KNOWN_LIMITATIONS.md's own record of that deviation. Functional
// requirement 4 ("impact preview occurs before selection flip") is
// enforced by construction here: previewedItemId only ever gets set by
// handlePreview, and the PUT in handleConfirm only ever fires for
// whichever item is currently previewed - there is no path from row to
// PUT that skips the preview step.
export function KnowledgeSelections() {
  const { tenderId } = useParams<{ tenderId: string }>();
  const { organizationId } = useAuth();
  const [items, setItems] = useState<KnowledgeItemResponse[]>([]);
  const [selections, setSelections] = useState<KnowledgeSelectionResponse[]>([]);
  const [previewedItemId, setPreviewedItemId] = useState<string | null>(null);
  const [impact, setImpact] = useState<GraphImpactRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!organizationId) return;
    let cancelled = false;
    listOrganizationKnowledge(organizationId)
      .then((result) => {
        if (!cancelled) setItems(result);
      })
      .catch(() => {
        if (!cancelled) setError("Failed to load the knowledge library");
      });
    return () => {
      cancelled = true;
    };
  }, [organizationId]);

  async function refreshSelections() {
    if (!tenderId) return;
    setSelections(await listKnowledgeSelections(tenderId));
  }

  useEffect(() => {
    // Unguarded before this - a rejected listKnowledgeSelections left
    // `selections` at its initial [], which isSelected reads as "every
    // item unselected" (wrong state, not a visible failure) rather than
    // showing the error.
    refreshSelections().catch(() => setError("Failed to load the current selections"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenderId]);

  function isSelected(itemId: string): boolean {
    return selections.some((s) => s.knowledge_item_id === itemId && s.selected);
  }

  async function handlePreview(itemId: string) {
    if (!tenderId) return;
    setError(null);
    setBusy(true);
    setPreviewedItemId(itemId);
    setImpact(null);
    try {
      let rows: GraphImpactRow[];
      try {
        rows = await previewGraphImpact(tenderId, itemId);
      } catch (err) {
        // Part I4's own documented retry policy: "graph stale: enqueue
        // projection, retry query once after completion." Retried
        // exactly once, not looped - a second staleness in a row is a
        // real failure, not something to keep silently retrying.
        if (err instanceof ApiError && err.status === 409) {
          await triggerGraphProjection(tenderId, crypto.randomUUID());
          rows = await previewGraphImpact(tenderId, itemId);
        } else {
          throw err;
        }
      }
      setImpact(rows);
    } catch (err) {
      setError(err instanceof ApiError ? `Preview failed (${err.status})` : "Preview failed");
      setPreviewedItemId(null);
    } finally {
      setBusy(false);
    }
  }

  async function handleConfirm(itemId: string) {
    if (!tenderId) return;
    setError(null);
    setBusy(true);
    try {
      await setKnowledgeSelection(
        tenderId,
        itemId,
        !isSelected(itemId),
        "Changed via knowledge selections screen",
        crypto.randomUUID(),
      );
      await refreshSelections();
      setPreviewedItemId(null);
      setImpact(null);
    } catch (err) {
      setError(err instanceof ApiError ? `Selection failed (${err.status})` : "Selection failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main>
      <h1>Company knowledge selections</h1>

      {error && (
        <p role="alert" style={{ color: "var(--color-danger)" }}>
          {error}
        </p>
      )}

      <table>
        <thead>
          <tr>
            <th>Title</th>
            <th>Type</th>
            <th>Selected</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.id}>
              <td>{item.title}</td>
              <td>{item.item_type}</td>
              <td>{isSelected(item.id) ? "yes" : "no"}</td>
              <td>
                <button type="button" onClick={() => handlePreview(item.id)} disabled={busy}>
                  Preview impact
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {previewedItemId && (
        <section>
          <h2>Impact preview</h2>
          {impact === null && <p>Loading...</p>}
          {impact !== null && impact.length === 0 && <p>No downstream facts would be affected.</p>}
          {impact !== null && impact.length > 0 && (
            <ul>
              {impact.map((row, i) => (
                <li key={i}>{JSON.stringify(row)}</li>
              ))}
            </ul>
          )}
          {impact !== null && (
            <button type="button" onClick={() => handleConfirm(previewedItemId)} disabled={busy}>
              {isSelected(previewedItemId) ? "Confirm deselect" : "Confirm select"}
            </button>
          )}
        </section>
      )}
    </main>
  );
}
