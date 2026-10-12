// The one status label map (DESIGN.md "Status mapping" and "Label map").
// Turns a backend status value into a tone (which colour + icon) and the
// words an operator reads. No screen shows a raw enum (No Raw Enum Rule);
// they all come through here.
//
// Vocabulary is the backend's (CHECK constraints, cited per family below,
// paths under packages/tenderos_backend/src/tenderos_backend/). The tone
// column is DESIGN.md's mapping table (DESIGN.md:274-281); labels are its
// Label map (DESIGN.md:296-317). Every row quotes the DESIGN.md line it
// implements; rows DESIGN.md does not specify are marked
// "chosen, not in DESIGN".
//
// "Ready" is reserved for the readiness verdict (DESIGN.md:309-311). No
// family below may use it; statusMap.test.ts enforces that.

export type StatusTone = "ready" | "running" | "warning" | "blocked" | "failed" | "neutral";

export interface StatusDescriptor {
  tone: StatusTone;
  label: string;
}

export type StatusFamily =
  | "gate"
  | "run"
  | "task"
  | "parse"
  | "severity"
  | "grounding"
  | "compliance"
  | "importItem"
  | "proposalSection"
  | "validity"
  | "importBatch"
  | "knowledgeItem"
  | "availability"
  | "profileReview";

type FamilyMap = Readonly<Record<string, StatusDescriptor>>;

// Gate - db/models/workflow.py:86
const gate: FamilyMap = {
  // DESIGN.md:302 'Gate `approved` -> **"Approved"**'; :276 Ready row, Gate `approved`
  approved: { tone: "ready", label: "Approved" },
  // DESIGN.md:302 '`rejected` -> "Rejected"'; :283 '`rejected` = Failed'
  rejected: { tone: "failed", label: "Rejected" },
  // DESIGN.md:303 '`changes_requested` -> "Changes requested"'; :283-284 '`changes_requested` = Blocked'
  changes_requested: { tone: "blocked", label: "Changes requested" },
  // DESIGN.md:303-304 '`pending` -> "Awaiting decision"'; :278 Blocked row, Gate `pending`
  pending: { tone: "blocked", label: "Awaiting decision" },
};

// Run - db/models/workflow.py:15
const run: FamilyMap = {
  // DESIGN.md:305-306 '`queued` and task `pending` -> "Queued"'; :277 Running row, Run `queued`
  queued: { tone: "running", label: "Queued" },
  // DESIGN.md:305 '`running` -> "Running"'; :277 Running row, Run `running`
  running: { tone: "running", label: "Running" },
  // DESIGN.md:306-307 '`waiting_for_human` -> "Waiting for review"'; :278 Blocked row
  waiting_for_human: { tone: "blocked", label: "Waiting for review" },
  // DESIGN.md:305 'Run and task `completed` -> **"Done"**'; :276 Ready row
  completed: { tone: "ready", label: "Done" },
  // DESIGN.md:307 '`failed` -> "Failed"'; :279 Failed row
  failed: { tone: "failed", label: "Failed" },
  // DESIGN.md:307 '`cancelled` -> "Cancelled"'; :281 Neutral row
  cancelled: { tone: "neutral", label: "Cancelled" },
};

// Task - db/models/workflow.py:42
const task: FamilyMap = {
  // DESIGN.md:306 'task `pending` -> "Queued"'; :277 Running row, Task `pending`
  pending: { tone: "running", label: "Queued" },
  // DESIGN.md:305 'Run and task ... `running` -> "Running"'; :277 Running row
  running: { tone: "running", label: "Running" },
  // DESIGN.md:305 'Run and task `completed` -> **"Done"**'; :276 Ready row
  completed: { tone: "ready", label: "Done" },
  // DESIGN.md:307 '`failed` -> "Failed"' (run-and-task bullet); :279 Failed row
  failed: { tone: "failed", label: "Failed" },
  // DESIGN.md:307-308 '`skipped` -> "Skipped"'; :281 Neutral row
  skipped: { tone: "neutral", label: "Skipped" },
  // DESIGN.md:308 'task `blocked` -> "Blocked"'; :278 Blocked row
  blocked: { tone: "blocked", label: "Blocked" },
};

// Document parse - db/models/document.py:25. Tones from DESIGN.md:276-279
// (Ready `parsed`, Running `uploaded, queued, parsing`, Failed `failed`);
// DESIGN.md gives no labels for this family.
const parse: FamilyMap = {
  // chosen, not in DESIGN (label); tone :277 Running row
  uploaded: { tone: "running", label: "Uploaded" },
  // chosen, not in DESIGN (label); tone :277 Running row
  queued: { tone: "running", label: "Queued" },
  // chosen, not in DESIGN (label); tone :277 Running row
  parsing: { tone: "running", label: "Parsing" },
  // chosen, not in DESIGN (label); tone :276 Ready row
  parsed: { tone: "ready", label: "Parsed" },
  // chosen, not in DESIGN (label); tone :279 Failed row
  failed: { tone: "failed", label: "Failed" },
};

