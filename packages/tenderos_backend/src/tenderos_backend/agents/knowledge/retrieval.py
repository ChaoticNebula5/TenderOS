import uuid
from dataclasses import dataclass
from datetime import date

from tenderos_backend.agents.knowledge.rerank import rerank
from tenderos_backend.core.config import settings
from tenderos_backend.db.models.document import DocumentChunk
from tenderos_backend.db.models.knowledge import KnowledgeItem
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.ingestion.embed import embed_texts

# Standard RRF constant (Cormack et al.) - dampens the influence of any
# single very-high rank so one list's #1 result doesn't automatically
# dominate fusion; not tuned for this dataset, just the well-known default.
_RRF_K = 60


def reciprocal_rank_fusion(
    ranked_lists: list[list[uuid.UUID]],
) -> dict[uuid.UUID, float]:
    """Chosen over weighted-score fusion (Build Guide names either as
    acceptable) because it's parameter-free - lexical ts_rank and vector
    cosine similarity live on different, incomparable scales, so a weighted
    sum would need a weighting constant to tune before the gate is even
    measured once. RRF only uses rank position, sidestepping that entirely.
    """
    scores: dict[uuid.UUID, float] = {}
    for ranked_list in ranked_lists:
        for rank, item_id in enumerate(ranked_list, start=1):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (_RRF_K + rank)
    return scores


@dataclass(frozen=True)
class KnowledgeSearchResult:
    knowledge_item_id: uuid.UUID
    item_type: str
    title: str
    excerpt: str
    document_id: uuid.UUID
    chunk_id: uuid.UUID
    page_num: int | None
    heading_path: list[str] | None
    fusion_score: float
    rerank_score: float


async def search_company_knowledge(
    organization_id: uuid.UUID,
    query_text: str,
    tender_id: uuid.UUID | None = None,
    selected_only: bool = False,
    valid_only: bool = False,
    retrieval_top_k: int | None = None,
    rerank_top_n: int | None = None,
    today: date | None = None,
) -> list[KnowledgeSearchResult]:
    """F3's search_company_knowledge internal tool. Hybrid: lexical +
    vector, RRF-fused, mapped from matched chunks to the knowledge_items
    they cite, metadata-filtered, then reranked.

    Organization-boundary safety: both search_chunks_lexical_for_org and
    search_chunks_vector_for_org filter on Document.organization_id, never
    Document.tender_id - a tender's own uploaded documents (client-side RFP
    content) can never surface here, only the organization's own knowledge
    documents. This is what satisfies the "retrieval crosses organization
    boundaries" do-not-proceed-if item.

    selected_only requires tender_id (a selection is inherently tender-
    scoped) - raises rather than silently returning unfiltered results if
    the caller asks for a filter it hasn't given enough information to
    apply, same "fail loud on a caller mistake" posture as
    AgentTraceRepository.create's tender_id/organization_id check.
    """
    if selected_only and tender_id is None:
        raise ValueError("selected_only=True requires a tender_id")

    retrieval_top_k = retrieval_top_k or settings.retrieval_top_k
    rerank_top_n = rerank_top_n or settings.rerank_top_n
    today = today or date.today()

    query_embedding = (await embed_texts([query_text]))[0]

    async with UnitOfWork() as uow:
        lexical = await uow.documents.search_chunks_lexical_for_org(
            organization_id, query_text, retrieval_top_k
        )
        vector = await uow.documents.search_chunks_vector_for_org(
            organization_id, query_embedding, retrieval_top_k
        )

        chunks_by_id = {chunk.id: chunk for chunk, _ in lexical}
        chunks_by_id.update({chunk.id: chunk for chunk, _ in vector})

        fused_scores = reciprocal_rank_fusion(
            [
                [chunk.id for chunk, _ in lexical],
                [chunk.id for chunk, _ in vector],
            ]
        )
        ranked_chunk_ids = sorted(
            fused_scores, key=lambda chunk_id: fused_scores[chunk_id], reverse=True
        )[:retrieval_top_k]

        selected_item_ids: set[uuid.UUID] | None = None
        if selected_only and tender_id is not None:
            selections = await uow.knowledge.list_selections_for_tender(
                tender_id, selected_only=True
            )
            selected_item_ids = {selection.knowledge_item_id for selection in selections}

        candidates: list[tuple[DocumentChunk, KnowledgeItem, float]] = []
        for chunk_id in ranked_chunk_ids:
            chunk = chunks_by_id[chunk_id]
            # One chunk can be the source for more than one knowledge_item
            # (B4's chunker groups several paragraphs under one heading
            # into a single chunk when they fit the token budget) - every
            # knowledge_item a matched chunk supports is its own candidate,
            # not just the first one found.
            knowledge_items = await uow.knowledge.get_knowledge_items_by_chunk_id(chunk_id)
            for knowledge_item in knowledge_items:
                if (
                    valid_only
                    and knowledge_item.valid_until is not None
                    and knowledge_item.valid_until < today
                ):
                    continue
                # B16: valid_only's other half - an archived/expired-status
                # item with no valid_until set at all (or one still in the
                # future) used to stay eligible regardless, since only the
                # date check above gated it. "active" is the only status
                # compliance_service._compute_candidate_signals itself
                # treats as currently good for non-employee items; mirrored
                # here rather than re-deriving a second definition of
                # "valid" for the same field.
                if valid_only and knowledge_item.status != "active":
                    continue
                if selected_item_ids is not None and knowledge_item.id not in selected_item_ids:
                    continue
                candidates.append((chunk, knowledge_item, fused_scores[chunk_id]))

    if not candidates:
        return []

    rerank_scores = await rerank(query_text, [chunk.chunk_text for chunk, _, _ in candidates])
    scored = sorted(
        zip(candidates, rerank_scores, strict=True), key=lambda pair: pair[1], reverse=True
    )

    results: list[KnowledgeSearchResult] = []
    for (chunk, knowledge_item, fusion_score), rerank_score in scored[:rerank_top_n]:
        results.append(
            KnowledgeSearchResult(
                knowledge_item_id=knowledge_item.id,
                item_type=knowledge_item.item_type,
                title=knowledge_item.title,
                excerpt=chunk.chunk_text,
                document_id=chunk.document_id,
                chunk_id=chunk.id,
                page_num=chunk.page_num,
                heading_path=chunk.heading_path,
                fusion_score=fusion_score,
                rerank_score=float(rerank_score),
            )
        )
    return results
