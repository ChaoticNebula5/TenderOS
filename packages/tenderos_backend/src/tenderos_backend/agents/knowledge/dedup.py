import re
import unicodedata
import uuid
from dataclasses import dataclass
from enum import StrEnum

from tenderos_backend.db.models.knowledge import Employee

# Deliberately narrow: strips parenthetical content only ("Dana Whitfield
# (dup)" -> "Dana Whitfield"), never a bare suffix token. Real names contain
# parentheticals essentially never - they're always an administrative
# annotation in this system's context ("(dup)", "(resubmitted)",
# "(contractor)") - so stripping them is safe with no false-merge risk.
# Deliberately NOT stripping suffix/title tokens (Jr, Sr, III, Dr, Mr, Mrs):
# "John Smith Jr." and "John Smith Sr." are two different real people, and
# collapsing that distinction would wrongly treat them as dedup candidates.
# Build Guide line 3069 requires normalized-name matching; it does not ask
# for fuzzy/similarity matching (nicknames, typos), which is out of scope
# here - see KNOWN_LIMITATIONS.md.
_PARENTHETICAL_PATTERN = re.compile(r"\([^)]*\)")


def normalize_employee_name(name: str) -> str:
    """Strip parenthetical content + accents, casefold, collapse whitespace,
    so "Dana Whitfield (dup)", "Tomás Reyes", and "tomas   reyes" each
    compare equal to their plain form - used both to build the
    normalized_name candidates are matched against and inside
    resolve_employee_duplicate when comparing a candidate's name.
    """
    without_parens = _PARENTHETICAL_PATTERN.sub(" ", name)
    decomposed = unicodedata.normalize("NFKD", without_parens)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(stripped.casefold().split())


class DuplicateVerdict(StrEnum):
    NEW = "new"  # no real match - create a fresh knowledge_items/employees row
    DUPLICATE = "duplicate"  # confidently the same person - update the existing row
    NEEDS_REVIEW = "needs_review"  # signals disagree - a human decides


@dataclass(frozen=True)
class DuplicateResolution:
    verdict: DuplicateVerdict
    matched_employee: Employee | None  # set when verdict is DUPLICATE or the
    # best guess for NEEDS_REVIEW - None for NEW
    reason: str  # short, human-readable explanation for the import item's
    # review queue entry - e.g. "employee_code matched but name differs"


