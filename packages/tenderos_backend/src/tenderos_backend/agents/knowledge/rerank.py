import asyncio
from functools import lru_cache

from sentence_transformers import CrossEncoder

# Flagged in the B6 plan, not locked by the Build Guide the way embeddings
# are (A2's stack table names "a reranking adapter" with no specific
# package) - verified live before committing to it in code (Part P):
# resolves via sentence-transformers, loads, and scores sane pairs. Reuses
# the same dependency embed.py already needs, no new package.
_MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"


@lru_cache(maxsize=1)
def _get_model() -> CrossEncoder:
    # Same "load once per process, cache" reasoning as ingestion/embed.py's
    # _get_model - constructing a CrossEncoder loads model weights.
    return CrossEncoder(_MODEL_NAME)


async def rerank(query: str, candidates: list[str]) -> list[float]:
    """Cross-encoder relevance score for (query, candidate) per candidate,
    same order as the input list - CPU-bound, so run in a thread per
    embed_texts's asyncio.to_thread pattern rather than blocking the event
    loop. Higher score = more relevant; scores are not bounded to [0,1]
    (this is a raw cross-encoder logit, not a probability) - callers sort by
    score, they don't threshold against an absolute value.
    """
    if not candidates:
        return []

    def _score() -> list[float]:
        pairs = [(query, candidate) for candidate in candidates]
        scores = _get_model().predict(pairs)
        return scores.tolist()

    return await asyncio.to_thread(_score)
