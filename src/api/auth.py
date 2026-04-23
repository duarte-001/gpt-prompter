from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status


@dataclass(frozen=True)
class ApiKeyIdentity:
    raw: str
    key_id: str  # stable, non-reversible identifier for logs/metrics


def _key_id(key: str) -> str:
    # Short, stable identifier; never log full keys.
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return h[:12]


def _load_keys() -> set[str]:
    raw = (os.environ.get("STOCK_ASSISTANT_API_KEYS") or "").strip()
    if not raw:
        return set()
    return {k.strip() for k in raw.split(",") if k.strip()}


def _auth_disabled() -> bool:
    v = (os.environ.get("STOCK_ASSISTANT_DISABLE_AUTH") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def require_api_key(request: Request) -> ApiKeyIdentity:
    """
    Enforce API key auth for public deployments.

    Header: X-API-Key: <key>
    Env:
      - STOCK_ASSISTANT_API_KEYS: comma-separated keys
      - STOCK_ASSISTANT_DISABLE_AUTH=1: dev-only escape hatch
    """
    # Default behavior: if no keys are configured, run open (personal/dev mode).
    # To enable auth, set STOCK_ASSISTANT_API_KEYS.
    if _auth_disabled():
        return ApiKeyIdentity(raw="", key_id="auth_disabled")

    keys = _load_keys()
    if not keys:
        return ApiKeyIdentity(raw="", key_id="no_auth_configured")

    key = (request.headers.get("X-API-Key") or "").strip()
    if not key or key not in keys:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid API key.",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    return ApiKeyIdentity(raw=key, key_id=_key_id(key))


ApiKeyDep = Depends(require_api_key)