# Employee duplicate-resolution policy implemented below.
#
# Steps (plain-English, not code):
#   1. If `candidates` is empty, return DuplicateResolution(NEW, None, "no
#      matching employee_code/email/name/resume on file").
#   2. Otherwise, for each candidate in `candidates`, work out which of the
#      four signals actually matched this specific candidate (the repository
#      call that produced `candidates` used OR logic - a candidate might
#      match on name only, or on employee_code only, or on several fields at
#      once. You'll need to re-compare `candidate` against each `Employee` to
#      know which).
#   3. Decide a precedence between signals - they are not equally trustworthy.
#      Some things worth weighing (not a complete list - this is the actual
#      decision to make):
#        - employee_code is assigned by the organization's own HR system, so
#          two rows sharing one is a strong signal - but what if the name is
#          completely different (a code got reused/typo'd)? Does that still
#          count as a duplicate, or does the mismatch make it NEEDS_REVIEW?
#        - resume_document_id matching means the literal same resume file
#          (by content hash, since documents dedup on sha256 within an org -
#          see B4's uq_org_document_hash) was already on file for that
#          employee. Does a re-uploaded identical resume alone justify
#          DUPLICATE, or should it still corroborate rather than decide alone?
#        - name-only matches (via normalize_employee_name) are the weakest
#          signal - common names collide. Should a name-only match ever
#          reach DUPLICATE on its own, or always NEEDS_REVIEW?
#        - what happens when two DIFFERENT candidates each match on a
#          different signal (e.g. one shares employee_code, another shares
#          email, and they're not the same Employee row)? That is a genuine
#          three-way ambiguity, not just "pick the best candidate."
#   4. Return a DuplicateResolution reflecting that decision, with `reason`
#      naming the specific signal(s) that drove it (this string ends up in
#      knowledge_import_items.error_message or a review-queue note, so it
#      needs to be useful to the human reviewer, not just to you right now).
#
# What's already available to use:
#   - `candidate_employee_code`, `candidate_name`, `candidate_email`,
#     `candidate_resume_document_id` - the incoming row's own fields.
#     candidate_email is always None for employee-master-sourced rows (that
#     template has no email column - see employee_master.py's headers); it's
#     only populated when the candidate came from LLM-parsed resume text.
#   - `candidates: list[Employee]` - already fetched via
#     KnowledgeRepository.find_employee_duplicate_candidates, i.e. the SQL
#     matching is already done; this function only adjudicates.
#   - `normalize_employee_name(str) -> str`, defined just above.
#   - `Employee` (imported above) has .employee_code, .email, .name,
#     .resume_document_id, .knowledge_item_id - all directly readable.
#
# Why this is worth writing by hand, not boilerplate: B5's mandatory
# classifier had one signal disagreeing with one other signal (the LLM vs.
# keyword rules) and "disagree -> review" was the whole rule. Here there are
# four signals that can each independently hit different candidates, so
# "does it match" isn't a yes/no - it's a precedence and conflict-resolution
# policy over multiple simultaneous partial matches, which is exactly the
# kind of design decision (not a lookup) CLAUDE.md's TODO(human) standard
# calls out as worth your own judgment rather than mine.
def resolve_employee_duplicate(
    candidate_employee_code: str | None,
    candidate_name: str,
    candidate_email: str | None,
    candidate_resume_document_id: uuid.UUID | None,
    candidates: list[Employee],
) -> DuplicateResolution:
    if not candidates:
        return DuplicateResolution(
            DuplicateVerdict.NEW,
            None,
            "no matching employee_code/email/name/resume on file",
        )

    candidate_normalized_name = normalize_employee_name(candidate_name)
    candidate_matches: list[tuple[Employee, tuple[str, ...]]] = []
    normalized_candidate_email = candidate_email.strip().casefold() if candidate_email else None

    for candidate in candidates:
        signals: list[str] = []

        if (
            candidate_employee_code is not None
            and candidate.employee_code == candidate_employee_code
        ):
            signals.append("employee_code")
        if normalize_employee_name(candidate.name) == candidate_normalized_name:
            signals.append("name")
        if (
            candidate_resume_document_id is not None
            and candidate.resume_document_id == candidate_resume_document_id
        ):
            signals.append("resume_document_id")
        if (
            normalized_candidate_email is not None
            and candidate.email is not None
            and candidate.email.strip().casefold() == normalized_candidate_email
        ):
            signals.append("email")
        if signals:
            candidate_matches.append((candidate, tuple(signals)))

    strong_signals = {
        "employee_code",
        "email",
        "resume_document_id",
    }

    strong_matches = [
        (employee, signals)
        for employee, signals in candidate_matches
        if any(signal in strong_signals for signal in signals)
    ]

    if len(strong_matches) > 1:
        conflicting_signals = sorted(
            {
                signal
                for _, matched_signals in strong_matches
                for signal in matched_signals
                if signal in strong_signals
            }
        )

        return DuplicateResolution(
            verdict=DuplicateVerdict.NEEDS_REVIEW,
            matched_employee=None,
            reason=(
                "conflicting strong signals matched different employees: "
                + ", ".join(conflicting_signals)
            ),
        )

    if len(strong_matches) == 1:
        employee, matched_signals = strong_matches[0]
        signal_names = ", ".join(matched_signals)

        if "name" in matched_signals:
            return DuplicateResolution(
                verdict=DuplicateVerdict.DUPLICATE,
                matched_employee=employee,
                reason=f"matched strong identity signal(s) and normalized name: {signal_names}",
            )

        return DuplicateResolution(
            verdict=DuplicateVerdict.NEEDS_REVIEW,
            matched_employee=employee,
            reason=f"{signal_names} matched but normalized name differs",
        )

    name_matches = [
        employee for employee, matched_signals in candidate_matches if "name" in matched_signals
    ]
    if name_matches:
        matched_employee = name_matches[0] if len(name_matches) == 1 else None
        return DuplicateResolution(
            verdict=DuplicateVerdict.NEEDS_REVIEW,
            matched_employee=matched_employee,
            reason="normalized name matched without a strong identity signal",
        )

    return DuplicateResolution(
        verdict=DuplicateVerdict.NEW,
        matched_employee=None,
        reason="candidate rows were returned but no identity signals matched",
    )
