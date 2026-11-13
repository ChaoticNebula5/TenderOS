from __future__ import annotations

import asyncio
import threading
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Types only. Importing sentence_transformers loads torch and
    # transformers, about 5 s and 400 MiB in the containers (measured
    # 2026-11-13, plan item 23), so the real import is inside _load_model:
    # a process pays it the first time it embeds, never for importing this
    # module. The api, the outbox dispatcher, the operations worker and
    # every job that only chunks or counts tokens reach this module and
    # never embed.
    from sentence_transformers import SentenceTransformer
    from tokenizers import Tokenizer

# Locked per Part A2.1 - EMBEDDING_MODEL/EMBEDDING_DIM in .env.example.
# 384-dimensional, matches document_chunks.embedding's VECTOR(384) column.
_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

# lru_cache alone does not hold a second caller back while the first one is
# still inside the function: two threads arriving together would each build
# a model. With the import inside, that window is about 5 s, and the api
# embeds from asyncio.to_thread, so two pricing-finding resolves on a fresh
# api process could load the model twice. The lock makes the second caller
# wait and then take the cached one.
_model_lock = threading.Lock()


@lru_cache(maxsize=1)
def _load_model() -> SentenceTransformer:
    # Loaded once per process and cached - constructing a SentenceTransformer
    # loads model weights, which is far too expensive to repeat per document.
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(_MODEL_NAME)


def _get_model() -> SentenceTransformer:
    with _model_lock:
        return _load_model()


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Runs the (CPU-bound, synchronous) embedding model in a thread so it
    doesn't block the worker's event loop - same asyncio.to_thread pattern as
    db/storage.py's boto3 calls.
    """

    def _encode() -> list[list[float]]:
        vectors = _get_model().encode(texts, convert_to_numpy=True)
        return vectors.tolist()

    return await asyncio.to_thread(_encode)


@lru_cache(maxsize=1)
def _get_tokenizer() -> Tokenizer:
    # The embedding model's own tokenizer, read from the tokenizer.json in the
    # same cached snapshot the model loads from (the images bake it; CI's
    # model cache holds it), through the tokenizers library, which needs no
    # torch. transformers' AutoTokenizer would import torch (measured).
    # try_to_load_from_cache only looks in the local cache, so this never
    # downloads, whatever HF_HUB_OFFLINE says.
    #
    # No lock: a tokenizer loads in about 0.03 s, so two threads loading it
    # at once cost a few milliseconds and one discarded copy.
    from huggingface_hub import try_to_load_from_cache
    from tokenizers import Tokenizer

    path = try_to_load_from_cache(_MODEL_NAME, "tokenizer.json")
    if not isinstance(path, str):
        # None (not cached) or the "known not to exist" marker.
        raise RuntimeError(
            f"{_MODEL_NAME}'s tokenizer.json is not in the local Hugging Face "
            "cache. The images bake the model and the tests run offline; "
            "nothing here downloads it."
        )
    tokenizer = Tokenizer.from_file(path)
    # The file carries the model's inference settings: pad and truncate to
    # 128 tokens. Without these two calls every text counts as 128 (measured),
    # and a long text would be cut short. count_tokens needs the full length.
    tokenizer.no_padding()
    tokenizer.no_truncation()
    return tokenizer


def count_tokens(text: str) -> int:
    # Tokenization alone (no forward pass) is cheap enough to stay
    # synchronous - chunk.py's build_chunks() calls this per-chunk directly
    # without needing to become async itself. Equal to
    # SentenceTransformer(_MODEL_NAME).tokenizer.encode(text,
    # add_special_tokens=False)'s length, which it replaced (plan item 23;
    # tests/unit/test_lazy_model_imports.py checks both).
    return len(_get_tokenizer().encode(text, add_special_tokens=False).ids)
