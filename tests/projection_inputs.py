"""What the graph projector reads and writes, declared once (plan item 95).

Two tests use this:
- tests/integration/test_projection_reach.py projects a fully populated bid
  into a recording Neo4j stand-in and checks the properties the projector
  writes are exactly PROJECTED_PROPERTIES, so this file cannot drift from
  graph_projection/projector.py;
- tests/unit/test_projection_bump_guard.py checks every repository method
  that writes a projected model is classified here, and every caller of a
  PROJECTED_WRITERS method bumps the bid's state version, or is in
  ALLOWED_WITHOUT_BUMP with a reason.

A new projected property, a new repository write method on a projected
model, or a new caller of one fails one of those until it is added here,
which is the point: that is the moment to decide whether it bumps.
"""

# The models whose rows the projector reads (an insert or delete changes
# which nodes or edges exist).
PROJECTED_MODELS = frozenset(
    {
        "Tender",
        "Requirement",
        "EvidenceLink",
        "ComplianceEntry",
        "ComplianceEvidence",
        "RoleRequirement",
        "RoleRequirementSource",
        "RoleMatch",
        "ProposalSection",
        "ProposalClaim",
        "ProposalClaimEvidence",
        "ProposalClaimRequirement",
        "ProposalClaimEmployee",
        "TenderKnowledgeSelection",
        "KnowledgeItem",
        "Employee",
        "Certification",
        "PastProject",
        "Capability",
        "KnowledgeRelationship",
    }
)

# Graph label (or relationship type) -> the properties the projector writes on
# it. "id", "tender_id" and "knowledge_item_id" are identity, not content.
PROJECTED_PROPERTIES: dict[str, frozenset[str]] = {
    "Tender": frozenset(
        {"id", "tender_id", "organization_id", "name", "status", "state_version", "submission_date"}
    ),
    # Not requirements.verification_checked_at (plan item 59, Q5, PR 21): the
    # node carries verified only, so a re-check with the same verdict, which
    # writes the time alone, changes no graph and does not bump.
    "Requirement": frozenset(
        {"id", "tender_id", "display_code", "category", "mandatory", "verified"}
    ),
    "Evidence": frozenset(
        {"id", "tender_id", "source_kind", "verified", "validity_status", "confidence"}
    ),
    "Compliance": frozenset({"id", "tender_id", "status", "human_approved", "confidence"}),
    "Role": frozenset({"id", "tender_id", "role_name", "min_experience_years"}),
    "ProposalSection": frozenset({"id", "tender_id", "section_name", "status", "version"}),
    "ProposalClaim": frozenset(
        {"id", "tender_id", "claim_type", "is_factual", "claim_kind", "grounding_status"}
    ),
    # Shared knowledge nodes: knowledge_items' own fields plus the typed row's.
    "Employee": frozenset(
        {
            "knowledge_item_id",
            "organization_id",
            "status",
            "valid_from",
            "valid_until",
            "name",
            "current_title",
            "eligible_for_bid",
            "profile_review_status",
            "availability_status",
        }
    ),
    "Certification": frozenset(
        {
            "knowledge_item_id",
            "organization_id",
            "status",
            "valid_from",
            "valid_until",
            "title",
            "issuer",
            "credential_id",
        }
    ),
    "Project": frozenset(
        {
            "knowledge_item_id",
            "organization_id",
            "status",
            "valid_from",
            "valid_until",
            "title",
            "client_name",
            "start_date",
            "end_date",
        }
    ),
    "Capability": frozenset(
        {
            "knowledge_item_id",
            "organization_id",
            "status",
            "valid_from",
            "valid_until",
            "title",
            "category",
        }
    ),
    "USED_IN": frozenset({"selected", "selected_at", "deselected_at"}),
    "WORKED_ON": frozenset({"confidence", "verified"}),
    "DEMONSTRATES": frozenset({"confidence", "verified"}),
}

