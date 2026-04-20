"""
Optional update prompt for PyInstaller builds: fetch a small JSON manifest over HTTPS,
compare ``version`` to ``APP_VERSION`` (from bundled ``VERSION`` file), offer to open ``download_url``.

Manifest URL: ``STOCK_ASSISTANT_UPDATE_MANIFEST_URL`` or the default GitHub raw URL.
Disable entirely: ``STOCK_ASSISTANT_DISABLE_UPDATE_CHECK=1``.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import webbrowser
from pathlib import Path
from typing import Any

from src.app_version import APP_VERSION

_log = logging.getLogger("stock_qa.frozen_update")

_DEFAULT_MANIFEST_URL = (
    "https://raw.githubusercontent.com/duarte-001/gpt-prompter/main/update/manifest.json"
)


def _state_path() -> Path:
    la = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    d = Path(la) / "StockAssistant"
    d.mkdir(parents=True, exist_ok=True)
    return d / "update_notify.json"


def _parse_version_tuple(s: str) -> tuple[int, ...]:
    """Parse ``1.2.3`` / ``v1.2`` into a tuple for lexicographic compare."""
    s = (s or "").strip().lower().lstrip("v")
    parts: list[int] = []
    for chunk in s.split("."):
        num = "".join(c for c in chunk if c.isdigit())
        if num:
            parts.append(int(num))
    return tuple(parts) if parts else (0,)


def _load_state() -> dict[str, Any]:
    p = _state_path()
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(data: dict[str, Any]) -> None:
    try:
        _state_path().write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


def _fetch_manifest(url: str) -> dict[str, Any] | None:
    try:
        import httpx

        r = httpx.get(url, timeout=8.0, follow_redirects=True)
        if r.status_code != 200:
            _log.warning("Update manifest HTTP %s", r.status_code)
            return None
        data = r.json()
        return data if isinstance(data, dict) else None
    except Exception as e:  # noqa: BLE001
        _log.debug("Manifest fetch failed: %s", e)
        return None


def _ask_open_download(latest: str, download_url: str, notes_url: str) -> bool:
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        extra = ""
        if notes_url and notes_url != download_url:
            extra = f"\n\nRelease notes: {notes_url}"
        msg = (
            "A newer Stock Assistant build may be available.\n\n"
            "Open the download page in your browser?"
            f"{extra}"
        )
        ok = messagebox.askyesno("Stock Assistant — Update", msg)
        root.destroy()
        return bool(ok)
    except Exception as e:  # noqa: BLE001
        _log.debug("Tk update dialog failed: %s", e)
        return False


def maybe_prompt_frozen_update() -> None:
    """If frozen, fetch manifest and optionally open the download URL."""
    if not getattr(sys, "frozen", False):
        return
    if os.environ.get("STOCK_ASSISTANT_DISABLE_UPDATE_CHECK", "").strip() == "1":
        _log.debug("Update check disabled by env.")
        return

    url = (os.environ.get("STOCK_ASSISTANT_UPDATE_MANIFEST_URL") or "").strip() or _DEFAULT_MANIFEST_URL

    manifest = _fetch_manifest(url)
    if not manifest:
        return

    latest = str(manifest.get("version") or "").strip()
    download_url = str(manifest.get("download_url") or "").strip()
    notes_url = str(manifest.get("notes_url") or "").strip()

    if not latest or not download_url:
        _log.warning("Manifest missing version or download_url.")
        return

    if _parse_version_tuple(latest) <= _parse_version_tuple(APP_VERSION):
        _log.info("Up to date (%s >= %s).", APP_VERSION, latest)
        return

    state = _load_state()
    if state.get("dismissed_version") == latest:
        _log.info("User previously dismissed notify for %s.", latest)
        return

    _log.info("Update available: build %s < manifest %s", APP_VERSION, latest)
    if _ask_open_download(latest, download_url, notes_url):
        try:
            webbrowser.open(download_url)
        except Exception as e:  # noqa: BLE001
            _log.warning("Could not open browser: %s", e)
    else:
        _save_state({**state, "dismissed_version": latest})
