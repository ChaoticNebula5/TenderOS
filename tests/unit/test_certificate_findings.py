"""Plan items 89 and 91: talent_service._certificate_findings, the one place
a role's required certificate is matched against what the employee holds,
judged now by core/evidence_validity.py. Before it, only the first matching
certificate's date was read, so a withdrawn or unverified certificate
counted as held and a renewed one behind its lapsed copy did not."""

from datetime import date

from tenderos_backend.core.evidence_validity import evidence_validity
from tenderos_backend.domain.services.talent_service import _certificate_findings

TODAY = date(2026, 11, 18)


def _held(title: str, status: str = "active", valid_until: date | None = None):
    return evidence_validity(status=status, valid_until=valid_until, today=TODAY, title=title)


def _status(required: str, held: list) -> tuple[bool, str]:
    (finding,) = _certificate_findings([required], held)
    return finding.matched, finding.validity_status


def test_an_in_date_certificate_is_held():
    assert _status("ISO 27001", [_held("ISO 27001 Lead Implementer")]) == (True, "valid")


def test_a_lapsed_certificate_is_expired():
    held = [_held("ISO 27001 Lead Implementer", valid_until=date(2026, 11, 17))]
    assert _status("ISO 27001", held) == (True, "expired")


def test_an_expired_status_certificate_is_expired():
    assert _status("CISSP", [_held("CISSP", "expired")]) == (True, "expired")


def test_a_withdrawn_or_unverified_certificate_is_not_held():
    assert _status("CISSP", [_held("CISSP", "archived")]) == (True, "unverified")
    assert _status("CISSP", [_held("CISSP", "unverified")]) == (True, "unverified")


def test_a_renewed_certificate_next_to_its_lapsed_copy_is_held():
    held = [
        _held("PMP", valid_until=date(2026, 11, 1)),
        _held("PMP", valid_until=date(2027, 2, 15)),
    ]
    assert _status("PMP", held) == (True, "valid")


def test_no_certificate_is_unverified_and_unmatched():
    assert _status("PMP", [_held("CISSP")]) == (False, "unverified")
