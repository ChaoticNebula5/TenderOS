import enum


class Role(enum.StrEnum):
    BID_MANAGER = "bid_manager"
    SOLUTION_ARCHITECT = "solution_architect"
    TALENT_REVIEWER = "talent_reviewer"
    COMMERCIAL_REVIEWER = "commercial_reviewer"
    PROPOSAL_WRITER = "proposal_writer"
    APPROVER = "approver"
    ADMIN = "admin"
