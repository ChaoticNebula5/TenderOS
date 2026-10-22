"""Generates B5's full synthetic ground-truth package into
data/synthetic/tender_a/ and data/ground_truth/, per Build Guide Part L1/L2.

One source-of-truth record list (REQUIREMENTS, EMPLOYEES, CERTIFICATIONS,
PROJECTS, CAPABILITIES, PRICING discrepancies, SUBMISSION_ITEMS) drives THREE
outputs that can never drift apart: the synthetic document files themselves,
data/ground_truth/tender_a_requirements.jsonl (Part L2's exact schema), and
data/ground_truth/tender_a_knowledge.jsonl (this project's own extension for
non-requirement ground truth - L2 only locks the requirement-row shape).

Every planted requirement embeds a short, unique literal token (e.g.
"[REQ-017]") in its own sentence. B5's eval runner (evals/extraction.py)
checks for that token's literal presence in an extracted requirement's text -
deterministic string containment, not fuzzy similarity, so recall is measured
against an unambiguous ground truth, not a second judgment call. Every
knowledge-bearing record (CERT-02, PROJECT-04, EMP-03, ...) uses the same key
namespace Part L2's own example already uses for valid_evidence_keys, so
B6/B7's evidence-linking ground truth reads the same identifiers B5 plants,
with no retrofit.

Requirements below are split into two tiers, by design, not by accident:
- HAND-AUTHORED: the seeded-defect cases, mandatory-language variety
  (including negation and modal-verb edge cases the classifier specifically
  handles), genuinely ambiguous clarification-worthy passages, and one
  representative of every category. These are worth individual attention.
- TEMPLATED FILLER: mechanically varied sentences reaching L1's 50-100/15+
  mandatory floor without hand-writing dozens of near-duplicate clauses.
  Real sentences, not boilerplate tokens (unlike B4's dense_compliance_matrix
  fixture, which deliberately wasn't prose) - but repetitive in structure,
  which is honestly labelled here rather than presented as hand-tuned realism
  throughout.

Run: uv run python scripts/synthetic_tender_a.py
"""

import io
import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypedDict

import docx
from openpyxl import Workbook
from synthetic_common import deterministic_ooxml, native_pdf, write_manifest

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "data" / "synthetic" / "tender_a"
GROUND_TRUTH_DIR = REPO_ROOT / "data" / "ground_truth"
MANIFEST_PATH = DOCS_DIR / "SHA256SUMS"

RFP_PDF = "rfp.pdf"
ANNEXURE_DOCX = "technical_annexure.docx"
AMENDMENT_PDF = "amendment_1.pdf"


# ============================================================
# Source-of-truth records
# ============================================================


@dataclass(frozen=True)
class RequirementSpec:
    key: str
    mandatory: bool
    category: str
    sentence: str
    source_document: str
    source_page: int | None = None
    expected_compliance: str = "needs_review"
    valid_evidence_keys: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SubmissionItemSpec:
    key: str
    name: str
    mandatory: bool
    source_document: str
    will_be_uploaded: bool  # False -> the seeded "missing document" case


@dataclass(frozen=True)
class EmployeeSpec:
    key: str
    name: str
    role: str
    resume_file: str | None  # None -> no resume uploaded (the "removed" case)
    incomplete: bool = False
    removed: bool = False


@dataclass(frozen=True)
class CertificationSpec:
    key: str
    employee_key: str
    name: str
    issuer: str
    issue_date: str
    expiry_date: str  # past date -> the seeded "expired certificate" case


@dataclass(frozen=True)
class ProjectSpec:
    key: str
    name: str
    client: str
    year: str
    description: str


@dataclass(frozen=True)
class CapabilitySpec:
    key: str
    name: str
    description: str


# --- Hand-authored requirements: one per notable case ------------------

