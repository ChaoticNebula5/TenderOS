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

// Screen 2 (Part J). GET /organizations/{org_id}/knowledge returns every
// knowledge_items row regardless of item_type (employee/certification/
// past_project/capability) - this screen is the org-wide browser over all
// of them. Only employee rows link out to screen 4 (richer, employee-
// specific view/verification); every other item_type is edited inline
// here via the generic PATCH /knowledge/{id}, since that route only ever
// covers title/status and there's no type-specific detail screen for
// non-employee items in Part J's 17.
export function TalentLibrary() {
  const { organizationId } = useAuth();
  const [items, setItems] = useState<KnowledgeItemResponse[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);

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

  async function handleStatusChange(itemId: string, status: string) {
    setError(null);
    try {
      const updated = await patchKnowledgeItem(itemId, {
        status: status as "active" | "expired" | "archived" | "unverified",
      });
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
      setItems(await listOrganizationKnowledge(organizationId));
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
      <p>
        <Link to="/talent/import">Import batches and review queue</Link>
      </p>

      {error && (
        <p role="alert" style={{ color: "var(--color-danger)" }}>
          {error}
        </p>
      )}

      <section>
        <h2>Upload a capability statement</h2>
        <input type="file" onChange={handleUpload} disabled={uploading} />
      </section>

      {items === null && !error && <p>Loading...</p>}
      {items !== null && items.length === 0 && <p>Nothing in the library yet.</p>}
      {items !== null && items.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Title</th>
              <th>Type</th>
              <th>Status</th>
              <th>Valid until</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.id}>
                <td>
                  {item.item_type === "employee" ? (
                    <Link to={`/talent/employees/${item.id}`}>{item.title}</Link>
                  ) : (
                    item.title
                  )}
                </td>
                <td>{item.item_type}</td>
                <td>
                  <select
                    value={item.status}
                    onChange={(e) => handleStatusChange(item.id, e.target.value)}
                  >
                    <option value="active">active</option>
                    <option value="expired">expired</option>
                    <option value="archived">archived</option>
                    <option value="unverified">unverified</option>
                  </select>
                </td>
                <td>{item.valid_until ?? "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </main>
  );
}
