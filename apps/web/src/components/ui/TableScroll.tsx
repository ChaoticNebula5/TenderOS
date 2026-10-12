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
//
// stickyLastColumn: also pins the last column to the right edge, for
// tables whose row actions live there - DESIGN.md Layout ("row actions stay
// reachable without horizontal scroll"). The actions cannot move to the
// first column instead: full-flow.spec.ts reads cells by position (e.g.
// :653, td index 5 on the requirement register).

interface TableScrollProps {
  label: string;
  stickyLastColumn?: boolean;
  children: ReactNode;
}

export function TableScroll({ label, stickyLastColumn = false, children }: TableScrollProps) {
  const className = stickyLastColumn ? "table-scroll table-scroll--sticky-last" : "table-scroll";
  return (
    <div className={className} role="region" aria-label={label} tabIndex={0}>
      {children}
    </div>
  );
}