_HAND_AUTHORED: list[RequirementSpec] = [
    RequirementSpec(
        "REQ-001", True, "security",
        "[REQ-001] The platform shall integrate with the Authority's Active Directory "
        "for single sign-on.",
        RFP_PDF, source_page=1,
    ),
    RequirementSpec(
        "REQ-002", True, "eligibility",
        "[REQ-002] The bidder must hold a valid tax clearance certificate issued "
        "within the last twelve months.",
        RFP_PDF, source_page=1,
        expected_compliance="compliant", valid_evidence_keys=["CERT-01"],
    ),
    RequirementSpec(
        "REQ-003", True, "experience",
        "[REQ-003] The bidder is required to demonstrate at least three comparable "
        "projects delivered in the past five years.",
        RFP_PDF, source_page=1,
        expected_compliance="partially_compliant",
        valid_evidence_keys=["PROJECT-01", "PROJECT-02"],
    ),
    RequirementSpec(
        "REQ-004", False, "technical",
        "[REQ-004] Support for a dark-mode user interface is preferred but not "
        "required for this engagement.",
        RFP_PDF, source_page=1,
    ),
    RequirementSpec(
        "REQ-005", True, "commercial",
        "[REQ-005] Pricing shall remain fixed for the full twelve-month contract "
        "term, with no mid-term escalation clauses.",
        RFP_PDF, source_page=2,
    ),
    RequirementSpec(
        "REQ-006", False, "commercial",
        "[REQ-006] A volume discount schedule for optional add-on modules is desirable.",
        RFP_PDF, source_page=2,
    ),
    RequirementSpec(
        "REQ-007", True, "submission",
        "[REQ-007] Proposals must be submitted no later than 31 March 2026 at "
        "17:00 local time.",
        RFP_PDF, source_page=2,
    ),
    RequirementSpec(
        "REQ-008", True, "submission",
        "[REQ-008] Late submissions shall not be considered under any circumstances "
        "whatsoever.",
        RFP_PDF, source_page=2,
    ),
    RequirementSpec(
        "REQ-009", True, "legal",
        "[REQ-009] The successful bidder will be responsible for maintaining "
        "professional indemnity insurance of at least 1,000,000 for the contract duration.",
        RFP_PDF, source_page=2,
        expected_compliance="unsupported",
    ),
    RequirementSpec(
        "REQ-010", False, "staffing",
        "[REQ-010] Where possible, the proposed team should include a certified "
        "project manager, though this is not a precondition for evaluation.",
        RFP_PDF, source_page=3,
    ),
    RequirementSpec(
        "REQ-011", True, "staffing",
        "[REQ-011] The bidder must assign a dedicated technical lead who is "
        "available for the full duration of the engagement.",
        RFP_PDF, source_page=3,
        expected_compliance="compliant", valid_evidence_keys=["PROJECT-03"],
    ),
    RequirementSpec(
        "REQ-012", True, "security",
        "[REQ-012] All personal data processed under this contract must comply "
        "with applicable data-protection legislation.",
        RFP_PDF, source_page=3,
    ),
    RequirementSpec(
        "REQ-013", False, "technical",
        "[REQ-013] Bidders may optionally propose an accelerated delivery timeline "
        "if it does not compromise quality.",
        RFP_PDF, source_page=3,
    ),
    RequirementSpec(
        # Negation edge case: contains "shall" (mandatory-list term) but the
        # sentence means the opposite - exercises the classifier's negation
        # handling, and the LLM's own contextual reading, on the same input.
        "REQ-014", False, "technical",
        "[REQ-014] The vendor shall not be required to provide on-site support "
        "outside standard business hours.",
        RFP_PDF, source_page=4,
    ),
    RequirementSpec(
        "REQ-015", True, "technical",
        "[REQ-015] The solution must, at a minimum, support concurrent access "
        "for five hundred named users.",
        ANNEXURE_DOCX,
        expected_compliance="compliant", valid_evidence_keys=["PROJECT-01"],
    ),
    RequirementSpec(
        "REQ-016", True, "eligibility",
        "[REQ-016] The bidder shall not have been subject to a debarment decision "
        "by any public authority within the past five years.",
        RFP_PDF, source_page=4,
    ),
    RequirementSpec(
        "REQ-017", True, "commercial",
        "[REQ-017] All prices quoted shall be inclusive of applicable duties and "
        "shall be denominated in USD.",
        ANNEXURE_DOCX,
        expected_compliance="unsupported",
    ),
    RequirementSpec(
        "REQ-018", False, "other",
        "[REQ-018] Additional case studies beyond the required minimum are "
        "welcome but will not affect the technical score.",
        ANNEXURE_DOCX,
    ),
]

