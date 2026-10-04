// Every outbound destination on the page, in one place. The values are the reference's
// placeholders (in-page anchors) until real destinations are chosen.

export const LINKS = {
  /** Hero, primary button: "Start a tender". */
  heroStart: "#bid",
  /** Hero, secondary button: "See how it works". */
  heroHowItWorks: "#pack",
  /** Submission chapter, primary button: "Start a tender". */
  closingStart: "#top",
  /** Submission chapter, secondary button: "Read the architecture". */
  closingArchitecture: "#top",
} as const;
