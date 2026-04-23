from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor


def _workers_from_env(default: int) -> int:
    raw = (os.environ.get("STOCK_ASSISTANT_WORKERS") or "").strip()
    if not raw:
        return int(default)
    try:
        n = int(raw)
    except ValueError:
        return int(default)
    return max(1, min(n, 64))


# Shared executor for blocking work (pipeline / yfinance / embeddings / llm).
EXECUTOR = ThreadPoolExecutor(max_workers=_workers_from_env(default=8))

