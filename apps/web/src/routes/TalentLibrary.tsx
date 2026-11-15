import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  ApiError,
  getImportBatch,
  listOrganizationKnowledge,
  patchKnowledgeItem,
  uploadKnowledgeDocument,
} from "../api/client";
import type { ImportBatchResponse, KnowledgeItemResponse } from "../api/client";
import { useAuth } from "../auth/useAuth";
import { PollStoppedNotice } from "../components/PollStoppedNotice";
import { EmptyState } from "../components/ui/EmptyState";
import { Field } from "../components/ui/Field";
import { Notice } from "../components/ui/Notice";
import { StatusBadge } from "../components/ui/StatusBadge";
import { TableScroll } from "../components/ui/TableScroll";
import { formatDay } from "../format/date";
import { humanize } from "../format/humanize";
import { useBoundedPoll } from "../hooks/useBoundedPoll";
import { useListReads } from "../hooks/useListReads";
import { describeStatus } from "../status/statusMap";
import { NOT_SET } from "./talentImportValues";
import "./TalentLibrary.css";

// The four knowledge item statuses, in the order the select offers them
// (ck_knowledge_item_status in db/models/knowledge.py; PATCH accepts exactly these,
// apps/api/.../schemas/knowledge.py:98). Option values stay these backend
// words; the visible text is the mapped label.
const KNOWLEDGE_STATUSES = ["active", "expired", "archived", "unverified"] as const;

// Plan item 78: a capability statement's import batch in one of these is
// still running (ck_knowledge_import_batch_status); the page follows it.
const UNFINISHED_BATCH_STATUSES = new Set(["queued", "processing"]);

// An upload this page is following: its batch, as last read, and the
// file's name for the line that says so.
interface FollowedImport {
  batch: ImportBatchResponse;
  filename: string;
}