// Severity - db/models/redteam.py:22, db/models/commercial.py:68,
// agents/schemas/redteam.py:5, agents/schemas/commercial.py:5.
// Applies to OPEN findings only (DESIGN.md:287-291) - see describeFinding.
const severity: FamilyMap = {
  // DESIGN.md:314 'Severity: "Critical"'; :279 Failed row, Severity `critical`
  critical: { tone: "failed", label: "Critical" },
  // DESIGN.md:314 '"Major"'; :280 Warning row, Severity `major`
  major: { tone: "warning", label: "Major" },
  // DESIGN.md:314 '"Minor"'; :281 Neutral row, Severity `minor`
  minor: { tone: "neutral", label: "Minor" },
};

// Proposal claim grounding - db/models/proposal.py:58
const grounding: FamilyMap = {
  // DESIGN.md:314-315 'Grounding: "Grounded"'; :276 Ready row
  grounded: { tone: "ready", label: "Grounded" },
  // DESIGN.md:315 '"Unsupported"'; :279 Failed row
  unsupported: { tone: "failed", label: "Unsupported" },
  // DESIGN.md:315 '"Needs review"'; :278 Blocked row
  needs_review: { tone: "blocked", label: "Needs review" },
  // DESIGN.md:315 '"Not required"'; :281 Neutral row
  not_required: { tone: "neutral", label: "Not required" },
};

// Compliance entry - db/models/compliance.py:53-56
const compliance: FamilyMap = {
  // DESIGN.md:315 'Compliance: "Compliant"'; :276 Ready row
  fully_compliant: { tone: "ready", label: "Compliant" },
  // DESIGN.md:316 '"Partly compliant"'; :280 Warning row
  partially_compliant: { tone: "warning", label: "Partly compliant" },
  // DESIGN.md:316 '"Not compliant"'; :279 Failed row
  non_compliant: { tone: "failed", label: "Not compliant" },
  // DESIGN.md:316 '"Clarification needed"'; :278 Blocked row
  clarification_required: { tone: "blocked", label: "Clarification needed" },
  // DESIGN.md:316-317 '"No evidence"'; :280 Warning row and :292-294
  evidence_unavailable: { tone: "warning", label: "No evidence" },
  // DESIGN.md:317 '"Not reviewed"'; :281 Neutral row
  not_reviewed: { tone: "neutral", label: "Not reviewed" },
};

// Knowledge import item - db/models/knowledge.py:61. Tones from
// DESIGN.md:276-279 "Other" column; DESIGN.md gives no labels.
const importItem: FamilyMap = {
  // chosen, not in DESIGN (label, matches task `pending`); tone :277 'import item `pending`'
  pending: { tone: "running", label: "Queued" },
  // chosen, not in DESIGN (label); tone :277 'import item ... `extracted`'
  extracted: { tone: "running", label: "Extracted" },
  // chosen, not in DESIGN (label); tone :278 'import item `needs_review`'
  needs_review: { tone: "blocked", label: "Needs review" },
  // chosen, not in DESIGN (label); tone :276 'import item `approved`'
  approved: { tone: "ready", label: "Approved" },
  // chosen, not in DESIGN (label); tone :279 'import item `failed`, `rejected`'
  rejected: { tone: "failed", label: "Rejected" },
  // chosen, not in DESIGN (label); tone :279 'import item `failed`, `rejected`'
  failed: { tone: "failed", label: "Failed" },
};

// Proposal section - db/models/proposal.py:23. DESIGN.md has no row for
// this family (batch 5, decided at plan review).
const proposalSection: FamilyMap = {
  // chosen, not in DESIGN: a draft is nobody's to act on yet - Neutral
  draft: { tone: "neutral", label: "Draft" },
  // chosen, not in DESIGN: waiting on a reviewer - Blocked (DESIGN.md:287's meaning)
  review: { tone: "blocked", label: "In review" },
  // chosen, not in DESIGN: the gate family's word for approval (DESIGN.md:311)
  approved: { tone: "ready", label: "Approved" },
};

// Evidence validity - db/models/compliance.py:22. DESIGN.md has no row
// for this family (batch 5, decided at plan review).
const validity: FamilyMap = {
  // chosen, not in DESIGN
  valid: { tone: "ready", label: "Valid" },
  // chosen, not in DESIGN
  invalid: { tone: "failed", label: "Invalid" },
  // chosen, not in DESIGN: Failed, not Warning, because the backend treats
  // expired exactly like invalid - only verified AND 'valid' evidence can
  // ground a claim (domain/services/proposal_service.py:154, :279) or count
  // as valid evidence for readiness (domain/services/readiness_service.py:219,
  // a blocker when none is left).
  expired: { tone: "failed", label: "Expired" },
  // chosen, not in DESIGN
  unknown: { tone: "neutral", label: "Unknown" },
};

