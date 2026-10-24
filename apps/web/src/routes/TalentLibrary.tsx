import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  ApiError,
  listOrganizationKnowledge,
  patchKnowledgeItem,
  uploadKnowledgeDocument,
} from "../api/client";
import type { KnowledgeItemResponse } from "../api/client";
import { useAuth } from "../auth/useAuth";
import { EmptyState } from "../components/ui/EmptyState";
import { Field } from "../components/ui/Field";
import { Notice } from "../components/ui/Notice";
import { StatusBadge } from "../components/ui/StatusBadge";
import { TableScroll } from "../components/ui/TableScroll";
import { formatDay } from "../format/date";
import { humanize } from "../format/humanize";
import { useListReads } from "../hooks/useListReads";
import { describeStatus } from "../status/statusMap";
import { NOT_SET } from "./talentImportValues";
import "./TalentLibrary.css";

// The four knowledge item statuses, in the order the select offers them
// (db/models/knowledge.py:103; PATCH accepts exactly these,
// apps/api/.../schemas/knowledge.py:98). Option values stay these backend
// words; the visible text is the mapped label.
const KNOWLEDGE_STATUSES = ["active", "expired", "archived", "unverified"] as const;

// Screen 2 (Part J). GET /organizations/{org_id}/knowledge returns every
// knowledge_items row regardless of item_type (employee/certification/
// past_project/capability) - this screen is the org-wide browser over all
// of them. Only employee rows link out to screen 4 (richer, employee-
// specific view/verification); every other item_type is edited inline
// here via the generic PATCH /knowledge/{id}, since that route only ever
// covers title/status and there's no type-specific detail screen for
// non-employee items in Part J's 17.
//
// Restyle batch 6 - what full-flow.spec.ts reads here: :476-492 count
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
      await uploadKnowledgeDocument(organizationId, file);
      const startedAt = itemReads.start();
      const result = await listOrganizationKnowledge(organizationId);
      setItems((current) => itemReads.merge(startedAt, result, current ?? []));
    } catch (err) {
      setError(err instanceof ApiError ? `Upload failed (${err.status})` : "Upload failed");
    } finally {
      setUploading(false);
      event.target.value = "";
    }
  }

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
          hint="Past projects and capabilities found in it are added here once it has been processed. Reload the page to see them."
        >
          <input type="file" onChange={handleUpload} disabled={uploading} />
        </Field>
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
