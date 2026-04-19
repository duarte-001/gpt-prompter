"""Central configuration and ticker list loading."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Tuple

# Project root (parent of src/); inside a PyInstaller bundle use _MEIPASS.
if getattr(sys, "frozen", False):
    PROJECT_ROOT = Path(sys._MEIPASS)
else:
    PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Local data (exports, future RAG/Chroma); created on demand
DATA_DIR = PROJECT_ROOT / "data"
EXPORTS_DIR = DATA_DIR / "exports"

# Default ticker universe (JSON: symbol -> {label, description})
TICKERS_JSON = PROJECT_ROOT / "some_tickers.json"
METRICS_SPEC_FILE = PROJECT_ROOT / "some_metrics.txt"

# Ollama (used in later steps)
OLLAMA_BASE_URL = "http://127.0.0.1:11434"
OLLAMA_MODEL = "llama3.2:latest"  # 2.0 GB local model; fits 4 GB VRAM GPUs

# Vector store (Chroma persists under data/chroma)
CHROMA_DIR = DATA_DIR / "chroma"
RAG_COLLECTION_NAME = "stock_rag"
RAG_TOP_K = 5
# How many texts to send per /api/embed call (fewer round-trips = faster indexing).
RAG_EMBED_BATCH_SIZE = 32

# Embeddings via Ollama (pull once: ollama pull nomic-embed-text)
OLLAMA_EMBED_MODEL = "nomic-embed-text"


def load_ollama_options() -> dict[str, Any]:
    """
    Runtime options sent to Ollama ``/api/chat`` and ``/api/embed`` (GPU layer offload, etc.).

    - Set ``OLLAMA_OPTIONS_JSON`` to a JSON object to override completely, e.g.
      ``{"num_gpu": 32}``.
    - Otherwise, if ``OLLAMA_USE_GPU`` is not disabled, default to ``num_gpu=-1``
      (Ollama: offload as many layers as fit in VRAM). Set ``OLLAMA_USE_GPU=0`` to
      omit options and rely on the server Modelfile/defaults.
    """
    raw = os.environ.get("OLLAMA_OPTIONS_JSON", "").strip()
    if raw:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("OLLAMA_OPTIONS_JSON must be a JSON object")
        return data
    use = os.environ.get("OLLAMA_USE_GPU", "1").strip().lower()
    if use in ("0", "false", "no", "off"):
        return {}
    return {"num_gpu": -1}


# Evaluated at import; set OLLAMA_OPTIONS_JSON / OLLAMA_USE_GPU before importing src.*
OLLAMA_OPTIONS = load_ollama_options()

# Default history window for momentum / RSI / rolling stats (yfinance period string)
DEFAULT_YF_PERIOD = "2y"

# yfinance OHLCV cache (under data/, gitignored)
YF_CACHE_DIR = DATA_DIR / "yfinance_cache"
# Refresh disk/in-memory cache after this many seconds (personal use default: 1 hour)
YF_CACHE_TTL_SECONDS = 3600

# Momentum lookback periods in trading days (multi-period returns)
MOMENTUM_PERIODS = (5, 10, 20, 60, 126, 252)

# Rolling window for volume / return baselines
ROLLING_DAYS = 20

# RSI period (Wilder)
RSI_PERIOD = 14


def load_ticker_mapping(path: Path | None = None) -> Dict[str, Tuple[str, str]]:
    """
    Load Yahoo symbols and display metadata from JSON.

    Supported shapes:
    - Object: { "NVDA": { "label": "...", "description": "..." }, ... }
    - Object: { "NVDA": ["NVDA", "NVIDIA ..."], ... }  (tuple-like array)
    - Array: [ { "symbol": "NVDA", "label": "...", "description": "..." }, ... ]
    """
    p = path or TICKERS_JSON
    raw = json.loads(p.read_text(encoding="utf-8"))
    out: Dict[str, Tuple[str, str]] = {}

    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                raise ValueError("Ticker list entries must be objects")
            sym = str(item["symbol"])
            out[sym] = (str(item.get("label", sym)), str(item.get("description", "")))
        return out

    if not isinstance(raw, dict):
        raise ValueError("Ticker file must be a JSON object or array")

    for sym, val in raw.items():
        key = str(sym)
        if isinstance(val, dict):
            out[key] = (
                str(val.get("label", key)),
                str(val.get("description", "")),
            )
        elif isinstance(val, (list, tuple)) and len(val) >= 2:
            out[key] = (str(val[0]), str(val[1]))
        else:
            raise ValueError(f"Invalid entry for {key!r}: expected object or [label, description]")
    return out


def load_alias_index(path: Path | None = None) -> Dict[str, str]:
    """Return {lowercase_alias: yahoo_symbol} from the ticker JSON.

    Sources for aliases (in priority order):
    1. Explicit ``"aliases"`` array in each ticker entry.
    2. The ``"label"`` field when it differs from the symbol (e.g. LVMH → MC.PA).
    """
    p = path or TICKERS_JSON
    raw = json.loads(p.read_text(encoding="utf-8"))
    idx: Dict[str, str] = {}

    if not isinstance(raw, dict):
        return idx

    for sym, val in raw.items():
        if not isinstance(val, dict):
            continue
        for alias in val.get("aliases", []):
            idx[alias.lower()] = sym
        label = val.get("label", sym)
        if isinstance(label, str) and label.lower() != sym.lower():
            idx[label.lower()] = sym
    return idx


def ensure_exports_dir() -> Path:
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    return EXPORTS_DIR
