import { useState } from "react";
import type { FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { createTender } from "../api/client";
import { formatErrorDetail } from "../api/formatErrorDetail";
import { useAuth } from "../auth/useAuth";
import { Button } from "../components/ui/Button";
import { Field } from "../components/ui/Field";
import { Notice } from "../components/ui/Notice";

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

  // Form column capped at --width-form (640px, DESIGN.md Layout "Content
  // width"; plan item 13), replacing the hardcoded 480. Labels keep their
  // exact text: full-flow.spec.ts (step 2, create bid) fills getByLabel("Name", exact) and
  // step 2, create bid clicks the page's only button named "Create bid".
  return (
    <main className="form-column">
      <h1>Create bid</h1>
      <form onSubmit={handleSubmit}>
        <Field label="Name">
          <input type="text" required value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="Client name">
          <input type="text" value={clientName} onChange={(e) => setClientName(e.target.value)} />
        </Field>
        <Field label="Submission date">
          <input
            type="date"
            value={submissionDate}
            onChange={(e) => setSubmissionDate(e.target.value)}
          />
        </Field>

        {error && (
          <Notice variant="failed" alert>
            {error}
          </Notice>
        )}

        <Button type="submit" variant="primary" busy={submitting} busyLabel="Creating...">
          Create bid
        </Button>
      </form>
    </main>
  );
}
