from __future__ import annotations

import os
import time
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status

from src.api.auth import ApiKeyIdentity, require_api_key


@dataclass
class Limit:
    max_requests: int
    window_s: float


class InMemoryFixedWindowLimiter:
    """
    Minimal per-key fixed-window rate limiter.

    Notes:
    - Works only within a single Python process.
    - For multi-worker deployments you would replace this with Redis.
    """

    def __init__(self) -> None:
        self._buckets: dict[tuple[str, str], tuple[float, int]] = {}

    def hit(self, *, key_id: str, bucket: str, limit: Limit) -> tuple[bool, int, float]:
        now = time.time()
        win = float(limit.window_s)
        start = now - (now % win)
        k = (key_id, bucket)
        prev_start, count = self._buckets.get(k, (start, 0))
        if prev_start != start:
            count = 0
        count += 1
        self._buckets[k] = (start, count)
        ok = count <= int(limit.max_requests)
        remaining = max(0, int(limit.max_requests) - count)
        reset_in = (start + win) - now
        return ok, remaining, max(0.0, reset_in)


_limiter = InMemoryFixedWindowLimiter()


def _rate_limits_enabled() -> bool:
    v = (os.environ.get("STOCK_ASSISTANT_RATE_LIMITS") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _limit_from_env(prefix: str, default_rpm: int) -> Limit:
    rpm_raw = (os.environ.get(prefix) or "").strip()
    try:
        rpm = int(rpm_raw) if rpm_raw else int(default_rpm)
    except ValueError:
        rpm = int(default_rpm)
    rpm = max(1, min(rpm, 10_000))
    return Limit(max_requests=rpm, window_s=60.0)


def rate_limit(bucket: str, *, default_rpm: int):
    """
    Create a dependency that rate-limits requests per API key.

    bucket: logical name (e.g. "ask", "status") to allow different limits per endpoint.
    """

    lim = _limit_from_env(f"STOCK_ASSISTANT_RPM_{bucket.upper()}", default_rpm)

    def _dep(request: Request, ident: ApiKeyIdentity = Depends(require_api_key)) -> None:
        if not _rate_limits_enabled():
            return
        ok, remaining, reset_in = _limiter.hit(key_id=ident.key_id, bucket=bucket, limit=lim)
        request.state.rate_limit = {
            "bucket": bucket,
            "limit": lim.max_requests,
            "remaining": remaining,
            "reset_in_s": reset_in,
        }
        if not ok:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded for bucket={bucket}. Try again soon.",
                headers={"Retry-After": str(max(1, int(reset_in)))},
            )

    return Depends(_dep)