# --- Templated filler: reaches the 50-100/15+ mandatory floor ----------
#
# Originally 27 templates, each reused twice with only the modal verb
# swapped (e.g. "...will be provided in PDF format" / "...is required to be
# provided in PDF format"). A B5 gate investigation (batch-size experiment,
# then chunk/template analysis) found the model treating same-subject-
# same-object pairs like that as one restated obligation and correctly
# suppressing the duplicate - meaning the generator, not the pipeline, was
# mislabeling one requirement as two ground-truth rows. Every entry below
# now has a genuinely distinct subject/object; no two items describe the
# same thing. The investigation also found the misses had zero enumeration
# cues (no clause code / row ID) unlike real dense compliance matrices,
# and that page assignment was accidentally coupled to template identity
# (both derived from i % <multiple-of-3> in the old scheme) - both are
# fixed below: _CLAUSE_SECTION_BY_CATEGORY adds a "Clause N.M" prefix per
# item, and _build_page_doc_assignments assigns (document, page) from an
# independently seeded shuffle instead of the same loop index.
_FILLER_TEMPLATES: list[tuple[str, str, bool]] = [
    ("technical", "The system {token} support single sign-on via SAML 2.0 for "
                  "all administrative users.", True),
    ("technical", "The platform {token} maintain 99.5% uptime measured monthly, "
                  "excluding scheduled maintenance windows.", True),
    ("technical", "The solution {token} provide a sandbox environment for user "
                  "acceptance testing prior to go-live.", True),
    ("technical", "Data exports {token} be available in both CSV and JSON "
                  "formats.", True),
    ("technical", "The vendor {token} support a phased migration of legacy "
                  "records over no more than three release cycles.", False),
    ("technical", "The platform {token} offer a dark-mode display option for "
                  "the end-user interface.", False),
    ("staffing", "The bidder {token} assign a dedicated project manager who "
                 "remains in place for the contract's first twelve months.", True),
    ("staffing", "Each key personnel substitution {token} be accompanied by a "
                 "CV of equivalent or greater experience.", True),
    ("staffing", "The bidder {token} maintain a minimum of two certified "
                 "database administrators on the account team throughout "
                 "delivery.", True),
    ("staffing", "Subcontracted staff {token} undergo the same background-check "
                 "process as direct employees.", True),
    ("staffing", "The bidder {token} offer an optional on-site training day for "
                 "the Authority's staff during handover.", False),
    ("staffing", "A staffing contingency plan for key-role absence longer than "
                 "two weeks {token} be included.", False),
    ("commercial", "The bidder's pricing schedule {token} disclose any "
                   "assumptions underlying the quoted unit rates.", True),
    ("commercial", "Any price escalation after year one {token} be capped at "
                   "the published consumer price index.", True),
    ("commercial", "The bidder {token} itemize implementation costs separately "
                   "from ongoing subscription costs.", True),
    ("commercial", "Late payment beyond forty-five days {token} accrue interest "
                   "at the statutory rate.", True),
    ("commercial", "A volume discount schedule for additional user licenses "
                   "{token} be proposed.", False),
    ("commercial", "A multi-year prepayment discount option {token} be offered.", False),
    ("submission", "The commercial proposal {token} be submitted as a separate "
                   "sealed file from the technical proposal.", True),
    ("submission", "All submission files {token} be named according to the "
                   "naming convention specified in Appendix B.", True),
    ("submission", "The bidder's authorized signatory {token} sign and date the "
                   "cover letter.", True),
    ("submission", "The bidder {token} provide proposal copies in both "
                   "electronic and one physical hard-copy format.", True),
    ("submission", "A video walkthrough of the proposed solution {token} be "
                   "submitted alongside the written proposal.", False),
    ("submission", "A one-page summary of team qualifications {token} be "
                   "attached as an appendix.", False),
    ("legal", "The successful bidder {token} execute a data processing "
              "agreement prior to contract commencement.", True),
    ("legal", "Intellectual property developed under this contract {token} "
              "vest in the Authority upon final payment.", True),
    ("legal", "The bidder {token} disclose any pending litigation relevant to "
              "the services described in this RFP.", True),
    ("legal", "Force majeure provisions {token} follow the standard clause "
              "published in Appendix C.", True),
    ("legal", "An indemnification clause exceeding the standard template "
              "{token} be flagged for legal review before signature.", False),
    ("legal", "A voluntary sustainability declaration {token} be included with "
              "the legal submission.", False),
    ("security", "The bidder {token} hold a current ISO 27001 certification or "
                 "provide a remediation timeline to obtain one.", True),
    ("security", "All data at rest {token} be encrypted using AES-256 or an "
                 "equivalent approved standard.", True),
    ("security", "The bidder {token} conduct an annual third-party penetration "
                 "test and share summary findings with the Authority.", True),
    ("security", "Administrative access to production databases {token} be "
                 "logged and retained for a minimum of one year.", True),
    ("security", "A dedicated security operations contact {token} be available "
                 "outside standard business hours.", False),
    ("security", "A voluntary bug-bounty program covering the delivered "
                 "solution {token} be described if one exists.", False),
    ("experience", "The bidder {token} identify the specific team members who "
                   "will remain assigned across all phases of a multi-phase "
                   "engagement.", True),
    ("experience", "The proposed delivery lead {token} have personally managed "
                   "at least one project of similar size and sector.", True),
    ("experience", "The bidder {token} disclose any contract terminated for "
                   "cause within the past five years.", True),
    ("experience", "Evidence of experience integrating with legacy mainframe "
                   "systems {token} be provided where claimed.", True),
    ("experience", "Awards or industry recognitions relevant to similar "
                   "engagements {token} be listed if available.", False),
    ("experience", "A short video testimonial from a past client {token} be "
                   "included where consent has been obtained.", False),
    ("eligibility", "The bidder {token} be currently registered as an active, "
                    "legally operating entity in its jurisdiction of "
                    "incorporation.", True),
    ("eligibility", "The bidder {token} hold the minimum annual turnover "
                    "threshold specified in the eligibility criteria.", True),
    ("eligibility", "A parent-company guarantee {token} be provided where the "
                    "bidding entity is a subsidiary.", True),
    ("eligibility", "The bidder {token} confirm there is no undisclosed common "
                    "ownership with a competing bidder on this tender.", True),
    ("eligibility", "Bidders operating as a consortium {token} identify a "
                    "single point of contact for all correspondence.", False),
    ("other", "The bidder {token} acknowledge and accept the Authority's "
              "standard code of conduct for suppliers.", True),
    ("other", "Any assumptions made in preparing the proposal {token} be "
              "listed explicitly in a dedicated appendix.", True),
    ("other", "The bidder {token} confirm the proposal remains valid for at "
              "least ninety days from the submission deadline.", True),
    ("other", "A short statement of the bidder's approach to environmental "
              "sustainability {token} be included.", False),
    ("other", "A proposed glossary of acronyms used throughout the response "
              "{token} be provided for the evaluators' convenience.", False),
]
_MANDATORY_MODALS = ("shall", "must", "is required to", "will")
_OPTIONAL_MODALS = ("should", "may", "is encouraged to")

# Section number per category, used to build a "Clause N.M" prefix per
# filler item - the enumeration cue real dense compliance matrices carry
# (clause codes, row IDs) that signal per-item distinctness independent of
# surrounding phrasing, which the old unprefixed filler lacked entirely.
_CLAUSE_SECTION_BY_CATEGORY = {
    "technical": 4, "staffing": 5, "commercial": 6, "submission": 7,
    "legal": 8, "security": 9, "experience": 10, "eligibility": 11,
    "other": 12,
}


def _build_page_doc_assignments(count: int) -> list[tuple[str, int | None]]:
    slots: list[tuple[str, int | None]] = [(ANNEXURE_DOCX, None)] * 16
    for page in (5, 6, 7, 8):
        slots.extend([(RFP_PDF, page)] * 9)
    # Fixed seed: ground truth must be identical across regenerations. The
    # shuffle itself - not just the seed - is what matters here: assignment
    # is independent of _FILLER_TEMPLATES' list order/category grouping, so
    # no future edit to that list can accidentally recreate a page/content
    # correlation the way the old i % 3 scheme did.
    rng = random.Random(20260810)
    rng.shuffle(slots)
    return slots[:count]


