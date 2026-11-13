"""Plan item 23: torch loads only where a model is used.

Importing sentence_transformers loads torch and transformers, about 5 s and
400 MiB in the containers (measured 2026-11-13). ingestion/embed.py and
agents/knowledge/rerank.py now import it inside the function that builds the
model, and count_tokens uses the model's tokenizer.json through the
tokenizers library, which needs no torch. These tests check, each in a fresh
interpreter (this pytest process has torch loaded by other tests already):

- importing the api app, the outbox dispatcher, the worker and every job
  module leaves torch, transformers and sentence_transformers unloaded;
- count_tokens, offline, gives the model tokenizer's own count on a fixed set
  of texts (edge cases plus the synthetic tender's blocks and chunks), and
  loads no torch while doing it.

And, in this process: two threads asking for a model at once build it once.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import types
from functools import cache
from pathlib import Path

import pytest
from tenderos_backend.agents.knowledge import rerank
from tenderos_backend.ingestion import embed
from tenderos_backend.ingestion.chunk import build_chunks
from tenderos_backend.ingestion.parse_docx import parse_docx
from tenderos_backend.ingestion.parse_pdf import parse_pdf
from tenderos_backend.ingestion.parse_xlsx import parse_xlsx

_HEAVY = ("torch", "transformers", "sentence_transformers")

_SYNTHETIC_TENDER = Path(__file__).resolve().parents[2] / "data" / "synthetic" / "tender_a"

# The job modules the dispatcher hands to RQ today. The subprocess finds the
# modules itself, so a new job module is covered without editing this list;
# the list only stops the test passing when it finds none.
_KNOWN_JOB_MODULES = {
    "ingestion",
    "knowledge_import",
    "operations",
    "placeholder",
    "reanalysis",
    "verification",
    "workflow",
}

# Prints, after each import, which of the heavy modules are loaded, so a
# failure names the first entry point that pulled one in.
_IMPORT_ENTRY_POINTS = """
import importlib, json, pkgutil, sys
heavy = json.loads(sys.argv[1])
import tenderos_worker.jobs as jobs
names = ["tenderos_api.main", "tenderos_worker.outbox_dispatcher", "tenderos_worker.worker"]
names += sorted("tenderos_worker.jobs." + m.name for m in pkgutil.iter_modules(jobs.__path__))
report = []
for name in names:
    importlib.import_module(name)
    report.append([name, sorted(h for h in heavy if h in sys.modules)])
