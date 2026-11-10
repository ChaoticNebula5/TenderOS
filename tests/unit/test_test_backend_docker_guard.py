"""scripts/test-backend.ps1 refuses to run when it cannot ask Docker.

The script's "app containers must be stopped" guard once read `docker compose
ps` without its exit code: with the engine unreachable, docker printed an
error to stderr and nothing to stdout, the guard saw no running containers,
and the run went on (found 2026-11-10, Docker Desktop not running after a
restart; the Postgres probe after it is what stopped the run).

Each case runs the real script under Windows PowerShell with a fake `docker`
first on PATH, and an empty Hugging Face cache, so a run that gets past the
Docker guard stops at the model-cache guard right after it: never at Postgres,
never in a nested pytest. The script is Windows-only, so these skip elsewhere
(CI runs pytest directly on Linux).
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "test-backend.ps1"
POWERSHELL = shutil.which("powershell")

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or POWERSHELL is None,
    reason="test-backend.ps1 is a Windows PowerShell script",
)

DOCKER_GUARD = "could not ask Docker which containers are running"
NO_DOCKER = "the docker command is not on PATH"
RUNNING = "stack app containers are running"
# The guard right after Docker's: reaching it means the Docker guard passed.
NEXT_GUARD = "is not in the Hugging Face cache"


def _run(tmp_path: Path, docker_cmd: str | None, path_dirs: list[str] | None = None) -> str:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    if docker_cmd is not None:
        (bin_dir / "docker.cmd").write_text(docker_cmd, encoding="ascii")
    cache = tmp_path / "hf-cache"
    cache.mkdir()
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([str(bin_dir), *(path_dirs or [os.environ["PATH"]])])
    env["HF_HUB_CACHE"] = str(cache)
    result = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
        ],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    out = result.stdout + result.stderr
    assert result.returncode != 0, out  # every case here must stop before pytest
    return " ".join(out.split())  # PowerShell wraps long messages; compare unwrapped


def test_an_unreachable_engine_refuses_with_dockers_own_error(tmp_path):
    out = _run(
        tmp_path,
        "@echo off\r\n"
        "echo failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine 1>&2\r\n"
        "exit /b 1\r\n",
    )
    assert DOCKER_GUARD in out
    assert "exited 1" in out
    assert "failed to connect to the docker API" in out
    assert NEXT_GUARD not in out


def test_a_running_app_container_still_refuses(tmp_path):
    out = _run(tmp_path, "@echo off\r\necho postgres\r\necho api\r\nexit /b 0\r\n")
    assert RUNNING in out
    assert "(api)" in out
    assert NEXT_GUARD not in out


def test_no_app_containers_running_passes_the_docker_guard(tmp_path):
    # Data services may run; only the app containers block a run.
    out = _run(tmp_path, "@echo off\r\necho postgres\r\necho redis\r\nexit /b 0\r\n")
    assert NEXT_GUARD in out
    assert DOCKER_GUARD not in out
    assert RUNNING not in out


def test_stderr_with_exit_0_is_never_read_as_a_container(tmp_path):
    # The guard reads docker's stderr too (2>&1, for its error message), so
    # only stdout lines may name containers. compose does warn on stderr and
    # still succeed (an unset variable, say); the bare "api" line is
    # deliberately the worst case, a stderr line equal to a service name.
    out = _run(
        tmp_path,
        # Redirect first: in `echo api 1>&2`, cmd echoes the space before 1>&2.
        "@echo off\r\n1>&2 echo WARN a variable is not set\r\n1>&2 echo api\r\nexit /b 0\r\n",
    )
    assert NEXT_GUARD in out
    assert RUNNING not in out


def test_no_docker_command_refuses(tmp_path):
    # Only Windows' own directories on PATH: no docker anywhere.
    system32 = str(Path(os.environ["SystemRoot"]) / "System32")
    out = _run(tmp_path, None, path_dirs=[system32, str(Path(POWERSHELL).parent)])
    assert NO_DOCKER in out
    assert NEXT_GUARD not in out
