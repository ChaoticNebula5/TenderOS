import type { ReactNode } from "react";
import { InfoIcon, StatusIcon } from "./icons";
import "./Notice.css";

// The one notice (DESIGN.md Components, "Notices"): icon + message on a
// tint, variants failed / warning / info. Replaces the repeated
// <p role="alert" style={{ color: "var(--color-danger)" }}> pattern.
//
// role="alert" only when the caller passes `alert` - DESIGN.md keeps it
// "only for errors that appear after an action", so a notice that is
// present on first render (e.g. a load failure shown with the page) is not
// announced as if something just happened. Batch 2's callers (Login,
// BidDashboard, CreateBid) all pass it because each has role="alert"
// today, and a restyle does not change what assistive tech announces;
// whether the dashboard's load error should keep it is a per-screen call
// for later, not a side effect of this component.
//
// A <p>, as the notices it replaces are: it keeps the prose measure from
// index.css and the same element type for anything that queries them
// (full-flow.spec.ts (step 10, claim trace) finds a notice by role="alert").

type NoticeVariant = "failed" | "warning" | "info";

interface NoticeProps {
  variant: NoticeVariant;
  alert?: boolean;
  children: ReactNode;
}

export function Notice({ variant, alert = false, children }: NoticeProps) {
  return (
    <p className={`notice notice--${variant}`} role={alert ? "alert" : undefined}>
      {variant === "info" ? (
        <InfoIcon className="notice__icon" />
      ) : (
        <StatusIcon tone={variant} className="notice__icon" />
      )}
      <span>{children}</span>
    </p>
  );
}
