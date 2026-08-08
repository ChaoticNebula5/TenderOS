"""Chunking strategy - the highest-leverage decision in B4, since chunk
boundaries set the ceiling for B5's mandatory-recall gate and B6's Recall@10
gate. No DB; does load the embedding model's tokenizer via count_tokens.
"""

from tenderos_backend.ingestion.chunk import (
    MAX_CHUNK_CHARS,
    MAX_CHUNK_TOKENS,
    ExtractedBlock,
    build_chunks,
)


def test_chunks_never_span_a_page_boundary():
    """The load-bearing guarantee. A chunk records ONE page_num, so merging
    two pages would make every requirement extracted from the second page
    cite the first - directly breaking B5's citation-validity gate and B4's
    "chunks cannot return to source page/sheet" condition.
    """
    blocks = [
        ExtractedBlock(text="Page one requires a tax clearance certificate.", page_num=1),
        ExtractedBlock(text="Page two requires professional indemnity cover.", page_num=2),
        ExtractedBlock(text="Page three requires site access arrangements.", page_num=3),
    ]
    drafts = build_chunks(blocks)

    assert [d.page_num for d in drafts] == [1, 2, 3]
    assert "indemnity" not in drafts[0].chunk_text
    assert "tax clearance" not in drafts[1].chunk_text


def test_chunks_never_span_a_sheet_boundary():
    blocks = [
        ExtractedBlock(text="Labour | 1000", sheet_name="Pricing", cell_range="A1:B2"),
        ExtractedBlock(text="Steel | 2000", sheet_name="Materials", cell_range="A1:B2"),
    ]
    drafts = build_chunks(blocks)

    assert len(drafts) == 2
    assert [d.sheet_name for d in drafts] == ["Pricing", "Materials"]
    assert "Steel" not in drafts[0].chunk_text


def test_chunks_never_span_a_heading_boundary():
    blocks = [
        ExtractedBlock(text="Eligibility clause.", heading_path=["Section 1"]),
        ExtractedBlock(text="Pricing clause.", heading_path=["Section 2"]),
    ]
    drafts = build_chunks(blocks)

    assert len(drafts) == 2
    assert drafts[0].heading_path == ["Section 1"]
    assert "Pricing" not in drafts[0].chunk_text


def test_small_blocks_under_one_heading_merge_into_one_chunk():
    """Several short DOCX paragraphs should not become several near-empty
    chunks - tiny chunks embed poorly and flood retrieval with low-signal
    hits. Five clauses (~50 chars each, well under MAX_CHUNK_CHARS) rather
    than ten - this is testing that merging happens at all, not a specific
    count that happens to fit under whatever the cap currently is."""
    blocks = [
        ExtractedBlock(
            text=f"Clause {i}: the contractor shall maintain records.",
            heading_path=["Section 1"],
        )
        for i in range(5)
    ]
    drafts = build_chunks(blocks)

    assert len(drafts) == 1
    for i in range(5):
        assert f"Clause {i}:" in drafts[0].chunk_text


def test_oversized_block_splits_and_stays_within_the_model_window():
    sentence = (
        "The bidder shall provide a valid tax clearance certificate issued "
        "within the preceding twelve months and certified by a registered auditor. "
    )
    drafts = build_chunks([ExtractedBlock(text=sentence * 40, page_num=7)])

    assert len(drafts) > 1
    assert all(d.page_num == 7 for d in drafts)
    assert all(d.token_count <= MAX_CHUNK_TOKENS for d in drafts)


def test_dense_text_is_split_by_real_token_count_not_the_character_proxy():
    """MAX_CHUNK_CHARS assumes ~4 chars/token, which holds for prose but not
    for unspaced tender content (part numbers, clause codes). Without the
    tokenizer pass such a chunk reaches ~438 tokens - past the model's 256
    window - and everything after the cutoff is silently dropped at embed
    time, invisibly shrinking what B6 can ever retrieve.
    """
    dense = "".join(f"-part{i:04d}" for i in range(600))
    drafts = build_chunks([ExtractedBlock(text=dense, page_num=1)])

    assert len(drafts) > 1
    over_limit = [d.token_count for d in drafts if d.token_count > MAX_CHUNK_TOKENS]
    assert not over_limit, f"chunks would be truncated at embed time: {over_limit}"


