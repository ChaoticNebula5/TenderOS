from __future__ import annotations

import asyncio
import threading
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Types only; the real import is inside _load_model, for the same reason
    # as ingestion/embed.py's (plan item 23): importing sentence_transformers
    # loads torch. PairInput is CrossEncoder.predict's own element type
    # (sentence-transformers 5.x imports it from this module itself).
    # Annotating with it, rather than letting the list be inferred as
    # list[tuple[str, str]], is what satisfies predict's invariant
    # list[PairInput] parameter without a cast.
    from sentence_transformers import CrossEncoder
    from sentence_transformers.base.modality_types import PairInput

# Flagged in the B6 plan, not locked by the Build Guide the way embeddings
# are (A2's stack table names "a reranking adapter" with no specific
# package) - verified live before committing to it in code (Part P):
# resolves via sentence-transformers, loads, and scores sane pairs. Reuses
# the same dependency embed.py already needs, no new package.
_MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"

# Same reason as embed.py's _model_lock: lru_cache does not hold a second
# caller back while the first is still building the model.
_model_lock = threading.Lock()


@lru_cache(maxsize=1)
def _load_model() -> CrossEncoder:
    # Same "load once per process, cache" reasoning as ingestion/embed.py's
    # _load_model - constructing a CrossEncoder loads model weights.
    from sentence_transformers import CrossEncoder

    return CrossEncoder(_MODEL_NAME)


def _get_model() -> CrossEncoder:
    with _model_lock:
        return _load_model()


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
        # A local variable's annotation is never evaluated at run time, so
        # PairInput needs no runtime import here.
        pairs: list[PairInput] = [(query, candidate) for candidate in candidates]
        scores = _get_model().predict(pairs)
        return scores.tolist()

    return await asyncio.to_thread(_score)
