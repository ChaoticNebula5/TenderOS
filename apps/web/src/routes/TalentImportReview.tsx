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

// Screen 3 (Part J). Three upload routes feed one shared batch/review
// queue - no live worker in the browser either, so a freshly created
// batch legitimately sits at "queued" (0 items) until the worker picks
// up the KnowledgeDocumentUploaded event; re-selecting the batch (or a
// page refresh) is how progress becomes visible, matching this screen's
// own polling-free design (B14 owns real-time updates project-wide).
export function TalentImportReview() {
  const { organizationId } = useAuth();
  const [batches, setBatches] = useState<ImportBatchResponse[] | null>(null);
  const [selectedBatchId, setSelectedBatchId] = useState<string | null>(null);
  const [items, setItems] = useState<ImportItemDetailResponse[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);

  const refreshBatches = useCallback(async () => {
    if (!organizationId) return;
    try {
      setBatches(await listImportBatches(organizationId));
    } catch {
      setError("Failed to load import batches");
    }
  }, [organizationId]);

  useEffect(() => {
    void refreshBatches();
  }, [refreshBatches]);

  useEffect(() => {
    if (!selectedBatchId) {
      setItems(null);
      return;
    }
    let cancelled = false;
    listImportItems(selectedBatchId)
      .then((result) => {
        if (!cancelled) setItems(result);
      })
      .catch(() => {
        if (!cancelled) setError("Failed to load import items");
      });
    return () => {
      cancelled = true;
    };
  }, [selectedBatchId]);

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
    try {
      await approveImportItem(itemId);
      await refreshSelectedItems();
    } catch (err) {
      setError(err instanceof ApiError ? `Approve failed (${err.status})` : "Approve failed");
    }
  }

  async function handleReject(itemId: string) {
    setError(null);
    try {
      await rejectImportItem(itemId, "Rejected by reviewer");
      await refreshSelectedItems();
    } catch (err) {
      setError(err instanceof ApiError ? `Reject failed (${err.status})` : "Reject failed");
    }
  }

  return (
    <main>
      <h1>Talent import and review queue</h1>

      {error && (
        <p role="alert" style={{ color: "var(--color-danger)" }}>
          {error}
        </p>
      )}

      <section>
        <h2>Import</h2>
        <label>
          Employee master (CSV or XLSX):{" "}
          <input type="file" onChange={handleEmployeeMasterUpload} disabled={uploading} />
        </label>
        <br />
        <label>
          Certifications (XLSX):{" "}
          <input type="file" onChange={handleCertificationsUpload} disabled={uploading} />
        </label>
        <br />
        <label>
          Resumes (multiple files):{" "}
          <input type="file" multiple onChange={handleResumesUpload} disabled={uploading} />
        </label>
      </section>

      <section>
        <h2>Batches</h2>
        {batches === null && <p>Loading...</p>}
        {batches !== null && batches.length === 0 && <p>No import batches yet.</p>}
        {batches !== null && batches.length > 0 && (
          <table>
            <thead>
              <tr>
                <th>Type</th>
                <th>Status</th>
                <th>Total</th>
                <th>Completed</th>
                <th>Failed</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {batches.map((batch) => (
                <tr key={batch.id}>
                  <td>{batch.import_type}</td>
                  <td>{batch.status}</td>
                  <td>{batch.total_items}</td>
                  <td>{batch.completed_items}</td>
                  <td>{batch.failed_items}</td>
                  <td>
                    <button type="button" onClick={() => setSelectedBatchId(batch.id)}>
                      Review items
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {selectedBatchId && (
        <section>
          <h2>Items</h2>
          {items === null && <p>Loading...</p>}
          {items !== null && items.length === 0 && <p>No items in this batch yet.</p>}
          {items !== null && items.length > 0 && (
            <table>
              <thead>
                <tr>
                  <th>Source</th>
                  <th>Status</th>
                  <th>Detail</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.id}>
                    <td>{item.source_ref}</td>
                    <td>{item.status}</td>
                    <td>{item.error_message ?? JSON.stringify(item.extracted_payload)}</td>
                    <td>
                      {item.status === "needs_review" && (
                        <>
                          <button type="button" onClick={() => handleApprove(item.id)}>
                            Approve
                          </button>
                          <button type="button" onClick={() => handleReject(item.id)}>
                            Reject
                          </button>
                        </>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}
    </main>
  );
}
