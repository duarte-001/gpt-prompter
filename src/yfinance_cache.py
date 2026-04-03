"""
Disk + in-memory cache for raw yfinance OHLCV history (symbol + period).

Avoids refetching the same series on every chat turn; TTL keeps data reasonably fresh.
When TTL expires, new bars are fetched incrementally (from the day after the last
cached session) and merged with on-disk history, then trimmed to the requested period.
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

# Approximate calendar span for yfinance period strings (trim merged history)
_PERIOD_TRIM_DAYS: dict[str, int] = {
    "1d": 1,
    "5d": 7,
    "1mo": 31,
    "3mo": 93,
    "6mo": 186,
    "1y": 365,
    "2y": 730,
    "5y": 1825,
    "10y": 3650,
}


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


def _trim_to_period_window(df: pd.DataFrame, period: str) -> pd.DataFrame:
    if df.empty:
        return df
    p = period.strip().lower()
    if p == "max":
        return df
    now = pd.Timestamp.now()
    if p == "ytd":
        cutoff = pd.Timestamp(year=now.year, month=1, day=1)
    else:
        days = _PERIOD_TRIM_DAYS.get(p, 730)
        cutoff = now - pd.Timedelta(days=days)
    trimmed = df[df.index >= cutoff]
    return trimmed if not trimmed.empty else df


def _round_numeric_columns(df: pd.DataFrame, decimals: int = 2) -> pd.DataFrame:
    out = df.copy()
    for c in out.columns:
        if pd.api.types.is_float_dtype(out[c]) or pd.api.types.is_integer_dtype(out[c]):
            out[c] = out[c].astype(float).round(decimals)
        elif out[c].dtype == object:
            continue
        else:
            try:
                num = pd.to_numeric(out[c], errors="coerce")
                if num.notna().any():
                    out[c] = num.astype(float).round(decimals)
            except (TypeError, ValueError):
                pass
    return out


def merge_history_frames(
    existing: pd.DataFrame | None,
    incoming: pd.DataFrame,
) -> pd.DataFrame:
    """Concatenate by index; duplicate timestamps keep the last row (newest fetch wins)."""
    if incoming.empty:
        return existing.copy() if existing is not None and not existing.empty else incoming.copy()
    if existing is None or existing.empty:
        return incoming.copy()
    combined = pd.concat([existing, incoming])
    combined = combined[~combined.index.duplicated(keep="last")]
    return combined.sort_index()


def read_stale_history(symbol: str, period: str) -> pd.DataFrame | None:
    """Load cached OHLCV from disk ignoring TTL (for merge / incremental updates)."""
    pkl_path, _ = _paths(symbol, period)
    if not pkl_path.is_file():
        return None
    try:
        df = pd.read_pickle(pkl_path)
        if not isinstance(df, pd.DataFrame):
            return None
        return df.copy()
    except Exception:  # noqa: BLE001
        return None


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


def put_history(symbol: str, period: str, df: pd.DataFrame) -> pd.DataFrame:
    """Merge with any on-disk history, trim to period window, round numerics, persist, return stored frame."""
    ensure_cache_dir()
    key = (symbol, period)
    ts = time.time()
    pkl_path, meta_path = _paths(symbol, period)
    on_disk = read_stale_history(symbol, period)
    merged = merge_history_frames(on_disk, df)
    merged = _trim_to_period_window(merged, period)
    merged = _round_numeric_columns(merged, decimals=2)
    df_to_store = merged.copy()
    df_to_store.to_pickle(pkl_path)
    meta_path.write_text(json.dumps({"ts": ts, "symbol": symbol, "period": period}), encoding="utf-8")
    _memory[key] = (ts, df_to_store)
    return df_to_store
