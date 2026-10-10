import { describe, expect, it } from "vitest";
import {
  STATUS_MAP,
  describeFinding,
  describeReadiness,
  describeStatus,
} from "./statusMap";

describe("statusMap", () => {
  it("maps gates as DESIGN.md decided: rejected is Failed, changes_requested is Blocked", () => {
    expect(describeStatus("gate", "approved")).toEqual({ tone: "ready", label: "Approved" });
    expect(describeStatus("gate", "rejected")).toEqual({ tone: "failed", label: "Rejected" });
    expect(describeStatus("gate", "changes_requested")).toEqual({
      tone: "blocked",
      label: "Changes requested",
    });
    expect(describeStatus("gate", "pending")).toEqual({
      tone: "blocked",
      label: "Awaiting decision",
    });
  });

  it("labels a completed run and task Done, never Ready", () => {
    expect(describeStatus("run", "completed")).toEqual({ tone: "ready", label: "Done" });
    expect(describeStatus("task", "completed")).toEqual({ tone: "ready", label: "Done" });
  });

  it("reserves the word Ready for the readiness verdict alone", () => {
    for (const family of Object.values(STATUS_MAP)) {
      for (const descriptor of Object.values(family)) {
        expect(descriptor.label).not.toBe("Ready");
      }
    }
    expect(describeReadiness(true)).toEqual({ tone: "ready", label: "Ready" });
    expect(describeReadiness(false)).toEqual({ tone: "blocked", label: "Not ready" });
  });

  it("never shows a raw enum for a known value", () => {
    for (const family of Object.values(STATUS_MAP)) {
      for (const [value, descriptor] of Object.entries(family)) {
        expect(descriptor.label).not.toBe(value);
        expect(descriptor.label).not.toContain("_");
      }
    }
  });

  it("shows a resolved finding as Resolved whatever its severity, an open one by severity", () => {
    expect(describeFinding(true, "critical")).toEqual({ tone: "ready", label: "Resolved" });
    expect(describeFinding(false, "critical")).toEqual({ tone: "failed", label: "Critical" });
    expect(describeFinding(false, "major")).toEqual({ tone: "warning", label: "Major" });
  });

  it("falls back to neutral with the raw value for a value it does not know", () => {
    expect(describeStatus("run", "some_new_status")).toEqual({
      tone: "neutral",
      label: "some_new_status",
    });
  });
});
