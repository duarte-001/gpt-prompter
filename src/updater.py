"""
Auto-updater: compares local HEAD against GitHub origin/main and offers
to pull + reinstall deps when behind.

Requires git on PATH. Skipped gracefully when git is unavailable, there
is no network, or the repo is not a git checkout (e.g. PyInstaller bundle).
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

_log = logging.getLogger("stock_qa.updater")

_ROOT = Path(__file__).resolve().parent.parent
_REQUIREMENTS = _ROOT / "requirements.txt"
_BRANCH = "main"


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, capture_output=True, text=True, cwd=str(_ROOT), timeout=30, **kw,
    )


def _git_available() -> bool:
    try:
        _run(["git", "--version"])
        return True
    except FileNotFoundError:
        return False


def _is_git_repo() -> bool:
    return (_ROOT / ".git").is_dir()


def _local_head() -> str | None:
    r = _run(["git", "rev-parse", "HEAD"])
    return r.stdout.strip() if r.returncode == 0 else None


def _fetch_origin() -> bool:
    r = _run(["git", "fetch", "origin", _BRANCH, "--quiet"])
    return r.returncode == 0


def _remote_head() -> str | None:
    r = _run(["git", "rev-parse", f"origin/{_BRANCH}"])
    return r.stdout.strip() if r.returncode == 0 else None


def _commits_behind() -> int:
    r = _run(["git", "rev-list", "--count", f"HEAD..origin/{_BRANCH}"])
    if r.returncode != 0:
        return 0
    try:
        return int(r.stdout.strip())
    except ValueError:
        return 0


def _pull() -> bool:
    r = _run(["git", "pull", "origin", _BRANCH, "--ff-only"])
    return r.returncode == 0


def _pip_install() -> bool:
    if not _REQUIREMENTS.exists():
        return True
    r = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(_REQUIREMENTS), "--quiet"],
        capture_output=True, text=True, cwd=str(_ROOT), timeout=120,
    )
    return r.returncode == 0


def _ask_user(behind: int) -> bool:
    """Prompt via a simple Tk dialog (no console needed)."""
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        answer = messagebox.askyesno(
            "Stock Assistant — Update Available",
            f"A new version is available ({behind} commit{'s' if behind != 1 else ''} behind).\n\n"
            "Do you want to update now?\n"
            "(The app will restart after updating.)",
        )
        root.destroy()
        return answer
    except Exception:
        return False


def check_and_update() -> bool:
    """Return True if the app was updated (caller should restart)."""
    if os.environ.get("STOCK_ASSISTANT_SERVER_MODE", "").strip() == "1":
        _log.info("Server mode: skipping auto-update.")
        return False
    if not _git_available() or not _is_git_repo():
        _log.debug("Skipping update check (no git or not a repo).")
        return False

    _log.info("Checking for updates…")
    if not _fetch_origin():
        _log.warning("Could not reach origin; skipping update.")
        return False

    behind = _commits_behind()
    if behind == 0:
        _log.info("Already up to date.")
        return False

    _log.info("%d new commit(s) on origin/%s.", behind, _BRANCH)
    if not _ask_user(behind):
        _log.info("User declined update.")
        return False

    _log.info("Pulling latest changes…")
    if not _pull():
        _log.error("git pull failed; continuing with current version.")
        return False

    _log.info("Installing dependencies…")
    if not _pip_install():
        _log.warning("pip install had issues; the app may still work.")

    _log.info("Update complete — restarting.")
    return True
