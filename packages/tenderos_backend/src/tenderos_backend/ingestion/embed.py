import asyncio
from functools import lru_cache

from sentence_transformers import SentenceTransformer

# Locked per Part A2.1 - EMBEDDING_MODEL/EMBEDDING_DIM in .env.example.
# 384-dimensional, matches document_chunks.embedding's VECTOR(384) column.
_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


@lru_cache(maxsize=1)
def _get_model() -> SentenceTransformer:
    # Loaded once per worker process and cached - constructing a
    # SentenceTransformer downloads/loads model weights, which is far too
    # expensive to repeat per document.
    return SentenceTransformer(_MODEL_NAME)


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Runs the (CPU-bound, synchronous) embedding model in a thread so it
    doesn't block the worker's event loop - same asyncio.to_thread pattern as
    db/storage.py's boto3 calls.
    """

    def _encode() -> list[list[float]]:
        vectors = _get_model().encode(texts, convert_to_numpy=True)
        return vectors.tolist()

    return await asyncio.to_thread(_encode)


def count_tokens(text: str) -> int:
    # Tokenization alone (no forward pass) is cheap enough to stay
    # synchronous - chunk.py's build_chunks() calls this per-chunk directly
    # without needing to become async itself.
    return len(_get_model().tokenizer.encode(text, add_special_tokens=False))