def _generate_filler(start_index: int, count: int) -> list[RequirementSpec]:
    assignments = _build_page_doc_assignments(count)
    category_item_counts: dict[str, int] = {}
    specs: list[RequirementSpec] = []
    for i in range(count):
        category, template, mandatory = _FILLER_TEMPLATES[i]
        modal_pool = _MANDATORY_MODALS if mandatory else _OPTIONAL_MODALS
        modal = modal_pool[i % len(modal_pool)]
        key = f"REQ-{start_index + i:03d}"
        token = f"[{key}]"
        category_item_counts[category] = category_item_counts.get(category, 0) + 1
        clause_prefix = (
            f"Clause {_CLAUSE_SECTION_BY_CATEGORY[category]}."
            f"{category_item_counts[category]}: "
        )
        # {token} sits where the modal verb goes, so the planted [REQ-nnn]
        # marker and the mandatory-language signal appear together - closer
        # to how a real clause reads than appending the token as a suffix.
        sentence = clause_prefix + template.replace("{token}", f"{token} {modal}")
        source_document, page = assignments[i]
        specs.append(
            RequirementSpec(key, mandatory, category, sentence, source_document, source_page=page)
        )
    return specs


_FILLER = _generate_filler(start_index=len(_HAND_AUTHORED) + 1, count=52)

# --- Amendment: adds/changes a few requirements -------------------------

_AMENDMENT_REQUIREMENTS: list[RequirementSpec] = [
    RequirementSpec(
        "REQ-071", True, "technical",
        "[REQ-071] Amendment 1: the concurrent-user requirement in Section 4.2 is "
        "revised - the solution shall now support concurrent access for seven "
        "hundred fifty named users, superseding the original figure of five hundred.",
        AMENDMENT_PDF, source_page=1,
    ),
    RequirementSpec(
        "REQ-072", True, "submission",
        "[REQ-072] Amendment 1: the submission deadline in Section 3.1 is extended "
        "- proposals must now be submitted no later than 14 April 2026 at 17:00 local time.",
        AMENDMENT_PDF, source_page=1,
    ),
]

# --- Divergent-restatement pair: known-limitations test case ------------
#
# Deliberately distinct from the near-verbatim duplicates the filler tier
# used to plant (see KNOWN_LIMITATIONS.md, "[B5] Silent suppression of
# restated obligations"). REQ-073/074 restate the SAME obligation (audit
# log retention) in two source documents with a MATERIALLY DIFFERENT
# duration - a correct extractor should keep both, not suppress either.
# Not asserted by B5's own eval (plain token-match recall/precision only,
# same as every other requirement); exists so a later block can test
# whether the pipeline surfaces the divergence rather than silently
# dropping the stricter (annexure) figure.
_DIVERGENT_RESTATEMENT_PAIR: list[RequirementSpec] = [
    RequirementSpec(
        "REQ-073", True, "security",
        "[REQ-073] Audit logs shall be retained for a minimum of one year.",
        RFP_PDF, source_page=4,
    ),
    RequirementSpec(
        "REQ-074", True, "security",
        "[REQ-074] Audit logs shall be retained for a minimum of three years.",
        ANNEXURE_DOCX,
    ),
]

REQUIREMENTS: list[RequirementSpec] = (
    _HAND_AUTHORED + _FILLER + _AMENDMENT_REQUIREMENTS + _DIVERGENT_RESTATEMENT_PAIR
)

# 5 unsupported (no valid evidence), 1 with exactly one valid path, 1 with
# exactly two - explicit per L1's seed list, layered onto the hand-authored
# set above rather than left to chance.
_UNSUPPORTED_KEYS = ("REQ-009", "REQ-017", "REQ-004", "REQ-006", "REQ-018")

# --- Submission checklist ------------------------------------------------

SUBMISSION_ITEMS: list[SubmissionItemSpec] = [
    SubmissionItemSpec("SUB-01", "Signed cover letter", True, RFP_PDF, will_be_uploaded=True),
    SubmissionItemSpec(
        "SUB-02", "Tax clearance certificate", True, RFP_PDF, will_be_uploaded=True
    ),
    SubmissionItemSpec(
        "SUB-03", "Three signed reference letters", True, RFP_PDF, will_be_uploaded=False
    ),
    SubmissionItemSpec(
        "SUB-04", "Proof of professional indemnity insurance", True,
        RFP_PDF, will_be_uploaded=False,
    ),
    SubmissionItemSpec(
        "SUB-05", "Company organizational chart", False, RFP_PDF, will_be_uploaded=True
    ),
    SubmissionItemSpec(
        "SUB-06", "Certificate of incorporation", True, RFP_PDF, will_be_uploaded=False
    ),
]

# --- Talent / knowledge records -------------------------------------------

EMPLOYEES: list[EmployeeSpec] = [
    EmployeeSpec("EMP-01", "Dana Whitfield", "Solution Architect", "resume_dana_whitfield.docx"),
    EmployeeSpec("EMP-02", "Marcus Ilori", "Senior Engineer", "resume_marcus_ilori.docx"),
    EmployeeSpec("EMP-03", "Priya Ostrowski", "Project Manager", "resume_priya_ostrowski.docx"),
    EmployeeSpec(
        "EMP-04", "Tomás Reyes", "Security Consultant",
        "resume_tomas_reyes.docx", incomplete=True,
    ),
    EmployeeSpec(
        "EMP-05", "Dana Whitfield (dup)", "Solution Architect",
        "resume_dana_whitfield_resubmit.docx",
    ),
    EmployeeSpec(
        "EMP-06", "Helen Achterberg", "Former Commercial Lead",
        resume_file=None, removed=True,
    ),
]

