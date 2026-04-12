"""FRED (St. Louis Fed) API: series metadata + latest observations with disk cache."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from src import config

log = logging.getLogger("stock_qa")

_FRED_BASE = "https://api.stlouisfed.org/fred"


def _cache_path(series_id: str, suffix: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in series_id)
    h = hashlib.sha256(suffix.encode()).hexdigest()[:16]
    return config.FRED_CACHE_DIR / f"{safe}_{h}.json"


def _cache_read(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    age = time.time() - path.stat().st_mtime
    if age > config.FRED_CACHE_TTL_SECONDS:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _cache_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _fred_get(
    client: httpx.Client,
    path: str,
    params: dict[str, Any],
    api_key: str,
) -> dict[str, Any]:
    q = {**params, "api_key": api_key, "file_type": "json"}
    r = client.get(f"{_FRED_BASE}/{path}", params=q, timeout=30.0)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict):
        raise ValueError("FRED response is not a JSON object")
    return data


def fetch_series_meta(
    client: httpx.Client,
    series_id: str,
    api_key: str,
) -> dict[str, Any]:
    cache_key = f"meta|{series_id}"
    cpath = _cache_path(series_id, cache_key)
    hit = _cache_read(cpath)
    if hit is not None:
        return hit
    raw = _fred_get(client, "series", {"series_id": series_id}, api_key)
    ser = raw.get("seriess")
    if not isinstance(ser, list) or not ser:
        raise ValueError(f"No series metadata for {series_id!r}")
    meta = ser[0]
    if not isinstance(meta, dict):
        raise ValueError("Unexpected FRED series shape")
    _cache_write(cpath, meta)
    return meta


def fetch_series_latest(
    client: httpx.Client,
    series_id: str,
    api_key: str,
    *,
    recent_n: int = 3,
) -> dict[str, Any]:
    """
    Return compact dict: id, title, units, frequency, last_date, value, recent (last N obs).
    On error, return {"id": series_id, "error": "..."}.
    """
    cache_key = f"obs|{series_id}|n={recent_n}"
    cpath = _cache_path(series_id, cache_key)
    hit = _cache_read(cpath)
    if hit is not None:
        return hit

    try:
        meta = fetch_series_meta(client, series_id, api_key)
        title = str(meta.get("title", series_id))
        units = str(meta.get("units", ""))
        freq = str(meta.get("frequency", ""))

        obs_raw = _fred_get(
            client,
            "series/observations",
            {
                "series_id": series_id,
                "sort_order": "desc",
                "limit": recent_n,
            },
            api_key,
        )
        obs = obs_raw.get("observations")
        if not isinstance(obs, list) or not obs:
            out = {
                "id": series_id,
                "title": title,
                "units": units,
                "frequency": freq,
                "error": "no observations",
            }
            _cache_write(cpath, out)
            return out

        def parse_row(row: dict[str, Any]) -> dict[str, Any] | None:
            d = row.get("date")
            v = row.get("value")
            if not isinstance(d, str):
                return None
            if v in (".", None):
                return None
            try:
                fv = float(v)
            except (TypeError, ValueError):
                return None
            return {"date": d, "value": round(fv, 6) if abs(fv) < 1e6 else float(v)}

        recent: list[dict[str, Any]] = []
        for row in obs:
            if not isinstance(row, dict):
                continue
            pr = parse_row(row)
            if pr:
                recent.append(pr)
        if not recent:
            out = {
                "id": series_id,
                "title": title,
                "units": units,
                "frequency": freq,
                "error": "all recent values missing",
            }
            _cache_write(cpath, out)
            return out

        last = recent[0]
        out = {
            "id": series_id,
            "title": title,
            "units": units,
            "frequency": freq,
            "last_date": last["date"],
            "value": last["value"],
            "recent": recent,
        }
        _cache_write(cpath, out)
        return out
    except Exception as e:  # noqa: BLE001
        log.warning("[fred]     %s: %s", series_id, e)
        return {"id": series_id, "error": str(e)}


def build_economic_context(
    series_ids: list[str],
    api_key: str,
    *,
    recent_n: int = 3,
) -> dict[str, Any] | None:
    if not series_ids or not api_key.strip():
        return None
    series_out: list[dict[str, Any]] = []
    t0 = time.perf_counter()
    with httpx.Client() as client:
        for sid in series_ids:
            series_out.append(fetch_series_latest(client, sid, api_key, recent_n=recent_n))
    elapsed = round(time.perf_counter() - t0, 3)
    return {
        "source": "FRED",
        "fetched_in_seconds": elapsed,
        "series": series_out,
    }
