/**
 * The six mandatory clauses shown in the pack chapter's list, each pointing at
 * the exact page/sheet/cell it lives on. Ported from reference-landing.html's
 * `CLAUSES` array (docs/reference-landing.html:1155-1161).
 *
 * Order matches `MANDATORY_STACK_POSITIONS` in `./kb.ts` one-for-one: clause i
 * is grounded by the document at `KB[MANDATORY_STACK_POSITIONS[i]]`.
 */

export interface MandatoryClause {
  /** Clause text. Contains inline `<b>` around the mandatory-language word
   *  (shall / must), matching the reference's rendering of rule-caught
   *  mandatory language - not a live citation, just markup for emphasis. */
  html: string;
  sourceFilename: string;
  /** Page/section, or sheet/cell, reference - free text, matches the
   *  provenance format for the document's medium. */
  location: string;
}

export const CLAUSES: MandatoryClause[] = [
  { html: "The Contractor <b>shall</b> hold professional indemnity insurance of not less than ₹100 crore.", sourceFilename: "RFP_Volume2.pdf", location: "p. 14 · §8.2" },
  { html: "Bidders <b>must</b> hold ISO/IEC 27001 certification valid for the contract term.", sourceFilename: "Spec_Security.pdf", location: "p. 6 · §3.1" },
  { html: "Key personnel <b>shall</b> be named and <b>shall not</b> be substituted without consent.", sourceFilename: "RFP_Volume2.pdf", location: "p. 22 · §11.4" },
  { html: "The Contractor <b>must</b> provide a business continuity plan tested within 12 months.", sourceFilename: "Spec_Resilience.pdf", location: "p. 3 · §2.2" },
  { html: "Prices <b>shall</b> be quoted in INR, exclusive of GST.", sourceFilename: "Price_Bid_Instructions.xlsx", location: "sheet Notes · cell B4" },
  { html: "All site staff <b>must</b> hold track competency certification before mobilisation.", sourceFilename: "Addendum_02.pdf", location: "p. 1 · item 3" },
];
