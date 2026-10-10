import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, listTenders } from "../api/client";
import type { TenderResponse } from "../api/client";
import { useAuth } from "../auth/useAuth";
import { buttonClassName } from "../components/ui/buttonClassName";
import { EmptyState } from "../components/ui/EmptyState";
import { Notice } from "../components/ui/Notice";
import { SkeletonRows } from "../components/ui/SkeletonRows";
import { TableScroll } from "../components/ui/TableScroll";
import "./BidDashboard.css";

// Screen 5 (Part J). GET /organizations/{org_id}/tenders lists the org's
// bids. The response also carries tenders.status, but this screen does NOT
// show it (DESIGN.md decision 9): nothing in the workflow writes that
// column, so it reads "created" for every tender - a Status column would
// claim more than the code does. It returns when a backend block makes the
// list carry a real run status (KNOWN_LIMITATIONS.md).
export function BidDashboard() {
  const { organizationId } = useAuth();
  const [tenders, setTenders] = useState<TenderResponse[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!organizationId) {
      // AuthProvider sets organizationId to null when the user has no
      // membership at all - without this, tenders stays null forever
      // and the screen shows "Loading..." permanently instead of an
      // explanation.
      setError("No organization is linked to this account.");
      return;
    }
    let cancelled = false;
    listTenders(organizationId)
      .then((result) => {
        if (!cancelled) setTenders(result);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(
          err instanceof ApiError ? `Failed to load bids (${err.status})` : "Failed to load bids",
        );
      });
    return () => {
      cancelled = true;
    };
  }, [organizationId]);

  // "Create bid" is navigation, so it stays a <Link> (role="link") styled
  // as the primary action. The tender name stays the only link per row:
  // full-flow.spec.ts:513 finds it with a substring getByRole("link").
  // "Talent library" moved to the top bar (AppShell, "Talent"). One
  // "Create bid" only - the empty state points at it rather than repeating it.
  return (
    <main>
      <div className="dashboard__header">
        <h1>Bids</h1>
        <Link to="/tenders/new" className={buttonClassName("primary")}>
          Create bid
        </Link>
      </div>

      {error && (
        <Notice variant="failed" alert>
          {error}
        </Notice>
      )}
      {tenders !== null && tenders.length === 0 && (
        <EmptyState title="No bids yet">
          Bids you create appear here. Use Create bid to start one.
        </EmptyState>
      )}
      {!error && (tenders === null || tenders.length > 0) && (
        <TableScroll label="Bids">
          <table aria-busy={tenders === null}>
            <thead>
              <tr>
                <th>Name</th>
                <th>Client</th>
              </tr>
            </thead>
            <tbody>
              {tenders === null ? (
                <SkeletonRows columns={2} />
              ) : (
                tenders.map((tender) => (
                  <tr key={tender.id}>
                    <td>
                      <Link to={`/tenders/${tender.id}`}>{tender.name}</Link>
                    </td>
                    <td>{tender.client_name ?? "-"}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </TableScroll>
      )}
    </main>
  );
}
