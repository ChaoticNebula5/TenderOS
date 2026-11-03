import "./SkeletonRows.css";

// Loading placeholder rows rendered INSIDE the real table's <tbody>
// (DESIGN.md: "loading = skeleton rows inside the real table, no spinner
// in content"), so the header and column widths are already in place when
// data arrives.
//
// The rows are aria-hidden and carry no text; the caller's table gets
// aria-busy while loading, which is what assistive tech reads.
//
// E2E caution for later batches: these ARE `tbody tr` elements while
// loading. Retrying assertions (toHaveCount, e.g. full-flow.spec.ts (step 3, upload))
// just wait them out, but a one-shot .count() on `table tbody tr` taken
// mid-load would include them - check each table's locators before
// adding skeleton rows to it. Batch 2 uses them only on the dashboard,
// whose rows the spec never counts.

interface SkeletonRowsProps {
  columns: number;
  rows?: number;
}

export function SkeletonRows({ columns, rows = 3 }: SkeletonRowsProps) {
  return (
    <>
      {Array.from({ length: rows }, (_, r) => (
        <tr key={r} className="skeleton-row" aria-hidden="true">
          {Array.from({ length: columns }, (_, c) => (
            <td key={c}>
              <span className="skeleton-row__bar" />
            </td>
          ))}
        </tr>
      ))}
    </>
  );
}
