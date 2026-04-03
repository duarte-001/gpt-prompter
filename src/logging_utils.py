"""Lightweight JSONL logging for Q&A turns."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src import config
from src.pipeline import QAResult


def _ensure_log_dir() -> Path:
    log_dir = config.DATA_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def log_qa_result(
    question: str,
    qa: QAResult,
    *,
    period: str,
    use_rag: bool,
    path: Path | None = None,
) -> None:
    """Append a single JSON object describing one answered turn."""
    log_dir = _ensure_log_dir()
    target = path or (log_dir / "qa.jsonl")
    payload: dict[str, Any] = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "question": question,
        "symbols_used": qa.symbols_used,
        "period": period,
        "use_rag": bool(use_rag),
        "indexed_chunks": int(qa.indexed_chunks),
        "rag_error": qa.rag_error,
        "error": qa.error,
        "live_context_bytes": len(qa.context_json or ""),
        "rag_context_bytes": len(qa.rag_context or ""),
        "answer_length": len(qa.answer or ""),
        "answer_preview": (qa.answer or "")[:300],
        "timings": qa.timings,
    }
    with target.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
