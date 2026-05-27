"""Spawn the CLI as a subprocess and stream stdout line-by-line.

Streamlit pattern:
    with st.status("Running phase 3…") as status:
        for line in run_phase(["--level","7","--phase","3","--yes"]):
            st.write(line)
        status.update(state="complete")
"""
from __future__ import annotations
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Iterator

from .projects import PIPELINE_DIR, REPO_ROOT


def build_cmd(args: list[str]) -> list[str]:
    """Build the command line. Uses the same python interpreter Streamlit runs under."""
    return [sys.executable, str(PIPELINE_DIR / "shine_it_pipeline.py"), *args]


def stream(args: list[str], env_extra: dict | None = None) -> Iterator[str]:
    """Yield stdout/stderr lines from a pipeline run. Blocks until the process exits.

    The final line is always a sentinel: '__EXIT__ <code>'.
    Callers can show this and update their status widget accordingly.
    """
    cmd = build_cmd(args)
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    proc = subprocess.Popen(
        cmd,
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        text=True,
        env=env,
        start_new_session=True,  # so we can kill the group
    )
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            yield line.rstrip("\n")
    finally:
        proc.wait()
    yield f"__EXIT__ {proc.returncode}"


def run_blocking(args: list[str]) -> tuple[int, str]:
    """Run synchronously, return (returncode, full_output). Use for short commands like --phase 2."""
    cmd = build_cmd(args)
    res = subprocess.run(
        cmd, cwd=str(REPO_ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    return res.returncode, res.stdout


def kill_group(pid: int) -> None:
    """Send SIGTERM to a process group started by `stream`."""
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
