import { POLL_IDLE_LIMIT_MS } from "../hooks/useBoundedPoll";
import { Button } from "./ui/Button";
import { Notice } from "./ui/Notice";

// Plan item 78: what a page shows once its bounded poll (useBoundedPoll.ts)
// has stopped: it says the page is no longer checking and why, and offers
// to check again in place. No "reload the page": the button does that read.
// One component so the three pages that poll (Documents, Talent import,
// Talent library) say it the same way.
const IDLE_MINUTES = Math.round(POLL_IDLE_LIMIT_MS / 60_000);

export function PollStoppedNotice({
  what,
  onCheckAgain,
}: {
  // What stopped being checked, e.g. "parse progress".
  what: string;
  onCheckAgain: () => void;
}) {
  return (
    <div className="action-row">
      <Notice variant="info">
        Stopped checking {what}: nothing has changed for {IDLE_MINUTES} minutes.
      </Notice>
      <Button variant="quiet" onClick={onCheckAgain}>
        Check again
      </Button>
    </div>
  );
}