// Knowledge import batch - the status the API RETURNS, which is computed
// from the batch's items (domain/services/knowledge_service.py:1211-1240),
// not the stored column (db/models/knowledge.py:34). Batch 6, decided at
// plan review; DESIGN.md has no row for this family. A completed batch is
// shown through describeImportBatch, which splits off "Done with failures".
const importBatch: FamilyMap = {
  // chosen, not in DESIGN: no items yet - the run family's word for waiting to start
  queued: { tone: "running", label: "Queued" },
  // chosen, not in DESIGN: some items still pending or extracted
  processing: { tone: "running", label: "Processing" },
  // chosen, not in DESIGN: the import item family's word and tone
  needs_review: { tone: "blocked", label: "Needs review" },
  // chosen, not in DESIGN: the run family's word for completed (DESIGN.md:320)
  completed: { tone: "ready", label: "Done" },
  // chosen, not in DESIGN: every item failed (knowledge_service.py:1225-1226)
  failed: { tone: "failed", label: "Failed" },
};

// Knowledge item - db/models/knowledge.py:103. Batch 6, decided at plan
// review; DESIGN.md has no row for this family. Only `active` counts as
// verified evidence (domain/services/compliance_service.py:79).
const knowledgeItem: FamilyMap = {
  // chosen, not in DESIGN
  active: { tone: "ready", label: "Active" },
  // chosen, not in DESIGN: Failed, as the validity family's expired
  expired: { tone: "failed", label: "Expired" },
  // chosen, not in DESIGN: Warning - usable data that compliance does not
  // count as verified, not a workflow step waiting on a person
  unverified: { tone: "warning", label: "Unverified" },
  // chosen, not in DESIGN
  archived: { tone: "neutral", label: "Archived" },
};

// Employee availability - db/models/knowledge.py:174. Batch 6, decided at
// plan review; DESIGN.md has no row for this family.
const availability: FamilyMap = {
  // chosen, not in DESIGN
  available: { tone: "ready", label: "Available" },
  // chosen, not in DESIGN
  partially_available: { tone: "warning", label: "Partly available" },
  // chosen, not in DESIGN: Blocked, not Neutral - partly available is
  // Warning, so a Neutral "Unavailable" would read as less severe than it
  unavailable: { tone: "blocked", label: "Unavailable" },
  // chosen, not in DESIGN
  unknown: { tone: "neutral", label: "Unknown" },
};

// Employee profile review - db/models/knowledge.py:178. Batch 6, decided at
// plan review; DESIGN.md has no row for this family. Only needs_review
// (the default, :217) and verified (knowledge_service.py:1156) have a
// writer today; stale, rejected and archived are read
// (talent_service.py:236, db/repositories/knowledge.py:340) but never set.
const profileReview: FamilyMap = {
  // chosen, not in DESIGN: the import item and grounding families' word and tone
  needs_review: { tone: "blocked", label: "Needs review" },
  // chosen, not in DESIGN: describeVerified's word and tone
  verified: { tone: "ready", label: "Verified" },
  // chosen, not in DESIGN: the graph's "Stale" (Warning, DESIGN.md:295)
  stale: { tone: "warning", label: "Stale" },
  // chosen, not in DESIGN
  rejected: { tone: "failed", label: "Rejected" },
  // chosen, not in DESIGN
  archived: { tone: "neutral", label: "Archived" },
};

export const STATUS_MAP: Readonly<Record<StatusFamily, FamilyMap>> = {
  gate,
  run,
  task,
  parse,
  severity,
  grounding,
  compliance,
  importItem,
  proposalSection,
  validity,
  importBatch,
  knowledgeItem,
  availability,
  profileReview,
};

// chosen, not in DESIGN: a value the backend adds later that this map does
// not know yet. Neutral tone, and the raw value as its label - honest and
// greppable, rather than a made-up word; the No Raw Enum Rule is broken
// visibly, so the gap gets noticed and mapped instead of hidden.
//
// Object.hasOwn, not a bare STATUS_MAP[family][value] lookup: the family
// maps are plain objects, so "constructor" or "toString" would otherwise
// return an inherited Object.prototype function instead of falling back.
export function describeStatus(family: StatusFamily, value: string): StatusDescriptor {
  const familyMap = STATUS_MAP[family];
  return Object.hasOwn(familyMap, value) ? familyMap[value] : { tone: "neutral", label: value };
}

