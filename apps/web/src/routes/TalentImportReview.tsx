import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  ApiError,
  approveImportItem,
  importItemAsNewEmployee,
  listCertificateHolders,
  listImportBatches,
  listImportItems,
  rejectImportItem,
  uploadCertifications,
  uploadEmployeeMaster,
  uploadResumes,
} from "../api/client";
import type {
  CertificateHolder,
  ImportBatchResponse,
  ImportItemCandidate,
  ImportItemDetailResponse,
  ImportItemFieldChange,
  ImportItemTarget,
  MasterApplyField,
  MasterCandidate,
  MasterTarget,
} from "../api/client";
import { formatErrorDetail, readEnvelopeCode } from "../api/formatErrorDetail";
import { useAuth } from "../auth/useAuth";
import { BreakAfterUnderscores } from "../components/BreakAfterUnderscores";
import { PollStoppedNotice } from "../components/PollStoppedNotice";
import { Button } from "../components/ui/Button";
import { EmptyState } from "../components/ui/EmptyState";
import { Field } from "../components/ui/Field";
import { Notice } from "../components/ui/Notice";
import { StatusBadge } from "../components/ui/StatusBadge";
import { TableScroll } from "../components/ui/TableScroll";
import { formatDate } from "../format/date";
import { useBoundedPoll } from "../hooks/useBoundedPoll";
import { describeImportBatch, describeStatus } from "../status/statusMap";
import {
  NOT_SET,
  formatYears,
  importTypeLabel,
  payloadFields,
  sourceRefLabel,
} from "./talentImportValues";
import "./TalentImportReview.css";

