import type { ReactNode } from "react";
import "./EmptyState.css";

// The one empty state (DESIGN.md Components, "Empty, loading and error
// states"): a Newsreader heading, one sentence saying what will fill this
// space, and optionally the action that does it.
//
// The heading is a <p> styled as a heading, not an <h2>/<h3>: the E2E finds
// gate panels and blocker lists by heading text and sibling position
// (full-flow.spec.ts:55, :1454), and an extra heading inside a screen's
// <main> is one more thing those locators could hit. The screen's own <h1>
// stays the page's real heading.

interface EmptyStateProps {
  title: string;
  children: ReactNode;
  action?: ReactNode;
}

export function EmptyState({ title, children, action }: EmptyStateProps) {
  return (
    <div className="empty-state">
      <p className="empty-state__title">{title}</p>
      <p className="empty-state__body">{children}</p>
      {action && <div className="empty-state__action">{action}</div>}
    </div>
  );
}