CERTIFICATIONS: list[CertificationSpec] = [
    CertificationSpec(
        "CERT-01", "EMP-01", "Tax Clearance Certificate",
        "Revenue Authority", "2025-09-01", "2026-09-01",
    ),
    CertificationSpec(
        "CERT-02", "EMP-02", "AWS Certified Solutions Architect",
        "Amazon Web Services", "2023-05-10", "2026-05-10",
    ),
    CertificationSpec(
        "CERT-03", "EMP-03", "PMP",
        "Project Management Institute", "2021-02-15", "2027-02-15",
    ),
    CertificationSpec(
        "CERT-04", "EMP-04", "CISSP", "ISC2", "2019-11-01", "2023-11-01"
    ),  # expired
    CertificationSpec(
        "CERT-05", "EMP-01", "ISO 27001 Lead Implementer", "PECB", "2024-01-20", "2027-01-20"
    ),
    CertificationSpec(
        "CERT-06", "EMP-03", "PRINCE2 Practitioner", "AXELOS", "2022-06-30", "2025-06-30"
    ),
]

PROJECTS: list[ProjectSpec] = [
    ProjectSpec(
        "PROJECT-01", "Regional Network Modernization", "Ministry of Transport", "2024",
        "Delivered network infrastructure upgrades across six regional sites, "
        "supporting over five hundred concurrent users.",
    ),
    ProjectSpec(
        "PROJECT-02", "Citizen Services Portal", "City of Alden", "2023",
        "Built and operated a public-facing citizen services portal integrated "
        "with single sign-on.",
    ),
    ProjectSpec(
        "PROJECT-03", "Health Records Migration", "Regional Health Authority", "2022",
        "Led a dedicated technical team through a multi-year records migration "
        "with a named on-site lead throughout.",
    ),
    ProjectSpec(
        "PROJECT-04", "Emergency Services Dispatch Upgrade", "County Emergency Services", "2021",
        "Upgraded dispatch systems under a fixed-price contract with no mid-term "
        "cost escalation.",
    ),
]

CAPABILITIES: list[CapabilitySpec] = [
    CapabilitySpec(
        "CAP-01", "Systems Integration",
        "Proven ability to integrate new platforms with legacy identity and "
        "directory services.",
    ),
    CapabilitySpec(
        "CAP-02", "Public-Sector Delivery",
        "A decade of continuous public-sector project delivery across multiple "
        "jurisdictions.",
    ),
    CapabilitySpec(
        "CAP-03", "Security and Compliance",
        "In-house security consulting practice covering data-protection and "
        "access-control compliance.",
    ),
]

# --- Clarifications: genuinely unresolvable from the text given -----------

CLARIFICATIONS = [
    "Bidders must comply with the requirements set out in Annex F, which is not "
    "enclosed with this document and is not otherwise available to bidders "
    "prior to submission.",
    "Section 5.2 references 'the standard warranty period' without stating its "
    "duration anywhere in this document or its annexures.",
]

class PricingDiscrepancy(TypedDict):
    sheet: str
    item_index: int | None
    issue: str


# 6 pricing discrepancies now (B8B extends B7's original 4), seeded across
# two pricing sheets. item_index is 0-based into that sheet's line_items
# list (None -> the grand-total row, not a specific line item) - the render
# function resolves this to a real cell address from the actual row layout,
# rather than a hardcoded cell reference that could silently drift out of
# sync with the row count.
#
# B8B's own required 6 defect categories, and how each is represented here:
# - wrong total: the two "line_total_..."/"grand_total_..." issues below
#   (a literal wrong number in place of the working formula).
# - missing cell: "unit_cost_missing_for_priced_line_item".
# - currency mismatch: "mismatched_currency" - a per-row Currency column
#   override targeting one specific row's currency cell.
# - duplicate cost: NOT a discrepancy-dict entry at all - PRICING_1_ITEMS
#   below simply lists "Senior Engineer", 1200, 12 twice. Two real,
#   otherwise-correct rows that happen to be identical is exactly what the
#   deterministic checker has to notice on its own, not something the
#   fixture needs to inject via cell-overwriting.
# - cross-sheet discrepancy: also not a discrepancy-dict entry - "Site
#   Survey" appears in both PRICING_1_ITEMS and PRICING_2_ITEMS with
#   different unit costs, the same "let the natural data disagree" approach
#   duplicate cost uses.
# - unsupported formula requiring human review: "unsupported_markup_formula" -
#   a real formula (unlike the wrong-total issues' literal numbers), just
#   not the simple `=B{row}*C{row}` shape the checker is built to recompute.
PRICING_DISCREPANCIES: list[PricingDiscrepancy] = [
    {
        "sheet": "pricing_schedule_1.xlsx", "item_index": 0,
        "issue": "line_total_does_not_equal_unit_cost_times_quantity",
    },
    {
        "sheet": "pricing_schedule_1.xlsx", "item_index": None,
        "issue": "grand_total_does_not_sum_line_items",
    },
    {
        "sheet": "pricing_schedule_2.xlsx", "item_index": 1,
        "issue": "line_total_does_not_equal_unit_cost_times_quantity",
    },
    {
        "sheet": "pricing_schedule_2.xlsx", "item_index": 2,
        "issue": "unit_cost_missing_for_priced_line_item",
    },
    {
        "sheet": "pricing_schedule_1.xlsx", "item_index": 4,
        "issue": "mismatched_currency",
    },
    {
        "sheet": "pricing_schedule_2.xlsx", "item_index": 4,
        "issue": "unsupported_markup_formula",
    },
]


# ============================================================
# Document rendering
# ============================================================


