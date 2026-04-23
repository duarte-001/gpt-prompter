from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Literal

import asyncio

from fastapi import HTTPException, status

from src.api.executor import EXECUTOR


JobState = Literal["queued", "running", "done", "error"]


@dataclass
class JobRecord:
    state: JobState
    created_at: float
    updated_at: float
    result: dict[str, Any] | None = None
    error: str | None = None


_jobs: dict[str, JobRecord] = {}


def _job_ttl_s(default: int = 15 * 60) -> int:
    raw = (os.environ.get("STOCK_ASSISTANT_JOB_TTL_SECONDS") or "").strip()
    if not raw:
        return int(default)
    try:
        v = int(raw)
    except ValueError:
        return int(default)
    return max(30, min(v, 24 * 60 * 60))


def _gc_jobs() -> None:
    ttl = _job_ttl_s()
    now = time.time()
    dead = [jid for jid, rec in _jobs.items() if now - rec.updated_at > ttl]
    for jid in dead:
        _jobs.pop(jid, None)


def create_job() -> str:
    _gc_jobs()
    jid = uuid.uuid4().hex
    now = time.time()
    _jobs[jid] = JobRecord(state="queued", created_at=now, updated_at=now)
    return jid


def get_job(jid: str) -> JobRecord:
    _gc_jobs()
    rec = _jobs.get(jid)
    if not rec:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")
    return rec


def _set_state(jid: str, *, state: JobState, result: dict[str, Any] | None = None, error: str | None = None) -> None:
    rec = _jobs.get(jid)
    if not rec:
        return
    now = time.time()
    rec.state = state
    rec.updated_at = now
    rec.result = result
    rec.error = error


async def run_job(jid: str, fn, *args, **kwargs) -> None:
    """
    Run a blocking function in the shared executor and store a JSON-serializable dict result.
    """
    _set_state(jid, state="running")
    loop = asyncio.get_running_loop()
    try:
        out = await loop.run_in_executor(EXECUTOR, lambda: fn(*args, **kwargs))
        _set_state(jid, state="done", result=out, error=None)
    except Exception as exc:  # noqa: BLE001
        _set_state(jid, state="error", result=None, error=str(exc))

