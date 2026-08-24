import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import {
  ApiError,
  getEmployee,
  patchEmployeeProfile,
  replaceEmployeeResume,
  verifyEmployee,
} from "../api/client";
import type { EmployeeResponse } from "../api/client";

// Screen 4 (Part J). GET /employees/{id} is the route added alongside
// this screen (KNOWN_LIMITATIONS.md) - the only fetch that returns a
// full, real profile for an employee who isn't bid-eligible yet, which
// is exactly the case a reviewer opens this screen for.
export function EmployeeProfile() {
  const { knowledgeItemId } = useParams<{ knowledgeItemId: string }>();
  const [employee, setEmployee] = useState<EmployeeResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [currentTitle, setCurrentTitle] = useState("");
  const [eligibleForBid, setEligibleForBid] = useState(false);
  const [uploadingResume, setUploadingResume] = useState(false);

  useEffect(() => {
    if (!knowledgeItemId) return;
    let cancelled = false;
    getEmployee(knowledgeItemId)
      .then((result) => {
        if (cancelled) return;
        setEmployee(result);
        setCurrentTitle(result.current_title ?? "");
        setEligibleForBid(result.eligible_for_bid);
      })
      .catch(() => {
        if (!cancelled) setError("Failed to load this employee's profile");
      });
    return () => {
      cancelled = true;
    };
  }, [knowledgeItemId]);

  async function handleSaveTitle() {
    if (!knowledgeItemId) return;
    setError(null);
    try {
      setEmployee(await patchEmployeeProfile(knowledgeItemId, { current_title: currentTitle }));
    } catch (err) {
      setError(err instanceof ApiError ? `Save failed (${err.status})` : "Save failed");
    }
  }

  async function handleVerify() {
    if (!knowledgeItemId) return;
    setError(null);
    try {
      setEmployee(await verifyEmployee(knowledgeItemId, eligibleForBid));
    } catch (err) {
      setError(err instanceof ApiError ? `Verify failed (${err.status})` : "Verify failed");
    }
  }

  async function handleResumeUpload(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file || !knowledgeItemId) return;
    setError(null);
    setUploadingResume(true);
    try {
      setEmployee(await replaceEmployeeResume(knowledgeItemId, file));
    } catch (err) {
      setError(err instanceof ApiError ? `Upload failed (${err.status})` : "Upload failed");
    } finally {
      setUploadingResume(false);
      event.target.value = "";
    }
  }

  if (error && !employee) {
    return (
      <main>
        <p role="alert" style={{ color: "var(--color-danger)" }}>
          {error}
        </p>
      </main>
    );
  }

  if (!employee) {
    return (
      <main>
        <p>Loading...</p>
      </main>
    );
  }

  return (
    <main>
      <h1>{employee.name}</h1>

      {error && (
        <p role="alert" style={{ color: "var(--color-danger)" }}>
          {error}
        </p>
      )}

      <section>
        <h2>Profile</h2>
        <dl>
          <dt>Email</dt>
          <dd>{employee.email ?? "-"}</dd>
          <dt>Current title</dt>
          <dd>
            <input value={currentTitle} onChange={(e) => setCurrentTitle(e.target.value)} />
            <button type="button" onClick={handleSaveTitle}>
              Save
            </button>
          </dd>
          <dt>Availability</dt>
          <dd>{employee.availability_status}</dd>
          <dt>Review status</dt>
          <dd>{employee.profile_review_status}</dd>
          <dt>Bid eligible</dt>
          <dd>{employee.eligible_for_bid ? "yes" : "no"}</dd>
        </dl>
      </section>

      <section>
        <h2>Verify profile</h2>
        <label>
          <input
            type="checkbox"
            checked={eligibleForBid}
            onChange={(e) => setEligibleForBid(e.target.checked)}
          />
          Eligible for bid
        </label>
        <button type="button" onClick={handleVerify}>
          Verify
        </button>
      </section>

      <section>
        <h2>Resume</h2>
        <p>{employee.resume_document_id ? "A resume is on file." : "No resume on file."}</p>
        <input type="file" onChange={handleResumeUpload} disabled={uploadingResume} />
      </section>
    </main>
  );
}
