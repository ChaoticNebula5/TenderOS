"""PR 30: no progress bar on the workers' stderr, so every line there is a
JSON log line. sentence-transformers' "Batches" bar is turned off at its two
calls (ingestion/embed.py, agents/knowledge/rerank.py); huggingface_hub's and
transformers' bars ("Loading weights") by configure_logging(), through
huggingface_hub's own variable, without importing either library.

The startup tests run in fresh processes: this test process has already been
through configure_logging() (tenderos_api.main does it at import), and a
library's setting is fixed when it is imported."""

import asyncio
import os
import subprocess
import sys

import pytest
from tenderos_backend.agents.knowledge import rerank
from tenderos_backend.core.log_redaction import configure_logging
from tenderos_backend.ingestion import embed

_VARIABLE = "HF_HUB_DISABLE_PROGRESS_BARS"


class _Vectors:
    def tolist(self) -> list:
        return []


class _RecordingModel:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def encode(self, texts, **kwargs):
        self.calls.append(kwargs)
        return _Vectors()

    def predict(self, pairs, **kwargs):
        self.calls.append(kwargs)
        return _Vectors()


def test_embedding_draws_no_progress_bar(monkeypatch):
    model = _RecordingModel()
    monkeypatch.setattr(embed, "_get_model", lambda: model)
    asyncio.run(embed.embed_texts(["a requirement"]))
    assert [call.get("show_progress_bar") for call in model.calls] == [False]


def test_reranking_draws_no_progress_bar(monkeypatch):
    model = _RecordingModel()
    monkeypatch.setattr(rerank, "_get_model", lambda: model)
    asyncio.run(rerank.rerank("a query", ["a candidate"]))
    assert [call.get("show_progress_bar") for call in model.calls] == [False]


def test_configure_logging_turns_the_libraries_bars_off_by_default(monkeypatch):
    monkeypatch.delenv(_VARIABLE, raising=False)
    configure_logging("json")
    assert os.environ[_VARIABLE] == "1"


def test_an_operators_own_setting_is_kept(monkeypatch):
    monkeypatch.setenv(_VARIABLE, "0")
    configure_logging("json")
    assert os.environ[_VARIABLE] == "0"


_REPORT = (
    "import huggingface_hub.utils as hub, transformers.utils.logging as tl; "
    "print('hub off', hub.are_progress_bars_disabled()); "
    "print('transformers on', tl.is_progress_bar_enabled())"
)


def _fresh_process(code: str, tmp_path) -> str:
    env = {k: v for k, v in os.environ.items() if k != _VARIABLE}
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    return result.stdout


def test_a_worker_process_starts_with_both_libraries_bars_off(tmp_path):
    out = _fresh_process(
        "import sys; from tenderos_backend.core.log_redaction import configure_logging; "
        "configure_logging('json'); "
        "print('imported at startup', 'huggingface_hub' in sys.modules, "
        "'transformers' in sys.modules); " + _REPORT,
        tmp_path,
    )
    assert "imported at startup False False" in out
    assert "hub off True" in out
    assert "transformers on False" in out


def test_without_it_transformers_would_draw_its_bars(tmp_path):
    """The control: the same process without configure_logging(), so the
    test above can tell the two apart."""
    out = _fresh_process(_REPORT, tmp_path)
    assert "transformers on True" in out


def test_a_library_imported_before_it_is_turned_off_too(tmp_path):
    out = _fresh_process(
        "import transformers.utils.logging; "
        "from tenderos_backend.core.log_redaction import configure_logging; "
        "configure_logging('json'); " + _REPORT,
        tmp_path,
    )
    assert "hub off True" in out
    assert "transformers on False" in out


@pytest.fixture(autouse=True)
def _restore_variable():
    saved = os.environ.get(_VARIABLE)
    yield
    if saved is None:
        os.environ.pop(_VARIABLE, None)
    else:
        os.environ[_VARIABLE] = saved