// Screen 2 (Part J). GET /organizations/{org_id}/knowledge returns every
// knowledge_items row regardless of item_type (employee/certification/
// past_project/capability) - this screen is the org-wide browser over all
// of them. Only employee rows link out to screen 4 (richer, employee-
// specific view/verification); every other item_type is edited inline
// here via the generic PATCH /knowledge/{id}, since that route only ever
// covers title/status and there's no type-specific detail screen for
// non-employee items in Part J's 17.
//
// Restyle batch 6 - what full-flow.spec.ts reads here: setup's résumé import count
// every a[href^="/talent/employees/"] on the page (it must equal the number
// of employees) and follow each href. So an employee row's title is its
// ONE link, with the href shape unchanged; no other row, the count line
// or the empty state links there.
export function TalentLibrary() {
  const { organizationId } = useAuth();
  const [items, setItems] = useState<KnowledgeItemResponse[] | null>(null);
  // Set once the list has answered (or failed), so the empty state only
  // claims "nothing yet" after the response says so.
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  // Plan item 78: capability statements uploaded here and still importing.
  // The upload returns its batch; the page reads that batch again, bounded
  // (useBoundedPoll.ts), and once it has finished reads the library again,
  // which is when its projects and capabilities appear. It said "Reload the
  // page to see them" before (item 66 walk).
  const [following, setFollowing] = useState<FollowedImport[]>([]);
  // How finished imports ended, when that needs saying: none of their
  // items made it into the library as they stand.
  const [importNotes, setImportNotes] = useState<string[]>([]);
  // The upload input works before the first read answers, and a status
  // change can land while the re-read after an upload is in flight; a
  // late, older answer must not undo either (useListReads.ts).
  const itemReads = useListReads();

  useEffect(() => {
    if (!organizationId) return;
    let cancelled = false;
    const startedAt = itemReads.start();
    listOrganizationKnowledge(organizationId)
      .then((result) => {
        if (!cancelled) setItems((current) => itemReads.merge(startedAt, result, current ?? []));
      })
      .catch(() => {
        if (!cancelled) setError("Failed to load the knowledge library");
      })
      .finally(() => {
        if (!cancelled) setLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, [organizationId, itemReads]);

  async function handleStatusChange(itemId: string, status: string) {
    setError(null);
    try {
      const updated = await patchKnowledgeItem(itemId, {
        status: status as "active" | "expired" | "archived" | "unverified",
      });
      itemReads.changed(itemId);
      setItems((prev) => (prev ? prev.map((i) => (i.id === itemId ? updated : i)) : prev));
    } catch (err) {
      setError(err instanceof ApiError ? `Update failed (${err.status})` : "Update failed");
    }
  }

  async function handleUpload(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file || !organizationId) return;
    setError(null);
    setUploading(true);
    try {
      const batch = await uploadKnowledgeDocument(organizationId, file);
      setFollowing((current) => [...current, { batch, filename: file.name }]);
      await rereadLibrary();
    } catch (err) {
      setError(err instanceof ApiError ? `Upload failed (${err.status})` : "Upload failed");
    } finally {
      setUploading(false);
      event.target.value = "";
    }
  }
  async function rereadLibrary() {
    if (!organizationId) return;
    const startedAt = itemReads.start();
    const result = await listOrganizationKnowledge(organizationId);
    setItems((current) => itemReads.merge(startedAt, result, current ?? []));
  }

  // One poll read: each followed batch again. A finished one is dropped
  // from the list, the library is read once for all of them, and a failed
  // or held-for-review one is said. A failed read keeps the batch followed
  // and quiet; the next read tries again.
  async function readFollowedImports() {
    const reads = await Promise.all(
      following.map(async (followed) => {
        try {
          return { followed, batch: await getImportBatch(followed.batch.id) };
        } catch {
          return { followed, batch: followed.batch };
        }
      }),
    );
    const finished = reads.filter(({ batch }) => !UNFINISHED_BATCH_STATUSES.has(batch.status));
    setFollowing((current) =>
      current.flatMap((followed) => {
        const read = reads.find((r) => r.followed.batch.id === followed.batch.id);
        if (read === undefined) return [followed];
        if (!UNFINISHED_BATCH_STATUSES.has(read.batch.status)) return [];
        return [{ ...followed, batch: read.batch }];
      }),
    );
    if (finished.length === 0) return;
    const notes = finished.flatMap(({ followed, batch }) => {
      if (batch.status === "failed") return [`Importing ${followed.filename} failed.`];
      if (batch.status === "needs_review")
        return [
          `Some projects or capabilities from ${followed.filename} need review in the import batches and review queue.`,
        ];
      return [];
    });
    if (notes.length > 0) setImportNotes((current) => [...current, ...notes]);
    await rereadLibrary().catch(() => {
      // The list already shown stays; Check again or the next upload reads it.
    });
  }

  const importPoll = useBoundedPoll(
    readFollowedImports,
    following.length > 0,
    following.map(({ batch }) => `${batch.id}:${batch.status}:${batch.total_items}`).join(","),
  );

  return (
    <main>
      <h1>Talent and knowledge library</h1>
      <p className="talent-library__links">
        <Link to="/talent/import">Import batches and review queue</Link>
      </p>

      {error && (
        <Notice variant="failed" alert>
          {error}
        </Notice>
      )}

      <section className="talent-library__upload">
        <h2>Upload a capability statement</h2>
        <Field
          label="Capability statement"
          hint="Past projects and capabilities found in it are added here once it has been processed."
        >
          <input type="file" onChange={handleUpload} disabled={uploading} />
        </Field>
        {following.length > 0 && (
          <ul className="talent-library__importing" aria-live="polite">
            {following.map(({ batch, filename }) => (
              <li key={batch.id}>Importing {filename}...</li>
            ))}
          </ul>
        )}
        {importNotes.map((note) => (
          <Notice key={note} variant="warning">
            {note}
          </Notice>
        ))}
        {importPoll.stopped && (
          <PollStoppedNotice
            what="the capability statement's import"
            onCheckAgain={importPoll.checkAgain}
          />
        )}
      </section>

      {items !== null && items.length > 0 && <p className="count-line">{countLine(items)}</p>}
      <TableScroll label="Knowledge library">
        <table className="talent-library" aria-busy={!loaded || undefined}>
          <thead>
            <tr>
              <th>Title</th>
              <th>Status</th>
              <th>Type</th>
              <th>Valid until</th>
            </tr>
          </thead>
          <tbody>
            {(items ?? []).map((item) => (
              <tr key={item.id}>
                <td className="talent-library__title">
                  {item.item_type === "employee" ? (
                    <Link to={`/talent/employees/${item.id}`}>{item.title}</Link>
                  ) : (
                    item.title
                  )}
                </td>
                <td>
                  <div className="talent-library__status">
                    <StatusBadge {...describeStatus("knowledgeItem", item.status)} />
                    <select
                      aria-label={`Status of ${item.title}`}
                      value={item.status}
                      onChange={(e) => handleStatusChange(item.id, e.target.value)}
                    >
                      {KNOWLEDGE_STATUSES.map((value) => (
                        <option key={value} value={value}>
                          {describeStatus("knowledgeItem", value).label}
                        </option>
                      ))}
                    </select>
                  </div>
                </td>
                <td>{humanize(item.item_type)}</td>
                {/* Polish pass (decided): only certifications ever carry a
                    valid_until (db/repositories/knowledge.py:367-379), so the
                    cell is blank where it does not apply and says "Not set"
                    only for a certification without one. A date the backend
                    sends for another type is still shown. */}
                <td className="talent-library__date">
                  {item.valid_until !== null
                    ? formatDay(item.valid_until)
                    : item.item_type === "certification"
                      ? NOT_SET
                      : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
      {loaded && items !== null && items.length === 0 && (
        <EmptyState title="Nothing in the library yet">
          Import employees on the import page, or upload a capability statement above.
        </EmptyState>
      )}
    </main>
  );
}

// "6 items, 2 employees" - counted from the response (DESIGN.md Screen
// patterns, "Register tables"); employees are what the talent screens act
// on, so they are the one type named.
function countLine(items: KnowledgeItemResponse[]): string {
  const employees = items.filter((i) => i.item_type === "employee").length;
  const total = `${items.length} ${items.length === 1 ? "item" : "items"}`;
  return `${total}, ${employees} ${employees === 1 ? "employee" : "employees"}`;
}
