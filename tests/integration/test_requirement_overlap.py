"""Plan item 125 (user decisions 2026-11-27: Q1 a, Q2 a): a requirement the
chunker's overlap puts whole in two neighbouring chunks is one requirement
with two citations, not two rows.

The measure is the planted fixture. The real-provider run found the same 11
of the RFP's planted [REQ-NNN] tokens in two live rows each, in every cold
run (T1, T3): each in two neighbouring chunks, the second copy in the lines
the chunker carried over. Here the real data/synthetic/tender_a/rfp.pdf is
parsed through ingestion, and each chunk gets one draft per planted token
it contains, in the form the model gave (the token moved first: observed,
"[REQ-024] The platform is encouraged ..." from "The platform [REQ-024] is
encouraged ..."), with the ground truth's category and mandatory flag. That
is what a model reading chunk by chunk returns, with no model call.

Then crafted chunks for what must never merge: the same sentence in a
distant chunk, across a page boundary, under another parent, outside the
carried lines, in other words, with another mandatory flag, in another
document. And a rejected requirement, a second extraction and the order of
drafts.

Real Postgres; the fixture test runs the real parser and embedder.
"""

import io
import json
import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select, text
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.requirement import Requirement, RequirementCitation
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import document_service
from tenderos_backend.domain.services.requirement_service import (
    RequirementDraft,
    citations_for,
    persist_extraction_results,
)
from tenderos_worker.jobs.ingestion import parse_and_embed_document

pytestmark = pytest.mark.asyncio

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RFP = REPO_ROOT / "data/synthetic/tender_a/rfp.pdf"
GROUND_TRUTH = REPO_ROOT / "data/ground_truth/tender_a_requirements.jsonl"
TOKEN = re.compile(r"\[REQ-\d+\]")
# The real-provider run's doubled tokens, the same in T1 and T3
# (gemini-run/results/item125-t1-pairs.txt).
DOUBLED_ON_THE_RUN = {
    "[REQ-008]", "[REQ-012]", "[REQ-024]", "[REQ-028]", "[REQ-033]", "[REQ-035]",
    "[REQ-043]", "[REQ-049]", "[REQ-052]", "[REQ-059]", "[REQ-062]",
}  # fmt: skip
# Every planted token the RFP's chunking puts in two chunks: the run's 11
# and three the model happened to extract from one chunk only (REQ-041,
# 054, 057), the same on T1's own chunks
# (gemini-run/results/item125-t1-tokens-in-two-chunks.txt).
IN_TWO_CHUNKS = DOUBLED_ON_THE_RUN | {"[REQ-041]", "[REQ-054]", "[REQ-057]"}


async def _tender() -> tuple[uuid.UUID, uuid.UUID]:
    async with get_session() as session:
        org = Organization(name=f"Requirement Overlap {uuid.uuid4().hex[:8]} Org")
        session.add(org)
        await session.flush()
        tender = Tender(organization_id=org.id, name="Requirement Overlap")
        session.add(tender)
        await session.commit()
        return org.id, tender.id


def _ground_truth() -> dict[str, dict]:
    rows = [json.loads(line) for line in GROUND_TRUTH.read_text().splitlines() if line.strip()]
    return {
        f"[{row['requirement_key']}]": row for row in rows if row["source_document"] == "rfp.pdf"
    }


def _as_the_model_said(line: str, token: str) -> str:
    """The line's requirement with its token moved first, as the model gave it."""
    return f"{token} " + " ".join(line.replace(token, " ").split())


async def _rfp_drafts(
    tender_id: uuid.UUID, org_id: uuid.UUID
) -> tuple[list[RequirementDraft], dict]:
    document, _ = await document_service.create_document(
        tender_id=tender_id,
        organization_id=org_id,
        actor_user_id=uuid.uuid4(),
        filename="rfp.pdf",
        content=io.BytesIO(RFP.read_bytes()).getvalue(),
        mime_type="application/pdf",
    )
    await parse_and_embed_document(str(document.id))
    async with UnitOfWork() as uow:
        chunks = await uow.documents.list_chunks_for_document(document.id)
    truth = _ground_truth()
    drafts: list[RequirementDraft] = []
    chunks_by_token: dict[str, list[int]] = {}
    for chunk in sorted(chunks, key=lambda c: c.chunk_index):
        for line in chunk.chunk_text.split("\n"):
            for token in TOKEN.findall(line):
                if token not in truth:
                    continue
                chunks_by_token.setdefault(token, []).append(chunk.chunk_index)
                drafts.append(
                    RequirementDraft(
                        requirement_text=_as_the_model_said(line, token),
                        category=truth[token]["expected_category"],
                        mandatory=truth[token]["mandatory"],
                        source_chunk_id=chunk.id,
                        document_id=document.id,
                    )
                )
    return drafts, chunks_by_token


