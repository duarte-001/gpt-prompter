from __future__ import annotations

import os

from fastapi import Request, Response


class MaxBodySizeMiddleware:
    """
    Simple max body-size guard for JSON APIs.

    Prefer enforcing this at the reverse proxy as well, but this catches misconfigurations.
    """

    def __init__(self, app, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = int(max_bytes)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)

        # Only enforce on requests that plausibly have a body.
        headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in (scope.get("headers") or [])}
        cl = headers.get("content-length")
        if cl:
            try:
                if int(cl) > self.max_bytes:
                    resp = Response("Request body too large.", status_code=413)
                    return await resp(scope, receive, send)
            except ValueError:
                pass

        return await self.app(scope, receive, send)


def body_limit_from_env(default_bytes: int = 200_000) -> int:
    raw = (os.environ.get("STOCK_ASSISTANT_MAX_BODY_BYTES") or "").strip()
    if not raw:
        return int(default_bytes)
    try:
        v = int(raw)
    except ValueError:
        return int(default_bytes)
    return max(10_000, min(v, 5_000_000))

