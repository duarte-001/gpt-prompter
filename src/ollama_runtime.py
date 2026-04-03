"""Ensure a local Ollama server is running before chat/embed calls."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

log = logging.getLogger("stock_qa")


def _is_local_ollama(base_url: str) -> bool:
    try:
        host = (urlparse(base_url).hostname or "").lower()
    except Exception:  # noqa: BLE001
        return False
    return host in ("127.0.0.1", "localhost", "::1") or host == ""


def find_ollama_executable() -> Path | None:
    """Resolve ``ollama.exe`` on Windows or ``ollama`` on PATH."""
    which = shutil.which("ollama")
    if which:
        return Path(which)
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        p = Path(local) / "Programs" / "Ollama" / "ollama.exe"
        if p.is_file():
            return p
    pf = os.environ.get("ProgramFiles", "")
    if pf:
        p = Path(pf) / "Ollama" / "ollama.exe"
        if p.is_file():
            return p
    return None


def ollama_reachable(base_url: str, *, timeout_s: float = 2.0) -> bool:
    base = base_url.rstrip("/")
    try:
        r = httpx.get(f"{base}/api/tags", timeout=timeout_s)
        return r.status_code == 200
    except Exception:  # noqa: BLE001
        return False


def ensure_ollama_running(
    base_url: str,
    *,
    max_wait_s: float = 90.0,
    poll_interval_s: float = 0.4,
) -> bool:
    """
    If ``base_url`` points at localhost and nothing is listening, start ``ollama serve``.

    Skips when ``OLLAMA_SKIP_AUTO_START=1`` or when using a remote Ollama URL.
    Returns True if the server is reachable (or already was), False otherwise.
    """
    if os.environ.get("OLLAMA_SKIP_AUTO_START") == "1":
        log.info("[ollama]   OLLAMA_SKIP_AUTO_START=1 — not auto-starting")
        return ollama_reachable(base_url)

    if not _is_local_ollama(base_url):
        log.info("[ollama]   Remote Ollama URL — not auto-starting local server")
        return ollama_reachable(base_url)

    if ollama_reachable(base_url, timeout_s=1.5):
        log.info("[ollama]   Server already up at %s", base_url)
        return True

    exe = find_ollama_executable()
    if not exe:
        log.error("[ollama]   ollama executable not found; install Ollama or add it to PATH")
        return False

    log.info("[ollama]   Starting local server: %s serve", exe)
    popen_kwargs: dict = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        popen_kwargs["start_new_session"] = True

    try:
        subprocess.Popen([str(exe), "serve"], **popen_kwargs)  # noqa: S603
    except Exception as e:  # noqa: BLE001
        log.error("[ollama]   Failed to spawn ollama serve: %s", e)
        return False

    t_wait = time.perf_counter()
    deadline = t_wait + max_wait_s
    while time.perf_counter() < deadline:
        if ollama_reachable(base_url, timeout_s=2.0):
            log.info(
                "[ollama]   Server is ready at %s (%.1fs)",
                base_url,
                time.perf_counter() - t_wait,
            )
            return True
        time.sleep(poll_interval_s)

    log.error("[ollama]   Server did not become ready within %.0fs", max_wait_s)
    return False