print(json.dumps(report))
"""

_COUNT_TOKENS = """
import json, sys
heavy = json.loads(sys.argv[1])
from tenderos_backend.ingestion.embed import _get_tokenizer, count_tokens
counts = [count_tokens(t) for t in json.load(sys.stdin)]
tokenizer = _get_tokenizer()
print(json.dumps({
    "counts": counts,
    "padding": tokenizer.padding,
    "truncation": tokenizer.truncation,
    "heavy": sorted(h for h in heavy if h in sys.modules),
}))
"""


def _run(code: str, *, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    # The model files are read from the local Hugging Face cache, never
    # downloaded: HF_HUB_OFFLINE=1 here even if the caller's environment
    # lacks it (scripts/test-backend.ps1 and CI set it too).
    env = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
    return subprocess.run(
        [sys.executable, "-c", code, json.dumps(_HEAVY)],
        input=stdin,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _edge_cases() -> list[str]:
    # WordPiece gives up on a word longer than 100 characters (it becomes
    # one [UNK]), hence the 99, 100 and 101 character words.
    return [
        "",
        "   \n\t  ",
        " ".join(["tender"] * 3000),
        "Café naïve façade: 価格表 ✅ 🚧 — résumé ñ ü ß",
        "line one\r\nline two\r\n\tindented\ttabbed",
        "x" * 3000,
        "a" * 99,
        "b" * 100,
        "c" * 101,
        "The supplier SHALL provide ISO 27001:2022 certification (mandatory).",
    ]


@cache
def _fixture_texts() -> tuple[str, ...]:
    # Every block the app's own parsers extract from the synthetic tender, and
    # every chunk built from them: the texts count_tokens sees in production.
    # The files are generated (scripts/synthetic_tender_a.py; CI runs it
    # before the tests), so missing files fail rather than skip.
    parsers = {
        ".docx": parse_docx,
        ".xlsx": parse_xlsx,
        ".pdf": lambda content: parse_pdf(content)[0],
    }
    texts = _edge_cases()
    files = sorted(p for p in _SYNTHETIC_TENDER.iterdir() if p.suffix in parsers)
    assert len(files) >= 10, f"synthetic tender not generated in {_SYNTHETIC_TENDER}"
    for path in files:
        blocks = parsers[path.suffix](path.read_bytes())
        texts += [b.text for b in blocks]
        texts += [c.chunk_text for c in build_chunks(blocks)]
    return tuple(texts)


def test_no_entry_point_imports_torch_or_sentence_transformers():
    result = _run(_IMPORT_ENTRY_POINTS)

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    imported = {name for name, _ in report}
    assert {f"tenderos_worker.jobs.{m}" for m in _KNOWN_JOB_MODULES} <= imported
    assert {"tenderos_api.main", "tenderos_worker.outbox_dispatcher"} <= imported
    loaded = [(name, heavy) for name, heavy in report if heavy]
    assert not loaded, f"first import to load {loaded[0][1]}: {loaded[0][0]}"


def test_count_tokens_matches_the_model_tokenizer_offline_and_without_torch():
    texts = list(_fixture_texts())
    # The reference: the tokenizer that count_tokens used before item 23, the
    # one sentence-transformers loads with the model (this process already
    # has torch; the counts under test come from a fresh one).
    model_tokenizer = embed._get_model().tokenizer
    expected = [len(model_tokenizer.encode(t, add_special_tokens=False)) for t in texts]

    result = _run(_COUNT_TOKENS, stdin=json.dumps(texts))

    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    # The tokenizer.json's own settings pad and truncate to 128 tokens.
    assert out["padding"] is None
    assert out["truncation"] is None
    mismatches = [
        (t[:60], want, got)
        for t, want, got in zip(texts, expected, out["counts"], strict=True)
        if want != got
    ]
    assert not mismatches, f"{len(mismatches)} of {len(texts)} differ, e.g. {mismatches[:3]}"
    # The set must reach past 128 tokens in both directions, or padding and
    # truncation could not show.
    assert min(expected) < 128 < max(expected)
    assert out["heavy"] == []


def _fake_library(class_name: str, built: list[object]) -> types.ModuleType:
    # Stands in for sentence_transformers: a constructor slow enough that two
    # threads are both waiting for it without the lock.
    class _SlowModel:
        def __init__(self, name: str) -> None:
            time.sleep(0.3)
            built.append(self)

    module = types.ModuleType("sentence_transformers")
    setattr(module, class_name, _SlowModel)
    return module


@pytest.mark.parametrize(
    ("module", "class_name"),
    [(embed, "SentenceTransformer"), (rerank, "CrossEncoder")],
    ids=["embedder", "reranker"],
)
def test_two_threads_asking_at_once_build_one_model(monkeypatch, module, class_name):
    built: list[object] = []
    monkeypatch.setitem(sys.modules, "sentence_transformers", _fake_library(class_name, built))
    module._load_model.cache_clear()
    start = threading.Barrier(2)
    got: list[object] = []

    def ask() -> None:
        start.wait()
        got.append(module._get_model())

    try:
        threads = [threading.Thread(target=ask) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
    finally:
        # The fake must not outlive this test: the next caller loads the
        # real model again.
        module._load_model.cache_clear()

    assert len(built) == 1
    assert len(got) == 2
    assert got[0] is got[1] is built[0]
