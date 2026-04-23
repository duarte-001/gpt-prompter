"""
QA pipeline: deterministic ticker matching -> live yfinance fetch -> optional RAG -> Ollama.

Matching is instant (regex + alias lookup + sector keywords). No LLM call needed.
Every step is timed and logged to the terminal via Python logging.
An optional step_callback lets the UI (Streamlit st.status) show live progress.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from src import config
from src.config import load_alias_index, load_ticker_mapping
from src.fetcher import fetch_all_tickers, summaries_to_json
from src.llm import ollama_chat
from src.rag import ingest_fetch_results, retrieve_for_question

log = logging.getLogger("stock_qa")

MAX_TICKERS_PER_QUESTION = 10
MAX_RECENT_MESSAGES = 4

StepCallback = Callable[[str, str], None]

SECTOR_KEYWORDS: dict[str, list[str]] = {
    "defense": ["defense", "defence", "aerospace", "military", "security"],
    "energy": ["energy", "oil", "gas", "nuclear", "uranium", "power", "utilities"],
    "technology": [
        "technology",
        "technologies",
        "tech",
        "software",
        "semiconductor",
        "semiconductors",
        "semi",
        "chip",
        "chips",
        "ai",
        "cybersecurity",
    ],
    "financials": ["bank", "banking", "financial", "finance", "insurance", "broker"],
    "pharma": ["pharma", "pharmaceutical", "biotech", "drug", "healthcare", "medical"],
    "crypto": ["crypto", "bitcoin", "mining", "blockchain"],
    "quantum": ["quantum"],
    "aviation": ["aviation", "airline", "airlines", "air", "aerospace"],
}

SYSTEM_PROMPT = """\
You are an analytical assistant for stock and market questions. Your goal is to initiate a useful finance discussion grounded in the provided live JSON data, and informed by what is happening in the world.

You should attempt to use up-to-date public information (news / macro context / sector developments) when relevant. If you cannot access browsing or you are not sure, say so briefly and continue with a data-grounded answer using the provided JSON.

Hard rules:
- Do not invent prices, returns, RSI, dates, or other metrics. If you use a number, it must come from live_market_data (JSON) exactly.

Data priority:
1) LIVE MARKET DATA (JSON) — authoritative for all numeric claims (prices/returns/RSI/dates).
2) RETRIEVAL BACKGROUND — optional qualitative context only; ignore if it conflicts with live data.
3) CONVERSATION CONTEXT — recent turns + optional session_summary for continuity.

If data is missing or a symbol has errors:
- Say so explicitly and proceed with what is available.

Trend label:
- Classify each relevant asset as trending_up / trending_down / bottoming / ranging, based on momentum and RSI when present in live_market_data.

