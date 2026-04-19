"""
Chroma vector store + Ollama embeddings. Metadata: ticker, source, as_of, doc_type.

Embeddings call Ollama ``/api/embed`` (same server as chat). Chat and embed requests
include ``config.OLLAMA_OPTIONS`` (default ``num_gpu=-1``) so Ollama offloads layers
to the GPU when VRAM allows. Chroma only stores vectors (CPU/disk).

Per plan: RAG is supplementary; live yfinance JSON remains authoritative for numbers.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

import chromadb
from chromadb.config import Settings

from src import config
from src.fetcher import FetchResult
from src.llm import ollama_embed, ollama_embed_many

log = logging.getLogger("stock_qa")


def ensure_chroma_dir() -> None:
    config.CHROMA_DIR.mkdir(parents=True, exist_ok=True)


def get_collection():
    ensure_chroma_dir()
    client = chromadb.PersistentClient(
        path=str(config.CHROMA_DIR),
        settings=Settings(anonymized_telemetry=False),
    )
    return client.get_or_create_collection(
        name=config.RAG_COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


def _as_of_slug(summary: dict[str, Any]) -> str:
    raw = str(summary.get("last_date") or "unknown")
    return re.sub(r"[^\w\-.]+", "_", raw)[:80]


def format_metrics_chunk(
    symbol: str,
    label: str,
    description: str,
    summary: dict[str, Any],
    period: str,
) -> str:
    """Natural-language chunk for embedding (not the live JSON; semantic retrieval)."""
    if summary.get("error") or "session" not in summary:
        return ""
    sess = summary["session"] or {}
    ld = summary.get("last_date", "")
    lines: list[str] = [
        f"{label} ({symbol}): {description}",
        f"Metrics snapshot from Yahoo Finance. History window: {period}. Last bar as-of: {ld}.",
        "This text is for background search only; current numbers must come from live data in the app.",
    ]
    priority_keys = (
        "Adj Close",
        "Close",
        "Volume",
        "rsi_14",
        "momentum_5d",
        "momentum_20d",
        "momentum_60d",
        "momentum_252d",
        "smart_money_day",
        "retail_fomo",
    )
    for k in priority_keys:
        if k in sess and sess[k] is not None:
            lines.append(f"{k}: {sess[k]}")
    pc = summary.get("period_counts") or {}
    if pc:
        lines.append(
            f"Over the full window: smart_money_days={pc.get('smart_money_days')}, "
            f"retail_fomo_days={pc.get('retail_fomo_days')}."
        )
    return "\n".join(lines)


def ingest_fetch_results(
    results: list[FetchResult],
    *,
    period: str,
    ollama_base_url: str | None = None,
    embed_model: str | None = None,
) -> tuple[int, str | None]:
    """
    Upsert one chunk per successful ticker (metrics_snapshot). Returns (count_ingested, error_or_none).

    Embeddings are computed in batches (see ``config.RAG_EMBED_BATCH_SIZE``) to minimize HTTP round-trips.
    """
    t_all = time.perf_counter()
    coll = get_collection()
    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict[str, Any]] = []

    t_prep = time.perf_counter()
    for r in results:
        if r.error or r.frame is None or r.frame.empty:
            continue
        summ = r.summary
        if summ.get("error") or "session" not in summ:
            continue
        text = format_metrics_chunk(r.symbol, r.label, r.description, summ, period)
        if not text.strip():
            continue
        aid = _as_of_slug(summ)
        cid = f"metrics_{r.symbol}_{aid}"
        ids.append(cid)
        documents.append(text)
        ld = str(summ.get("last_date") or "")
        metadatas.append(
            {
                "ticker": r.symbol,
                "source": "yahoo_metrics_snapshot",
                "as_of": ld,
                "doc_type": "metrics_snapshot",
                "period": period,
            }
        )
    prep_s = time.perf_counter() - t_prep

    if not ids:
        log.info("[rag]      ingest: 0 chunks (prepare %.3fs)", prep_s)
        return 0, None

    log.info(
        "[rag]      ingest: %d chunks to embed (batch_size=%d, prepare %.3fs)…",
        len(ids),
        config.RAG_EMBED_BATCH_SIZE,
        prep_s,
    )

    try:
        t_emb = time.perf_counter()
        embeddings = ollama_embed_many(
            documents,
            base_url=ollama_base_url,
            model=embed_model,
            batch_size=config.RAG_EMBED_BATCH_SIZE,
            quiet=False,
        )
        emb_s = time.perf_counter() - t_emb
    except Exception as e:  # noqa: BLE001
        log.error("[rag]      ingest: embedding failed after %.2fs: %s", time.perf_counter() - t_all, e)
        return 0, f"embedding failed: {e}"

    try:
        t_up = time.perf_counter()
        coll.upsert(ids=ids, documents=documents, embeddings=embeddings, metadatas=metadatas)
        up_s = time.perf_counter() - t_up
    except Exception as e:  # noqa: BLE001
        log.error("[rag]      ingest: Chroma upsert failed: %s", e)
        return 0, f"chroma upsert failed: {e}"

    total_s = time.perf_counter() - t_all
    log.info(
        "[rag]      ingest: done %d chunks | embed %.2fs | chroma upsert %.3fs | total %.2fs",
        len(ids),
        emb_s,
        up_s,
        total_s,
    )
    return len(ids), None


def retrieve_for_question(
    question: str,
    tickers: list[str],
    *,
    top_k: int | None = None,
    ollama_base_url: str | None = None,
    embed_model: str | None = None,
) -> tuple[str, list[dict[str, Any]], str | None]:
    """
    Query Chroma with optional ticker filter. Returns (formatted_block, raw_hits, error_or_none).
    """
    if not tickers:
        return "", [], None

    k = top_k or config.RAG_TOP_K
    coll = get_collection()
    if coll.count() == 0:
        return "", [], None

    try:
        qemb = ollama_embed(
            question,
            base_url=ollama_base_url,
            model=embed_model,
        )
    except Exception as e:  # noqa: BLE001
        return "", [], str(e)

    where: dict[str, Any] = {"ticker": {"$in": tickers}}
    try:
        out = coll.query(
            query_embeddings=[qemb],
            n_results=min(k, max(1, coll.count())),
            where=where,
            include=["documents", "metadatas", "distances"],
        )
    except Exception as e:  # noqa: BLE001
        return "", [], str(e)

    docs = (out.get("documents") or [[]])[0]
    metas = (out.get("metadatas") or [[]])[0]
    dists = (out.get("distances") or [[]])[0]
    hits: list[dict[str, Any]] = []
    blocks: list[str] = []
    for i, doc in enumerate(docs or []):
        meta = metas[i] if i < len(metas) else {}
        dist = dists[i] if i < len(dists) else None
        hits.append({"metadata": meta or {}, "distance": dist, "excerpt": (doc or "")[:500]})
        if doc:
            meta_s = ", ".join(f"{k}={meta.get(k)}" for k in ("ticker", "as_of", "doc_type") if meta.get(k))
            blocks.append(f"[{meta_s}]\n{doc}")

    text = "\n\n---\n\n".join(blocks) if blocks else ""
    return text, hits, None
