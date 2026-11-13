"""Phase 4 Stage 2: the operations worker imports its job module before it
forks jobs (tenderos_worker.worker.preload_operations_module).

A process that forks while other threads run can hang in the child, so that
import must start no OS thread. numpy's bundled OpenBLAS starts a thread pool
when it loads, unless OPENBLAS_NUM_THREADS and OMP_NUM_THREADS are 1, which
the operations_worker service sets. Before plan item 23 scipy's did too;
since then the module no longer imports torch, transformers or scipy, and
test_the_preload_refuses_to_start_when_the_pin_is_missing rests on numpy's
pool alone, reached through openpyxl (openpyxl/compat/numbers.py; observed
2026-11-13: unpinned, the import still goes from 1 to 4 OS threads). These
tests run in fresh subprocesses, so a future import-time thread or client
fails here instead of quietly breaking forked jobs.

Linux only: OS threads are counted through /proc, and Linux is where the
workers run, where RQ forks and where CI runs these tests. (On Windows a
fresh interpreter's own loader threads come and go, and there is no fork.)
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="counts OS threads through /proc (Linux)"
)

_PIN_VARIABLES = ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS")
_PINNED = {name: "1" for name in _PIN_VARIABLES}

_COUNT_THREADS = """
import sys
def threads():
    for line in open("/proc/self/status"):
        if line.startswith("Threads:"):
            return int(line.split()[1])
if sys.argv[1] == "import":
    import tenderos_worker.jobs.operations
print(threads())
"""

# argv[1], when given, replaces the module the preload imports.
_PRELOAD = """
import logging, sys
logging.basicConfig(stream=sys.stderr, level=logging.INFO)
from tenderos_worker import worker
if len(sys.argv) > 1:
    worker._OPERATIONS_JOB_MODULE = sys.argv[1]
worker.preload_operations_module()
print("preloaded")
"""

# A stand-in for a future dependency that starts a thread when imported.
_THREAD_AT_IMPORT = """
import threading
import time

threading.Thread(target=time.sleep, args=(60,), daemon=True).start()
"""


def _env(overrides: dict[str, str], extra_path: Path | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _PIN_VARIABLES}
    env.update(overrides)
    if extra_path is not None:
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(extra_path), env.get("PYTHONPATH")]))
    return env


def _run(code: str, *args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code, *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _assert_refused(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 1, result.stderr
    assert "preloaded" not in result.stdout
    assert "ERROR" in result.stderr
    assert "refusing to start" in result.stderr
    assert "OPENBLAS_NUM_THREADS and OMP_NUM_THREADS" in result.stderr


def test_importing_the_operations_job_module_starts_no_os_thread():
    bare = _run(_COUNT_THREADS, "bare", env=_env(_PINNED))
    imported = _run(_COUNT_THREADS, "import", env=_env(_PINNED))

    assert bare.returncode == 0, bare.stderr
    assert imported.returncode == 0, imported.stderr
    assert int(imported.stdout.split()[-1]) == int(bare.stdout.split()[-1])


def test_the_preload_succeeds_with_blas_pinned_to_one_thread():
    result = _run(_PRELOAD, env=_env(_PINNED))

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("preloaded")
    assert "OS threads 1 -> 1" in result.stderr


def test_the_preload_refuses_to_start_when_an_import_starts_a_thread(tmp_path):
    """Independent of the machine's CPU count: the imported module starts a
    thread itself, the way a future dependency could."""
    (tmp_path / "thread_at_import.py").write_text(_THREAD_AT_IMPORT)

    result = _run(_PRELOAD, "thread_at_import", env=_env(_PINNED, extra_path=tmp_path))

    _assert_refused(result)
    assert "OS threads 1 -> 2" in result.stderr


@pytest.mark.skipif(
    len(os.sched_getaffinity(0)) < 2 if hasattr(os, "sched_getaffinity") else True,
    reason=(
        "OpenBLAS starts no pool when the process can use only one CPU, even with "
        "OPENBLAS_NUM_THREADS=4 set (observed 2026-10-16 with taskset -c 0)"
    ),
)
def test_the_preload_refuses_to_start_when_the_pin_is_missing():
    # OPENBLAS_NUM_THREADS set explicitly above 1, not left to the default;
    # OMP_NUM_THREADS unset, as when the pin is missing from compose.
    result = _run(_PRELOAD, env=_env({"OPENBLAS_NUM_THREADS": "4"}))

    _assert_refused(result)
    assert "OPENBLAS_NUM_THREADS=4" in result.stderr
    assert "OMP_NUM_THREADS=<unset>" in result.stderr
