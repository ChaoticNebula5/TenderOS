import type { GateType } from "../api/client";

// Plan item 73: where each gate is decided. One entry per gate type, so no
// gate can pause a run without a screen:
//
// - `Record<GateType, ...>`: a gate type in client.ts's GateResponse union
//   with no entry here fails `tsc -b`;
// - tests/unit/test_every_gate_has_a_screen.py reads the keys between the
//   two marker lines below and fails CI when they differ from the backend's
//   GATE_TYPES, or from the gate types the graph's nodes open;
// - gateScreens.test.tsx renders each entry's route with a pending gate of
//   that type and fails when its heading and panel are missing.
//
// `path` is relative to /tenders/:tenderId ("" is the Documents page).
// `heading` is the <h2> the screen renders right before the panel's
// <section>, the shape full-flow.spec.ts's gatePanel() finds (the five
// headings it uses are kept as they were). `label` names the gate in a
// sentence ("Waiting for review at Compliance review", plan item 74).
export interface GateScreen {
  path: string;
  heading: string;
  label: string;
}

export const GATE_SCREENS: Readonly<Record<GateType, GateScreen>> = {
  // gate-screens:begin (one key per line; read by test_every_gate_has_a_screen.py)
  requirement_review: {
    path: "requirements",
    heading: "Requirement review gate",
    label: "Requirement review",
  },
  cost_budget: { path: "", heading: "Cost budget gate", label: "Cost budget" },
  compliance_review: {
    path: "compliance",
    heading: "Compliance review gate",
    label: "Compliance review",
  },
  resource_selection: {
    path: "team",
    heading: "Resource selection gate",
    label: "Resource selection",
  },
  commercial_approval: {
    path: "commercial",
    heading: "Commercial approval gate",
    label: "Commercial approval",
  },
  finding_resolution: {
    path: "redteam",
    heading: "Finding resolution gate",
    label: "Finding resolution",
  },
  final_readiness: {
    path: "readiness",
    heading: "Final readiness gate",
    label: "Final readiness",
  },
  // gate-screens:end
};

// The gate's screen, as a path from the app's root.
export function gateScreenPath(tenderId: string, gateType: GateType): string {
  const { path } = GATE_SCREENS[gateType];
  return path ? `/tenders/${tenderId}/${path}` : `/tenders/${tenderId}`;
}
