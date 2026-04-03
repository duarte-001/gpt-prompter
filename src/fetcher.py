"""
Fetch OHLCV from yfinance and attach computed metrics.

Persistence: optional yfinance OHLCV cache under data/yfinance_cache/ (see
config). After TTL expiry, new sessions are fetched incrementally from the day
after the last cached bar, merged with on-disk history, trimmed to the
requested period, and rounded to two decimal places before pickle export.
JSON/CSV exports of summaries remain explicit (CLI, Streamlit, data/exports/).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import yfinance as yf

import logging
import time

from src import config
from src.metrics import enrich_ohlcv, latest_snapshot_row
from src.yfinance_cache import get_history, put_history, read_stale_history

log = logging.getLogger("stock_qa")

# yfinance appends these; metrics spec uses OHLCV only for features
_DROP_SESSION_KEYS = frozenset({"Dividends", "Stock Splits", "Capital Gains"})


@dataclass
class FetchResult:
    symbol: str
    label: str
    description: str
    error: Optional[str]
    frame: Optional[pd.DataFrame]
    summary: Dict[str, Any]


def _fetch_ticker_history_from_network(
    symbol: str,
    period: str = config.DEFAULT_YF_PERIOD,
) -> pd.DataFrame:
    t = yf.Ticker(symbol)
    df = t.history(period=period, auto_adjust=False)
    if df.empty:
        return df
    df = df.rename(
        columns={
            "Open": "Open",
            "High": "High",
            "Low": "Low",
            "Close": "Close",
            "Adj Close": "Adj Close",
            "Volume": "Volume",
        }
    )
    # yfinance uses timezone-aware index sometimes
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df


def _fetch_incremental_network(symbol: str, stale: pd.DataFrame) -> pd.DataFrame:
    """Fetch sessions strictly after the last cached date (smaller download than full period)."""
    if stale.empty:
        return pd.DataFrame()
    last = stale.index[-1]
    start = last + pd.Timedelta(days=1)
    t = yf.Ticker(symbol)
    df = t.history(start=start, auto_adjust=False)
    if df.empty:
        return df
    df = df.rename(
        columns={
            "Open": "Open",
            "High": "High",
            "Low": "Low",
            "Close": "Close",
            "Adj Close": "Adj Close",
            "Volume": "Volume",
        }
    )
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df


def fetch_ticker_history(
    symbol: str,
    period: str = config.DEFAULT_YF_PERIOD,
) -> pd.DataFrame:
    """OHLCV history with disk/memory TTL cache."""
    cached = get_history(symbol, period)
    if cached is not None:
        log.info("[fetch]    %s: cache hit (%d rows)", symbol, len(cached))
        return cached
    t0 = time.perf_counter()
    stale = read_stale_history(symbol, period)
    df: pd.DataFrame
    if stale is not None and not stale.empty:
        try:
            inc = _fetch_incremental_network(symbol, stale)
            if inc.empty:
                df = stale
                log.info(
                    "[fetch]    %s: incremental fetch empty, reusing %d cached rows",
                    symbol,
                    len(stale),
                )
            else:
                df = inc
                log.info(
                    "[fetch]    %s: incremental fetch +%d new session(s) (merge on save)",
                    symbol,
                    len(inc),
                )
        except Exception as e:  # noqa: BLE001
            log.warning(
                "[fetch]    %s: incremental fetch failed (%s) — full period download",
                symbol,
                e,
            )
            df = _fetch_ticker_history_from_network(symbol, period)
    else:
        df = _fetch_ticker_history_from_network(symbol, period)
    elapsed = time.perf_counter() - t0
    if not df.empty:
        df = put_history(symbol, period, df)
        log.info(
            "[fetch]    %s: stored → %d rows (%.1fs)",
            symbol,
            len(df),
            elapsed,
        )
    else:
        log.warning("[fetch]    %s: empty history (%.1fs)", symbol, elapsed)
    return df


def _clean_scalar(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, (float, np.floating)) and pd.isna(val):
        return None
    if hasattr(val, "item"):
        try:
            val = val.item()
        except Exception:
            pass
    if isinstance(val, (float, np.floating)):
        if pd.isna(val):
            return None
        return round(float(val), 2)
    if isinstance(val, (pd.Timestamp,)):
        return val.isoformat()
    return val


def build_summary(symbol: str, df: pd.DataFrame) -> Dict[str, Any]:
    if df.empty:
        return {"symbol": symbol, "rows": 0}
    last = latest_snapshot_row(df)
    last_date = df.index[-1]
    if hasattr(last_date, "isoformat"):
        last_date_s = last_date.isoformat()
    else:
        last_date_s = str(last_date)

    smart_days = int(df["smart_money_day"].fillna(False).sum()) if "smart_money_day" in df.columns else 0
    fomo_days = int(df["retail_fomo"].fillna(False).sum()) if "retail_fomo" in df.columns else 0

    snap = {
        str(k): _clean_scalar(last[k])
        for k in last.index
        if str(k) not in _DROP_SESSION_KEYS
    }
    return {
        "symbol": symbol,
        "rows": len(df),
        "last_date": last_date_s,
        "session": snap,
        "period_counts": {
            "smart_money_days": smart_days,
            "retail_fomo_days": fomo_days,
        },
    }


def fetch_all_tickers(
    ticker_map: Dict[str, tuple[str, str]],
    period: str = config.DEFAULT_YF_PERIOD,
) -> List[FetchResult]:
    log.info("[fetch]    Fetching %d symbol(s) (period=%s)…", len(ticker_map), period)
    t_all = time.perf_counter()
    results: List[FetchResult] = []
    errors = 0
    for yahoo_sym, (label, desc) in ticker_map.items():
        err: Optional[str] = None
        raw: Optional[pd.DataFrame] = None
        try:
            hist = fetch_ticker_history(yahoo_sym, period=period)
            if hist.empty:
                err = "empty history"
                raw = None
            else:
                raw = enrich_ohlcv(
                    hist,
                    momentum_periods=config.MOMENTUM_PERIODS,
                    rsi_period=config.RSI_PERIOD,
                    rolling_days=config.ROLLING_DAYS,
                )
        except Exception as e:  # noqa: BLE001
            err = str(e)
            raw = None
            log.warning("[fetch]    %s: error — %s", yahoo_sym, err)

        if err:
            errors += 1

        summary: Dict[str, Any]
        if err:
            summary = {"symbol": yahoo_sym, "error": err}
        elif raw is not None:
            summary = build_summary(yahoo_sym, raw)
        else:
            summary = {"symbol": yahoo_sym, "error": "no data"}

        results.append(
            FetchResult(
                symbol=yahoo_sym,
                label=label,
                description=desc,
                error=err,
                frame=raw,
                summary=summary,
            )
        )
    elapsed = time.perf_counter() - t_all
    log.info(
        "[fetch]    Done: %d ok, %d errors (%.1fs)",
        len(results) - errors, errors, elapsed,
    )
    return results


def summaries_to_json(results: List[FetchResult]) -> str:
    payload = [
        {
            "symbol": r.symbol,
            "label": r.label,
            "description": r.description,
            "summary": r.summary,
        }
        for r in results
    ]
    return json.dumps(payload, indent=2)


def flatten_fetch_results(results: List[FetchResult]) -> List[Dict[str, Any]]:
    """One dict per ticker: identifiers + latest session fields (or error)."""
    rows: List[Dict[str, Any]] = []
    for r in results:
        if r.frame is None or r.frame.empty:
            rows.append({"symbol": r.symbol, "label": r.label, "error": r.error or "no data"})
            continue
        last = latest_snapshot_row(r.frame)
        flat: Dict[str, Any] = {"symbol": r.symbol, "label": r.label, "description": r.description}
        for k in last.index:
            if str(k) in _DROP_SESSION_KEYS:
                continue
            flat[str(k)] = _clean_scalar(last[k])
        rows.append(flat)
    return rows


def results_to_dataframe(results: List[FetchResult]) -> pd.DataFrame:
    return pd.DataFrame(flatten_fetch_results(results))


def write_csv_last_session(results: List[FetchResult], path: Path) -> None:
    """One row per ticker: latest computed metrics + identifiers."""
    results_to_dataframe(results).to_csv(path, index=False)
