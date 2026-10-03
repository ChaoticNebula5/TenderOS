/**
 * The 40-document knowledge base the whole world is built from.
 * Ported verbatim (facts, filenames, requirement codes, INR figures) from
 * reference-landing.html's `KB` array (docs/reference-landing.html:586-627).
 * Spec §5: "None of it should live in components."
 */

export type DocStatus = "ok" | "exp" | "old";

export type TextureSpec =
  | { kind: "cv"; name: string }
  | { kind: "cert"; kicker: string; title: string; sub: string; badge?: string }
  | { kind: "report"; title1: string; title2: string; rows: [string, string][] }
  | { kind: "table"; kicker: string; title: string; cols?: number }
  | { kind: "letter"; kicker: string; title: string; stamp?: string }
  | { kind: "policy"; kicker: string; title: string }
  | { kind: "org"; kicker: string; title: string }
  | { kind: "drawing"; title: string }
  | { kind: "gantt"; kicker: string; title: string };

export interface KbDoc {
  /** Document category shown on the readable page's header, e.g. "Résumé". */
  type: string;
  page: number;
  filename: string;
  /** The one fact this document contributes to the knowledge base. */
  fact: string;
  requirementCode: string;
  requirementName: string;
  status: DocStatus;
  /** Index into a proposal claim this document grounds, or -1 if it grounds none. */
  claimIndex: number;
  texture: TextureSpec;
}

