"""
Metrics from some_metrics.txt:
- OHLCV from yfinance (Adj Close for returns/RSI)
- Momentum_n = AdjClose_t / AdjClose_{t-n} - 1; vol-adjusted optional
- RSI(14) Wilder
- Smart money: volume > 2×20d mean volume OR reversal near 20d high/low
- Retail FOMO: 1d return > 2×mean(20d daily returns), RSI>70, volume > 1.5×mean(20d)
"""

from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd


def _ensure_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "Adj Close" not in out.columns and "Close" in out.columns:
        out["Adj Close"] = out["Close"]
    need = ("Open", "High", "Low", "Close", "Adj Close", "Volume")
    missing = [c for c in need if c not in out.columns]
    if missing:
        raise ValueError(f"History missing columns: {missing}")
    return out


def rsi_wilder(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI using Wilder smoothing (EMA alpha = 1/period on gains/losses)."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def add_momentum(
    df: pd.DataFrame,
    adj_col: str = "Adj Close",
    periods: Iterable[int] = (5, 10, 20, 60, 126, 252),
) -> pd.DataFrame:
    out = df.copy()
    ac = out[adj_col].astype(float)
    for n in periods:
        col = f"momentum_{n}d"
        out[col] = ac / ac.shift(n) - 1.0
        rets = ac.pct_change()
        vol = rets.rolling(n, min_periods=n).std()
        out[f"momentum_{n}d_vol_adj"] = out[col] / vol.replace(0, np.nan)
    return out


def add_smart_money_flags(df: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    """
    Volume spike: Volume > 2 × mean(Volume, 20).
    Reversal near extremes: at/near 20d high with down close, or at/near 20d low with up close.
    """
    out = df.copy()
    vol = out["Volume"].astype(float)
    ma_vol = vol.rolling(window, min_periods=window).mean()
    out["vol_ma_20"] = ma_vol
    out["smart_volume_spike"] = vol > (2.0 * ma_vol)

    high = out["High"].astype(float)
    low = out["Low"].astype(float)
    open_px = out["Open"].astype(float)
    close = out["Close"].astype(float)
    roll_high = high.rolling(window, min_periods=window).max()
    roll_low = low.rolling(window, min_periods=window).min()

    near_high = high >= roll_high * 0.995
    near_low = low <= roll_low * 1.005
    reversal_high = near_high & (close < open_px)
    reversal_low = near_low & (close > open_px)
    out["smart_reversal_near_extreme"] = reversal_high | reversal_low
    out["smart_money_day"] = out["smart_volume_spike"] | out["smart_reversal_near_extreme"]
    return out


def add_retail_fomo(df: pd.DataFrame, rsi: pd.Series, window: int = 20) -> pd.DataFrame:
    """
    FOMO day: 1d return > 2 × mean(20d daily returns of Adj Close),
    RSI > 70, Volume > 1.5 × mean(Volume, 20).
    """
    out = df.copy()
    ac = out["Adj Close"].astype(float)
    daily_ret = ac.pct_change()
    mean_ret_20 = daily_ret.rolling(window, min_periods=window).mean()
    vol = out["Volume"].astype(float)
    ma_vol = vol.rolling(window, min_periods=window).mean()

    out["retail_fomo"] = (
        (daily_ret > 2.0 * mean_ret_20)
        & (rsi > 70.0)
        & (vol > 1.5 * ma_vol)
    )
    return out


def enrich_ohlcv(
    df: pd.DataFrame,
    momentum_periods: Iterable[int],
    rsi_period: int,
    rolling_days: int,
) -> pd.DataFrame:
    if df.empty:
        return df
    out = _ensure_columns(df)
    out = add_momentum(out, periods=momentum_periods)
    rsi_col = f"rsi_{rsi_period}"
    out[rsi_col] = rsi_wilder(out["Adj Close"].astype(float), period=rsi_period)
    out = add_smart_money_flags(out, window=rolling_days)
    out = add_retail_fomo(out, rsi=out[rsi_col], window=rolling_days)
    return out


def latest_snapshot_row(df: pd.DataFrame) -> pd.Series:
    """Last row with valid Adj Close."""
    if df.empty:
        return pd.Series(dtype=object)
    sub = df.dropna(subset=["Adj Close"])
    if sub.empty:
        return pd.Series(dtype=object)
    return sub.iloc[-1]
