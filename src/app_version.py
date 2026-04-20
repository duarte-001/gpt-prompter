"""Shipped app version: read from ``VERSION`` at repo root (dev) or bundle root (frozen)."""

from __future__ import annotations

import sys
from pathlib import Path


def _root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent


def _read_version() -> str:
    p = _root() / "VERSION"
    if p.is_file():
        line = p.read_text(encoding="utf-8").strip().splitlines()
        if line:
            return line[0].strip()
    return "0.0.0"


APP_VERSION = _read_version()