User messages may include a fenced ```json``` payload. Keys include current_question, live_market_data (authoritative numbers), retrieval_background, and optionally session_summary."""


def should_include_session_summary_for_payload(
    conversation_summary: str,
    recent_messages: list[dict[str, str]] | None,
    prior_message_count: int | None,
) -> bool:
    """
    Include rolling summary only when it adds history not already in recent_messages
    (last MAX_RECENT_MESSAGES), to avoid duplicating the same context for GPT.
    """
    if not (conversation_summary or "").strip():
        return False
    if not recent_messages:
        return True
    if prior_message_count is not None:
        return prior_message_count > MAX_RECENT_MESSAGES
    return len(recent_messages) >= MAX_RECENT_MESSAGES


def build_structured_stock_user_content(
    *,
    question: str,
    context_json: str,
    rag_text: str,
    symbols: list[str],
    conversation_summary: str,
    recent_messages: list[dict[str, str]] | None,
    prior_message_count: int | None,
    idx_err: str | None,
    prompt_size: Literal["small", "medium", "large"] = "large",
) -> str:
    """Single user turn: intro line + fenced JSON; live_market_data is full parsed metrics."""
    include_summary = should_include_session_summary_for_payload(
        conversation_summary, recent_messages, prior_message_count,
    )
    try:
        live_data: Any = json.loads(context_json) if context_json.strip() else []
    except json.JSONDecodeError:
        live_data = {"_raw": context_json}

    def _compact_live_market_data(data: Any) -> Any:
        """
        Reduce token usage while preserving the most discussion-relevant fields.

        Expected shape from fetcher: a list of {symbol,label,description,summary:{last_date,session:{...}}}.
        For unknown shapes, return as-is.
        """
        if prompt_size != "small":
            return data
        if not isinstance(data, list):
            return data
        out: list[dict[str, Any]] = []
        keep_session_keys = (
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
        for row in data:
            if not isinstance(row, dict):
                continue
            sym = row.get("symbol")
            if not sym:
                continue
            summ = row.get("summary") if isinstance(row.get("summary"), dict) else {}
            sess = summ.get("session") if isinstance(summ.get("session"), dict) else {}
            compact_sess = {k: sess.get(k) for k in keep_session_keys if k in sess}
            out.append(
                {
                    "symbol": sym,
                    "label": row.get("label"),
                    "description": row.get("description"),
                    "last_date": summ.get("last_date"),
                    "session": compact_sess,
                    "error": summ.get("error") if isinstance(summ, dict) else None,
                }
            )
        return out

    def _truncate_rag(text: str) -> tuple[str | None, str | None]:
        t = (text or "").strip()
        if not t:
            return None, None
        # Keep "large" as default: generous cap; small keeps only a short slice.
        max_chars_by_size: dict[str, int] = {
            "small": 2000,
            "medium": 6000,
            "large": 14000,
        }
        cap = max_chars_by_size.get(prompt_size, 14000)
        if len(t) <= cap:
            return t, None
        truncated = t[:cap].rstrip() + "\n\n…(retrieval_background truncated)"
        note = f"retrieval_background truncated to {cap} chars for prompt_size={prompt_size}"
        return truncated, note

    live_for_payload = _compact_live_market_data(live_data)
    rag_for_payload, rag_note = _truncate_rag(rag_text)

    payload: dict[str, Any] = {
        "schema_version": 1,
        "current_question": question,
        "symbols_universe": symbols,
        "live_market_data": live_for_payload,
        "retrieval_background": rag_for_payload,
    }
    if include_summary:
        payload["session_summary"] = conversation_summary.strip()
    if idx_err:
        payload["indexing_note"] = idx_err
    if rag_note:
        payload["retrieval_note"] = rag_note
    if prompt_size != "large":
        payload["prompt_size"] = prompt_size

    body = json.dumps(payload, ensure_ascii=False, indent=2)
    return (
        "Use the JSON payload below. Field live_market_data is authoritative for "
        "all prices, returns, RSI, and dates; do not invent numbers.\n\n"
        f"```json\n{body}\n```"
    )


def build_structured_general_user_content(
    *,
    question: str,
    conversation_summary: str,
    recent_messages: list[dict[str, str]] | None,
    prior_message_count: int | None,
) -> str:
    include_summary = should_include_session_summary_for_payload(
        conversation_summary, recent_messages, prior_message_count,
    )
    payload: dict[str, Any] = {
        "schema_version": 1,
        "current_question": question,
        "live_market_data": [],
        "retrieval_background": None,
    }
    if include_summary:
        payload["session_summary"] = conversation_summary.strip()

    body = json.dumps(payload, ensure_ascii=False, indent=2)
    return (
        "Use the JSON payload below. live_market_data is empty (no ticker-specific "
        "fetch for this turn).\n\n"
        f"```json\n{body}\n```"
    )


@dataclass
class QAResult:
    answer: str
    symbols_used: list[str]
    context_json: str
    rag_context: str = ""
    rag_hits: list[dict] = field(default_factory=list)
    rag_error: str | None = None
    indexed_chunks: int = 0
    error: str | None = None
    timings: dict[str, float] = field(default_factory=dict)
    # Exact ChatGPT-ready messages (system + optional recent + user), when built.
    export_messages: list[dict[str, str]] | None = None


# ---------------------------------------------------------------------------
# Deterministic ticker matching (instant, no LLM)
# ---------------------------------------------------------------------------

def _extract_symbols_regex(question: str, universe: set[str]) -> list[str]:
    """Match exact ticker symbols that appear in the question."""
    canon = {s.upper(): s for s in universe}
    parts = re.split(r"[^A-Za-z0-9\.\^]+", question)
    found: list[str] = []
    seen: set[str] = set()
    for raw in parts:
        t = raw.strip().upper()
        while t and t[0] in ".,!?;:":
            t = t[1:]
        while t and t[-1] in ".,!?;:":
            t = t[:-1]
        if t and t in canon:
            s = canon[t]
            if s not in seen:
                found.append(s)
                seen.add(s)
    return found


def _extract_by_alias(
    question: str,
    alias_index: dict[str, str],
) -> list[str]:
    """Match company names / aliases against the question text."""
    q_lower = question.lower()
    # Split on non-alphanum but keep & and ' so "s&p" and "l'oreal" stay intact
    q_words = set(re.split(r"[^a-z0-9&']+", q_lower)) - {""}

    found: list[str] = []
    seen: set[str] = set()
    # Longest aliases first so "palo alto networks" beats "palo alto"
    for alias in sorted(alias_index, key=len, reverse=True):
        sym = alias_index[alias]
        if sym in seen:
            continue
        if " " in alias:
            matched = alias in q_lower
        else:
            matched = alias in q_words
        if matched:
            found.append(sym)
            seen.add(sym)
    return found


def _extract_sector(
    question: str,
    full_mapping: dict[str, tuple[str, str]],
    alias_index: dict[str, str],
) -> list[str]:
    """Match sector keywords and return all tickers whose description matches."""
    q = question.lower()
    q_words = set(re.split(r"[^a-z0-9&']+", q)) - {""}

    def phrase_in_question(phrase: str) -> bool:
        if " " in phrase:
            return phrase in q
        return phrase in q_words

    requested: list[str] = []
    for key, phrases in SECTOR_KEYWORDS.items():
        if any(phrase_in_question(p) for p in phrases):
            requested.append(key)
    if not requested:
        return []

    aliases_by_symbol: dict[str, list[str]] = {}
    for alias, sym in alias_index.items():
        aliases_by_symbol.setdefault(sym, []).append(alias)

    chosen: list[str] = []
    seen: set[str] = set()
    for sym, (label, desc) in full_mapping.items():
        text = f"{label} {desc}".lower()
        if aliases := aliases_by_symbol.get(sym):
            text = f"{text} {' '.join(aliases)}"
        text_words = set(re.split(r"[^a-z0-9&']+", text)) - {""}

        def phrase_in_text(phrase: str) -> bool:
            if " " in phrase:
                return phrase in text
            return phrase in text_words

        for key in requested:
            for phrase in SECTOR_KEYWORDS.get(key, []):
                if phrase_in_text(phrase):
                    if sym not in seen:
                        chosen.append(sym)
                        seen.add(sym)
                    break
            if sym in seen:
                break
    return chosen


_GENERAL_RE = re.compile(
    r"^\s*(what\s+(is|are|does)|explain\b|how\s+(does|do|is|are)\b|define\b)",
    re.IGNORECASE,
)


def _merge_unique(*lists: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for lst in lists:
        for s in lst:
            if s not in seen:
                out.append(s)
                seen.add(s)
    return out


def _extract_symbols_from_context(
    text: str,
    *,
    universe: set[str],
    alias_index: dict[str, str],
    full_mapping: dict[str, tuple[str, str]],
) -> list[str]:
    if not text.strip():
        return []
    sym_regex = _extract_symbols_regex(text, universe)
    sym_alias = _extract_by_alias(text, alias_index)
    sym_sector = _extract_sector(text, full_mapping, alias_index)
    return _merge_unique(sym_regex, sym_alias, sym_sector)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _step(cb: StepCallback | None, label: str = "", detail: str = "") -> None:
    if cb:
        cb(label, detail)


def build_mapping_subset(
    symbols: list[str],
    full_mapping: dict[str, tuple[str, str]],
) -> dict[str, tuple[str, str]]:
    return {s: full_mapping[s] for s in symbols if s in full_mapping}


SKIP_LLM_PLACEHOLDER = (
    "_Local model skipped — use **Export to GPT** for the full prompt._"
)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def answer_question(
    question: str,
    *,
    period: str | None = None,
    tickers_json_path: Path | None = None,
    model: str | None = None,
    ollama_base_url: str | None = None,
    embedding_model: str | None = None,
    use_rag: bool = True,
    index_metrics_to_rag: bool = False,
    conversation_summary: str = "",
    recent_messages: list[dict[str, str]] | None = None,
    prior_message_count: int | None = None,
    step_callback: StepCallback | None = None,
    skip_llm: bool = False,
    prompt_size: Literal["small", "medium", "large"] = "large",
) -> QAResult:
    t_total = time.perf_counter()
    timings: dict[str, float] = {}
    q_preview = question[:80] + ("…" if len(question) > 80 else "")
    log.info("[pipeline] Question: \"%s\"", q_preview)

    path = tickers_json_path or config.TICKERS_JSON
    full_mapping = load_ticker_mapping(path)
    alias_index = load_alias_index(path)

    # --- 1. Deterministic ticker matching (instant) ---
    _step(step_callback, label="Matching tickers…")
    t0 = time.perf_counter()

    universe = set(full_mapping.keys())
    sym_regex = _extract_symbols_regex(question, universe)
    sym_alias = _extract_by_alias(question, alias_index)
    sym_sector = _extract_sector(question, full_mapping, alias_index)
    symbols = _merge_unique(sym_regex, sym_alias, sym_sector)[:MAX_TICKERS_PER_QUESTION]

    # If the user didn't mention a symbol/sector explicitly this turn, try to
    # carry over from recent context (keeps chat natural and reduces retries).
    ctx_symbols: list[str] = []
    if not symbols:
        ctx_text_parts: list[str] = []
        if recent_messages:
            ctx_text_parts.extend(
                m.get("content", "")
                for m in recent_messages[-MAX_RECENT_MESSAGES:]
                if isinstance(m, dict)
            )
        if conversation_summary:
            ctx_text_parts.append(conversation_summary)
        ctx_text = "\n\n".join(p for p in ctx_text_parts if p)
        ctx_symbols = _extract_symbols_from_context(
            ctx_text,
            universe=universe,
            alias_index=alias_index,
            full_mapping=full_mapping,
        )[:MAX_TICKERS_PER_QUESTION]
        if ctx_symbols:
            symbols = ctx_symbols

    timings["match"] = round(time.perf_counter() - t0, 4)

    match_parts: list[str] = []
    if sym_regex:
        match_parts.append(f"regex: {', '.join(sym_regex)}")
    if sym_alias:
        match_parts.append(f"alias: {', '.join(sym_alias)}")
    if sym_sector:
        match_parts.append(f"sector: {', '.join(sym_sector)}")
    if ctx_symbols:
        match_parts.append(f"context: {', '.join(ctx_symbols)}")
    if match_parts:
        log.info("[match]    %s (%.4fs)", " | ".join(match_parts), timings["match"])
        _step(step_callback, detail=f"Matched {len(symbols)} ticker(s): {', '.join(symbols[:6])}")
    else:
        log.info("[match]    No tickers found (%.4fs)", timings["match"])

    is_general = bool(_GENERAL_RE.search(question))

    period_eff = period or config.DEFAULT_YF_PERIOD

    # --- 2. No tickers found ---
    if not symbols:
        if is_general:
            # Educational question without market data
            _step(step_callback, label="Generating response…")
            log.info("[llm]      Generating response (general question)…")

            chat_msgs: list[dict[str, str]] = [
                {"role": "system", "content": SYSTEM_PROMPT},
            ]
            if recent_messages:
                chat_msgs.extend(recent_messages[-MAX_RECENT_MESSAGES:])
            general_content = build_structured_general_user_content(
                question=question,
                conversation_summary=conversation_summary,
                recent_messages=recent_messages,
                prior_message_count=prior_message_count,
            )
            chat_msgs.append({"role": "user", "content": general_content})

            t0 = time.perf_counter()
            if skip_llm:
                timings["llm"] = 0.0
                timings["total"] = round(time.perf_counter() - t_total, 2)
                _step(step_callback, detail="Skipped local LLM (prompt export mode)")
                return QAResult(
                    answer=SKIP_LLM_PLACEHOLDER,
                    symbols_used=[],
                    context_json="{}",
                    timings=timings,
                    export_messages=list(chat_msgs),
                )
            try:
                ans = ollama_chat(
                    chat_msgs,
                    model=model,
                    base_url=ollama_base_url,
                    log_context="general_answer",
                )
            except Exception as e:  # noqa: BLE001
                timings["llm"] = round(time.perf_counter() - t0, 2)
                timings["total"] = round(time.perf_counter() - t_total, 2)
                log.error("[llm]      Failed after %.1fs: %s", timings["llm"], e)
                _step(step_callback, detail=f"LLM error: {e}")
                return QAResult(
                    answer="", symbols_used=[], context_json="{}",
                    error=str(e), timings=timings,
                    export_messages=list(chat_msgs),
                )
            timings["llm"] = round(time.perf_counter() - t0, 2)
            timings["total"] = round(time.perf_counter() - t_total, 2)
            log.info(
                "[pipeline] general_answer: %d chars in %.1fs (total pipeline %.1fs)",
                len(ans), timings["llm"], timings["total"],
            )
            _step(step_callback, detail=f"LLM response: {len(ans)} chars ({timings['llm']}s)")
            return QAResult(
                answer=ans, symbols_used=[], context_json="{}",
                timings=timings,
                export_messages=list(chat_msgs),
            )

        # Not general, no tickers → be specific
        log.info("[pipeline] No tickers found — asking user to be specific")
        timings["total"] = round(time.perf_counter() - t_total, 2)
        return QAResult(
            answer=(
                "I couldn't identify which stocks or sectors you're asking "
                "about. Could you be more specific? For example:\n\n"
                "- **Name a ticker** — NVDA, AAPL, ^GSPC\n"
                "- **Name a company** — Nvidia, Marathon Digital\n"
                "- **Ask about a sector** — defense stocks, tech sector\n\n"
                "I'll pull live market data and give you a grounded analysis."
            ),
            symbols_used=[],
            context_json="{}",
            timings=timings,
        )

    # --- 3. Fetch live data for identified tickers ---
    _step(step_callback, label=f"Fetching live data for {len(symbols)} symbol(s)…")

    t0 = time.perf_counter()
    sub = build_mapping_subset(symbols, full_mapping)
    results = fetch_all_tickers(sub, period=period_eff)
    ctx = summaries_to_json(results)
    timings["fetch"] = round(time.perf_counter() - t0, 2)
    _step(step_callback, detail=f"Fetched {len(results)} symbol(s) ({timings['fetch']}s)")

    rag_text = ""
    rag_hits: list[dict] = []
    rag_err: str | None = None
    indexed = 0
    idx_err: str | None = None

    if index_metrics_to_rag:
        _step(step_callback, label="Indexing to Chroma…")
        log.info("[rag]      Indexing metrics to Chroma…")
        t0 = time.perf_counter()
        indexed, idx_err = ingest_fetch_results(
            results, period=period_eff,
            ollama_base_url=ollama_base_url, embed_model=embedding_model,
        )
        timings["rag_index"] = round(time.perf_counter() - t0, 2)
        log.info("[rag]      Indexed %d chunk(s) (%.1fs)", indexed, timings["rag_index"])
        if idx_err:
            log.warning("[rag]      Indexing issue: %s", idx_err)

    if use_rag:
        _step(step_callback, label="Retrieving RAG context…")
        log.info("[rag]      Retrieving from Chroma…")
        t0 = time.perf_counter()
        rag_text, rag_hits, rag_err = retrieve_for_question(
            question, list(sub.keys()),
            top_k=config.RAG_TOP_K,
            ollama_base_url=ollama_base_url, embed_model=embedding_model,
        )
        timings["rag_retrieve"] = round(time.perf_counter() - t0, 2)
        log.info(
            "[rag]      %d chunk(s) retrieved (%.1fs)",
            len(rag_hits), timings["rag_retrieve"],
        )
        if rag_err:
            log.warning("[rag]      Retrieval issue: %s", rag_err)
        _step(step_callback, detail=f"RAG: {len(rag_hits)} chunk(s) ({timings['rag_retrieve']}s)")

    # --- 4. Build prompt and call LLM ---
    _step(step_callback, label="Generating response…")
    rag_block = rag_text.strip() if rag_text else "(none)"

    user_content = build_structured_stock_user_content(
        question=question,
        context_json=ctx,
        rag_text=rag_text,
        symbols=list(sub.keys()),
        conversation_summary=conversation_summary,
        recent_messages=recent_messages,
        prior_message_count=prior_message_count,
        idx_err=idx_err,
        prompt_size=prompt_size,
    )

    chat_msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    if recent_messages:
        chat_msgs.extend(recent_messages[-MAX_RECENT_MESSAGES:])
    chat_msgs.append({"role": "user", "content": user_content})

    sum_chars = len(conversation_summary) if conversation_summary else 0
    log.info(
        "[llm]      stock_answer: building chat | live_JSON_chars=%d | rag_block_chars=%d | "
        "conv_summary_chars=%d | recent_msgs=%d | include_summary=%s",
        len(ctx),
        len(rag_block),
        sum_chars,
        len(recent_messages or []),
        should_include_session_summary_for_payload(
            conversation_summary, recent_messages, prior_message_count,
        ),
    )

    if skip_llm:
        timings["llm"] = 0.0
        timings["total"] = round(time.perf_counter() - t_total, 2)
        _step(step_callback, detail="Skipped local LLM (prompt export mode)")
        return QAResult(
            answer=SKIP_LLM_PLACEHOLDER,
            symbols_used=list(sub.keys()),
            context_json=ctx,
            rag_context=rag_text,
            rag_hits=rag_hits,
            rag_error=rag_err or idx_err,
            indexed_chunks=indexed,
            timings=timings,
            export_messages=list(chat_msgs),
        )

    t0 = time.perf_counter()
    try:
        ans = ollama_chat(
            chat_msgs,
            model=model,
            base_url=ollama_base_url,
            log_context="stock_answer",
        )
    except Exception as e:  # noqa: BLE001
        timings["llm"] = round(time.perf_counter() - t0, 2)
        timings["total"] = round(time.perf_counter() - t_total, 2)
        log.error("[llm]      Failed after %.1fs: %s", timings["llm"], e)
        _step(step_callback, detail=f"LLM error: {e}")
        return QAResult(
            answer="", symbols_used=list(sub.keys()), context_json=ctx,
            rag_context=rag_text, rag_hits=rag_hits,
            rag_error=rag_err or idx_err, indexed_chunks=indexed,
            error=str(e), timings=timings,
            export_messages=list(chat_msgs),
        )

    timings["llm"] = round(time.perf_counter() - t0, 2)
    timings["total"] = round(time.perf_counter() - t_total, 2)
    log.info(
        "[pipeline] Total: %.1fs (stock_answer llm=%.1fs) | symbols: %s",
        timings["total"], timings["llm"], ", ".join(symbols),
    )
    _step(step_callback, detail=f"LLM response: {len(ans)} chars ({timings['llm']}s)")

    return QAResult(
        answer=ans, symbols_used=list(sub.keys()), context_json=ctx,
        rag_context=rag_text, rag_hits=rag_hits,
        rag_error=rag_err or idx_err, indexed_chunks=indexed,
        timings=timings,
        export_messages=list(chat_msgs),
    )