// Readiness verdict - the only place the word "Ready" appears.
// DESIGN.md:309-310 '(`ready: true` -> "Ready", `false` -> "Not ready")';
// tones :276 'readiness `ready: true`' (Ready) and :278 'readiness `ready: false`' (Blocked).
export function describeReadiness(ready: boolean): StatusDescriptor {
  return ready ? { tone: "ready", label: "Ready" } : { tone: "blocked", label: "Not ready" };
}

// Requirement verification (RequirementRegister's Verified column).
// chosen, not in DESIGN: DESIGN.md says booleans that are states become
// badges but gives no tones for "verified". An unverified MANDATORY
// requirement is Warning because it is what the requirement review gate
// counts against approval ("Unverified mandatory", the gate summary's
// unverified_mandatory_count, GateDecisionPanel's requirement_review
// summary); an unverified optional one is Neutral. Never "Ready" (reserved).
export function describeVerified(verified: boolean, mandatory: boolean): StatusDescriptor {
  if (verified) return { tone: "ready", label: "Verified" };
  return mandatory
    ? { tone: "warning", label: "Not verified" }
    : { tone: "neutral", label: "Not verified" };
}

// Compliance entry approval (ComplianceMatrix's Approved column).
// DESIGN.md:585-587: 'Booleans that are states (verified, approved,
// resolved, selected) become badges'. chosen, not in DESIGN (tones and the
// "Not approved" label): approved -> Ready "Approved", the gate family's own
// word for approval (DESIGN.md:311); not approved -> Neutral, not Blocked,
// so a matrix of unreviewed rows does not read as a wall of violet. Never
// "Ready" (reserved).
export function describeApproved(approved: boolean): StatusDescriptor {
  return approved
    ? { tone: "ready", label: "Approved" }
    : { tone: "neutral", label: "Not approved" };
}

// Candidate and knowledge-item selection (TeamWorkspace's and
// KnowledgeSelections' Selected columns). DESIGN.md:585-587 as above.
// chosen, not in DESIGN (tones and labels): selected -> Ready "Selected";
// not selected -> Neutral "Not selected". Never "Ready" (reserved).
export function describeSelected(selected: boolean): StatusDescriptor {
  return selected
    ? { tone: "ready", label: "Selected" }
    : { tone: "neutral", label: "Not selected" };
}

// Red-team and commercial findings. DESIGN.md:287-289: 'A resolved finding
// shows the Ready treatment with the label "Resolved", whatever its
// severity; an open one shows its severity badge.' DESIGN.md:317's
// '"Resolved" / "Open"' label pair is therefore only half used: an open
// finding is labelled by its severity, never "Open".
export function describeFinding(resolved: boolean, findingSeverity: string): StatusDescriptor {
  return resolved
    ? { tone: "ready", label: "Resolved" }
    : describeStatus("severity", findingSeverity);
}

// For a spec that reads a badge's text exactly (full-flow.spec.ts:971 reads
// "/ unsupported]", :1410 reads "critical"): when the mapped label is just
// the backend word capitalised, the badge can keep the raw word as its DOM
// text and let CSS draw it capitalised - the operator reads the label, the
// DOM keeps the word (the batch 3 parse-badge technique). keepsRawWord says
// whether that holds; when it does not (needs_review -> "Needs review"),
// the label is the mapped one and nothing is capitalised. An unknown value
// already falls back to its raw word, so it keeps it too.
export function describeStatusKeepingRawWord(
  family: StatusFamily,
  value: string,
): StatusDescriptor & { keepsRawWord: boolean } {
  const descriptor = describeStatus(family, value);
  const keepsRawWord = descriptor.label.toLowerCase() === value.toLowerCase();
  return { ...descriptor, label: keepsRawWord ? value : descriptor.label, keepsRawWord };
}

// Graph projection freshness (GraphExplorer). DESIGN.md:289 puts "graph
// stale" in Warning; chosen, not in DESIGN: fresh is Ready-toned "Fresh",
// shown only when the response says fresh (DESIGN.md Copy rules). Never
// "Ready" (reserved).
export function describeGraphFreshness(fresh: boolean): StatusDescriptor {
  return fresh ? { tone: "ready", label: "Fresh" } : { tone: "warning", label: "Stale" };
}

// Import batch status (TalentImportReview's Batches table). The backend
// calls a batch "completed" once no item is still in progress, even when
// some items failed (knowledge_service.py:1227-1228: only an all-failed
// batch is "failed"). Decided at batch 6 plan review: "Done" only when
// failed_items is 0; otherwise Warning "Done with failures", derived from
// the same response. Every other status goes through the family map.
export function describeImportBatch(status: string, failedItems: number): StatusDescriptor {
  if (status === "completed" && failedItems > 0) {
    return { tone: "warning", label: "Done with failures" };
  }
  return describeStatus("importBatch", status);
}
