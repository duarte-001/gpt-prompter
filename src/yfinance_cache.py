"""
Disk + in-memory cache for raw yfinance OHLCV history (symbol + period).

Avoids refetching the same series on every chat turn; TTL keeps data reasonably fresh.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import pandas as pd

from src import config

# Second layer: in-process cache for hot paths within one Python process
_memory: dict[tuple[str, str], tuple[float, pd.DataFrame]] = {}


def _safe_filename_part(symbol: str) -> str:
    s = re.sub(r"[^\w\-.]+", "_", symbol)
    return s[:120] or "sym"


def _paths(symbol: str, period: str) -> tuple[Path, Path]:
    base = config.YF_CACHE_DIR
    stem = f"{_safe_filename_part(symbol)}__{period}"
    return base / f"{stem}.pkl", base / f"{stem}.meta.json"


def ensure_cache_dir() -> Path:
    config.YF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return config.YF_CACHE_DIR


def get_history(symbol: str, period: str) -> pd.DataFrame | None:
    """Return cached DataFrame if fresh; else None."""
    key = (symbol, period)
    now = time.time()
    ttl = float(config.YF_CACHE_TTL_SECONDS)

    if key in _memory:
        ts, df = _memory[key]
        if now - ts < ttl:
            return df.copy()

    pkl_path, meta_path = _paths(symbol, period)
    if not pkl_path.is_file() or not meta_path.is_file():
        return None
    try:
        meta: dict[str, Any] = json.loads(meta_path.read_text(encoding="utf-8"))
        ts = float(meta.get("ts", 0))
        if now - ts >= ttl:
            return None
        df = pd.read_pickle(pkl_path)
        _memory[key] = (ts, df)
        return df.copy()
    except Exception:  # noqa: BLE001
        return None


def put_history(symbol: str, period: str, df: pd.DataFrame) -> None:
    """Store a copy of df with current timestamp."""
    ensure_cache_dir()
    key = (symbol, period)
    ts = time.time()
    pkl_path, meta_path = _paths(symbol, period)
    df_to_store = df.copy()
    df_to_store.to_pickle(pkl_path)
    meta_path.write_text(json.dumps({"ts": ts, "symbol": symbol, "period": period}), encoding="utf-8")
    _memory[key] = (ts, df_to_store)