def test_consecutive_chunks_overlap_within_a_group():
    """A requirement straddling a boundary must survive whole in at least one
    chunk, which is what mandatory recall ultimately measures."""
    lines = [f"Clause {i}: records shall be retained for seven years." for i in range(40)]
    drafts = build_chunks([ExtractedBlock(text="\n".join(lines), page_num=2)])

    assert len(drafts) > 1
    last_line_of_first = drafts[0].chunk_text.strip().split("\n")[-1]
    assert last_line_of_first in drafts[1].chunk_text


def test_identical_text_on_different_pages_gets_distinct_hashes():
    """document_chunks has UNIQUE(document_id, content_hash). Tender
    documents repeat boilerplate verbatim, so hashing text alone would make
    the same phrase on two pages collide and fail the insert."""
    blocks = [
        ExtractedBlock(text="Not applicable.", page_num=3),
        ExtractedBlock(text="Not applicable.", page_num=7),
    ]
    drafts = build_chunks(blocks)

    assert len(drafts) == 2
    assert drafts[0].content_hash != drafts[1].content_hash
    assert drafts[0].normalized_text == drafts[1].normalized_text


def test_no_duplicate_hashes_are_ever_emitted():
    """Whatever the input, the output must be insertable under
    UNIQUE(document_id, content_hash)."""
    blocks = [
        ExtractedBlock(text="Not applicable.", page_num=3),
        ExtractedBlock(text="Not applicable.", page_num=3),
        ExtractedBlock(text="A" * 5000, page_num=4),
    ]
    hashes = [d.content_hash for d in build_chunks(blocks)]
    assert len(hashes) == len(set(hashes))


def test_blank_blocks_do_not_become_chunks():
    """parse_pdf emits a block per page including blank ones; an empty chunk
    is unsearchable noise and chunk_text is NOT NULL."""
    blocks = [
        ExtractedBlock(text="", page_num=1),
        ExtractedBlock(text="   \n\t  ", page_num=2),
        ExtractedBlock(text="Real content here.", page_num=3),
    ]
    drafts = build_chunks(blocks)

    assert len(drafts) == 1
    assert drafts[0].page_num == 3


def test_chunking_is_deterministic():
    """ "Stable chunking": re-parsing an unchanged document must reproduce the
    same sequence, or a retry writes different chunk_index/content_hash rows
    and duplicates content."""
    blocks = [
        ExtractedBlock(text="Alpha clause about delivery. " * 30, page_num=1),
        ExtractedBlock(text="Beta clause about payment. " * 30, page_num=2),
        ExtractedBlock(text="Gamma.", heading_path=["Annex A"]),
    ]
    first = build_chunks(blocks)
    second = build_chunks(blocks)

    assert [d.content_hash for d in first] == [d.content_hash for d in second]
    assert [d.chunk_text for d in first] == [d.chunk_text for d in second]
    assert [d.page_num for d in first] == [d.page_num for d in second]


def test_unpunctuated_row_terminates_and_keeps_provenance():
    """An XLSX row has no sentence punctuation to split on, so the packer
    must fall back to a hard split rather than loop."""
    row = " | ".join(f"cell{i}" for i in range(500))
    drafts = build_chunks([ExtractedBlock(text=row, sheet_name="Pricing", cell_range="A1:Z50")])

    assert len(drafts) > 1
    assert all(d.sheet_name == "Pricing" for d in drafts)
    assert all(d.cell_range == "A1:Z50" for d in drafts)
    assert all(len(d.chunk_text) <= MAX_CHUNK_CHARS + 50 for d in drafts)


def test_empty_input_produces_no_chunks():
    assert build_chunks([]) == []
