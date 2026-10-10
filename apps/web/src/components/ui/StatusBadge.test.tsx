import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import {
  describeFinding,
  describeReadiness,
  describeStatus,
} from "../../status/statusMap";
import type { StatusDescriptor } from "../../status/statusMap";
import { StatusBadge } from "./StatusBadge";

// One render test per tone with fixture props (DESIGN.md Restyle batches,
// "Every status gets a render test"): no tender in the live data reaches
// Ready, so this is the only place the Ready badge is proven to render.
// Each fixture goes through the real label map, so a test also fails if the
// map's tone for that value changes.
const FIXTURES: [string, StatusDescriptor][] = [
  ["ready", describeReadiness(true)],
  ["running", describeStatus("run", "running")],
  ["warning", describeStatus("compliance", "partially_compliant")],
  ["blocked", describeStatus("gate", "pending")],
  ["failed", describeFinding(false, "critical")],
  ["neutral", describeStatus("task", "skipped")],
];

const EXPECTED_LABELS: Record<string, string> = {
  ready: "Ready",
  running: "Running",
  warning: "Partly compliant",
  blocked: "Awaiting decision",
  failed: "Critical",
  neutral: "Skipped",
};

// Same as the existing render tests: vitest globals are off, so Testing
// Library cannot register its own automatic cleanup.
afterEach(() => {
  cleanup();
});

describe("StatusBadge", () => {
  it.each(FIXTURES)("renders the %s tone with icon, label and class", (tone, descriptor) => {
    const { container } = render(<StatusBadge {...descriptor} />);
    const badge = screen.getByText(EXPECTED_LABELS[tone]);

    expect(descriptor.tone).toBe(tone);
    expect(badge).toHaveClass("status-badge", `status-badge--${tone}`);
    const icon = badge.querySelector("svg[data-icon]");
    expect(icon).not.toBeNull();
    expect(icon).toHaveAttribute("aria-hidden", "true");
    // Not a <strong>: full-flow.spec.ts:1451 needs the readiness verdict to
    // be the page's only <strong>.
    expect(container.querySelector("strong")).toBeNull();
  });
});