def _render_rfp_pdf() -> bytes:
    rfp_reqs = [r for r in REQUIREMENTS if r.source_document == RFP_PDF]
    by_page: dict[int, list[str]] = {}
    for r in rfp_reqs:
        assert r.source_page is not None, f"{r.key} is RFP-sourced but has no source_page"
        by_page.setdefault(r.source_page, []).append(r.sentence)

    # Submission checklist lands on page 2, alongside the hand-authored
    # submission-category requirements already placed there.
    checklist_lines = [f"[{item.key}] {item.name}" for item in SUBMISSION_ITEMS]
    by_page.setdefault(2, []).extend(["", "Submission Checklist:", *checklist_lines])

    # Clarifications get their own page, after every requirement page.
    last_req_page = max(by_page)
    clarification_page = last_req_page + 1
    by_page[clarification_page] = ["Other Terms", "", *CLARIFICATIONS]

    pages: list[list[str]] = []
    for page_num in range(1, clarification_page + 1):
        lines = by_page.get(page_num, [f"(page {page_num} intentionally left blank)"])
        pages.append([f"RFP-2026-014 - Page {page_num}", "", *lines])
    return native_pdf(pages)


def _render_annexure_docx() -> bytes:
    document = docx.Document()
    document.add_heading("Technical Annexure", level=1)
    document.add_paragraph(
        "This annexure supplements the main RFP with additional technical, commercial, "
        "legal, and security requirements."
    )
    for r in REQUIREMENTS:
        if r.source_document != ANNEXURE_DOCX:
            continue
        document.add_paragraph(r.sentence)

    buf_path = _bytes_buffer()
    document.save(buf_path)
    return buf_path.getvalue()


def _render_amendment_pdf() -> bytes:
    lines = ["AMENDMENT No. 1 TO RFP-2026-014", ""]
    for r in REQUIREMENTS:
        if r.source_document == AMENDMENT_PDF:
            lines.append(r.sentence)
            lines.append("")
    return native_pdf([lines])


def _render_employee_master_xlsx() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Employees"
    headers = ["Employee Code", "Name", "Role", "Status"]
    for col, header in enumerate(headers, start=1):
        sheet.cell(row=1, column=col, value=header)
    for row_idx, emp in enumerate(EMPLOYEES, start=2):
        status = "removed" if emp.removed else "active"
        sheet.cell(row=row_idx, column=1, value=emp.key)
        sheet.cell(row=row_idx, column=2, value=emp.name)
        sheet.cell(row=row_idx, column=3, value=emp.role)
        sheet.cell(row=row_idx, column=4, value=status)

    buf_path = _bytes_buffer()
    workbook.save(buf_path)
    return buf_path.getvalue()


def _render_resume_docx(employee: EmployeeSpec) -> bytes:
    document = docx.Document()
    document.add_heading(employee.name, level=1)
    document.add_paragraph(employee.role)
    if not employee.incomplete:
        document.add_heading("Certifications", level=2)
        for cert in CERTIFICATIONS:
            if cert.employee_key == employee.key:
                document.add_paragraph(f"{cert.name} ({cert.issuer}, expires {cert.expiry_date})")
        document.add_heading("Relevant Projects", level=2)
        for project in PROJECTS:
            document.add_paragraph(f"{project.name} - {project.client} ({project.year})")
    else:
        # The seeded "incomplete profile" - no certifications/projects
        # sections at all, just a name and role.
        document.add_paragraph("(Profile incomplete - candidate did not supply further detail.)")

    buf_path = _bytes_buffer()
    document.save(buf_path)
    return buf_path.getvalue()


def _render_certifications_xlsx() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Certifications"
    headers = ["Cert Key", "Employee", "Certification", "Issuer", "Issue Date", "Expiry Date"]
    for col, header in enumerate(headers, start=1):
        sheet.cell(row=1, column=col, value=header)
    for row_idx, cert in enumerate(CERTIFICATIONS, start=2):
        sheet.cell(row=row_idx, column=1, value=cert.key)
        sheet.cell(row=row_idx, column=2, value=cert.employee_key)
        sheet.cell(row=row_idx, column=3, value=cert.name)
        sheet.cell(row=row_idx, column=4, value=cert.issuer)
        sheet.cell(row=row_idx, column=5, value=cert.issue_date)
        sheet.cell(row=row_idx, column=6, value=cert.expiry_date)

    buf_path = _bytes_buffer()
    workbook.save(buf_path)
    return buf_path.getvalue()


def _render_capability_statement_docx() -> bytes:
    document = docx.Document()
    document.add_heading("Company Capability Statement", level=1)

    document.add_heading("Past Projects", level=2)
    for project in PROJECTS:
        document.add_paragraph(
            f"[{project.key}] {project.name} - {project.client} ({project.year})"
        )
        document.add_paragraph(project.description)

    document.add_heading("Capabilities", level=2)
    for capability in CAPABILITIES:
        document.add_paragraph(f"[{capability.key}] {capability.name}")
        document.add_paragraph(capability.description)

    buf_path = _bytes_buffer()
    document.save(buf_path)
    return buf_path.getvalue()


