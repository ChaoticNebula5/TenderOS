"""The structural half of "Red Team cannot silently edit approved
content" (Q3): redteam_service.py's own source never calls a write method
on any repository except uow.redteam. Grep-based, not exhaustive (a write
hidden behind a helper function elsewhere wouldn't be caught), but it
catches the direct case - the same "checkable by inspection" claim the
plan makes about this file's design. The integration-level proof (a real
red_team pass leaves proposal_claims/compliance_entries byte-identical)
lives in tests/integration/test_redteam_service.py, which needs a real DB.
"""

import re
from pathlib import Path

_SERVICE_PATH = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "tenderos_backend"
    / "src"
    / "tenderos_backend"
    / "domain"
    / "services"
    / "redteam_service.py"
)

# Every write-shaped call this codebase's other services use (create_*,
# update_*, mark_*, link_*, set_*, resolve_*, upsert_*, delete_*) on any
# uow.<repo> other than uow.redteam/uow.audit (audit writes are the
# standard cross-cutting log every resolve_* action in this codebase makes
# - see commercial_service.resolve_pricing_finding's own uow.audit.create
# call, which this file's resolve_finding mirrors) or uow.workflow (task
# bookkeeping, not content).
_WRITE_METHOD = re.compile(
    r"uow\.(\w+)\.(create_|update_|mark_|link_|set_|resolve_|upsert_|delete_)\w*\("
)
_ALLOWED_REPOS = {"redteam", "audit", "workflow"}


def test_redteam_service_only_writes_through_redteam_and_audit_repositories():
    source = _SERVICE_PATH.read_text(encoding="utf-8")
    offending = [
        (repo, method)
        for repo, method in _WRITE_METHOD.findall(source)
        if repo not in _ALLOWED_REPOS
    ]
    assert offending == [], (
        f"redteam_service.py calls a write method outside "
        f"{_ALLOWED_REPOS}: {offending} - this is the exact shape of a "
        f"silent-edit regression the structural argument in the plan "
        f"depends on never happening."
    )