# Repository methods that write projected data (Class.method). A caller of any
# of these must bump, or be in ALLOWED_WITHOUT_BUMP.
PROJECTED_WRITERS = frozenset(
    {
        "ComplianceRepository.create_evidence_link",
        "ComplianceRepository.refresh_evidence_link_signals",
        "ComplianceRepository.create_compliance_entry",
        "ComplianceRepository.update_compliance_entry",
        "ComplianceRepository.link_evidence",
        "ComplianceRepository.unlink_all_evidence",
        "KnowledgeRepository.update_knowledge_item",
        "KnowledgeRepository.create_employee",
        "KnowledgeRepository.update_employee",
        "KnowledgeRepository.create_certification",
        "KnowledgeRepository.create_past_project",
        "KnowledgeRepository.create_capability",
        "KnowledgeRepository.create_relationship",
        "KnowledgeRepository.upsert_selection",
        "ProposalRepository.create_proposal_section",
        "ProposalRepository.update_proposal_section",
        "ProposalRepository.create_proposal_claim",
        "ProposalRepository.update_proposal_claim",
        "ProposalRepository.link_claim_evidence",
        "ProposalRepository.link_claim_requirement",
        "ProposalRepository.link_claim_employee",
        "RequirementRepository.create",
        "RequirementRepository.update_fields",
        "RequirementRepository.delete",
        "RequirementRepository.set_verdict_for_text",
        "RequirementRepository.reject",
        "TalentRepository.create_role_requirement",
        "TalentRepository.link_role_requirement_source",
        "TalentRepository.create_role_match",
        "TalentRepository.update_role_match",
        "TenderRepository.create",
        "TenderRepository.update",
    }
)

# Repository methods that touch a projected model but write nothing the graph
# shows, each with why.
NOT_PROJECTED_WRITES: dict[str, str] = {
    "TenderRepository.increment_state_version": "the counter itself",
    "TenderRepository.bump_state_version": "the counter itself",
    "GateRepository.record_decision": (
        "reads tenders.state_version in its condition (plan item 93); writes the gate"
    ),
    "KnowledgeRepository.create_company_document": (
        "a company_document item has no graph node (D1)"
    ),
    "ProposalRepository.rewrite_draft_section": (
        "writes only a draft section's content, which the projector does not copy; the "
        "section's node carries its version and status (plan item 59)"
    ),
}

# The calls that move a bid's state version.
BUMP_CALLS = frozenset(
    {
        "bump_state_version",
        "increment_state_version",
        "_bump_bids_projecting",
        "_bump_bids_projecting_both",
    }
)

# Functions (module path relative to the scanned root, ::name) that call a
# projected writer without bumping, each with why.
ALLOWED_WITHOUT_BUMP: dict[str, str] = {
    "tenderos_backend/domain/services/tender_service.py::create_tender": (
        "a new bid has no projection yet; its first project_graph reads it"
    ),
    "tenderos_backend/domain/services/knowledge_service.py::_create_or_get_employee": (
        "a new knowledge item reaches no bid until something links it"
    ),
    "tenderos_backend/domain/services/knowledge_service.py::_create_or_get_certification": (
        "a new knowledge item reaches no bid until something links it"
    ),
    "tenderos_backend/domain/services/knowledge_service.py::import_past_project": (
        "a new knowledge item reaches no bid until something links it"
    ),
    "tenderos_backend/domain/services/knowledge_service.py::import_capability": (
        "a new knowledge item reaches no bid until something links it"
    ),
    "tenderos_backend/domain/services/knowledge_service.py::replace_employee_resume": (
        "writes only employees.resume_document_id, which is not projected"
    ),
}

# Helpers that call a projected writer without bumping because every caller
# bumps in the same transaction (the _in_uow shape): helper -> its callers.
# The guard checks each named caller calls the helper and bumps, and that no
# other function calls it. Empty today: every _in_uow helper bumps itself.
ALLOWED_BECAUSE_CALLERS_BUMP: dict[str, frozenset[str]] = {}
