import type { ReactNode } from "react";
import "./TableScroll.css";

// Horizontal scroll region for a real <table> (DESIGN.md Layout,
// "Responsive (390px)"): the table keeps its markup, the region scrolls,
// the page does not, and the first column stays put.
//
// Safe for the E2E's structural selectors: they reach tables with
// page.locator("table") and `tbody tr` / `td:nth-child(n)` (e.g.
// full-flow.spec.ts:344, :561), none of which cross this wrapper, and
// DESIGN.md's selector notes confirm a table wrapper does not break the
// heading-sibling hops at :56 and :1455 (the table is not on that axis).
//
// Focusable with a name so a keyboard user can scroll it (an overflowing
// region with no focusable content is otherwise unreachable); tabIndex 0,
// never above (DESIGN.md Accessibility, "Keyboard order").

interface TableScrollProps {
  label: string;
  children: ReactNode;
}

export function TableScroll({ label, children }: TableScrollProps) {
  return (
    <div className="table-scroll" role="region" aria-label={label} tabIndex={0}>
      {children}
    </div>
  );
}