def _render_pricing_xlsx(
    sheet_filename: str, line_items: list[tuple[str, int | float, int]]
) -> bytes:
    """Lays out line_items with working =B*C formulas, a Currency column
    (every row "USD" by default), and a working =SUM total, THEN overwrites
    specific cells per this sheet's PRICING_DISCREPANCIES entries - the
    defects are real seeded errors in the generated file, not just entries
    in a list that describes errors nobody actually wrote.

    openpyxl never actually evaluates formulas it writes (there is no Excel
    engine involved) - a workbook it saves has no cached value behind any
    "=B2*C2"/"=SUM(...)" cell, so pricing_checks.py's deterministic engine
    can never rely on reading a "computed" value back out of these cells.
    It has to recompute Line Total = Unit Cost x Quantity and Grand Total =
    SUM(Line Totals) itself from the raw Unit Cost/Quantity/Line Total cell
    contents - which is exactly why a defect here is a literal wrong NUMBER
    replacing the formula (a real discrepancy between what a human sees
    displayed and what the numbers actually support), not a broken formula
    string.
    """
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Pricing"
    sheet["A1"], sheet["B1"], sheet["C1"] = "Item", "Unit Cost", "Quantity"
    sheet["D1"], sheet["E1"] = "Line Total", "Currency"
    for i, (item, cost, qty) in enumerate(line_items, start=2):
        sheet[f"A{i}"] = item
        sheet[f"B{i}"] = cost
        sheet[f"C{i}"] = qty
        sheet[f"D{i}"] = f"=B{i}*C{i}"
        sheet[f"E{i}"] = "USD"
    total_row = len(line_items) + 2
    sheet[f"A{total_row}"] = "Total"
    sheet[f"D{total_row}"] = f"=SUM(D2:D{total_row - 1})"

    for discrepancy in PRICING_DISCREPANCIES:
        if discrepancy["sheet"] != sheet_filename:
            continue
        item_index = discrepancy["item_index"]
        issue = discrepancy["issue"]
        if issue == "grand_total_does_not_sum_line_items":
            # Literal wrong number in place of the working =SUM formula.
            correct_total = sum(cost * qty for _, cost, qty in line_items)
            sheet[f"D{total_row}"] = round(correct_total * 0.9, 2)
        elif issue == "line_total_does_not_equal_unit_cost_times_quantity":
            assert item_index is not None, "this issue type always targets a specific line item"
            row = item_index + 2
            cost, qty = line_items[item_index][1], line_items[item_index][2]
            sheet[f"D{row}"] = round(cost * qty * 1.5, 2)  # literal, not =B*C
        elif issue == "unit_cost_missing_for_priced_line_item":
            assert item_index is not None, "this issue type always targets a specific line item"
            row = item_index + 2
            sheet[f"B{row}"] = None
            sheet[f"D{row}"] = None
        elif issue == "mismatched_currency":
            assert item_index is not None, "this issue type always targets a specific line item"
            row = item_index + 2
            sheet[f"E{row}"] = "EUR"
        elif issue == "unsupported_markup_formula":
            # A real formula, unlike the wrong-total issues above - just not
            # the simple =B{row}*C{row} shape the checker knows how to
            # recompute (an undocumented 10% markup folded into the cell
            # itself), so it must fall to needs_human_review rather than
            # being silently accepted or guessed at.
            assert item_index is not None, "this issue type always targets a specific line item"
            row = item_index + 2
            sheet[f"D{row}"] = f"=B{row}*C{row}*1.1"
        else:
            raise ValueError(f"Unknown pricing discrepancy issue: {issue!r}")

    buf_path = _bytes_buffer()
    workbook.save(buf_path)
    return buf_path.getvalue()


def _bytes_buffer() -> io.BytesIO:
    return io.BytesIO()


# ============================================================
# Ground truth
# ============================================================


def _write_requirements_ground_truth() -> None:
    path = GROUND_TRUTH_DIR / "tender_a_requirements.jsonl"
    # newline="\n": the same bytes on every platform (Windows text mode
    # would write CRLF), which the manifest's hash depends on.
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for r in REQUIREMENTS:
            valid_keys = list(r.valid_evidence_keys)
            expected_compliance = r.expected_compliance
            if r.key in _UNSUPPORTED_KEYS:
                valid_keys = []
                expected_compliance = "unsupported"
            record = {
                "requirement_key": r.key,
                "mandatory": r.mandatory,
                "source_document": r.source_document,
                "source_page": r.source_page,
                "expected_category": r.category,
                "expected_compliance": expected_compliance,
                "valid_evidence_keys": valid_keys,
            }
            f.write(json.dumps(record) + "\n")
    print(f"wrote {path.relative_to(REPO_ROOT)} ({len(REQUIREMENTS)} records)")


