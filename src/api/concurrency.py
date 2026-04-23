from __future__ import annotations

import os
import threading
from contextlib import contextmanager

from fastapi import HTTPException, status


def _max_inflight_from_env(default: int) -> int:
    raw = (os.environ.get("STOCK_ASSISTANT_MAX_INFLIGHT") or "").strip()
    if not raw:
        return int(default)
    try:
        n = int(raw)
    except ValueError:
        return int(default)
    return max(1, min(n, 10_000))


_sem = threading.Semaphore(_max_inflight_from_env(default=12))


@contextmanager
def acquire_or_503(bucket: str = "global"):
    ok = _sem.acquire(blocking=False)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Server is busy (inflight limit reached). bucket={bucket}",
        )
    try:
        yield
    finally:
        _sem.release()