// Screen 3 (Part J). Three upload routes feed one shared batch/review
// queue. A freshly created batch sits at "queued" (0 items) until the
// worker picks up the KnowledgeDocumentUploaded event. Plan item 78: the
// page follows a batch's progress on its own (it said "reload it to see a
// batch's progress", observed in the item 66 walk). No event stream covers
// imports, so while any batch is queued or processing the batch list, and
// the open batch's items, are read again, bounded (useBoundedPoll.ts).
//
// Restyle batch 6 - what full-flow.spec.ts reads here (setup's résumé import), and so
// what must not move:
// - setup's résumé import getByLabel("Resumes (multiple files):") - a substring
//   match, colon included - then toBeEnabled(): the native file input
//   stays, labelled through Field, disabled while an upload is in flight.
//   All three labels are kept verbatim (decided at plan review).
// - setup's résumé import the FIRST <table> on the page is the batches table, and its
//   row's <td> texts are read by index: [1] status, [2] total, [3]
//   completed, [4] failed, the numbers through Number(). So the Type cell
//   stays a <td> (a <th> would shift every index), columns keep this
//   order, and the three number cells hold the digits and nothing else.
// - setup's résumé import the button "Review items" (substring, strict: one batch).
// - setup's résumé import `section` that has a heading "Items" -> `tbody tr`, one row
//   per item, each with its own "Approve" button while needs_review. That
//   locator also matches ANY ancestor section of the heading, so the Items
//   <section> is a direct child of <main>, never nested. That "Approve" is a
//   substring match, so plan item 111's buttons ("Merge into NAME", "Import
//   as a new person") never contain the word.
// - A batch's status is computed from its items on every read
//   (knowledge_service._compute_batch_progress), so after an Approve or
//   Reject the batch list is read again too (item 66 walk: the batch row
//   said "Needs review" after the last item was rejected).
// - No skeleton rows in either table: setup's résumé import and setup's résumé import count `tbody tr` as
//   "loaded". A table shows aria-busy while its fetch is in flight instead.
// Plan item 78: a batch in one of these is still being imported, so the
// page keeps reading (ck_knowledge_import_batch_status; needs_review,
// completed and failed wait on nothing but a reviewer).
const UNFINISHED_BATCH_STATUSES = new Set(["queued", "processing"]);

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
  const [acting, setActing] = useState<{
    itemId: string;
    action: "approve" | "reject" | "pick" | "new" | "apply" | "record";
    employeeId?: string;
  } | null>(null);
  // Plan item 112: a certification batch's holder choice (the organization's
  // live employees) and the holder chosen on each row.
  const [holders, setHolders] = useState<CertificateHolder[] | null>(null);
  const [chosenHolder, setChosenHolder] = useState<Record<string, string>>({});
  const selectedImportType = batches?.find((b) => b.id === selectedBatchId)?.import_type ?? null;
  const certificationBatch = selectedImportType === "certification_documents";

  // `quiet` for the poll's reads: a failed one shows no error (the rows
  // stay, and the next read tries again).
  const refreshBatches = useCallback(
    async (quiet = false) => {
      if (!organizationId) return;
      try {
        setBatches(await listImportBatches(organizationId));
      } catch {
        if (!quiet) setError("Failed to load import batches");
      } finally {
        setBatchesLoaded(true);
      }
    },
    [organizationId],
  );

  // Plan item 78: the open batch's items are read from three places (the
  // poll, and after an Approve or Reject), and a read can answer out of
  // order. An answer is applied only if no later read's answer has been,
  // and only while its batch is still the one open: a poll read sent before
  // an Approve must not bring back the Approve button the refresh after it
  // removed. The same rule as useEventStream.ts's reads.
  const openBatchId = useRef<string | null>(null);
  openBatchId.current = selectedBatchId;
  const itemReads = useRef({ started: 0, applied: 0 });
  const readOpenBatchItems = useCallback(async (batchId: string) => {
    const read = ++itemReads.current.started;
    const result = await listImportItems(batchId);
    if (read > itemReads.current.applied && openBatchId.current === batchId) {
      itemReads.current.applied = read;
      setItems(result);
    }
  }, []);

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

  // Plan item 112: a certification batch's holders, read with its items.
  useEffect(() => {
    setHolders(null);
    if (!selectedBatchId || !certificationBatch) return;
    let cancelled = false;
    listCertificateHolders(selectedBatchId)
      .then((result) => {
        if (!cancelled) setHolders(result);
      })
      .catch(() => {
        if (!cancelled) setError("Failed to load the employees a certificate can be given to");
      });
    return () => {
      cancelled = true;
    };
  }, [selectedBatchId, certificationBatch, itemsReload]);

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

  // After an Approve or Reject: the item's row, and its batch's status,
  // which the server computes from the items.
  async function refreshSelectedItems() {
    if (selectedBatchId) await readOpenBatchItems(selectedBatchId);
    if (selectedBatchId && certificationBatch) {
      setHolders(await listCertificateHolders(selectedBatchId));
    }
    void refreshBatches(true);
  }

  // Plan item 80: a resume merge sends the fingerprint of the employee
  // values this row shows. If the employee changed since (409
  // IMPORT_ITEM_TARGET_CHANGED), nothing was merged: the items are read
  // again, so the row shows the current values before a second Approve.
  async function handleApprove(item: ImportItemDetailResponse) {
    const itemId = item.id;
    setError(null);
    setActing({ itemId, action: "approve" });
    try {
      // Plan item 112: a master item sends its master_target's fingerprint.
      await approveImportItem(
        itemId,
        item.target_employee?.fingerprint ?? item.master_target?.fingerprint ?? null,
      );
      await refreshSelectedItems();
    } catch (err) {
      const code = err instanceof ApiError ? readEnvelopeCode(err.body) : null;
      if (code === "IMPORT_ITEM_TARGET_CHANGED") {
        setError(
          "The employee changed after this item was loaded. The row now shows the current " +
            "values; review them, then approve again.",
        );
        await refreshSelectedItems().catch(() => {
          // The error above stays; Review items reads them again.
        });
      } else if (code !== null) {
        setError(`Approve failed: ${formatErrorDetail(err)}`);
      } else {
        setError(err instanceof ApiError ? `Approve failed (${err.status})` : "Approve failed");
      }
    } finally {
      setActing(null);
    }
  }

  // Plan item 111: a refusal because the employee or the candidate list
  // changed since this row was loaded wrote nothing; the items are read
  // again so the row shows the current list before a second decision.
  async function handleDecisionError(err: unknown, what: string) {
    const code = err instanceof ApiError ? readEnvelopeCode(err.body) : null;
    if (code === "IMPORT_ITEM_TARGET_CHANGED" || code === "IMPORT_ITEM_CANDIDATES_CHANGED") {
      setError(
        "The matching employees changed after this item was loaded. The row now shows the " +
          "current values; review them, then decide again.",
      );
      await refreshSelectedItems().catch(() => {
        // The error above stays; Review items reads them again.
      });
    } else if (code !== null) {
      setError(`${what} failed: ${formatErrorDetail(err)}`);
    } else {
      setError(err instanceof ApiError ? `${what} failed (${err.status})` : `${what} failed`);
    }
  }

  async function handlePick(item: ImportItemDetailResponse, candidate: ImportItemCandidate) {
    if (item.candidates === null) return;
    setError(null);
    setActing({ itemId: item.id, action: "pick", employeeId: candidate.knowledge_item_id });
    try {
      await approveImportItem(item.id, candidate.fingerprint, {
        employeeId: candidate.knowledge_item_id,
        candidatesFingerprint: item.candidates.fingerprint,
      });
      await refreshSelectedItems();
    } catch (err) {
      await handleDecisionError(err, "Merge");
    } finally {
      setActing(null);
    }
  }

  // Plan item 112: the master row applied to a chosen candidate.
  async function handleMasterPick(item: ImportItemDetailResponse, candidate: MasterCandidate) {
    if (item.master_candidates === null) return;
    setError(null);
    setActing({ itemId: item.id, action: "apply", employeeId: candidate.knowledge_item_id });
    try {
      await approveImportItem(item.id, candidate.fingerprint, {
        employeeId: candidate.knowledge_item_id,
        candidatesFingerprint: item.master_candidates.fingerprint,
      });
      await refreshSelectedItems();
    } catch (err) {
      await handleDecisionError(err, "Apply");
    } finally {
      setActing(null);
    }
  }

  // Plan item 112: the certificate recorded for the chosen holder, with the
  // holder's fingerprint.
  async function handleRecordCertificate(item: ImportItemDetailResponse) {
    const holder = holders?.find((h) => h.knowledge_item_id === chosenHolder[item.id]);
    if (holder === undefined) return;
    setError(null);
    setActing({ itemId: item.id, action: "record" });
    try {
      await approveImportItem(item.id, holder.fingerprint, {
        employeeId: holder.knowledge_item_id,
      });
      await refreshSelectedItems();
    } catch (err) {
      await handleDecisionError(err, "Recording the certificate");
    } finally {
      setActing(null);
    }
  }

  async function handleImportAsNew(item: ImportItemDetailResponse) {
    const fingerprint = item.candidates?.fingerprint ?? item.master_candidates?.fingerprint;
    if (fingerprint === undefined) return;
    setError(null);
    setActing({ itemId: item.id, action: "new" });
    try {
      await importItemAsNewEmployee(item.id, fingerprint);
      await refreshSelectedItems();
    } catch (err) {
      await handleDecisionError(err, "Import as a new person");
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
  // Plan item 80: a resume item in review merges into the employee it
  // names, so one naming none can only be rejected (Approve hidden), as
  // can one whose changes cannot be shown (target_employee.changes null).
  const resumeBatch = selectedBatch?.import_type === "bulk_resumes";
  // Plan item 112: a master item applies its row to the employee it names
  // (Approve, with the change list), or to a candidate; a certification
  // item is recorded for a chosen holder (no Approve).
  const masterBatch =
    selectedBatch?.import_type === "employee_master_csv" ||
    selectedBatch?.import_type === "employee_master_xlsx";

  function approvable(item: ImportItemDetailResponse): boolean {
    if (resumeBatch) return item.target_employee !== null && item.target_employee.changes !== null;
    if (masterBatch) return item.master_target !== null && item.master_target.changes !== null;
    return !certificationBatch;
  }

  // Plan item 78: the open batch's items are read again too while that
  // batch is unfinished, including once more on the read that finds it
  // finished, so its last items show.
  const openBatchUnfinished =
    selectedBatch !== null && UNFINISHED_BATCH_STATUSES.has(selectedBatch.status);
  const batchPoll = useBoundedPoll(
    async () => {
      await refreshBatches(true);
      if (selectedBatchId !== null && openBatchUnfinished) {
        await readOpenBatchItems(selectedBatchId).catch(() => {
          // As the batch read: quiet, the next read tries again.
        });
      }
    },
    (batches ?? []).some((b) => UNFINISHED_BATCH_STATUSES.has(b.status)),
    (batches ?? [])
      .map((b) => `${b.id}:${b.status}:${b.total_items}:${b.completed_items}:${b.failed_items}`)
      .join(","),
  );

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
          Each upload becomes a batch below, and its progress shows there as the import runs.
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
        {batchPoll.stopped && (
          <PollStoppedNotice what="import progress" onCheckAgain={batchPoll.checkAgain} />
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
                        <ItemDetail
                          item={item}
                          resumeBatch={resumeBatch}
                          masterBatch={masterBatch}
                        />
                        {item.status === "needs_review" && item.candidates !== null && (
                          <ResumeCandidates
                            item={item}
                            busy={busy}
                            acting={acting}
                            onPick={(candidate) => handlePick(item, candidate)}
                            onImportAsNew={() => handleImportAsNew(item)}
                          />
                        )}
                        {item.status === "needs_review" && item.master_candidates !== null && (
                          <MasterCandidates
                            item={item}
                            busy={busy}
                            acting={acting}
                            onPick={(candidate) => handleMasterPick(item, candidate)}
                            onImportAsNew={() => handleImportAsNew(item)}
                          />
                        )}
                        {item.status === "needs_review" && certificationBatch && (
                          <CertificateHolderChoice
                            item={item}
                            holders={holders}
                            chosen={chosenHolder[item.id] ?? ""}
                            busy={busy}
                            recording={busy && acting?.action === "record"}
                            onChoose={(holderId) =>
                              setChosenHolder((chosen) => ({ ...chosen, [item.id]: holderId }))
                            }
                            onRecord={() => handleRecordCertificate(item)}
                          />
                        )}
                      </td>
                      <td className="cell-actions">
                        {item.status === "needs_review" && (
                          <div className="action-row">
                            {approvable(item) && (
                              <Button
                                variant="quiet"
                                onClick={() => handleApprove(item)}
                                disabled={busy}
                                busy={busy && acting?.action === "approve"}
                              >
                                Approve
                              </Button>
                            )}
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
function ItemDetail({
  item,
  resumeBatch,
  masterBatch,
}: {
  item: ImportItemDetailResponse;
  resumeBatch: boolean;
  masterBatch: boolean;
}) {
  const fields = payloadFields(item.extracted_payload);
  return (
    <>
      {item.error_message && <p className="talent-import__reason">{item.error_message}</p>}
      {item.target_employee !== null && <ResumeMatch item={item} target={item.target_employee} />}
      {item.master_target !== null && <MasterMatch item={item} target={item.master_target} />}
      {resumeBatch &&
        item.target_employee === null &&
        item.status === "needs_review" &&
        item.candidates === null && (
          <p className="talent-import__match">
            This resume&apos;s fields or document could not be read, so it can only be rejected.
          </p>
        )}
      {masterBatch &&
        item.master_target === null &&
        item.status === "needs_review" &&
        item.master_candidates === null && (
          <p className="talent-import__match">
            This row&apos;s fields could not be read, so it can only be rejected.
          </p>
        )}
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

// Plan item 80 (option C): what approving this resume would change, shown
// before Approve, field by field. The list is the backend's own
// (knowledge_service.resume_merge_fields, the one the merge writes from), in
// its order, with on file, from resume and whether it changes: this screen
// renders it and decides nothing. A description list, not a table:
// full-flow.spec.ts counts the Items section's `tbody tr` as one per item.
const FIELD_LABELS: Record<string, string> = {
  email: "Email",
  current_title: "Title",
  professional_location: "Location",
  years_experience: "Experience",
};

function showValue(field: string, value: string | number | null): string {
  if (value === null) return NOT_SET;
  return field === "years_experience" ? formatYears(Number(value)) : String(value);
}

// One line per field of the merge's own change list (the backend decides
// every value; this renders it).
function changeLine(entry: ImportItemFieldChange): string {
  if (entry.held_by_other) {
    return `${showValue(entry.field, entry.on_file)} (kept: another employee has ${showValue(entry.field, entry.from_resume)})`;
  }
  return entry.changes
    ? `${showValue(entry.field, entry.on_file)} → ${showValue(entry.field, entry.from_resume)} (changes)`
    : `${showValue(entry.field, entry.on_file)} (kept)`;
}

const SIGNAL_LABELS: Record<string, string> = {
  name: "name",
  email: "email",
  resume_document_id: "this resume on file",
  employee_code: "employee code",
};

// The text naming each employee on a button or in a choice. Two sharing a
// name (plan item 111's own case) would give two buttons one accessible name,
// so a shared name carries the first detail it has (a resume candidate's
// email, else title; a master candidate's code, else title; a holder's code,
// else email, else title); a label still shared after that carries the
// position.
function distinctLabels<T extends { knowledge_item_id: string; name: string }>(
  people: T[],
  details: (person: T) => (string | null)[],
): Map<string, string> {
  const count = (values: string[], value: string) => values.filter((v) => v === value).length;
  const names = people.map((p) => p.name);
  const labels = people.map((p) => {
    if (count(names, p.name) < 2) return p.name;
    const detail = details(p).find((d) => d !== null && d !== "");
    return detail ? `${p.name} (${detail})` : p.name;
  });
  return new Map(
    people.map((p, i) => [
      p.knowledge_item_id,
      count(labels, labels[i]) < 2 ? labels[i] : `${labels[i]} #${i + 1}`,
    ]),
  );
}

function candidateLabels(candidates: ImportItemCandidate[]): Map<string, string> {
  return distinctLabels(candidates, (c) => [c.email, c.current_title]);
}

// Plan item 111: the employees this resume matches (the duplicate check's
// own list), each with what merging into it would change and a "Merge into
// NAME" button, and "Import as a new person" unless one matched on a strong
// signal. The employee the item already names keeps its Approve above; the
// others are listed here, so a name-only best guess can be overruled (Q2).
function ResumeCandidates({
  item,
  busy,
  acting,
  onPick,
  onImportAsNew,
}: {
  item: ImportItemDetailResponse;
  busy: boolean;
  acting: { itemId: string; action: string; employeeId?: string } | null;
  onPick: (candidate: ImportItemCandidate) => void;
  onImportAsNew: () => void;
}) {
  if (item.candidates === null) return null;
  // Over every candidate, the named one included: its panel above shows the
  // same name, so a button below must still say which employee it is.
  const labels = candidateLabels(item.candidates.candidates);
  const others = item.candidates.candidates.filter(
    (c) => c.knowledge_item_id !== item.target_employee?.knowledge_item_id,
  );
  return (
    <div className="talent-import__candidates">
      {others.length > 0 && (
        <p className="talent-import__match">
          {item.target_employee === null
            ? "No single employee matches this resume. Choose the one it belongs to:"
            : "It also matches:"}
        </p>
      )}
      {others.map((candidate) => (
        <div
          key={candidate.knowledge_item_id}
          className="talent-import__candidate"
          aria-label={`Candidate ${candidate.name}`}
        >
          <p>
            <Link to={`/talent/employees/${candidate.knowledge_item_id}`}>{candidate.name}</Link> (
            {candidate.archived
              ? "archived"
              : candidate.profile_review_status === "verified"
                ? "verified profile"
                : "profile not verified yet"}
            ), matched on {candidate.signals.map((s) => SIGNAL_LABELS[s] ?? s).join(", ")}
          </p>
          <dl
            className="talent-import__merge-list"
            aria-label={`Changes merging into ${candidate.name}`}
          >
            {candidate.changes.map((entry) => (
              <div key={entry.field} className={entry.changes ? "is-changed" : undefined}>
                <dt>{FIELD_LABELS[entry.field] ?? entry.field}</dt>
                <dd>{changeLine(entry)}</dd>
              </div>
            ))}
          </dl>
          {candidate.archived ? (
            <p>Archived: restore this employee before merging a resume into them.</p>
          ) : (
            <Button
              variant="quiet"
              onClick={() => onPick(candidate)}
              disabled={busy}
              busy={
                busy &&
                acting?.action === "pick" &&
                acting.employeeId === candidate.knowledge_item_id
              }
            >
              Merge into {labels.get(candidate.knowledge_item_id)}
            </Button>
          )}
        </div>
      ))}
      {item.candidates.strong_match ? (
        <p className="talent-import__match">
          An employee on file already has this resume&apos;s email or document, so it cannot be
          imported as a new person.
        </p>
      ) : (
        <Button
          variant="quiet"
          onClick={onImportAsNew}
          disabled={busy}
          busy={busy && acting?.action === "new"}
        >
          Import as a new person
        </Button>
      )}
    </div>
  );
}

function ResumeMatch({
  item,
  target,
}: {
  item: ImportItemDetailResponse;
  target: ImportItemTarget;
}) {
  const employee = <Link to={`/talent/employees/${target.knowledge_item_id}`}>{target.name}</Link>;
  if (item.status !== "needs_review") {
    return (
      <p className="talent-import__match">
        {target.merged_into_existing ? "Merged into " : "Employee: "}
        {employee}
      </p>
    );
  }
  const profile =
    target.profile_review_status === "verified" ? "verified profile" : "profile not verified yet";
  if (target.changes === null) {
    // The item's extracted payload is not a valid profile: what a merge would
    // write cannot be shown, so Approve is hidden (the API refuses it too, 409
    // IMPORT_ITEM_PAYLOAD_INVALID).
    return (
      <div className="talent-import__match">
        <p>
          Matches {employee} ({profile}).
        </p>
        <p>
          This resume&apos;s extracted fields could not be read, so what approving would change
          cannot be shown. Reject it, or upload the resume again.
        </p>
      </div>
    );
  }
  return (
    <div className="talent-import__match">
      <p>
        Matches {employee} ({profile}). Approving merges this resume into their profile:
      </p>
      <dl className="talent-import__merge-list" aria-label="Changes on approval">
        {target.changes.map((entry) => (
          <div key={entry.field} className={entry.changes ? "is-changed" : undefined}>
            <dt>{FIELD_LABELS[entry.field] ?? entry.field}</dt>
            <dd>
              {/* One text per value line, so "Surveyor" in the parsed
                  fields above stays the only element with that text. */}
              {changeLine(entry)}
            </dd>
          </div>
        ))}
        <div>
          <dt>Resume</dt>
          <dd>this file becomes their resume; its text is added to their record</dd>
        </div>
      </dl>
    </div>
  );
}

// Plan item 112: an employee-master row's apply, field by field, as the
// backend computes it (knowledge_service.master_apply_fields).
const MASTER_FIELD_LABELS: Record<string, string> = {
  name: "Name",
  employee_code: "Employee code",
  current_title: "Title",
  status: "Status",
};

function masterLine(entry: MasterApplyField): string {
  const onFile = entry.on_file ?? NOT_SET;
  if (entry.held_by_other) {
    return `${onFile} (kept: another employee has ${entry.from_sheet ?? NOT_SET})`;
  }
  return entry.changes
    ? `${onFile} → ${entry.from_sheet ?? NOT_SET} (changes)`
    : `${onFile} (kept)`;
}

function MasterChanges({ label, changes }: { label: string; changes: MasterApplyField[] }) {
  return (
    <dl className="talent-import__merge-list" aria-label={label}>
      {changes.map((entry) => (
        <div key={entry.field} className={entry.changes ? "is-changed" : undefined}>
          <dt>{MASTER_FIELD_LABELS[entry.field] ?? entry.field}</dt>
          <dd>{masterLine(entry)}</dd>
        </div>
      ))}
    </dl>
  );
}

// Q6 (user decision 2026-11-20): an approval restores no one; an archived
// employee's record is updated and they stay archived.
const ARCHIVED_STAYS =
  "is archived (removed from the roster). Approving updates their record and they stay " +
  "archived; restore them from their profile if they are back.";

function MasterMatch({ item, target }: { item: ImportItemDetailResponse; target: MasterTarget }) {
  const employee = <Link to={`/talent/employees/${target.knowledge_item_id}`}>{target.name}</Link>;
  if (item.status !== "needs_review") {
    return <p className="talent-import__match">Employee: {employee}</p>;
  }
  if (target.changes === null) {
    return (
      <div className="talent-import__match">
        <p>Matches {employee}.</p>
        <p>
          This row&apos;s fields could not be read, so what approving would change cannot be shown.
          Reject it, or upload the file again.
        </p>
      </div>
    );
  }
  return (
    <div className="talent-import__match">
      <p>Matches {employee}. Approving applies this row to their record:</p>
      <MasterChanges label="Changes on approval" changes={target.changes} />
      {target.archived && (
        <p>
          {target.name} {ARCHIVED_STAYS}
        </p>
      )}
    </div>
  );
}

const MASTER_SIGNAL_LABELS: Record<string, string> = {
  name: "name",
  employee_code: "employee code",
};

// Plan item 112: a master row's candidates (the code, the name), each with
// what applying the row would change and an "Apply to NAME" button, and
// "Import as a new person" unless a code matched. The employee the item
// names keeps its Approve; the others are listed here.
function MasterCandidates({
  item,
  busy,
  acting,
  onPick,
  onImportAsNew,
}: {
  item: ImportItemDetailResponse;
  busy: boolean;
  acting: { itemId: string; action: string; employeeId?: string } | null;
  onPick: (candidate: MasterCandidate) => void;
  onImportAsNew: () => void;
}) {
  if (item.master_candidates === null) return null;
  const labels = distinctLabels(item.master_candidates.candidates, (c) => [
    c.employee_code,
    c.current_title,
  ]);
  const others = item.master_candidates.candidates.filter(
    (c) => c.knowledge_item_id !== item.master_target?.knowledge_item_id,
  );
  return (
    <div className="talent-import__candidates">
      {others.length > 0 && (
        <p className="talent-import__match">
          {item.master_target === null
            ? "No single employee matches this row. Choose the one it belongs to:"
            : "It also matches:"}
        </p>
      )}
      {others.map((candidate) => (
        <div
          key={candidate.knowledge_item_id}
          className="talent-import__candidate"
          aria-label={`Candidate ${labels.get(candidate.knowledge_item_id)}`}
        >
          <p>
            <Link to={`/talent/employees/${candidate.knowledge_item_id}`}>{candidate.name}</Link>
            {candidate.archived ? " (archived)" : ""}, matched on{" "}
            {candidate.signals.map((s) => MASTER_SIGNAL_LABELS[s] ?? s).join(", ")}
          </p>
          <MasterChanges
            label={`Changes applying to ${labels.get(candidate.knowledge_item_id)}`}
            changes={candidate.changes}
          />
          {candidate.archived && (
            <p>
              {candidate.name} {ARCHIVED_STAYS}
            </p>
          )}
          <Button
            variant="quiet"
            onClick={() => onPick(candidate)}
            disabled={busy}
            busy={
              busy &&
              acting?.action === "apply" &&
              acting.employeeId === candidate.knowledge_item_id
            }
          >
            Apply to {labels.get(candidate.knowledge_item_id)}
          </Button>
        </div>
      ))}
      {item.master_candidates.strong_match ? (
        <p className="talent-import__match">
          An employee on file already has this row&apos;s employee code, or was made from this same
          row, so it cannot be imported as a new person.
        </p>
      ) : (
        <Button
          variant="quiet"
          onClick={onImportAsNew}
          disabled={busy}
          busy={busy && acting?.action === "new"}
        >
          Import as a new person
        </Button>
      )}
    </div>
  );
}

// Plan item 112 (Q7 a): a certification row in review is recorded for a
// holder chosen from the organization's live employees. The sheet's holder
// code is written onto a holder who has none, so the same row finds them
// next time. A blank holder cell may be a certificate the company holds,
// which cannot be recorded yet.
function CertificateHolderChoice({
  item,
  holders,
  chosen,
  busy,
  recording,
  onChoose,
  onRecord,
}: {
  item: ImportItemDetailResponse;
  holders: CertificateHolder[] | null;
  chosen: string;
  busy: boolean;
  recording: boolean;
  onChoose: (holderId: string) => void;
  onRecord: () => void;
}) {
  const ref = item.extracted_payload.employee_ref;
  const sheetCode = typeof ref === "string" && ref !== "" ? ref : null;
  const labels = distinctLabels(holders ?? [], (h) => [h.employee_code, h.email, h.current_title]);
  const holder = holders?.find((h) => h.knowledge_item_id === chosen);
  return (
    <div className="talent-import__candidates">
      {sheetCode === null ? (
        <p className="talent-import__match">
          This row names no holder. A certificate held by the company rather than a person cannot be
          recorded yet: choose the person who holds it, or reject it.
        </p>
      ) : (
        <p className="talent-import__match">
          No employee has the code {sheetCode}. Choose the person who holds this certificate:
        </p>
      )}
      <Field label="Holder">
        <select
          value={chosen}
          onChange={(event) => onChoose(event.target.value)}
          disabled={busy || holders === null}
        >
          <option value="">Choose an employee</option>
          {(holders ?? []).map((h) => (
            <option key={h.knowledge_item_id} value={h.knowledge_item_id}>
              {labels.get(h.knowledge_item_id)}
            </option>
          ))}
        </select>
      </Field>
      {holder !== undefined && sheetCode !== null && holder.employee_code === null && (
        <p className="talent-import__match">
          {holder.name} has no employee code; recording this gives them {sheetCode}.
        </p>
      )}
      {/* PR 25's review: a different code on file is kept (the certification
          sheet is not the HR record), so the row comes back until the sheet
          is fixed. */}
      {holder !== undefined &&
        sheetCode !== null &&
        holder.employee_code !== null &&
        holder.employee_code !== sheetCode && (
          <p className="talent-import__match">
            This sheet&apos;s holder code ({sheetCode}) doesn&apos;t match {holder.name}&apos;s code
            ({holder.employee_code}). Fix it in the sheet so the row imports cleanly next time.
          </p>
        )}
      <Button
        variant="quiet"
        onClick={onRecord}
        disabled={busy || holder === undefined}
        busy={recording}
      >
        Record for the chosen holder
      </Button>
    </div>
  );
}