export const KB: KbDoc[] = [
  { type: "Résumé", page: 3, filename: "A_Mehta_CV.pdf", fact: "Fourteen years on metro rail programmes, RDSO signalling competency current.", requirementCode: "R-11", requirementName: "Key personnel", status: "ok", claimIndex: 0, texture: { kind: "cv", name: "A. Mehta" } },
  { type: "Clarification log", page: 4, filename: "ClarificationLog_07.pdf", fact: "The buyer confirms GST at 12% applies to this works contract.", requirementCode: "R-02", requirementName: "Scope", status: "ok", claimIndex: -1, texture: { kind: "letter", kicker: "CLARIFICATION LOG", title: "Response 7" } },
  { type: "Certificate", page: 2, filename: "ISO27001_2024.pdf", fact: "Certified to ISO/IEC 27001:2022, expiring four months before contract start.", requirementCode: "R-04", requirementName: "Security certification", status: "exp", claimIndex: -1, texture: { kind: "cert", kicker: "CERTIFICATE OF REGISTRATION", title: "ISO 27001", sub: "Information security", badge: "EXPIRING" } },
  { type: "Rate card", page: 1, filename: "RateCard_Band3.xlsx", fact: "Band 3 engineer day rate ₹22,000, fixed for 24 months.", requirementCode: "R-22", requirementName: "Pricing", status: "ok", claimIndex: -1, texture: { kind: "table", kicker: "RATE CARD", title: "Band 3 rates", cols: 5 } },
  { type: "Résumé", page: 2, filename: "R_Iyer_CV.pdf", fact: "Chartered civil engineer, nine years on live railway blocks.", requirementCode: "R-11", requirementName: "Key personnel", status: "ok", claimIndex: -1, texture: { kind: "cv", name: "R. Iyer" } },
  { type: "Addendum", page: 2, filename: "Addendum_02.pdf", fact: "The submission deadline moves to 14 November, superseding the RFP timetable.", requirementCode: "R-01", requirementName: "Instructions", status: "ok", claimIndex: -1, texture: { kind: "letter", kicker: "ADDENDUM", title: "Timetable change", stamp: "ISSUED" } },
  { type: "Org chart", page: 1, filename: "OrgChart_Delivery.pdf", fact: "A three-tier delivery team with eleven named roles.", requirementCode: "R-12", requirementName: "Governance", status: "ok", claimIndex: -1, texture: { kind: "org", kicker: "ORGANISATION", title: "Delivery team" } },
  { type: "Policy", page: 12, filename: "HealthSafety_Policy_v6.pdf", fact: "Lost time injury frequency 0.12 per lakh hours worked, three-year average.", requirementCode: "R-15", requirementName: "Health and safety", status: "ok", claimIndex: -1, texture: { kind: "policy", kicker: "POLICY", title: "Health and safety" } },
  { type: "Site plan", page: 1, filename: "SitePlan_Depot.pdf", fact: "Depot access through Gate 4 only, with no deliveries between 07:00 and 09:30.", requirementCode: "R-09", requirementName: "Site constraints", status: "ok", claimIndex: -1, texture: { kind: "drawing", title: "DEPOT GA" } },
  { type: "Insurance certificate", page: 2, filename: "PI_Certificate.pdf", fact: "Professional indemnity limit ₹100 crore any one claim, policy year 2026.", requirementCode: "R-19", requirementName: "Insurance", status: "ok", claimIndex: 1, texture: { kind: "cert", kicker: "CERTIFICATE OF INSURANCE", title: "₹100 cr", sub: "Professional indemnity" } },
  { type: "Insurance schedule", page: 3, filename: "PI_Schedule_2023.pdf", fact: "PI limit ₹40 crore, replaced by the 2026 certificate and never cited.", requirementCode: "R-19", requirementName: "Insurance", status: "old", claimIndex: -1, texture: { kind: "letter", kicker: "SCHEDULE", title: "PI limits 2023", stamp: "VOID" } },
  { type: "Programme", page: 6, filename: "Programme_Baseline.pdf", fact: "Mobilisation completes six weeks after award.", requirementCode: "R-14", requirementName: "Programme", status: "ok", claimIndex: -1, texture: { kind: "gantt", kicker: "PROGRAMME", title: "Baseline rev C" } },
  { type: "Certificate", page: 2, filename: "ISO9001_Cert.pdf", fact: "Quality management certified, valid to 2028.", requirementCode: "R-05", requirementName: "Quality", status: "ok", claimIndex: -1, texture: { kind: "cert", kicker: "CERTIFICATE OF REGISTRATION", title: "ISO 9001", sub: "Quality management" } },
  { type: "Résumé", page: 3, filename: "S_Nair_CV.pdf", fact: "Signalling design lead on two metro resignalling schemes.", requirementCode: "R-11", requirementName: "Key personnel", status: "ok", claimIndex: -1, texture: { kind: "cv", name: "S. Nair" } },
  { type: "Register", page: 2, filename: "Subcontractor_Register.xlsx", fact: "Seven approved subcontractors, two of them registered MSMEs.", requirementCode: "R-13", requirementName: "Supply chain", status: "ok", claimIndex: -1, texture: { kind: "table", kicker: "REGISTER", title: "Subcontractors", cols: 4 } },
  { type: "Method statement", page: 9, filename: "MS_NightPossessions.pdf", fact: "Night traffic blocks run 01:00 to 04:30 with a thirty minute handback buffer.", requirementCode: "R-10", requirementName: "Method", status: "ok", claimIndex: -1, texture: { kind: "policy", kicker: "METHOD STATEMENT", title: "Night possessions" } },
  { type: "Accounts", page: 24, filename: "Accounts_FY25.pdf", fact: "Turnover ₹402 crore, three-year average above the ₹200 crore threshold.", requirementCode: "R-03", requirementName: "Financial standing", status: "ok", claimIndex: -1, texture: { kind: "table", kicker: "ANNUAL ACCOUNTS", title: "FY 2025", cols: 3 } },
  { type: "Case study", page: 5, filename: "PastPerf_Metro.docx", fact: "Blue Line resignalling for DMRC, ₹48 crore, completed 2023.", requirementCode: "R-07", requirementName: "Relevant experience", status: "ok", claimIndex: 3, texture: { kind: "report", title1: "Blue Line", title2: "resignalling", rows: [["CLIENT", "DMRC"], ["VALUE", "₹48 crore"], ["YEAR", "2023"], ["ROLE", "Principal contractor"]] } },
  { type: "Reference", page: 1, filename: "Reference_DMRC.pdf", fact: "The client reference rates delivery excellent on programme and on safety.", requirementCode: "R-07", requirementName: "Relevant experience", status: "ok", claimIndex: -1, texture: { kind: "letter", kicker: "CLIENT REFERENCE", title: "Delhi Metro Rail Corporation" } },
  { type: "Register", page: 2, filename: "Security_Clearances.xlsx", fact: "Police verification complete for eleven staff, four hold restricted-area passes.", requirementCode: "R-16", requirementName: "Vetting", status: "ok", claimIndex: -1, texture: { kind: "table", kicker: "REGISTER", title: "Security clearances", cols: 4 } },
  { type: "Case study", page: 4, filename: "PastPerf_Crossrail.docx", fact: "Station fit-out package, ₹30 crore, completed 2021.", requirementCode: "R-07", requirementName: "Relevant experience", status: "ok", claimIndex: -1, texture: { kind: "report", title1: "Station", title2: "fit-out", rows: [["CLIENT", "NCRTC"], ["VALUE", "₹30 crore"], ["YEAR", "2021"], ["ROLE", "Subcontractor"]] } },
  { type: "Plan", page: 8, filename: "Carbon_Plan.pdf", fact: "Net zero by 2070, in line with the national target.", requirementCode: "R-18", requirementName: "Carbon", status: "ok", claimIndex: -1, texture: { kind: "policy", kicker: "CARBON REDUCTION PLAN", title: "Net zero 2070" } },
  { type: "Risk register", page: 3, filename: "RiskRegister.xlsx", fact: "Twenty-three live risks, four of them rated high.", requirementCode: "R-20", requirementName: "Risk", status: "ok", claimIndex: -1, texture: { kind: "table", kicker: "RISK REGISTER", title: "Live risks", cols: 5 } },
  { type: "Résumé", page: 2, filename: "J_Patel_CV.pdf", fact: "Commercial manager, FIDIC contracts accredited.", requirementCode: "R-11", requirementName: "Key personnel", status: "ok", claimIndex: -1, texture: { kind: "cv", name: "J. Patel" } },
  { type: "Audit report", page: 9, filename: "CERTIn_Audit_2026.pdf", fact: "CERT-In empanelled audit closed with no high findings, January 2026.", requirementCode: "R-04", requirementName: "Security certification", status: "ok", claimIndex: -1, texture: { kind: "report", title1: "CERT-In", title2: "security audit", rows: [["AUDITOR", "Empanelled"], ["SCOPE", "SCADA and IT"], ["FINDINGS", "0 high"], ["DATE", "Jan 2026"]] } },
  { type: "Statement", page: 4, filename: "Labour_Compliance.pdf", fact: "EPF and ESI contributions current for all 212 employees.", requirementCode: "R-17", requirementName: "Social value", status: "ok", claimIndex: -1, texture: { kind: "policy", kicker: "STATEMENT", title: "Labour compliance" } },
  { type: "Case study", page: 4, filename: "PastPerf_TramDepot.docx", fact: "Depot upgrade for CMRL, ₹19 crore, completed 2022.", requirementCode: "R-07", requirementName: "Relevant experience", status: "ok", claimIndex: -1, texture: { kind: "report", title1: "Depot", title2: "upgrade", rows: [["CLIENT", "CMRL"], ["VALUE", "₹19 crore"], ["YEAR", "2022"], ["ROLE", "Principal contractor"]] } },
  { type: "Training matrix", page: 2, filename: "Training_Matrix.xlsx", fact: "Track competency current for 42 of 44 operatives, two renewals due.", requirementCode: "R-15", requirementName: "Health and safety", status: "exp", claimIndex: -1, texture: { kind: "table", kicker: "TRAINING MATRIX", title: "Competence", cols: 6 } },
  { type: "Certificate", page: 2, filename: "ISO14001_Cert.pdf", fact: "Environmental management certified, valid to 2027.", requirementCode: "R-06", requirementName: "Environment", status: "ok", claimIndex: -1, texture: { kind: "cert", kicker: "CERTIFICATE OF REGISTRATION", title: "ISO 14001", sub: "Environmental management" } },
  { type: "Data agreement", page: 6, filename: "DPA_Processor_Terms.docx", fact: "Processor terms drafted to the DPDP Act 2023.", requirementCode: "R-21", requirementName: "Data protection", status: "ok", claimIndex: -1, texture: { kind: "policy", kicker: "AGREEMENT", title: "Data processing" } },
  { type: "Plant list", page: 1, filename: "Plant_Register.xlsx", fact: "Two road-rail vehicles owned outright, both RDSO certified.", requirementCode: "R-10", requirementName: "Method", status: "ok", claimIndex: -1, texture: { kind: "table", kicker: "PLANT REGISTER", title: "Owned plant", cols: 4 } },
  { type: "Plan", page: 7, filename: "SocialValue_Plan.pdf", fact: "Twelve NAPS apprenticeships committed over the contract term.", requirementCode: "R-17", requirementName: "Social value", status: "ok", claimIndex: -1, texture: { kind: "gantt", kicker: "SOCIAL VALUE", title: "Delivery plan" } },
  { type: "Insurance certificate", page: 2, filename: "WC_Policy.pdf", fact: "Employees' compensation policy in force for all site staff.", requirementCode: "R-19", requirementName: "Insurance", status: "ok", claimIndex: -1, texture: { kind: "cert", kicker: "CERTIFICATE OF INSURANCE", title: "₹25 crore", sub: "Employees' compensation" } },
  { type: "Quality plan", page: 10, filename: "Quality_Plan.docx", fact: "An inspection and test plan with fourteen hold points.", requirementCode: "R-05", requirementName: "Quality", status: "ok", claimIndex: -1, texture: { kind: "policy", kicker: "QUALITY PLAN", title: "Inspection and test" } },
  { type: "Minutes", page: 3, filename: "Board_Minutes_Q2.pdf", fact: "The bid is approved and signed by two directors.", requirementCode: "R-01", requirementName: "Instructions", status: "ok", claimIndex: -1, texture: { kind: "letter", kicker: "BOARD MINUTES", title: "Quarter two" } },
  { type: "Site plan", page: 1, filename: "Possession_Limits.pdf", fact: "Worksite limits run between chainage 12.0 and 14.0 km.", requirementCode: "R-09", requirementName: "Site constraints", status: "ok", claimIndex: -1, texture: { kind: "drawing", title: "WORKSITE" } },
  { type: "Register", page: 2, filename: "Environmental_Aspects.xlsx", fact: "CPCB noise limit 70 dB at the boundary for night working.", requirementCode: "R-06", requirementName: "Environment", status: "ok", claimIndex: -1, texture: { kind: "table", kicker: "ASPECTS REGISTER", title: "Environment", cols: 4 } },
  { type: "Plan", page: 5, filename: "Handover_Plan.pdf", fact: "Operation and maintenance manuals within 28 days of completion.", requirementCode: "R-14", requirementName: "Programme", status: "ok", claimIndex: -1, texture: { kind: "gantt", kicker: "HANDOVER", title: "Close-out plan" } },
  { type: "Warranty", page: 3, filename: "Warranty_Terms.pdf", fact: "A twelve-month defect liability period on all installed equipment.", requirementCode: "R-23", requirementName: "Commercial", status: "ok", claimIndex: -1, texture: { kind: "letter", kicker: "WARRANTY", title: "Defects period" } },
  { type: "Lessons learned", page: 6, filename: "LessonsLearned_2023.pdf", fact: "Block overruns cut by forty percent after the 2023 review.", requirementCode: "R-10", requirementName: "Method", status: "ok", claimIndex: -1, texture: { kind: "policy", kicker: "REVIEW", title: "Lessons learned" } },
];

/** Stack positions (0-indexed) of the six clauses `data/clauses.ts` cites, in pack order. */
export const MANDATORY_STACK_POSITIONS = [3, 8, 14, 21, 29, 35] as const;
