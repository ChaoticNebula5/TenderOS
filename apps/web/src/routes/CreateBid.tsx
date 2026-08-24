import { useState } from "react";
import type { FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { createTender } from "../api/client";
import { formatErrorDetail } from "../api/formatErrorDetail";
import { useAuth } from "../auth/useAuth";

// Screen 6 (Part J). Navigates to the dashboard on success - screen 7
// (Upload and processing status, §7 step 4) will give a created tender its
// own detail route to land on instead; not built yet.
export function CreateBid() {
  const { organizationId } = useAuth();
  const navigate = useNavigate();

  const [name, setName] = useState("");
  const [clientName, setClientName] = useState("");
  const [submissionDate, setSubmissionDate] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!organizationId) return;
    setError(null);
    setSubmitting(true);
    try {
      await createTender(organizationId, {
        name,
        client_name: clientName || null,
        submission_date: submissionDate || null,
      });
      navigate("/", { replace: true });
    } catch (err) {
      setError(formatErrorDetail(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main style={{ maxWidth: 480 }}>
      <h1>Create bid</h1>
      <form onSubmit={handleSubmit}>
        <label>
          Name
          <input type="text" required value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label>
          Client name
          <input type="text" value={clientName} onChange={(e) => setClientName(e.target.value)} />
        </label>
        <label>
          Submission date
          <input
            type="date"
            value={submissionDate}
            onChange={(e) => setSubmissionDate(e.target.value)}
          />
        </label>

        {error && (
          <p role="alert" style={{ color: "var(--color-danger)" }}>
            {error}
          </p>
        )}

        <button type="submit" disabled={submitting}>
          {submitting ? "Creating..." : "Create bid"}
        </button>
      </form>
    </main>
  );
}