async def _rows(tender_id: uuid.UUID) -> list[Requirement]:
    async with get_session() as session:
        result = await session.execute(
            select(Requirement)
            .where(Requirement.tender_id == tender_id)
            .order_by(Requirement.created_at)
        )
        return list(result.scalars().all())


async def _citations(requirement_id: uuid.UUID) -> set[uuid.UUID]:
    async with get_session() as session:
        result = await session.execute(
            select(RequirementCitation.chunk_id).where(
                RequirementCitation.requirement_id == requirement_id
            )
        )
        return set(result.scalars().all())


async def test_the_fixtures_11_overlap_pairs_are_one_requirement_each_with_both_citations():
    org_id, tender_id = await _tender()
    drafts, chunks_by_token = await _rfp_drafts(tender_id, org_id)
    doubled = {token for token, indices in chunks_by_token.items() if len(indices) == 2}
    # The measure: the 14 tokens in two chunks, the run's 11 among them, each
    # pair one apart (the chunker is deterministic, so the stack's chunks are
    # these). A model reading chunk by chunk doubles any of them.
    assert doubled == IN_TWO_CHUNKS
    assert DOUBLED_ON_THE_RUN <= doubled
    assert all(abs(chunks_by_token[t][0] - chunks_by_token[t][1]) == 1 for t in doubled)
    assert all(len(indices) <= 2 for indices in chunks_by_token.values())

    await persist_extraction_results(tender_id, drafts)

    rows = await _rows(tender_id)
    by_token: dict[str, list[Requirement]] = {}
    for row in rows:
        by_token.setdefault(TOKEN.findall(row.requirement_text)[0], []).append(row)
    assert set(by_token) == set(chunks_by_token), "every planted token is a requirement"
    assert [t for t, found in by_token.items() if len(found) > 1] == [], "0 doubled"
    assert len(rows) == len(chunks_by_token), "nothing genuinely separate merged"
    async with UnitOfWork() as uow:
        index_of = {
            c.id: c.chunk_index for c in await uow.documents.list_chunks_for_tender(tender_id)
        }
    for token, (row,) in by_token.items():
        cited = sorted(index_of[c] for c in await _citations(row.id))
        assert cited == sorted(chunks_by_token[token]), token


async def test_a_second_extraction_and_the_drafts_in_reverse_make_nothing_new():
    org_id, tender_id = await _tender()
    drafts, _ = await _rfp_drafts(tender_id, org_id)
    await persist_extraction_results(tender_id, list(reversed(drafts)))
    first = {row.id for row in await _rows(tender_id)}
    async with get_session() as session:
        cited_before = (
            await session.execute(text("SELECT count(*) FROM requirement_citations"))
        ).scalar_one()

    await persist_extraction_results(tender_id, drafts)
    # Each doubled token's copy in its later chunk, alone (drafts are in
    # chunk order, so a token's second draft is its later chunk's).
    seen: set[str] = set()
    later_copies: list[RequirementDraft] = []
    for draft in drafts:
        token = TOKEN.findall(draft.requirement_text)[0]
        if token in IN_TWO_CHUNKS:
            if token in seen:
                later_copies.append(draft)
            seen.add(token)
    assert len(later_copies) == 14
    await persist_extraction_results(tender_id, later_copies)

    assert {row.id for row in await _rows(tender_id)} == first
    async with get_session() as session:
        cited_after = (
            await session.execute(text("SELECT count(*) FROM requirement_citations"))
        ).scalar_one()
    assert cited_after == cited_before
    rows = await _rows(tender_id)
    assert sum(1 for row in rows if TOKEN.findall(row.requirement_text)[0] in IN_TWO_CHUNKS) == 14


# ----------------------------------------------------------------------
# Crafted chunks: what must never merge.
# ----------------------------------------------------------------------

REQUIREMENT = "The bidder shall hold a valid tax clearance certificate."
OTHER = "Each line item shall state its unit price in USD."


async def _document(tender_id: uuid.UUID, org_id: uuid.UUID) -> uuid.UUID:
    async with UnitOfWork() as uow:
        document = await uow.documents.create(
            side="client",
            doc_type="rfp",
            filename=f"{uuid.uuid4().hex[:6]}.docx",
            storage_key=f"test/{uuid.uuid4()}",
            sha256=uuid.uuid4().hex,
            mime_type="application/octet-stream",
            file_size_bytes=10,
            tender_id=tender_id,
        )
        await uow.commit()
        return document.id