def _write_knowledge_ground_truth() -> None:
    """Not locked by Part L2 (which only specifies the requirement-row
    schema) - this project's own extension for the non-requirement seeded
    defects L1 asks for, documented here rather than invented silently at
    eval time."""
    path = GROUND_TRUTH_DIR / "tender_a_knowledge.jsonl"
    records: list[dict] = []

    for emp in EMPLOYEES:
        records.append(
            {
                "record_type": "employee",
                "key": emp.key,
                "name": emp.name,
                "resume_file": emp.resume_file,
                "expected_duplicate_of": "EMP-01" if emp.key == "EMP-05" else None,
                "expected_incomplete": emp.incomplete,
                "expected_removed": emp.removed,
            }
        )
    for cert in CERTIFICATIONS:
        records.append(
            {
                "record_type": "certification",
                "key": cert.key,
                "name": cert.name,
                "employee_key": cert.employee_key,
                "expiry_date": cert.expiry_date,
                "expected_expired": cert.expiry_date < "2026-08-09",
            }
        )
    for project in PROJECTS:
        records.append(
            {"record_type": "project", "key": project.key, "name": project.name}
        )
    for capability in CAPABILITIES:
        records.append(
            {"record_type": "capability", "key": capability.key, "name": capability.name}
        )
    for item in SUBMISSION_ITEMS:
        records.append(
            {
                "record_type": "submission_item",
                "key": item.key,
                "name": item.name,
                "mandatory": item.mandatory,
                "expected_status": "present" if item.will_be_uploaded else "missing",
            }
        )
    for discrepancy in PRICING_DISCREPANCIES:
        records.append({"record_type": "pricing_discrepancy", **discrepancy})

    # Known-limitations test case (KNOWN_LIMITATIONS.md, "[B5] Silent
    # suppression of restated obligations"): REQ-073/074 restate the same
    # obligation with a materially different duration. Recorded here, not
    # in tender_a_requirements.jsonl, because L2 locks that file's row
    # schema and doesn't have a field for "this diverges from that other
    # key" - this file is this project's own extension, built for exactly
    # this kind of cross-reference.
    records.append(
        {
            "record_type": "requirement_divergence",
            "requirement_keys": ["REQ-073", "REQ-074"],
            "obligation": "audit log retention period",
            "divergent_values": {"REQ-073": "1 year", "REQ-074": "3 years"},
            "note": (
                "Same obligation restated in the RFP body (REQ-073) and the "
                "technical annexure (REQ-074) with different durations - "
                "unlike the old filler tier's near-verbatim duplicates, both "
                "should be extracted as distinct requirements, not "
                "deduplicated. Not asserted by B5's own eval; exists for a "
                "later block to test whether the pipeline surfaces the "
                "divergence rather than silently keeping only one."
            ),
        }
    )

    with path.open("w", encoding="utf-8", newline="\n") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")
    print(f"wrote {path.relative_to(REPO_ROOT)} ({len(records)} records)")


def main() -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    GROUND_TRUTH_DIR.mkdir(parents=True, exist_ok=True)

    documents: dict[str, bytes] = {
        RFP_PDF: _render_rfp_pdf(),
        ANNEXURE_DOCX: _render_annexure_docx(),
        AMENDMENT_PDF: _render_amendment_pdf(),
        "employee_master.xlsx": _render_employee_master_xlsx(),
        "certifications.xlsx": _render_certifications_xlsx(),
        "capability_statement.docx": _render_capability_statement_docx(),
    }

    for emp in EMPLOYEES:
        if emp.resume_file is None:
            continue
        documents[emp.resume_file] = _render_resume_docx(emp)
    # EMP-05 is deliberately a byte-identical duplicate of EMP-01's resume
    # (the seeded "duplicate resume" case) - not a second call to the
    # renderer, which would produce distinct-but-similar bytes and defeat
    # the point of a duplicate-detection test keyed on resume hash.
    documents["resume_dana_whitfield_resubmit.docx"] = documents["resume_dana_whitfield.docx"]

    # item_index 3/4 in each sheet exist for B8B's own seeded defects, not
    # B5's original scope: index 3 in pricing_1 is a literal duplicate of
    # index 0 (same description/cost/quantity - the "duplicate cost" case,
    # not injected via PRICING_DISCREPANCIES since two ordinary-looking rows
    # that happen to be identical IS the defect). "Site Survey" appears in
    # BOTH sheets with different unit costs (8000 here, 8500 below) - the
    # "cross-sheet discrepancy" case, same "let the data disagree naturally"
    # approach. Index 4 in each sheet is the target row for a
    # PRICING_DISCREPANCIES-injected cell overwrite (currency mismatch in
    # pricing_1, unsupported markup formula in pricing_2).
    pricing_1_items: list[tuple[str, int | float, int]] = [
        ("Senior Engineer", 1200, 12),
        ("Project Manager", 1500, 6),
        ("Site Survey", 8000, 1),
        ("Senior Engineer", 1200, 12),
        ("Cross-Border Logistics", 2000, 2),
    ]
    pricing_2_items: list[tuple[str, int | float, int]] = [
        ("Security Consultant", 1400, 8),
        ("Data Migration Specialist", 1600, 5),
        ("Training Delivery", 900, 3),
        ("Site Survey", 8500, 1),
        ("Custom Integration Work", 3000, 4),
    ]
    documents["pricing_schedule_1.xlsx"] = _render_pricing_xlsx(
        "pricing_schedule_1.xlsx", pricing_1_items
    )
    documents["pricing_schedule_2.xlsx"] = _render_pricing_xlsx(
        "pricing_schedule_2.xlsx", pricing_2_items
    )

    written: list[Path] = []
    for name, content in documents.items():
        if name.endswith((".docx", ".xlsx")):
            content = deterministic_ooxml(content)
        path = DOCS_DIR / name
        path.write_bytes(content)
        written.append(path)
        print(f"wrote {path.relative_to(REPO_ROOT)} ({len(content)} bytes)")

    _write_requirements_ground_truth()
    _write_knowledge_ground_truth()

    # The tracked record of every synthetic file this script produces, so
    # "this deployment serves only synthetic data" can be checked against a
    # file (KNOWN_LIMITATIONS, the synthetic-manifest entry). CI regenerates
    # and diffs it. B4's fixtures (make_synthetic_fixtures.py) are not in
    # it: its scanned PDF uses a system font that differs between hosts.
    write_manifest(
        MANIFEST_PATH,
        written
        + [
            GROUND_TRUTH_DIR / "tender_a_requirements.jsonl",
            GROUND_TRUTH_DIR / "tender_a_knowledge.jsonl",
        ],
        REPO_ROOT,
    )
    print(f"wrote {MANIFEST_PATH.relative_to(REPO_ROOT)}")

    mandatory_count = sum(1 for r in REQUIREMENTS if r.mandatory)
    print(
        f"\n{len(REQUIREMENTS)} requirements planted, {mandatory_count} mandatory "
        f"(L1 floor: 50-100 total, >=15 mandatory)."
    )


if __name__ == "__main__":
    main()
