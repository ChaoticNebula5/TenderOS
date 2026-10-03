import { KB } from "./kb";

/**
 * The 23 requirement codes and names the tender asks for. Ported from
 * reference-landing.html:578-582 (`REQ`).
 */
export const REQUIREMENTS: Record<string, string> = {
  "R-01": "Instructions",
  "R-02": "Scope",
  "R-03": "Financial standing",
  "R-04": "Security certification",
  "R-05": "Quality",
  "R-06": "Environment",
  "R-07": "Relevant experience",
  "R-08": "Business continuity",
  "R-09": "Site constraints",
  "R-10": "Method",
  "R-11": "Key personnel",
  "R-12": "Governance",
  "R-13": "Supply chain",
  "R-14": "Programme",
  "R-15": "Health and safety",
  "R-16": "Vetting",
  "R-17": "Social value",
  "R-18": "Carbon",
  "R-19": "Insurance",
  "R-20": "Risk",
  "R-21": "Data protection",
  "R-22": "Pricing",
  "R-23": "Commercial",
};

export const REQUIREMENT_CODES = Object.keys(REQUIREMENTS);

/**
 * Requirement codes no document in the knowledge base covers - computed
 * from the data, never hard-coded (spec §5: "R-08 has no document: that gap
 * is computed, never hard-coded"). Ported from reference-landing.html:630.
 */
export const REQUIREMENT_GAPS = REQUIREMENT_CODES.filter((code) => !KB.some((doc) => doc.requirementCode === code));

export const STATUS_LABEL: Record<"ok" | "exp" | "old", string> = {
  ok: "Valid",
  exp: "Expiring",
  old: "Superseded",
};
