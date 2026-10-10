import "./Button.css";

// The button look as a class string, for the one case a <button> is the
// wrong element: navigation that should look like an action (the
// dashboard's "Create bid" is a <Link>, so it stays role="link" and keeps
// working with middle-click and open-in-new-tab).
//
// Own file, not Button.tsx: react-refresh/only-export-components
// (eslint.config.js) warns when a component file also exports a function.

export type ButtonVariant = "primary" | "secondary" | "quiet" | "danger";

export function buttonClassName(variant: ButtonVariant = "secondary", extra?: string): string {
  return extra ? `btn btn--${variant} ${extra}` : `btn btn--${variant}`;
}
