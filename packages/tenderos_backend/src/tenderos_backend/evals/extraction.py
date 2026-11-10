import re
from dataclasses import dataclass, field

from tenderos_backend.db.models.requirement import Requirement
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.evals.datasets import GroundTruthRequirement

_TOKEN_PATTERN = re.compile(r"\[REQ-\d{3}\]")


@dataclass(frozen=True)
class CitationFailure:
    requirement_key: str
    reason: str


@dataclass(frozen=True)
class ExtractionEvalReport:
    precision: float
    recall: float
    f1: float
    mandatory_recall: float
    citation_validity: float
    total_ground_truth: int
    total_mandatory_ground_truth: int
    total_extracted: int
    false_negatives: list[GroundTruthRequirement] = field(default_factory=list)
    citation_failures: list[CitationFailure] = field(default_factory=list)


def _token(key: str) -> str:
    return f"[{key}]"


async def evaluate_extraction(
    ground_truth: list[GroundTruthRequirement],
    extracted: list[Requirement],
) -> ExtractionEvalReport:
    """Matching is deterministic string containment on the `[REQ-nnn]` token
    the synthetic package plants (scripts/synthetic_tender_a.py) and the
    extraction prompt is explicitly instructed to preserve verbatim - not
    fuzzy text similarity, so a second judgment call never contaminates the
    recall number itself.

    Recall and citation validity are deliberately independent checks: recall
    only asks "does SOME extracted requirement's text contain this token,"
    while citation validity asks, of the ones that do, "does that specific
    extracted requirement's source_chunk_id actually point to a chunk in the
    right document/page." A batching resolution bug (extract.py resolving
    source_chunk_ref to the wrong chunk while the model still gets the text
    right) would show up as a citation-validity failure, not a recall one -
    if recall alone were checked via "the cited chunk contains the token,"
    that failure mode would be invisible (citation validity would be 100% by
    construction).
    """
    matched: dict[str, Requirement] = {}
    for gt in ground_truth:
        token = _token(gt.requirement_key)
        for req in extracted:
            if token in req.requirement_text:
                matched[gt.requirement_key] = req
                break

    false_negatives = [gt for gt in ground_truth if gt.requirement_key not in matched]

    mandatory_gt = [gt for gt in ground_truth if gt.mandatory]
    mandatory_matched = sum(1 for gt in mandatory_gt if gt.requirement_key in matched)
    mandatory_recall = mandatory_matched / len(mandatory_gt) if mandatory_gt else 1.0

    recall = len(matched) / len(ground_truth) if ground_truth else 1.0

    # Precision: of everything extracted, how much corresponds to a real
    # ground-truth token (present or not) rather than being unmatched noise.
    # An extracted item whose text contains no [REQ-nnn] token at all cannot
    # be judged against this synthetic ground truth either way, so it is
    # excluded from both numerator and denominator - it is real production
    # documents' normal case (no planted token), just not a case this
    # specific synthetic benchmark can score precision on.
    all_keys = {gt.requirement_key for gt in ground_truth}
    scoreable_extracted = [req for req in extracted if _TOKEN_PATTERN.search(req.requirement_text)]
    correctly_matched_extracted = sum(
        1
        for req in scoreable_extracted
        if any(_token(key) in req.requirement_text for key in all_keys)
    )
    precision = (
        correctly_matched_extracted / len(scoreable_extracted) if scoreable_extracted else 1.0
    )

    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    citation_failures: list[CitationFailure] = []
    citation_checked = 0
    citation_valid = 0
    async with UnitOfWork() as uow:
        for gt in ground_truth:
            matched_req = matched.get(gt.requirement_key)
            if matched_req is None:
                continue
            citation_checked += 1
            chunk = await uow.documents.get_chunk(matched_req.source_chunk_id)
            if chunk is None:
                citation_failures.append(
                    CitationFailure(gt.requirement_key, "source_chunk_id does not resolve")
                )
                continue
            document = await uow.documents.get(chunk.document_id)
            if document is None or document.filename != gt.source_document:
                citation_failures.append(
                    CitationFailure(
                        gt.requirement_key,
                        f"cited document {document.filename if document else None!r} "
                        f"!= expected {gt.source_document!r}",
                    )
                )
                continue
            if gt.source_page is not None and chunk.page_num != gt.source_page:
                citation_failures.append(
                    CitationFailure(
                        gt.requirement_key,
                        f"cited page {chunk.page_num} != expected {gt.source_page}",
                    )
                )
                continue
            citation_valid += 1

    citation_validity = citation_valid / citation_checked if citation_checked else 1.0

    return ExtractionEvalReport(
        precision=precision,
        recall=recall,
        f1=f1,
        mandatory_recall=mandatory_recall,
        citation_validity=citation_validity,
        total_ground_truth=len(ground_truth),
        total_mandatory_ground_truth=len(mandatory_gt),
        total_extracted=len(extracted),
        false_negatives=false_negatives,
        citation_failures=citation_failures,
    )
