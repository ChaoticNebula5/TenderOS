// The tender, the company's evidence and the bid drafts. Copy is final as written
// in reference/tenderos-v3.html; HTML entities there are plain characters here.

export const BUYER = "Kaveri Metro Rail Corporation";
export const TNO = "KMRC/S&T/2026/14";

/** [page, heading, text, short name, full name]. Names only on mandatory pages. */
export type PageRow = readonly [number, string, string, string?, string?];

export const PAGES: readonly PageRow[] = [
  [4, "Notice inviting tender", "Online bids are invited for signalling and telecom works on the Phase 2 corridor, estimated at ₹96 crore."],
  [11, "Instructions to bidders", "Bids are submitted on the e-procurement portal in two covers, technical and financial."],
  [18, "Section 3.2, earnest money", "Bidders shall furnish earnest money of ₹1.92 crore as a bank guarantee, valid 45 days beyond bid validity.", "Earnest money", "Earnest money deposit"],
  [27, "Section 3.6, bid validity", "Bids shall remain valid for 180 days from technical bid opening."],
  [34, "Section 3.9, pre-bid meeting", "Queries received seven days before the pre-bid meeting are answered in a clarification log."],
  [41, "Clause 4.1, financial standing", "Average annual turnover over the last three financial years shall be at least ₹50 crore, certified by a chartered accountant.", "Turnover", "Average annual turnover"],
  [49, "Clause 4.2, net worth", "Net worth in the last audited financial year shall be positive."],
  [58, "Corrigendum 1", "Bid submission is extended to 14 November 2026. All other terms are unchanged."],
  [67, "Clause 4.3, similar works", "The bidder shall have completed three similar works in the last seven years, each costing at least 40% of the estimated cost.", "Similar works", "Similar works experience"],
  [76, "Clause 4.5, joint ventures", "A joint venture may have at most three members. The lead member holds at least 51%."],
  [85, "Section 5, scope of work", "Design, supply, installation and commissioning of CBTC signalling across 14 stations."],
  [94, "Section 5.4, interfaces", "Interfaces with rolling stock, traction power and platform screen doors are listed in Annex F."],
  [103, "Section 5.7, testing", "Factory acceptance tests are witnessed by the Employer's engineer."],
  [112, "Pre-bid clarification log", "Reply 23: work done as a subcontractor counts as a similar work only with a client certificate."],
  [122, "Clause 6.1, key personnel", "The signalling lead and two deputies shall each have at least five years of metro signalling experience.", "Key personnel", "Key personnel"],
  [131, "Clause 6.4, training", "Forty operations staff are trained before revenue service begins."],
  [142, "Section 7, quality plan", "A quality assurance plan is due within 30 days of the letter of award."],
  [153, "Section 8, safety", "An independent safety assessor certifies the system to SIL 4."],
  [164, "Clause 8.6, site security", "Site access is restricted to personnel holding a valid permit."],
  [173, "Annex B, information security", "The Contractor shall hold ISO/IEC 27001 certification, valid for the full contract term.", "ISO 27001", "ISO/IEC 27001 certification"],
  [186, "Annex C, data handling", "Signalling data is stored only on servers located in India."],
  [199, "Schedule of rates, part B", "Rates are quoted in Indian rupees, inclusive of all taxes except GST."],
  [214, "Clause 11.7, subcontracting", "All subcontractors shall be named at bid stage. Any later change needs the Employer's written consent.", "Subcontracting", "Subcontractor disclosure"],
  [228, "Integrity pact", "The bidder signs the integrity pact. Bids without it are rejected."],
];

/** Card ids of the six mandatory-criterion pages. */
export const MAND: readonly number[] = [2, 5, 8, 14, 19, 22];

export type Evidence =
  | { f: string; k: "cv"; name: string; role: string; ct?: number; rows: string[]; extra: string[] }
  | { f: string; k: "cert" }
  | { f: string; k: "ca" }
  | { f: string; k: "done"; proj: string; work: string; val: string; date: string }
  | { f: string; k: "bg" }
  | { f: string; k: "sheet" }
  | { f: string; k: "reg"; title: string; rows: [string, string][] };

