import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
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
import { Button } from "../components/ui/Button";
import { buttonClassName } from "../components/ui/buttonClassName";
import { EmptyState } from "../components/ui/EmptyState";
import { Notice } from "../components/ui/Notice";
import { StatusBadge } from "../components/ui/StatusBadge";
import { TableScroll } from "../components/ui/TableScroll";
import { humanize } from "../format/humanize";
import { labelWords, summarizeImpact } from "../format/impactSummary";
import { describeSelected } from "../status/statusMap";
import "./KnowledgeSelections.css";

// "3 downstream facts affected: Requirement 2, Compliance 1". null when
// there are no rows: the empty response has its own sentence, which the
// E2E reads (full-flow.spec.ts:1254).
function impactSummaryText(rows: GraphImpactRow[]): string | null {
  const { total, byLabel } = summarizeImpact(rows);
  if (total === 0) return null;
  const head = `${total} downstream ${total === 1 ? "fact" : "facts"} affected`;
  if (byLabel.length === 0) return head;
  return `${head}: ${byLabel.map(({ label, count }) => `${labelWords(label)} ${count}`).join(", ")}`;
}

// Screen 10 (Part J). Built against POST /tenders/{id}/graph/impact, not
// the documented-but-never-built POST .../impact-preview - see
// KNOWN_LIMITATIONS.md's own record of that deviation. Functional
// requirement 4 ("impact preview occurs before selection flip") is
// enforced by construction here: previewedItemId only ever gets set by
// handlePreview, and the PUT in handleConfirm only ever fires for
// whichever item is currently previewed - there is no path from row to
// PUT that skips the preview step.
//
// No run status badge: this screen fetches no workflow run, and no restyle
// batch adds one (DESIGN.md Layout, "Tender header status badge").
export function KnowledgeSelections() {
  const { tenderId } = useParams<{ tenderId: string }>();
  const { organizationId } = useAuth();
  const [items, setItems] = useState<KnowledgeItemResponse[]>([]);
  // Set once the library answers, so the empty state only claims "no
  // knowledge" after the response says so (DESIGN.md Copy rules).
  const [itemsLoaded, setItemsLoaded] = useState(false);
  const [selections, setSelections] = useState<KnowledgeSelectionResponse[]>([]);
  const [previewedItemId, setPreviewedItemId] = useState<string | null>(null);
  const [impact, setImpact] = useState<GraphImpactRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!organizationId) return;
    let cancelled = false;
    setItemsLoaded(false);
    listOrganizationKnowledge(organizationId)
      .then((result) => {
        if (cancelled) return;
        setItems(result);
        setItemsLoaded(true);
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

  const summary = impact !== null ? impactSummaryText(impact) : null;

  return (
    <main>
      <h1>Company knowledge selections</h1>

      {error && (
        <Notice variant="failed" alert>
          {error}
        </Notice>
      )}

      {/* E2E contract (full-flow.spec.ts:1182-1216, :1320): the page's first
          table, data rows only in tbody, the item title in the first cell,
          a "Preview impact" button per row. */}
      <TableScroll label="Company knowledge" stickyLastColumn>
        <table className="knowledge" aria-busy={!itemsLoaded || undefined}>
          <thead>
            <tr>
              <th>Title</th>
              <th>Type</th>
              <th>Selected</th>
              <th>
                <span className="visually-hidden">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.id}>
                <td>{item.title}</td>
                <td>{humanize(item.item_type)}</td>
                <td>
                  <StatusBadge {...describeSelected(isSelected(item.id))} />
                </td>
                <td>
                  {/* Every row's button is disabled while busy, as before;
                      the row being previewed or confirmed carries
                      aria-busy. Label unchanged (no participle existed). */}
                  <Button
                    variant="quiet"
                    onClick={() => handlePreview(item.id)}
                    disabled={busy}
                    busy={busy && previewedItemId === item.id}
                  >
                    Preview impact
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>

      {itemsLoaded && items.length === 0 && (
        <EmptyState
          title="No company knowledge yet"
          action={
            <Link to="/talent" className={buttonClassName("secondary")}>
              Go to Talent
            </Link>
          }
        >
          Knowledge items appear here once they are imported and approved in the talent library.
        </EmptyState>
      )}

      {/* E2E contract (full-flow.spec.ts:1222-1327): the "Impact preview"
          heading; :1253 counts every <li> on the page and :1258 JSON.parses
          every `ul li`, so the list is one <li> per row whose textContent is
          EXACTLY JSON.stringify(row) (the <code> wrapper adds no text), and
          nothing else on this page is an <li>. The summary is a <p>. The
          empty sentence is read verbatim (:1254). The confirm label is
          exactly "Confirm select" / "Confirm deselect" (:1323, :1325). A
          spec-forced deviation from DESIGN.md's readable impact list. */}
      {/* Polish pass (P2): the item being previewed is named under the
          heading - a <p>, so the heading's text, every <li> count and the
          `ul li` parse above are untouched; the title comes from the list
          this screen already loaded (no fetch). */}
      {previewedItemId && (
        <section className="impact">
          <h2>Impact preview</h2>
          <p className="impact__item">
            For {items.find((i) => i.id === previewedItemId)?.title ?? "the selected item"}
          </p>
          {impact === null && <p className="impact__loading">Loading...</p>}
          {impact !== null && impact.length === 0 && <p>No downstream facts would be affected.</p>}
          {summary && <p className="impact__summary">{summary}</p>}
          {impact !== null && impact.length > 0 && (
            <ul className="impact__list">
              {impact.map((row, i) => (
                <li key={i}>
                  <code>{JSON.stringify(row)}</code>
                </li>
              ))}
            </ul>
          )}
          {impact !== null && (
            <Button variant="primary" onClick={() => handleConfirm(previewedItemId)} busy={busy}>
              {isSelected(previewedItemId) ? "Confirm deselect" : "Confirm select"}
            </Button>
          )}
        </section>
      )}
    </main>
  );
}
