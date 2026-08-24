import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, listTenders } from "../api/client";
import type { TenderResponse } from "../api/client";
import { useAuth } from "../auth/useAuth";

// Screen 5 (Part J). GET /organizations/{org_id}/tenders returns the coarse
// 6-value tenders.status column directly (core/enums.py::coarse_status_for)
// - enough for a dashboard rollup without a second call per row.
export function BidDashboard() {
  const { organizationId } = useAuth();
  const [tenders, setTenders] = useState<TenderResponse[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!organizationId) return;
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

  return (
    <main>
      <h1>Bids</h1>
      <p>
        <Link to="/tenders/new">Create bid</Link> · <Link to="/talent">Talent library</Link>
      </p>

      {error && (
        <p role="alert" style={{ color: "var(--color-danger)" }}>
          {error}
        </p>
      )}
      {tenders === null && !error && <p>Loading...</p>}
      {tenders !== null && tenders.length === 0 && <p>No bids yet.</p>}
      {tenders !== null && tenders.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Client</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {tenders.map((tender) => (
              <tr key={tender.id}>
                <td>
                  <Link to={`/tenders/${tender.id}`}>{tender.name}</Link>
                </td>
                <td>{tender.client_name ?? "-"}</td>
                <td>{tender.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </main>
  );
}
