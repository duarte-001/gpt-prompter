"""Ollama HTTP client for chat completions and embeddings."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from src import config

log = logging.getLogger("stock_qa")


def _ollama_options(override: dict[str, Any] | None) -> dict[str, Any] | None:
    base = getattr(config, "OLLAMA_OPTIONS", None) or {}
    merged: dict[str, Any] = {**base, **(override or {})}
    return merged or None


def ollama_chat(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    base_url: str | None = None,
    timeout_s: float = 120.0,
    log_context: str = "chat",
    options: dict[str, Any] | None = None,
) -> str:
    """
    Non-streaming chat. Returns assistant message content.
    Raises httpx.HTTPError or ValueError on empty response.

    ``log_context`` labels terminal lines so you can see which path is slow
    (e.g. ``stock_answer`` vs ``memory_summary``).
    """
    mdl = model or config.OLLAMA_MODEL
    url = (base_url or config.OLLAMA_BASE_URL).rstrip("/") + "/api/chat"
    payload: dict[str, Any] = {
        "model": mdl,
        "messages": messages,
        "stream": False,
    }
    opts = _ollama_options(options)
    if opts:
        payload["options"] = opts
    total_chars = sum(len(str(m.get("content", ""))) for m in messages)
    roles = [m.get("role", "?") for m in messages]
    log.info(
        "[llm]      %s → POST /api/chat | model=%s | messages=%d %s | ~chars=%d (~%d tok est.)",
        log_context,
        mdl,
        len(messages),
        roles,
        total_chars,
        max(1, total_chars // 4),
    )
    t0 = time.perf_counter()
    with httpx.Client(timeout=timeout_s) as client:
        r = client.post(url, json=payload)
        r.raise_for_status()
        data = r.json()
    http_s = time.perf_counter() - t0

    msg = data.get("message") or {}
    content = msg.get("content")
    if not content:
        raise ValueError(f"Ollama returned no message content: {data!r}")

    out = content.strip()
    cps = len(out) / http_s if http_s > 0 else 0.0
    extra: list[str] = []
    if isinstance(data, dict):
        pe = data.get("prompt_eval_count")
        ev = data.get("eval_count")
        td_ns = data.get("total_duration")
        ld_ns = data.get("load_duration")
        if pe is not None:
            extra.append(f"prompt_tokens={pe}")
        if ev is not None:
            extra.append(f"gen_tokens={ev}")
        if td_ns is not None:
            extra.append(f"ollama_total={td_ns / 1e9:.2f}s")
        if ld_ns is not None:
            extra.append(f"model_load={ld_ns / 1e9:.2f}s")
    tail = (" | " + ", ".join(extra)) if extra else ""
    log.info(
        "[llm]      %s ← reply | http=%.2fs | out_chars=%d | %.0f chars/s%s",
        log_context,
        http_s,
        len(out),
        cps,
        tail,
    )
    return out


def _parse_embed_response(data: dict[str, Any], n_expected: int) -> list[list[float]]:
    """Parse /api/embed JSON into ``n_expected`` vectors (order preserved)."""
    embs = data.get("embeddings")
    if isinstance(embs, list) and embs:
        if n_expected == 1:
            first = embs[0]
            if isinstance(first, list) and first and isinstance(first[0], (int, float)):
                return [list(first)]
        if len(embs) == n_expected:
            out: list[list[float]] = []
            for e in embs:
                if isinstance(e, list) and e:
                    out.append([float(x) for x in e])
                else:
                    raise ValueError(f"bad embedding row: {data!r}")
            return out
    emb = data.get("embedding")
    if n_expected == 1 and isinstance(emb, list) and emb and isinstance(emb[0], (int, float)):
        return [list(map(float, emb))]
    raise ValueError(f"Ollama returned no usable embeddings (expected {n_expected}): {data!r}")


def ollama_embed_many(
    texts: list[str],
    *,
    model: str | None = None,
    base_url: str | None = None,
    timeout_s: float = 300.0,
    batch_size: int | None = None,
    quiet: bool = False,
    options: dict[str, Any] | None = None,
) -> list[list[float]]:
    """
    Embed many strings via Ollama /api/embed using batched ``input`` arrays.

    One HTTP POST per batch (much faster than one POST per text for large lists).
    """
    if not texts:
        return []
    bs = batch_size or getattr(config, "RAG_EMBED_BATCH_SIZE", 32)
    base = (base_url or config.OLLAMA_BASE_URL).rstrip("/")
    mdl = model or config.OLLAMA_EMBED_MODEL
    url = f"{base}/api/embed"
    all_vecs: list[list[float]] = []
    n_batches = (len(texts) + bs - 1) // bs

    opts = _ollama_options(options)
    with httpx.Client(timeout=timeout_s) as client:
        for bi in range(0, len(texts), bs):
            batch = texts[bi : bi + bs]
            t0 = time.perf_counter()
            payload: dict[str, Any] = {"model": mdl, "input": batch}
            if opts:
                payload["options"] = opts
            r = client.post(url, json=payload)
            r.raise_for_status()
            data = r.json()
            vecs = _parse_embed_response(data, len(batch))
            elapsed = time.perf_counter() - t0
            bn = bi // bs + 1
            if not quiet:
                log.info(
                    "[embed]    batch %d/%d: %d texts in %.2fs (%.0f texts/s)",
                    bn,
                    n_batches,
                    len(batch),
                    elapsed,
                    len(batch) / elapsed if elapsed > 0 else 0.0,
                )
            all_vecs.extend(vecs)

    if len(all_vecs) != len(texts):
        raise ValueError(f"embedding count mismatch: got {len(all_vecs)}, want {len(texts)}")
    return all_vecs


def ollama_embed(
    text: str,
    *,
    model: str | None = None,
    base_url: str | None = None,
    timeout_s: float = 120.0,
    options: dict[str, Any] | None = None,
) -> list[float]:
    """Single-text embedding via Ollama /api/embed."""
    vecs = ollama_embed_many(
        [text],
        model=model,
        base_url=base_url,
        timeout_s=timeout_s,
        batch_size=1,
        quiet=True,
        options=options,
    )
    return vecs[0]