async def _chunk(
    document_id: uuid.UUID,
    index: int,
    lines: list[str],
    page: int | None = 1,
    origin: str = "parsed",
    **provenance: object,
) -> uuid.UUID:
    body = "\n".join(lines)
    async with UnitOfWork() as uow:
        chunk = await uow.documents.create_chunk(
            document_id=document_id,
            chunk_index=index,
            chunk_text=body,
            normalized_text=" ".join(body.split()),
            content_hash=uuid.uuid4().hex,
            page_num=page,
            origin=origin,
            **provenance,  # type: ignore[arg-type]
        )
        await uow.commit()
        return chunk.id


def _draft(
    document_id: uuid.UUID,
    chunk_id: uuid.UUID,
    wording: str = REQUIREMENT,
    mandatory: bool = True,
    category: str = "eligibility",
) -> RequirementDraft:
    return RequirementDraft(
        requirement_text=wording,
        category=category,
        mandatory=mandatory,
        source_chunk_id=chunk_id,
        document_id=document_id,
    )


async def _neighbours(
    tender_id: uuid.UUID, org_id: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Two chunks as _pack_units leaves them: the requirement in the first
    one's tail, carried into the start of the second."""
    document_id = await _document(tender_id, org_id)
    earlier = await _chunk(document_id, 0, ["Section 2: Eligibility.", OTHER, REQUIREMENT])
    later = await _chunk(document_id, 1, [REQUIREMENT, "Bids are valid for ninety days."])
    return document_id, earlier, later


async def test_crafted_overlap_copies_are_one_requirement():
    """The positive control for the crafted cases below."""
    org_id, tender_id = await _tender()
    document_id, earlier, later = await _neighbours(tender_id, org_id)

    (row,) = await persist_extraction_results(
        tender_id, [_draft(document_id, earlier), _draft(document_id, later)]
    )

    assert await _citations(row.id) == {earlier, later}


async def test_each_requirement_is_returned_once_in_the_order_first_met():
    """The extraction node verifies once per returned entry (a model call)
    and counts the entries: an overlap copy, and the same requirement twice
    from one chunk, must not appear twice."""
    org_id, tender_id = await _tender()
    document_id, earlier, later = await _neighbours(tender_id, org_id)
    other = _draft(document_id, earlier, wording=OTHER)

    returned = await persist_extraction_results(
        tender_id,
        [_draft(document_id, earlier), other, _draft(document_id, later), other],
    )

    rows = {row.requirement_text: row.id for row in await _rows(tender_id)}
    assert [row.id for row in returned] == [rows[REQUIREMENT], rows[OTHER]]


async def test_the_primary_citation_comes_first_though_both_were_recorded_together():
    """Both citations are written in one transaction, so they share
    created_at (now()). The copy persisted first is the primary; it is made
    the one whose chunk id sorts last, so an order by id alone would put it
    second."""
    org_id, tender_id = await _tender()
    document_id, earlier, later = await _neighbours(tender_id, org_id)
    first, second = sorted([earlier, later], key=str, reverse=True)

    rows = await persist_extraction_results(
        tender_id, [_draft(document_id, first), _draft(document_id, second)]
    )

    assert rows[0].source_chunk_id == first
    assert (await citations_for([rows[0].id]))[rows[0].id] == [first, second]


async def _two_rows(tender_id: uuid.UUID, drafts: list[RequirementDraft]) -> None:
    rows = await persist_extraction_results(tender_id, drafts)
    assert len({row.id for row in rows}) == 2, "two separate requirements"


async def test_the_same_sentence_in_a_distant_chunk_stays_separate():
    org_id, tender_id = await _tender()
    document_id = await _document(tender_id, org_id)
    first = await _chunk(document_id, 0, ["Section 2.", REQUIREMENT])
    await _chunk(document_id, 1, ["Section 3.", OTHER])
    distant = await _chunk(document_id, 2, [REQUIREMENT, "Section 9 repeats it."])
    await _two_rows(tender_id, [_draft(document_id, first), _draft(document_id, distant)])


@pytest.mark.parametrize(
    "field, earlier_value, later_value",
    [
        ("page_num", 1, 2),
        ("sheet_name", "Pricing", "Terms"),
        ("cell_range", "A1:D20", "A21:D40"),
        ("heading_path", ["2 Eligibility"], ["3 Pricing"]),
    ],
)
async def test_identical_text_across_a_provenance_boundary_stays_separate(
    field: str, earlier_value: object, later_value: object
):
    """The chunker carries text only inside one provenance group (page,
    sheet, cell range, heading path), so consecutive chunks of two groups
    never share carried lines, whatever their text."""
    org_id, tender_id = await _tender()
    document_id = await _document(tender_id, org_id)
    name = "page" if field == "page_num" else field
    earlier = await _chunk(document_id, 0, [OTHER, REQUIREMENT], **{name: earlier_value})
    later = await _chunk(
        document_id, 1, [REQUIREMENT, "Bids are valid for ninety days."], **{name: later_value}
    )
    await _two_rows(tender_id, [_draft(document_id, earlier), _draft(document_id, later)])


async def test_a_synthetic_chunk_is_never_an_overlap_neighbour():
    """A synthetic chunk (a finding's note, an imported row) numbers its own
    sequence and was never packed with the parsed text."""
    org_id, tender_id = await _tender()
    document_id = await _document(tender_id, org_id)
    earlier = await _chunk(document_id, 0, [OTHER, REQUIREMENT])
    synthetic = await _chunk(
        document_id, 1, [REQUIREMENT, "Added by a resolved finding."], origin="synthetic"
    )
    await _two_rows(tender_id, [_draft(document_id, earlier), _draft(document_id, synthetic)])


async def test_another_category_stays_separate():
    org_id, tender_id = await _tender()
    document_id, earlier, later = await _neighbours(tender_id, org_id)
    await _two_rows(
        tender_id,
        [_draft(document_id, earlier), _draft(document_id, later, category="legal")],
    )


async def test_identical_text_under_another_parent_stays_separate():
    org_id, tender_id = await _tender()
    document_id, earlier, later = await _neighbours(tender_id, org_id)
    async with get_session() as session:
        await session.execute(
            text("UPDATE document_chunks SET parent_chunk_id = :parent WHERE id = :id"),
            {"parent": earlier, "id": later},
        )
        await session.commit()
    await _two_rows(tender_id, [_draft(document_id, earlier), _draft(document_id, later)])


async def test_identical_text_outside_the_carried_lines_stays_separate():
    """Neighbours with carried lines, but the requirement repeated in the
    second chunk's own text, not in what was carried."""
    org_id, tender_id = await _tender()
    document_id = await _document(tender_id, org_id)
    earlier = await _chunk(document_id, 0, [REQUIREMENT, OTHER])
    later = await _chunk(document_id, 1, [OTHER, "Bids are valid for ninety days.", REQUIREMENT])
    await _two_rows(tender_id, [_draft(document_id, earlier), _draft(document_id, later)])


async def test_other_words_in_overlapping_neighbours_stay_separate():
    org_id, tender_id = await _tender()
    document_id, earlier, later = await _neighbours(tender_id, org_id)
    await _two_rows(
        tender_id,
        [
            _draft(document_id, earlier),
            _draft(
                document_id,
                later,
                "The bidder shall hold a valid tax clearance certificate and a VAT number.",
            ),
        ],
    )


async def test_another_mandatory_flag_stays_separate():
    org_id, tender_id = await _tender()
    document_id, earlier, later = await _neighbours(tender_id, org_id)
    await _two_rows(
        tender_id,
        [_draft(document_id, earlier), _draft(document_id, later, mandatory=False)],
    )


async def test_the_same_requirement_in_another_document_stays_separate():
    org_id, tender_id = await _tender()
    document_id, earlier, _later = await _neighbours(tender_id, org_id)
    amendment = await _document(tender_id, org_id)
    # Index 1 and the same leading line as an overlap copy would have: only
    # the document separates them.
    restated = await _chunk(amendment, 1, [REQUIREMENT, "Bids are valid for ninety days."])
    await _two_rows(tender_id, [_draft(document_id, earlier), _draft(amendment, restated)])


async def test_a_rejected_requirement_is_not_brought_back_by_its_overlap_copy():
    org_id, tender_id = await _tender()
    document_id, earlier, later = await _neighbours(tender_id, org_id)
    (first,) = await persist_extraction_results(tender_id, [_draft(document_id, earlier)])
    async with UnitOfWork() as uow:
        await uow.requirements.reject(first.id)
        await uow.commit()

    (again,) = await persist_extraction_results(tender_id, [_draft(document_id, later)])

    assert again.id == first.id and again.rejected_at is not None
    assert [row.id for row in await _rows(tender_id)] == [first.id]
    assert await _citations(first.id) == {earlier, later}


async def test_a_copy_already_cited_is_matched_whatever_its_flags():
    """A second extraction finds a requirement whose mandatory flag a person
    changed: the copy in its second chunk is still that requirement."""
    org_id, tender_id = await _tender()
    document_id, earlier, later = await _neighbours(tender_id, org_id)
    await persist_extraction_results(
        tender_id, [_draft(document_id, earlier), _draft(document_id, later)]
    )
    (row,) = await _rows(tender_id)
    async with UnitOfWork() as uow:
        await uow.requirements.update_fields(row.id, mandatory=False)
        await uow.commit()

    await persist_extraction_results(tender_id, [_draft(document_id, later)])

    assert [r.id for r in await _rows(tender_id)] == [row.id]
