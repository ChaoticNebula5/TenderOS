import { describe, expect, it } from "vitest";
import {
  STATUS_MAP,
  describeApproved,
  describeFinding,
  describeGraphFreshness,
  describeSelected,
  describeStatusKeepingRawWord,
  describeReadiness,
  describeStatus,
  describeVerified,
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
    // Batch 4: every boolean describer, both values, and every finding
    // combination over the severity family.
    for (const flag of [true, false]) {
      expect(describeApproved(flag).label).not.toBe("Ready");
      expect(describeSelected(flag).label).not.toBe("Ready");
      expect(describeVerified(flag, true).label).not.toBe("Ready");
      expect(describeVerified(flag, false).label).not.toBe("Ready");
      for (const severity of Object.keys(STATUS_MAP.severity)) {
        expect(describeFinding(flag, severity).label).not.toBe("Ready");
      }
      // Batch 5: graph freshness. (The proposalSection and validity families
      // are covered by the STATUS_MAP loop above.)
      expect(describeGraphFreshness(flag).label).not.toBe("Ready");
    }
  });

  it("maps proposal sections and evidence validity as decided at batch 5 plan review", () => {
    expect(describeStatus("proposalSection", "draft")).toEqual({ tone: "neutral", label: "Draft" });
    expect(describeStatus("proposalSection", "review")).toEqual({
      tone: "blocked",
      label: "In review",
    });
    expect(describeStatus("proposalSection", "approved")).toEqual({
      tone: "ready",
      label: "Approved",
    });
    expect(describeStatus("validity", "valid")).toEqual({ tone: "ready", label: "Valid" });
    expect(describeStatus("validity", "invalid")).toEqual({ tone: "failed", label: "Invalid" });
    // Failed, not Warning: the backend treats expired exactly like invalid.
    expect(describeStatus("validity", "expired")).toEqual({ tone: "failed", label: "Expired" });
    expect(describeStatus("validity", "unknown")).toEqual({ tone: "neutral", label: "Unknown" });
    expect(describeGraphFreshness(true)).toEqual({ tone: "ready", label: "Fresh" });
    expect(describeGraphFreshness(false)).toEqual({ tone: "warning", label: "Stale" });
  });

  // The proposal claim tag and the red-team severity cell are read as text by
  // full-flow.spec.ts (:971 "/ unsupported]", :1410 "critical"), so their
  // badges keep the raw word where the label is just that word capitalised.
  it("keeps the raw word only where the label is that word capitalised", () => {
    expect(describeStatusKeepingRawWord("grounding", "grounded")).toEqual({
      tone: "ready",
      label: "grounded",
      keepsRawWord: true,
    });
    expect(describeStatusKeepingRawWord("grounding", "unsupported")).toEqual({
      tone: "failed",
      label: "unsupported",
      keepsRawWord: true,
    });
    expect(describeStatusKeepingRawWord("grounding", "needs_review")).toEqual({
      tone: "blocked",
      label: "Needs review",
      keepsRawWord: false,
    });
    expect(describeStatusKeepingRawWord("grounding", "not_required")).toEqual({
      tone: "neutral",
      label: "Not required",
      keepsRawWord: false,
    });
    for (const severity of ["critical", "major", "minor"]) {
      const d = describeStatusKeepingRawWord("severity", severity);
      expect(d.label).toBe(severity);
      expect(d.keepsRawWord).toBe(true);
    }
    // Not a backend value (only a test fixture uses it): falls back to the raw word.
    expect(describeStatusKeepingRawWord("severity", "high")).toEqual({
      tone: "neutral",
      label: "high",
      keepsRawWord: true,
    });
  });

  it("describes approval and selection: Approved/Selected as Ready-toned, the negatives Neutral", () => {
    expect(describeApproved(true)).toEqual({ tone: "ready", label: "Approved" });
    expect(describeApproved(false)).toEqual({ tone: "neutral", label: "Not approved" });
    expect(describeSelected(true)).toEqual({ tone: "ready", label: "Selected" });
    expect(describeSelected(false)).toEqual({ tone: "neutral", label: "Not selected" });
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

  it.each(["constructor", "toString", "__proto__"])(
    "treats the Object.prototype key %s as unknown, not as a map entry",
    (key) => {
      expect(describeStatus("gate", key)).toEqual({ tone: "neutral", label: key });
    },
  );

  it("describes verification: Verified, or Not verified as Warning when mandatory, Neutral when not", () => {
    expect(describeVerified(true, true)).toEqual({ tone: "ready", label: "Verified" });
    expect(describeVerified(true, false)).toEqual({ tone: "ready", label: "Verified" });
    expect(describeVerified(false, true)).toEqual({ tone: "warning", label: "Not verified" });
    expect(describeVerified(false, false)).toEqual({ tone: "neutral", label: "Not verified" });
  });

  // UploadAndProcessingStatus renders a parse badge whose DOM text is the
  // backend word ("parsed"), capitalised by CSS, because full-flow.spec.ts
  // :558-566 reads that cell's textContent and compares it to "parsed".
  // That only shows the right words if every parse label IS the capitalised
  // backend word - this test keeps the map and the screen from drifting.
  it("keeps every parse label equal to its backend word, capitalised", () => {
    for (const [value, descriptor] of Object.entries(STATUS_MAP.parse)) {
      expect(descriptor.label).toBe(value.charAt(0).toUpperCase() + value.slice(1));
    }
  });
});
