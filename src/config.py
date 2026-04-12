"""Central configuration and ticker list loading."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

from dotenv import load_dotenv

# Project root (parent of src/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Load `.env` from project root (never commit secrets; `.gitignore` covers `.env`).
# override=True: a blank `FRED_API_KEY` in the parent environment must not block the file.
load_dotenv(PROJECT_ROOT / ".env", override=True)

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

# FRED macro series (see fred_series.json); API key from .env or environment
FRED_SERIES_JSON = PROJECT_ROOT / "fred_series.json"
FRED_CACHE_DIR = DATA_DIR / "fred_cache"
FRED_CACHE_TTL_SECONDS = int(os.environ.get("FRED_CACHE_TTL_SECONDS", "3600"))
MAX_FRED_SERIES_PER_QUESTION = 8


def _read_fred_key_from_dotenv_file() -> str:
    """Fallback if os.environ is empty (BOM, UTF-16, or load order issues)."""
    path = PROJECT_ROOT / ".env"
    if not path.is_file():
        return ""
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError:
        return ""
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("FRED_API_KEY"):
            _, _, rest = s.partition("=")
            val = rest.strip().strip('"').strip("'")
            return val
    return ""


def fred_api_key() -> str:
    k = os.environ.get("FRED_API_KEY", "").strip()
    if k:
        return k
    return _read_fred_key_from_dotenv_file().strip()


def fred_api_key_line_empty_in_dotenv() -> bool:
    """True if `.env` has `FRED_API_KEY=` with no value (common when the file is open but not saved)."""
    path = PROJECT_ROOT / ".env"
    if not path.is_file():
        return False
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError:
        return False
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.upper().startswith("FRED_API_KEY"):
            _, _, rest = s.partition("=")
            return not rest.strip()
    return False


@dataclass(frozen=True)
class FredSeriesDef:
    series_id: str
    label: str
    always: bool
    keywords: tuple[str, ...]
    # Pipeline sector keys (see SECTOR_KEYWORDS); include this series when any match inferred tickers
    sectors: tuple[str, ...]


def load_fred_series_registry(path: Path | None = None) -> List[FredSeriesDef]:
    """
    Load FRED series registry from JSON.

    Supported shape: array of objects:
      {"series_id": "...", "label": "...", "always": false,
       "keywords": ["..."], "sectors": ["energy", "financials"]}
    """
    p = path or FRED_SERIES_JSON
    if not p.is_file():
        return []
    raw = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("fred_series.json must be a JSON array")
    out: list[FredSeriesDef] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"fred_series.json entry {i} must be an object")
        sid = str(item.get("series_id", "")).strip()
        if not sid:
            raise ValueError(f"fred_series.json entry {i} missing series_id")
        label = str(item.get("label", sid)).strip() or sid
        always = bool(item.get("always", False))
        kws = item.get("keywords", [])
        if kws is None:
            kws_t: tuple[str, ...] = ()
        elif isinstance(kws, list):
            kws_t = tuple(str(k).lower() for k in kws if str(k).strip())
        else:
            raise ValueError(f"fred_series.json entry {i}: keywords must be a list or null")
        sec = item.get("sectors", [])
        if sec is None:
            sec_t: tuple[str, ...] = ()
        elif isinstance(sec, list):
            sec_t = tuple(str(s).strip() for s in sec if str(s).strip())
        else:
            raise ValueError(f"fred_series.json entry {i}: sectors must be a list or null")
        out.append(
            FredSeriesDef(
                series_id=sid, label=label, always=always, keywords=kws_t, sectors=sec_t,
            ),
        )
    return out


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


def load_ticker_fred_sectors(path: Path | None = None) -> Dict[str, Tuple[str, ...]]:
    """
    Optional per-ticker FRED sector tags from the same JSON as tickers.

    Entry shape: { "XOM": { ..., "fred_sectors": ["energy"] }, ... }
    Keys must match SECTOR_KEYWORDS in pipeline (e.g. energy, financials).
    """
    p = path or TICKERS_JSON
    raw = json.loads(p.read_text(encoding="utf-8"))
    out: Dict[str, Tuple[str, ...]] = {}
    if not isinstance(raw, dict):
        return out
    for sym, val in raw.items():
        if not isinstance(val, dict):
            continue
        fs = val.get("fred_sectors")
        if not fs:
            continue
        if not isinstance(fs, (list, tuple)):
            raise ValueError(f"fred_sectors for {sym!r} must be a list")
        out[str(sym)] = tuple(str(x).strip() for x in fs if str(x).strip())
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
