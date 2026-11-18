"""Plan item 80 (PR 18): the resume merge's rule, the fingerprint the review
panel carries, and a resume item's source key - pure functions, no database.
Employees are built in memory. Every fixture value is fake.
"""

import uuid
from decimal import Decimal

from tenderos_backend.agents.knowledge.dedup import (
    DuplicateVerdict,
    normalize_employee_email,
    resolve_employee_duplicate,
)
from tenderos_backend.agents.schemas.knowledge import ExtractedEmployeeProfile
from tenderos_backend.db.models.knowledge import Employee
from tenderos_backend.domain.services.knowledge_service import (
    resume_merge_changes,
    resume_merge_fields,
    resume_merge_overwrites,
    resume_target_fingerprint,
)
from tenderos_worker.jobs.knowledge_import import resume_source_ref


def _employee(**fields: object) -> Employee:
    values: dict[str, object] = {
        "knowledge_item_id": uuid.UUID(int=1),
        "organization_id": uuid.UUID(int=2),
        "name": "Quinn Ash",
        "email": None,
        "current_title": "Engineer",
        "professional_location": "Pune",
        "years_experience": None,
        "resume_document_id": uuid.UUID(int=3),
        "profile_review_status": "verified",
    }
    values.update(fields)
    return Employee(**values)


def _profile(**fields: object) -> ExtractedEmployeeProfile:
    return ExtractedEmployeeProfile.model_validate(
        {"name": "Quinn Ash", "confidence": 0.9, **fields}
    )


def test_the_resume_wins_where_it_has_a_value_and_a_blank_keeps_what_is_on_file():
    changes = resume_merge_changes(
        _employee(),
        _profile(email="q@example.test", current_title="Lead", professional_location=None),
    )
    assert changes == {"email": (None, "q@example.test"), "current_title": ("Engineer", "Lead")}


def test_an_empty_string_from_the_resume_is_no_value():
    assert resume_merge_changes(_employee(), _profile(current_title="", email="")) == {}


def test_years_compare_as_numbers_across_decimal_and_float():
    employee = _employee(years_experience=Decimal("12"))
    assert resume_merge_changes(employee, _profile(years_experience=12.0)) == {}
    assert resume_merge_changes(employee, _profile(years_experience=12.5)) == {
        "years_experience": (12.0, 12.5)
    }


def test_an_overwrite_is_a_change_to_a_field_that_holds_a_value():
    profile = _profile(email="q@example.test", current_title="Lead", years_experience=0.0)
    assert resume_merge_overwrites(_employee(), profile) == ["current_title"]
    assert resume_merge_overwrites(_employee(years_experience=Decimal("0.5")), profile) == [
        "current_title",
        "years_experience",
    ]


def test_the_fingerprint_is_stable_and_ignores_what_the_panel_does_not_show():
    assert resume_target_fingerprint(_employee()) == resume_target_fingerprint(_employee())
    # The name and eligibility are not on the panel, and no merge writes them.
    assert resume_target_fingerprint(_employee(name="Other")) == resume_target_fingerprint(
        _employee()
    )
    assert resume_target_fingerprint(_employee(years_experience=Decimal("4"))) == (
        resume_target_fingerprint(_employee(years_experience=4.0))
    )


def test_a_resume_source_ref_carries_its_document():
    document_id = uuid.UUID(int=7)
    assert resume_source_ref(document_id, "CV.pdf") == f"{document_id}/CV.pdf"
    assert resume_source_ref(document_id, "CV.pdf") != resume_source_ref(uuid.UUID(int=8), "CV.pdf")


def test_the_matchers_email_normaliser_strips_and_casefolds():
    assert normalize_employee_email("  Quinn.Ash@Example.TEST ") == "quinn.ash@example.test"
    assert normalize_employee_email(None) is None
    assert normalize_employee_email("") is None


def test_the_duplicate_check_matches_an_email_differing_in_case_or_spaces():
    on_file = _employee(name="Quinn Ash", email="quinn.ash@example.test")
    resolution = resolve_employee_duplicate(
        candidate_employee_code=None,
        candidate_name="Quinn Ash",
        candidate_email=" Quinn.Ash@EXAMPLE.test",
        candidate_resume_document_id=None,
        candidates=[on_file],
    )
    assert resolution.verdict == DuplicateVerdict.DUPLICATE
    assert "email" in resolution.reason


def test_an_email_differing_only_in_case_or_spaces_is_not_a_change():
    """The merge compares emails as the duplicate check does: such an email
    neither rewrites the stored one nor sends a verified profile to review."""
    employee = _employee(email="quinn.ash@example.test")
    same = _profile(email="  Quinn.Ash@Example.TEST")
    assert resume_merge_changes(employee, same) == {}
    assert resume_merge_overwrites(employee, same) == []
    other = _profile(email="quinn@example.test")
    assert resume_merge_changes(employee, other) == {
        "email": ("quinn.ash@example.test", "quinn@example.test")
    }
    assert resume_merge_overwrites(employee, other) == ["email"]


def test_the_change_list_has_every_field_in_order_with_both_values():
    fields = resume_merge_fields(
        _employee(email="quinn.ash@example.test"),
        _profile(email="QUINN.ASH@example.test", current_title="Lead", years_experience=4),
    )
    assert [(f.field, f.on_file, f.from_resume, f.changes) for f in fields] == [
        ("email", "quinn.ash@example.test", "QUINN.ASH@example.test", False),
        ("current_title", "Engineer", "Lead", True),
        ("professional_location", "Pune", None, False),
        ("years_experience", None, 4.0, True),
    ]
