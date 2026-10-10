import type { StatusTone } from "../../status/statusMap";
import { StatusIcon } from "./icons";
import "./StatusBadge.css";

// The one status badge (DESIGN.md Components, "Status badge"): icon + label
// in the status hue on a 14% tint of that hue. Presentational only - it is
// handed a tone and a label and never looks at a backend value itself;
// statusMap.ts decides both, so a screen writes
//   <StatusBadge {...describeStatus("gate", gate.status)} />
//
// A <span>, never <strong>: full-flow.spec.ts:1451 asserts on the page's
// only <strong> (the readiness verdict) in strict mode.

interface StatusBadgeProps {
  tone: StatusTone;
  label: string;
}

export function StatusBadge({ tone, label }: StatusBadgeProps) {
  return (
    <span className={`status-badge status-badge--${tone}`}>
      <StatusIcon tone={tone} className="status-badge__icon" />
      {label}
    </span>
  );
}
