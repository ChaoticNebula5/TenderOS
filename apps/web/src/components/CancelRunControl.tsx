import { useEffect, useRef, useState } from "react";

import { ApiError, cancelWorkflowRun } from "../api/client";
import type { WorkflowRunResponse } from "../api/client";
import { Button } from "./ui/Button";
import { Notice } from "./ui/Notice";

// Plan item 72: cancel the active run, confirmed in the page rather than in a
// browser dialog. Shown to every role, like the gate buttons: the web app has
// no role data, and the API answers 403 for a role that may not cancel.
//
// Keyed by the run id (housekeeping after PR 12): each run gets its own
// question, which starts closed, and "Yes, cancel the run" cancels the run
// it was opened for. The page used to reset one shared question in an effect
// whenever the followed run changed; that effect also fired when a run first
// appeared, in the same commit that showed "Cancel run", and a click landing
// before it ran opened the question only for the effect to close it (seen
// once in CI, reproduced deterministically). With a key there is no reset
// effect to race, and a question can never outlive the run it was about.
interface CancelRunControlProps {
  runId: string;
  // Called with the cancel's answer: "cancelled", or the terminal status
  // the run reached first (a cancel on an ended run is a no-op).
  onCancelled: (run: WorkflowRunResponse) => void;
}

export function CancelRunControl({ runId, onCancelled }: CancelRunControlProps) {
  return <CancelRunQuestion key={runId} runId={runId} onCancelled={onCancelled} />;
}

function CancelRunQuestion({ runId, onCancelled }: CancelRunControlProps) {
  const [confirming, setConfirming] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);
  const regionRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLDivElement>(null);

  // Focus follows the question: into it on open, on the safe choice ("Keep
  // running"); back to "Cancel run" when it is closed without a cancel. An
  // effect after the commit, not a requestAnimationFrame: the button exists
  // by then, however busy the page is (a frame callback lost that race once,
  // under the full test suite's load, PR 3). It moves focus only; it never
  // changes whether the question is open.
  const returnFocusToCancel = useRef(false);
  useEffect(() => {
    if (confirming) {
      regionRef.current?.querySelector<HTMLButtonElement>(".processing__keep")?.focus();
    } else if (returnFocusToCancel.current) {
      returnFocusToCancel.current = false;
      buttonRef.current?.querySelector("button")?.focus();
    }
  }, [confirming]);

  function close() {
    returnFocusToCancel.current = true;
    setConfirming(false);
    setCancelError(null);
  }

  async function handleCancel() {
    setCancelError(null);
    setCancelling(true);
    try {
      // runId is this question's own run: the key gives every run its own
      // instance, so it is the run the user was asked about.
      const run = await cancelWorkflowRun(runId);
      setConfirming(false);
      onCancelled(run);
    } catch (err) {
      setCancelError(
        err instanceof ApiError && err.status === 403
          ? "Your role can't cancel runs."
          : "Failed to cancel the run.",
      );
    } finally {
      setCancelling(false);
    }
  }

  return (
    <div className="processing__cancel">
      {confirming ? (
        <div
          ref={regionRef}
          role="group"
          aria-labelledby="processing-cancel-title"
          className="processing__cancel-confirm"
          onKeyDown={(event) => {
            if (event.key === "Escape" && !cancelling) close();
          }}
        >
          <p id="processing-cancel-title" className="processing__cancel-title">
            Cancel this run?
          </p>
          <p>
            Work already done is kept. A cancelled run can&apos;t be resumed; start processing again
            to run it from the beginning.
          </p>
          {cancelError && (
            <Notice variant="failed" alert>
              {cancelError}
            </Notice>
          )}
          <div className="action-row">
            <Button
              variant="danger"
              onClick={handleCancel}
              busy={cancelling}
              busyLabel="Cancelling..."
            >
              Yes, cancel the run
            </Button>
            <Button
              variant="quiet"
              className="processing__keep"
              onClick={close}
              disabled={cancelling}
            >
              Keep running
            </Button>
          </div>
        </div>
      ) : (
        <div ref={buttonRef}>
          <Button variant="secondary" onClick={() => setConfirming(true)}>
            Cancel run
          </Button>
        </div>
      )}
    </div>
  );
}
