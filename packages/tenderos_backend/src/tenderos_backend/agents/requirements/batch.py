import uuid
from dataclasses import dataclass, field

from tenderos_backend.db.models.document import DocumentChunk
from tenderos_backend.ingestion.embed import count_tokens

# Tested at 400 during the B5 gate-failure investigation (hypothesis: batch
# density causes near-duplicate suppression). Result: recall moved
# (0.816->0.878) but two clean discriminators - REQ-066 missed alone in a
# 3-item batch, and a template pair split across two separate 400-token
# batches both still missing - ruled out density as the mechanism. The
# actual cause was the ground-truth generator planting near-verbatim
# restatements (same subject/object, only the modal verb differed) that the
# model correctly treated as one obligation, not two. Fixed at the
# generator (see synthetic_tender_a.py's _FILLER_TEMPLATES), so batch size
# reverts to its original starting value - this was never the right lever.
MAX_EXTRACTION_BATCH_TOKENS = 1200


@dataclass(frozen=True)
class ChunkRef:
    """One chunk's identity, paired with the short label the LLM sees in its
    own batch's prompt instead of a real UUID. E1: "no agent may fabricate
    IDs" - a model asked to echo a UUID back correctly is a model that can
    get a UUID back wrong; a model asked to echo "chunk_3" back has nothing
    to invent. extract.py resolves label -> (chunk_id, document_id) after
    parsing the model's response, never the model itself.
    """

    label: str
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    text: str


@dataclass(frozen=True)
class ExtractionBatch:
    chunk_refs: list[ChunkRef] = field(default_factory=list)

    def resolve(self, label: str) -> ChunkRef | None:
        return next((ref for ref in self.chunk_refs if ref.label == label), None)


def _provenance_key(chunk: DocumentChunk) -> tuple:
    # document_id is part of the key (unlike ingestion/chunk.py's grouping,
    # which never needs it - one call to build_chunks already operates
    # within a single document). This batcher runs over a whole tender's
    # chunks at once (list_chunks_for_tender spans every document), so two
    # unrelated documents that happen to share page_num=1/no heading must
    # not be treated as one provenance group.
    return (
        chunk.document_id,
        chunk.page_num,
        chunk.sheet_name,
        chunk.cell_range,
        tuple(chunk.heading_path or []),
    )


def build_batches(chunks: list[DocumentChunk]) -> list[ExtractionBatch]:
    """Greedy token-budget packer: fill a batch until either the token budget
    or a provenance boundary is hit, whichever comes first - mirroring
    ingestion/chunk.py's _pack_units shape one level up (chunks instead of
    atomic text units, a batch instead of a chunk). No overlap between
    batches is needed the way chunk.py overlaps chunks: a batch is a
    prompt input, not a stored, independently-retrieved artifact, and the
    next batch still contains the very next chunk in full, un-truncated.

    Safe against a single oversized chunk starving the packer: B4's
    MAX_CHUNK_TOKENS=256 hard-caps every individual chunk well under this
    module's 1200-token budget, so the first chunk placed into a fresh batch
    can never itself exceed the budget.
    """
    batches: list[ExtractionBatch] = []
    current: list[ChunkRef] = []
    current_tokens = 0
    current_key: tuple | None = None
    label_counter = 0

    for chunk in chunks:
        key = _provenance_key(chunk)
        chunk_tokens = count_tokens(chunk.normalized_text)

        crosses_boundary = current_key is not None and key != current_key
        exceeds_budget = current_tokens + chunk_tokens > MAX_EXTRACTION_BATCH_TOKENS

        if current and (crosses_boundary or exceeds_budget):
            batches.append(ExtractionBatch(chunk_refs=current))
            current = []
            current_tokens = 0

        label_counter += 1
        current.append(
            ChunkRef(
                label=f"chunk_{label_counter}",
                chunk_id=chunk.id,
                document_id=chunk.document_id,
                text=chunk.chunk_text,
            )
        )
        current_tokens += chunk_tokens
        current_key = key

    if current:
        batches.append(ExtractionBatch(chunk_refs=current))

    return batches