export const EV: readonly Evidence[] = [
  { f: "A_Mehta_CV.pdf", k: "cv", name: "A. Mehta", role: "Signalling lead", ct: 0,
    rows: ["2018 to 2025, signalling lead, Nagpur Metro Reach 2", "2015 to 2018, signalling engineer, Kochi Metro Phase 1", "2012 to 2015, site engineer, railway S&T works"],
    extra: ["IRSE licence, category 4", "B.Tech, electrical engineering"] },
  { f: "R_Iyer_CV.pdf", k: "cv", name: "R. Iyer", role: "Deputy lead, interlocking",
    rows: ["2017 to 2025, interlocking design, Chennai Metro", "2014 to 2017, testing engineer, Bengaluru Metro", "2011 to 2014, graduate engineer"],
    extra: ["IRSE licence, category 3", "M.Tech, control systems"] },
  { f: "S_Khan_CV.pdf", k: "cv", name: "S. Khan", role: "Deputy lead, telecom",
    rows: ["2019 to 2025, telecom lead, Pune Metro Line 1", "2016 to 2019, network engineer, Mumbai Metro Line 1", "2013 to 2016, field engineer"],
    extra: ["TETRA radio system design", "B.E., electronics"] },
  { f: "ISO27001_2022.pdf", k: "cert" },
  { f: "Turnover_CA_Cert.pdf", k: "ca" },
  { f: "Completion_Nagpur.pdf", k: "done", proj: "Nagpur Metro, Reach 2", work: "Signalling and train control", val: "₹41.2 crore", date: "30 June 2023" },
  { f: "Completion_Kochi.pdf", k: "done", proj: "Kochi Metro, Phase 1", work: "Signalling and telecom", val: "₹39.0 crore", date: "12 March 2021" },
  { f: "Completion_Bhopal.pdf", k: "done", proj: "Bhopal Metro, priority corridor", work: "Telecom systems", val: "₹22.5 crore", date: "3 October 2024" },
  { f: "EMD_BankGuarantee.pdf", k: "bg" },
  { f: "RateSchedule_2026.xlsx", k: "sheet" },
  { f: "Udyam_Registration.pdf", k: "reg", title: "Udyam registration",
    rows: [["Enterprise type", "Medium"], ["Major activity", "Services"], ["Registered on", "4 August 2021"]] },
  { f: "GST_Registration.pdf", k: "reg", title: "GST registration",
    rows: [["Form", "GST REG-06"], ["Taxpayer type", "Regular"], ["Valid from", "1 July 2017"]] },
];

export type Draft = { k: string; t: string; p?: string; gate?: true };

export const DR: readonly Draft[] = [
  { k: "Technical bid, section 4", t: "Capability and experience", p: "Our signalling lead, A. Mehta, has seven years of metro signalling experience." },
  { k: "Technical bid, section 6", t: "Delivery team", p: "Three named engineers lead signalling, interlocking and telecom delivery." },
  { k: "Financial bid, section 9", t: "Pricing narrative", p: "Rates follow the 2026 schedule and are quoted in Indian rupees, exclusive of GST." },
  { k: "Technical bid", t: "Executive summary", p: "CBTC signalling commissioned on two Indian metro corridors, delivered under ISO/IEC 27001 controls." },
  { k: "Readiness gate", t: "Before submission", gate: true },
];

// Card ids: tender pages 0..23, evidence 24..35, drafts 36..40.
export const NP = PAGES.length;
export const NE = EV.length;
export const N = NP + NE + DR.length;
export const isPg = (i: number) => i < NP;
export const isEv = (i: number) => i >= NP && i < NP + NE;
export const isDr = (i: number) => i >= NP + NE;
export const E = (j: number) => NP + j;
export const D = (j: number) => NP + NE + j;
export const isM: readonly boolean[] = Array.from({ length: N }, (_, i) => MAND.includes(i));

/**
 * Submission: the evidence and drafts as one stack, bottom to top, executive summary on
 * top. The starting order; thrown pages move to the bottom as the page is used.
 */
export const STACK: readonly number[] = [E(10), E(11), E(7), E(9), E(5), E(6), E(8), E(4), E(1), E(2), E(0), E(3), D(4), D(2), D(1), D(0), D(3)];

// Eligibility: each mandatory page, its decision, and the evidence that settled it.
export type Status = "met" | "part" | "gap";
export const REQ: readonly { pg: number; st: Status; ev: number[] }[] = [
  { pg: 2, st: "met", ev: [E(8)] },
  { pg: 5, st: "met", ev: [E(4)] },
  { pg: 8, st: "part", ev: [E(5), E(6)] },
  { pg: 14, st: "met", ev: [E(0), E(1), E(2)] },
  { pg: 19, st: "met", ev: [E(3)] },
  { pg: 22, st: "gap", ev: [] },
];
/** The evidence row under the requirements, left to right. */
export const EVROW: readonly number[] = [E(8), E(4), E(5), E(6), E(0), E(1), E(2), E(3)];
export const ST: Record<Status, string> = { met: "Met", part: "Partially met", gap: "Not met" };
/** Short names for the evidence row's labels. */
export const SHORT: Record<number, string> = {
  [E(8)]: "EMD guarantee",
  [E(4)]: "CA turnover certificate",
  [E(5)]: "Nagpur completion",
  [E(6)]: "Kochi completion",
  [E(0)]: "A. Mehta CV",
  [E(1)]: "R. Iyer CV",
  [E(2)]: "S. Khan CV",
  [E(3)]: "ISO 27001 certificate",
};
