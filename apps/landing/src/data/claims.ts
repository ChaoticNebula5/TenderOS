import { E } from "./tender";

// Evidence tracing: the five statements in the draft sheet. A supported claim
// names its source card and the three hops to it; the rest carry a reason.
export type Claim =
  | { t: string; src: number; k: string; c: string; d: string }
  | { t: string; kind: "no" | "flag"; why: string };

export const CLAIMS: readonly Claim[] = [
  { t: "Our signalling lead, A. Mehta, has seven years of metro signalling experience.", src: E(0),
    k: "Employee profile, A. Mehta", c: "Résumé, page 2, experience", d: "A_Mehta_CV.pdf" },
  { t: "Our average annual turnover over the last three financial years is ₹68.4 crore.", src: E(4),
    k: "Financial record, turnover", c: "CA certificate, three-year average", d: "Turnover_CA_Cert.pdf" },
  { t: "We have completed three similar works, each exceeding 40% of the estimated cost.", kind: "no",
    why: "Only two completion certificates exceed ₹38.4 crore, which is 40% of the ₹96 crore estimated cost. The Bhopal contract, at ₹22.5 crore, does not qualify." },
  { t: "Our information security management system is certified to ISO/IEC 27001:2022.", src: E(3),
    k: "Certification, ISO 27001", c: "Certificate, standard and scope", d: "ISO27001_2022.pdf" },
  { t: "We guarantee 99.9% system availability from commissioning.", kind: "flag",
    why: "A forward-looking commitment that existing records cannot evidence. TenderOS classifies it separately from factual claims and flags it for approval." },
];
