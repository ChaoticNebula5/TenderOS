"""Token-budget batching for the extraction agent - pure function over
in-memory DocumentChunk objects, no DB session needed."""

import uuid

from tenderos_backend.agents.requirements.batch import (
    MAX_EXTRACTION_BATCH_TOKENS,
    build_batches,
)
from tenderos_backend.db.models.document import DocumentChunk


def _chunk(document_id, page_num, text, heading_path=None) -> DocumentChunk:
    return DocumentChunk(
        id=uuid.uuid4(),
        document_id=document_id,
        chunk_index=0,
        page_num=page_num,
        sheet_name=None,
        cell_range=None,
        heading_path=heading_path or [],
        chunk_text=text,
        normalized_text=text,
        content_hash=str(uuid.uuid4()),
    )


def test_never_crosses_a_provenance_boundary_even_under_budget():
    doc_id = uuid.uuid4()
    chunks = [_chunk(doc_id, page_num=1, text="short"), _chunk(doc_id, page_num=2, text="short")]
    batches = build_batches(chunks)
    assert len(batches) == 2
    assert len(batches[0].chunk_refs) == 1
    assert len(batches[1].chunk_refs) == 1


def test_never_crosses_a_document_boundary_with_identical_page_metadata():
    """Two different documents whose chunks happen to share page_num=1 and no
    heading must not be merged into one batch - document_id is part of the
    provenance key precisely to catch this."""
    doc_a, doc_b = uuid.uuid4(), uuid.uuid4()
    chunks = [_chunk(doc_a, page_num=1, text="a"), _chunk(doc_b, page_num=1, text="b")]
    batches = build_batches(chunks)
    assert len(batches) == 2


def test_consecutive_same_provenance_chunks_pack_into_one_batch():
    doc_id = uuid.uuid4()
    chunks = [_chunk(doc_id, page_num=1, text="a"), _chunk(doc_id, page_num=1, text="b")]
    batches = build_batches(chunks)
    assert len(batches) == 1
    assert len(batches[0].chunk_refs) == 2


def test_exceeding_token_budget_starts_a_new_batch():
    doc_id = uuid.uuid4()
    # Each chunk's text alone is well under B4's 256-token chunk cap, but
    # enough of them together exceed MAX_EXTRACTION_BATCH_TOKENS.
    big_text = "clause " * 400
    chunks = [_chunk(doc_id, page_num=1, text=big_text) for _ in range(4)]
    batches = build_batches(chunks)
    assert len(batches) > 1
    for batch in batches:
        total_tokens = sum(len(ref.text.split()) for ref in batch.chunk_refs)
        # A loose sanity check (word count, not the real tokenizer) - the
        # real assertion is just "packing stopped well before unbounded
        # growth," not an exact token count.
        assert total_tokens < MAX_EXTRACTION_BATCH_TOKENS * 2


def test_labels_are_unique_across_the_whole_run_not_just_per_batch():
    doc_id = uuid.uuid4()
    chunks = [_chunk(doc_id, page_num=n, text="x") for n in range(5)]
    batches = build_batches(chunks)
    all_labels = [ref.label for batch in batches for ref in batch.chunk_refs]
    assert len(all_labels) == len(set(all_labels))


def test_resolve_finds_the_matching_ref_by_label():
    doc_id = uuid.uuid4()
    batches = build_batches([_chunk(doc_id, page_num=1, text="x")])
    ref = batches[0].chunk_refs[0]
    assert batches[0].resolve(ref.label) is ref
    assert batches[0].resolve("nonexistent_label") is None


def test_empty_input_produces_no_batches():
    assert build_batches([]) == []
