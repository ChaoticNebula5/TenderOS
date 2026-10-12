import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  approveImportItem,
  listImportBatches,
  listImportItems,
  rejectImportItem,
  uploadCertifications,
  uploadEmployeeMaster,
  uploadResumes,
} from "../api/client";
import type { ImportBatchResponse, ImportItemDetailResponse } from "../api/client";
import { useAuth } from "../auth/useAuth";
import { BreakAfterUnderscores } from "../components/BreakAfterUnderscores";
import { Button } from "../components/ui/Button";
import { EmptyState } from "../components/ui/EmptyState";
import { Field } from "../components/ui/Field";
import { Notice } from "../components/ui/Notice";
import { StatusBadge } from "../components/ui/StatusBadge";
import { TableScroll } from "../components/ui/TableScroll";
import { formatDate } from "../format/date";
import { describeImportBatch, describeStatus } from "../status/statusMap";
import { importTypeLabel, payloadFields, sourceRefLabel } from "./talentImportValues";
import "./TalentImportReview.css";

// Screen 3 (Part J). Three upload routes feed one shared batch/review
// queue - no live worker in the browser either, so a freshly created
// batch legitimately sits at "queued" (0 items) until the worker picks
// up the KnowledgeDocumentUploaded event; re-selecting the batch (or a
// page refresh) is how progress becomes visible, matching this screen's
// own polling-free design (B14 owns real-time updates project-wide).
//
// Restyle batch 6 - what full-flow.spec.ts reads here (:310-427), and so
// what must not move:
// - :317, :329 getByLabel("Resumes (multiple files):") - a substring
//   match, colon included - then toBeEnabled(): the native file input
//   stays, labelled through Field, disabled while an upload is in flight.
//   All three labels are kept verbatim (decided at plan review).
// - :344-353 the FIRST <table> on the page is the batches table, and its
//   row's <td> texts are read by index: [1] status, [2] total, [3]
//   completed, [4] failed, the numbers through Number(). So the Type cell
//   stays a <td> (a <th> would shift every index), columns keep this
//   order, and the three number cells hold the digits and nothing else.
// - :387 the button "Review items" (substring, strict: one batch).
// - :388-424 `section` that has a heading "Items" -> `tbody tr`, one row
//   per item, each with its own "Approve" button while needs_review. That
//   locator also matches ANY ancestor section of the heading, so the Items
//   <section> is a direct child of <main>, never nested.
// - No skeleton rows in either table: :344 and :397 count `tbody tr` as
//   "loaded". A table shows aria-busy while its fetch is in flight instead.
export function TalentImportReview() {
  const { organizationId } = useAuth();
  const [batches, setBatches] = useState<ImportBatchResponse[] | null>(null);
  // Set once the batch list has answered (or failed), so the empty state
  // only claims "none yet" after the response says so.
  const [batchesLoaded, setBatchesLoaded] = useState(false);
  const [selectedBatchId, setSelectedBatchId] = useState<string | null>(null);
  // Bumped when "Review items" is clicked on the batch that is already open
  // (polish pass, item 18b): the items effect re-runs and refetches. Before,
  // re-selecting the same id changed nothing, so it did not refresh,
  // although this screen's own comment says re-selecting shows progress.
  const [itemsReload, setItemsReload] = useState(0);
  const [items, setItems] = useState<ImportItemDetailResponse[] | null>(null);
  const [itemsLoaded, setItemsLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  // The item whose Approve or Reject is in flight, and which one: that
  // button carries aria-busy, and both of that row's buttons are disabled.
  const [acting, setActing] = useState<{ itemId: string; action: "approve" | "reject" } | null>(
    null,
  );

  const refreshBatches = useCallback(async () => {
    if (!organizationId) return;
    try {
      setBatches(await listImportBatches(organizationId));
    } catch {
      setError("Failed to load import batches");
    } finally {
      setBatchesLoaded(true);
    }
  }, [organizationId]);

  useEffect(() => {
    void refreshBatches();
  }, [refreshBatches]);

  useEffect(() => {
    // Display state only (as the team screen's candidates, batch 4): a
    // newly selected batch shows as loading, not the previous batch's
    // items under its name. Runs again, refetching, on itemsReload.
    setItems(null);
    setItemsLoaded(false);
    if (!selectedBatchId) return;
    let cancelled = false;
    listImportItems(selectedBatchId)
      .then((result) => {
        if (!cancelled) setItems(result);
      })
      .catch(() => {
        if (!cancelled) setError("Failed to load import items");
      })
      .finally(() => {
        if (!cancelled) setItemsLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedBatchId, itemsReload]);

  // Opening a batch, or - on the batch already open - fetching its items
  // again (item 18b).
  function handleReviewItems(batchId: string) {
    if (batchId === selectedBatchId) setItemsReload((n) => n + 1);
    else setSelectedBatchId(batchId);
  }

  async function handleEmployeeMasterUpload(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file || !organizationId) return;
    setError(null);
    setUploading(true);
    try {
      await uploadEmployeeMaster(organizationId, file);
      await refreshBatches();
    } catch (err) {
      setError(err instanceof ApiError ? `Upload failed (${err.status})` : "Upload failed");
    } finally {
      setUploading(false);
      event.target.value = "";
    }
  }

  async function handleCertificationsUpload(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file || !organizationId) return;
    setError(null);
    setUploading(true);
    try {
      await uploadCertifications(organizationId, file);
      await refreshBatches();
    } catch (err) {
      setError(err instanceof ApiError ? `Upload failed (${err.status})` : "Upload failed");
    } finally {
      setUploading(false);
      event.target.value = "";
    }
  }

  async function handleResumesUpload(event: React.ChangeEvent<HTMLInputElement>) {
    const files = event.target.files ? Array.from(event.target.files) : [];
    if (files.length === 0 || !organizationId) return;
    setError(null);
    setUploading(true);
    try {
      await uploadResumes(organizationId, files);
      await refreshBatches();
    } catch (err) {
      setError(err instanceof ApiError ? `Upload failed (${err.status})` : "Upload failed");
    } finally {
      setUploading(false);
      event.target.value = "";
    }
  }

  async function refreshSelectedItems() {
    if (selectedBatchId) setItems(await listImportItems(selectedBatchId));
  }

  async function handleApprove(itemId: string) {
    setError(null);
    setActing({ itemId, action: "approve" });
    try {
      await approveImportItem(itemId);
      await refreshSelectedItems();
    } catch (err) {
      setError(err instanceof ApiError ? `Approve failed (${err.status})` : "Approve failed");
    } finally {
      setActing(null);
    }
  }

  async function handleReject(itemId: string) {
    setError(null);
    setActing({ itemId, action: "reject" });
    try {
      await rejectImportItem(itemId, "Rejected by reviewer");
      await refreshSelectedItems();
    } catch (err) {
      setError(err instanceof ApiError ? `Reject failed (${err.status})` : "Reject failed");
    } finally {
      setActing(null);
    }
  }

  const selectedBatch = batches?.find((b) => b.id === selectedBatchId) ?? null;

  return (
    <main>
      <h1>Talent import and review queue</h1>

      {error && (
        <Notice variant="failed" alert>
          {error}
        </Notice>
      )}

      <section className="talent-import__uploads">
        <h2>Import</h2>
        <div className="talent-import__fields">
          <Field label="Employee master (CSV or XLSX):">
            <input type="file" onChange={handleEmployeeMasterUpload} disabled={uploading} />
          </Field>
          <Field label="Certifications (XLSX):">
            <input type="file" onChange={handleCertificationsUpload} disabled={uploading} />
          </Field>
          <Field label="Resumes (multiple files):">
            <input type="file" multiple onChange={handleResumesUpload} disabled={uploading} />
          </Field>
        </div>
        <p className="talent-import__hint">
          Each upload becomes a batch below. This page does not refresh on its own: reload it to see
          a batch's progress.
        </p>
      </section>

      <section>
        <h2>Batches</h2>
        {batches !== null && batches.length > 0 && (
          <p className="count-line">{batchCountLine(batches)}</p>
        )}
        {/* No sticky last column (measured at 390px with five batches): pinning
            "Review items" left 125px between the sticky columns for a 152px
            column, so that column could never be seen whole. The same rule
            as batch 4's compliance, candidates and findings tables. The
            items table has none either (159px left for a 224px Detail). */}
        <TableScroll label="Import batches">
          <table className="talent-import__batches" aria-busy={!batchesLoaded || undefined}>
            <thead>
              <tr>
                <th>Type</th>
                <th>Status</th>
                <th className="cell-num">Total</th>
                <th className="cell-num">Completed</th>
                <th className="cell-num">Failed</th>
                <th className="cell-actions">
                  <span className="visually-hidden">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {(batches ?? []).map((batch) => (
                <tr
                  key={batch.id}
                  className={batch.id === selectedBatchId ? "is-selected" : undefined}
                  // Polish pass (P8): the open batch is named for assistive
                  // tech too, not only by its tint.
                  aria-current={batch.id === selectedBatchId ? "true" : undefined}
                >
                  <td>{importTypeLabel(batch.import_type)}</td>
                  <td>
                    <StatusBadge {...describeImportBatch(batch.status, batch.failed_items)} />
                  </td>
                  <td className="cell-num">{batch.total_items}</td>
                  <td className="cell-num">{batch.completed_items}</td>
                  <td className="cell-num">{batch.failed_items}</td>
                  <td className="cell-actions">
                    <Button variant="quiet" onClick={() => handleReviewItems(batch.id)}>
                      Review items
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
        {batchesLoaded && batches !== null && batches.length === 0 && (
          <EmptyState title="No import batches yet">
            Upload an employee master, certifications or resumes above to start one.
          </EmptyState>
        )}
      </section>

      {selectedBatchId && (
        <section className="talent-import__items">
          <h2>Items</h2>
          {selectedBatch && (
            <p className="talent-import__batch-meta">
              {importTypeLabel(selectedBatch.import_type)} batch, created{" "}
              {formatDate(selectedBatch.created_at)}
            </p>
          )}
          {items !== null && items.length > 0 && (
            <p className="count-line">{itemCountLine(items)}</p>
          )}
          <TableScroll label="Import items">
            <table className="talent-import__item-table" aria-busy={!itemsLoaded || undefined}>
              <thead>
                <tr>
                  <th>Source</th>
                  <th>Status</th>
                  <th>Detail</th>
                  <th className="cell-actions">
                    <span className="visually-hidden">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {(items ?? []).map((item) => {
                  const busy = acting?.itemId === item.id;
                  return (
                    <tr key={item.id}>
                      <td className="talent-import__source">
                        <BreakAfterUnderscores text={sourceRefLabel(item.source_ref)} />
                      </td>
                      <td>
                        <StatusBadge {...describeStatus("importItem", item.status)} />
                      </td>
                      <td className="talent-import__detail">
                        <ItemDetail item={item} />
                      </td>
                      <td className="cell-actions">
                        {item.status === "needs_review" && (
                          <div className="action-row">
                            <Button
                              variant="quiet"
                              onClick={() => handleApprove(item.id)}
                              disabled={busy}
                              busy={busy && acting?.action === "approve"}
                            >
                              Approve
                            </Button>
                            <Button
                              variant="danger"
                              onClick={() => handleReject(item.id)}
                              disabled={busy}
                              busy={busy && acting?.action === "reject"}
                            >
                              Reject
                            </Button>
                          </div>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </TableScroll>
          {itemsLoaded && items !== null && items.length === 0 && (
            <EmptyState title="No items in this batch yet">
              A queued batch gets its items once the worker picks it up. Select Review items again
              to check.
            </EmptyState>
          )}
        </section>
      )}
    </main>
  );
}

// "4 batches, 1 needs review" - from the response, nothing derived beyond
// counting it (DESIGN.md Screen patterns, "Register tables").
function batchCountLine(batches: ImportBatchResponse[]): string {
  const needsReview = batches.filter((b) => b.status === "needs_review").length;
  const total = `${batches.length} ${batches.length === 1 ? "batch" : "batches"}`;
  return needsReview > 0 ? `${total}, ${needsReview} needs review` : total;
}

function itemCountLine(items: ImportItemDetailResponse[]): string {
  const needsReview = items.filter((i) => i.status === "needs_review").length;
  const total = `${items.length} ${items.length === 1 ? "item" : "items"}`;
  return needsReview > 0 ? `${total}, ${needsReview} needs review` : total;
}

// What the reviewer decides on: the backend's reason (a needs_review
// item's duplicate-match reason, a rejected item's reason, a failed item's
// error) AND the parsed fields - both, decided at plan review; before the
// restyle the reason replaced the fields entirely. Replaces the
// JSON.stringify dump with labelled fields (talentImportValues.ts).
function ItemDetail({ item }: { item: ImportItemDetailResponse }) {
  const fields = payloadFields(item.extracted_payload);
  return (
    <>
      {item.error_message && <p className="talent-import__reason">{item.error_message}</p>}
      {fields.length > 0 && (
        <dl className="talent-import__fields-list">
          {fields.map((field) => (
            <div key={field.key}>
              <dt>{field.label}</dt>
              <dd className={field.kind === "id" ? "talent-import__id" : undefined}>
                {field.kind === "code" ? <code>{field.value}</code> : field.value}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </>
  );
}
