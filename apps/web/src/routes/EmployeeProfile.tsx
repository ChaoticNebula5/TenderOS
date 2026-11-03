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
import { Button } from "../components/ui/Button";
import { Field } from "../components/ui/Field";
import { Notice } from "../components/ui/Notice";
import { StatusBadge } from "../components/ui/StatusBadge";
import { formatDate, formatDay } from "../format/date";
import { describeStatus } from "../status/statusMap";
import { NOT_SET, formatYears } from "./talentImportValues";
import "./EmployeeProfile.css";

// Screen 4 (Part J). GET /employees/{id} is the route added alongside
// this screen (KNOWN_LIMITATIONS.md) - the only fetch that returns a
// full, real profile for an employee who isn't bid-eligible yet, which
// is exactly the case a reviewer opens this screen for.
//
// Restyle batch 6 - what full-flow.spec.ts reads here (setup's résumé import):
// - setup's résumé import ticks the checkbox "Eligible for bid". It exists only once
//   getEmployee has answered: the load sets the box from the response
//   (setEligibleForBid below), so a box shown earlier and ticked by the
//   spec would be reset to false, and Verify would send false.
// - setup's résumé import clicks the button named "Verify" - a substring match, strict, so
//   no other button's name contains "verify".
// - setup's résumé import waits for exactly one element inside a <dl> whose text is exactly
//   "yes". Bid eligible is a badge whose DOM text is the raw "yes"/"no",
//   capitalised by CSS (.badge-raw-word, the batch 5 technique), and no
//   other <dl> value is ever exactly "yes".
export function EmployeeProfile() {
  const { knowledgeItemId } = useParams<{ knowledgeItemId: string }>();
  const [employee, setEmployee] = useState<EmployeeResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [currentTitle, setCurrentTitle] = useState("");
  const [eligibleForBid, setEligibleForBid] = useState(false);
  const [savingTitle, setSavingTitle] = useState(false);
  const [verifying, setVerifying] = useState(false);
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
    setSavingTitle(true);
    try {
      setEmployee(await patchEmployeeProfile(knowledgeItemId, { current_title: currentTitle }));
    } catch (err) {
      setError(err instanceof ApiError ? `Save failed (${err.status})` : "Save failed");
    } finally {
      setSavingTitle(false);
    }
  }

  async function handleVerify() {
    if (!knowledgeItemId) return;
    setError(null);
    setVerifying(true);
    try {
      setEmployee(await verifyEmployee(knowledgeItemId, eligibleForBid));
    } catch (err) {
      setError(err instanceof ApiError ? `Verify failed (${err.status})` : "Verify failed");
    } finally {
      setVerifying(false);
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
        <h1>Employee profile</h1>
        <Notice variant="failed" alert>
          {error}
        </Notice>
      </main>
    );
  }

  if (!employee) {
    return (
      <main aria-busy="true">
        <h1>Employee profile</h1>
        <p className="employee__loading">Loading the profile...</p>
      </main>
    );
  }

  const hasResume = employee.resume_document_id !== null;

  return (
    <main>
      <h1>{employee.name}</h1>

      {error && (
        <Notice variant="failed" alert>
          {error}
        </Notice>
      )}

      <section>
        <h2>Profile</h2>
        <dl className="employee__profile">
          <div>
            <dt>Email</dt>
            <dd>{employee.email ?? NOT_SET}</dd>
          </div>
          <div>
            <dt>Employee code</dt>
            <dd>{employee.employee_code ?? NOT_SET}</dd>
          </div>
          <div>
            <dt>Current title</dt>
            <dd>{employee.current_title ?? NOT_SET}</dd>
          </div>
          <div>
            <dt>Location</dt>
            <dd>{employee.professional_location ?? NOT_SET}</dd>
          </div>
          <div>
            <dt>Experience</dt>
            <dd>
              {employee.years_experience === null
                ? NOT_SET
                : formatYears(employee.years_experience)}
            </dd>
          </div>
          <div>
            <dt>Availability</dt>
            <dd>
              <StatusBadge {...describeStatus("availability", employee.availability_status)} />
            </dd>
          </div>
          <div>
            <dt>Availability as of</dt>
            <dd>
              {employee.availability_as_of === null
                ? NOT_SET
                : formatDay(employee.availability_as_of)}
            </dd>
          </div>
          <div>
            <dt>Review status</dt>
            <dd>
              <StatusBadge {...describeStatus("profileReview", employee.profile_review_status)} />
            </dd>
          </div>
          <div>
            <dt>Last verified</dt>
            <dd>
              {employee.profile_last_verified_at === null
                ? NOT_SET
                : formatDate(employee.profile_last_verified_at)}
            </dd>
          </div>
          <div>
            <dt>Bid eligible</dt>
            {/* The raw "yes"/"no" in the DOM, drawn "Yes"/"No" by CSS
                (full-flow.spec.ts (setup's résumé import) reads "yes" exactly). Ready when
                eligible; Neutral when not, as describeSelected's negative. */}
            <dd className="badge-raw-word">
              <StatusBadge
                tone={employee.eligible_for_bid ? "ready" : "neutral"}
                label={employee.eligible_for_bid ? "yes" : "no"}
              />
            </dd>
          </div>
        </dl>

        <div className="employee__correct">
          <Field label="Correct title">
            <input value={currentTitle} onChange={(e) => setCurrentTitle(e.target.value)} />
          </Field>
          <Button variant="secondary" onClick={handleSaveTitle} busy={savingTitle}>
            Save
          </Button>
        </div>
      </section>

      <section>
        <h2>Verify profile</h2>
        <label className="employee__check">
          <input
            type="checkbox"
            checked={eligibleForBid}
            onChange={(e) => setEligibleForBid(e.target.checked)}
          />
          Eligible for bid
        </label>
        <p className="employee__hint">
          Only a verified profile marked eligible is offered as a candidate.
        </p>
        <Button variant="primary" onClick={handleVerify} busy={verifying}>
          Verify
        </Button>
      </section>

      <section>
        <h2>Resume</h2>
        <p>{hasResume ? "A resume is on file." : "No resume on file."}</p>
        <Field label={hasResume ? "Replace resume" : "Upload a resume"}>
          <input
            type="file"
            onChange={handleResumeUpload}
            disabled={uploadingResume}
            aria-busy={uploadingResume || undefined}
          />
        </Field>
      </section>
    </main>
  );
}
