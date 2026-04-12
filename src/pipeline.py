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
from typing import Any, Callable

from src import config
from src.config import (
    FredSeriesDef,
    load_alias_index,
    load_fred_series_registry,
    load_ticker_fred_sectors,
    load_ticker_mapping,
)
from src.fetcher import fetch_all_tickers, summaries_to_json
from src.fred_client import build_economic_context
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
You are an analytical assistant for stock and market questions. Ground answers in \
the JSON payload; explore implications and risks analytically, not as personalized \
financial advice.

Data priority:
1) LIVE MARKET DATA (JSON) — authoritative for prices, returns, momentum_*, rsi_14, \
and summary.last_date. Quote these numbers exactly. Path: each ticker object has \
summary.session for latest bar fields.
2) ECONOMIC CONTEXT (JSON, FRED) — when the key is present and non-empty, \
authoritative for macro series (ids, dates, values). Use those numbers exactly; \
never substitute equity fields for macro.
3) RETRIEVED CONTEXT — optional; may be stale. Background only. If it conflicts \
with live or FRED JSON, ignore retrieval.
4) CONVERSATION CONTEXT — session_summary / thread for continuity only; does not \
override 1–2.

Missing data (mandatory):
- For every symbol in live_market_data: if summary.error, rows==0, or critical \
session fields are absent, state explicitly what is missing and do not fabricate \
values. If some tickers are ok and others are not, address each.
- If economic_context is absent, null, or empty, write "Macro (FRED): not provided \
for this turn" in Sources and do not invent macro statistics.
- If retrieval_background is null/empty, do not imply RAG was used.

Output structure (strict order and caps):
1) **Brief answer** — exactly 2–4 sentences, ≤120 words total. Must include the \
rule-based regime label (below) per ticker when equity data exists.
2) **Key metrics** — bullet list, 3–7 bullets, each one label + value from live JSON \
(e.g., momentum_20d, rsi_14, last_date). One bullet may summarize period_counts.
3) **Macro link** — if economic_context is non-empty: mandatory 2–4 sentences \
tying at least one named series and its latest value/date to the asset (transmission: \
rates, growth, inflation, credit, or sector-relevant channel). If multiple tickers, \
cover the basket or state shared vs idiosyncratic exposure. Skip this subsection \
only when economic_context is absent/null/empty (then one line: macro not provided).
4) **Recent context** — 2–4 bullets max. Each bullet must name a development relevant \
to the question’s ticker(s), sector, or macro theme; include approximate recency \
(e.g., “within ~14 days”, “past quarter”) or mark **timing unverified** if unsure. \
Do not exceed four bullets. If you have no browsing/tools, preface this block once \
with: “(General knowledge — not a live news pull.)”
5) **Risks** — at least 2 bullets: (a) one technical risk tied to live JSON metrics \
(momentum, RSI, volume/smart_money/retail_fomo if present); (b) one macro or \
fundamental risk (macro must use economic_context when present; otherwise sector or \
company factors from label/description). For macro-only questions with no equity \
rows, give at least two macro/fundamental risks and omit the technical requirement \
only if no ticker metrics exist.
6) **Follow-ups** — 1–2 short prompts for deeper exploration.
7) **Sources** — exactly one block at the end, using this template (fill or write \
“none” / “not used” as appropriate):
   Sources:
   - Symbols: <comma-separated, or "none">
   - Equity data as-of: <summary.last_date per symbol; or "n/a">
   - FRED series used: <list series ids or titles from economic_context; or "none">
   - Retrieval: <used | not used>; if used, one clause on how it informed the answer

Regime classification (rule-based; use summary.session numerics only). Use \
momentum_20d and momentum_60d; if 60d is missing, note “partial data” and use only \
20d rules below where 60d is required. rsi_14 is the session RSI field. Apply \
**first matching rule** per ticker:
1) If momentum_20d is missing/null → **unclassified (insufficient momentum data)** \
(do not infer).
2) Else **trending up**: momentum_20d ≥ +0.03 AND (momentum_60d > 0 if 60d present; \
if 60d absent, require momentum_20d ≥ +0.05 instead).
3) Else **trending down**: momentum_20d ≤ −0.03 AND (momentum_60d < 0 if 60d present; \
if 60d absent, require momentum_20d ≤ −0.05 instead).
4) Else **bottoming**: momentum_20d < 0 AND (momentum_5d > 0 OR momentum_10d > 0) AND \
(rsi_14 < 40 when rsi_14 is present).
5) Else **ranging** (residual chop/drift not caught above).
If rsi_14 ≥ 70 on a **trending up** label, append “(stretched RSI)” in the brief answer.

Timing / advice:
- For “good time to invest” or timing questions: analytical and risk-aware only; \
never guarantee returns or instruct buy/sell.

User messages may include a fenced ```json``` block with keys: current_question, \
symbols_universe, symbol_resolution (when present), live_market_data, economic_context, \
retrieval_background, session_summary, indexing_note."""


# Shorter system prompt for prompt-export / external GPT (skip local LLM).
SYSTEM_PROMPT_PROMPT_EXPORT = """\
You are an analytical assistant for stock and market discussion. Ground every number \
in the JSON user payload; do not invent prices, returns, RSI, momentum, dates, or macro values.

Data priority: (1) live_market_data — authoritative for equity metrics \
(summary.session: momentum_*, rsi_14, last_date, etc.). \
(2) economic_context — macro from FRED when present; use series ids/values/dates exactly. \
(3) retrieval_background — background only; ignore if it conflicts with (1)–(2). \
(4) session_summary — continuity only.

Missing data: state explicitly per symbol if summary.error, rows==0, or fields absent. \
If economic_context is absent, say macro was not provided. If retrieval is empty, do not imply RAG.

Output: brief answer (2–4 sentences) with regime label per ticker; 3–7 metric bullets from live JSON; \
macro subsection (2–4 sentences) only if economic_context is non-empty, else one line; \
2–4 “recent context” bullets (mark as general knowledge if no live news); \
≥2 risk bullets; 1–2 follow-up prompts; Sources line with symbols, as-of dates, FRED series used, retrieval used/not.

Regime (summary.session, first match): unclassified if momentum_20d missing; \
trending up if 20d≥+0.03 and (60d>0 if present else 20d≥+0.05); \
trending down if 20d≤−0.03 and (60d<0 if present else 20d≤−0.05); \
bottoming if 20d<0 and (5d>0 or 10d>0) and rsi_14<40 when RSI present; else ranging. \
Append “(stretched RSI)” if rsi_14≥70 on trending up.

No personalized buy/sell advice. User JSON may include symbol_resolution (how tickers were chosen)."""


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
    economic_context: dict[str, Any] | None = None,
    symbol_resolution: dict[str, Any] | None = None,
) -> str:
    """Single user turn: intro line + fenced JSON; live_market_data is full parsed metrics."""
    include_summary = should_include_session_summary_for_payload(
        conversation_summary, recent_messages, prior_message_count,
    )
    try:
        live_data: Any = json.loads(context_json) if context_json.strip() else []
    except json.JSONDecodeError:
        live_data = {"_raw": context_json}

    payload: dict[str, Any] = {
        "schema_version": 1,
        "current_question": question,
        "symbols_universe": symbols,
        "live_market_data": live_data,
        "retrieval_background": rag_text.strip() if rag_text.strip() else None,
    }
    if symbol_resolution is not None:
        payload["symbol_resolution"] = symbol_resolution
    if economic_context is not None:
        payload["economic_context"] = economic_context
    if include_summary:
        payload["session_summary"] = conversation_summary.strip()
    if idx_err:
        payload["indexing_note"] = idx_err

    body = json.dumps(payload, ensure_ascii=False, indent=2)
    return (
        "Use the JSON payload below. Field live_market_data is authoritative for "
        "all prices, returns, RSI, and dates; if economic_context is included, it is "
        "authoritative for macro series from FRED; do not invent numbers.\n\n"
        f"```json\n{body}\n```"
    )


def build_structured_general_user_content(
    *,
    question: str,
    conversation_summary: str,
    recent_messages: list[dict[str, str]] | None,
    prior_message_count: int | None,
    economic_context: dict[str, Any] | None = None,
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
    if economic_context is not None:
        payload["economic_context"] = economic_context
    if include_summary:
        payload["session_summary"] = conversation_summary.strip()

    body = json.dumps(payload, ensure_ascii=False, indent=2)
    return (
        "Use the JSON payload below. live_market_data is empty (no ticker-specific "
        "fetch for this turn); if economic_context is included, it is authoritative "
        "for macro series from FRED.\n\n"
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
    # FRED macro block attached to the same JSON user payload (export / rebuild).
    economic_context: dict[str, Any] | None = None
    # How tickers were chosen (export / Streamlit fallback rebuild).
    symbol_resolution: dict[str, Any] | None = None
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


def _sectors_for_symbols(
    symbols: list[str],
    full_mapping: dict[str, tuple[str, str]],
    alias_index: dict[str, str],
    *,
    ticker_fred_sectors: dict[str, tuple[str, ...]] | None = None,
) -> list[str]:
    """Infer SECTOR_KEYWORDS bucket names from ticker metadata + optional fred_sectors in tickers JSON."""
    aliases_by_symbol: dict[str, list[str]] = {}
    for alias, sym in alias_index.items():
        aliases_by_symbol.setdefault(sym, []).append(alias)

    found: list[str] = []
    seen: set[str] = set()
    ov = ticker_fred_sectors or {}
    for sym in symbols:
        for tag in ov.get(sym, ()):
            if tag not in seen:
                found.append(tag)
                seen.add(tag)
        if sym not in full_mapping:
            continue
        label, desc = full_mapping[sym]
        text = f"{label} {desc}".lower()
        if aliases := aliases_by_symbol.get(sym):
            text = f"{text} {' '.join(aliases)}"
        text_words = set(re.split(r"[^a-z0-9&']+", text)) - {""}

        def phrase_in_text(phrase: str) -> bool:
            if " " in phrase:
                return phrase in text
            return phrase in text_words

        for sector_key, phrases in SECTOR_KEYWORDS.items():
            if sector_key in seen:
                continue
            if any(phrase_in_text(p) for p in phrases):
                found.append(sector_key)
                seen.add(sector_key)
    return found


_GENERAL_RE = re.compile(
    r"^\s*(what\s+(is|are|does)|explain\b|how\s+(does|do|is|are)\b|define\b)",
    re.IGNORECASE,
)


def select_fred_series_ids(
    question: str,
    registry: list[FredSeriesDef],
    *,
    recent_messages: list[dict[str, str]] | None,
    conversation_summary: str,
    ticker_macro: bool = False,
    inferred_sectors: frozenset[str] | None = None,
) -> list[str]:
    """
    Pick FRED series IDs in registry order.

    When ``ticker_macro`` is True (resolved stock tickers): include ``always`` series
    plus any whose ``sectors`` overlap ``inferred_sectors`` — optimized for ticker-only
    prompts without relying on macro keywords in the question.

    Otherwise (e.g. general questions): always-flag, keyword/context match, or raw FRED ID token.
    """
    inferred = inferred_sectors or frozenset()

    if ticker_macro:
        chosen: list[str] = []
        seen: set[str] = set()
        for entry in registry:
            sid = entry.series_id
            if sid in seen:
                continue
            if entry.always:
                chosen.append(sid)
                seen.add(sid)
                continue
            if entry.sectors and inferred & frozenset(entry.sectors):
                chosen.append(sid)
                seen.add(sid)
        return chosen[: config.MAX_FRED_SERIES_PER_QUESTION]

    parts: list[str] = [question]
    if recent_messages:
        parts.extend(
            str(m.get("content", ""))
            for m in recent_messages[-MAX_RECENT_MESSAGES:]
            if isinstance(m, dict)
        )
    if conversation_summary:
        parts.append(conversation_summary)
    haystack = "\n".join(parts).lower()

    tokens_upper = re.split(r"[^A-Z0-9]+", question.upper())
    token_set = {t for t in tokens_upper if t}

    chosen = []
    seen = set()
    for entry in registry:
        sid = entry.series_id
        if sid in seen:
            continue
        if entry.always:
            chosen.append(sid)
            seen.add(sid)
            continue
        if entry.keywords and any(kw in haystack for kw in entry.keywords):
            chosen.append(sid)
            seen.add(sid)
            continue
        if sid.upper() in token_set:
            chosen.append(sid)
            seen.add(sid)
    return chosen[: config.MAX_FRED_SERIES_PER_QUESTION]


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
    use_fred: bool = True,
    conversation_summary: str = "",
    recent_messages: list[dict[str, str]] | None = None,
    prior_message_count: int | None = None,
    step_callback: StepCallback | None = None,
    skip_llm: bool = False,
) -> QAResult:
    t_total = time.perf_counter()
    timings: dict[str, float] = {}
    q_preview = question[:80] + ("…" if len(question) > 80 else "")
    log.info("[pipeline] Question: \"%s\"", q_preview)

    path = tickers_json_path or config.TICKERS_JSON
    full_mapping = load_ticker_mapping(path)
    alias_index = load_alias_index(path)
    ticker_fred_sectors = load_ticker_fred_sectors(path)

    # --- 1. Deterministic ticker matching (instant) ---
    _step(step_callback, label="Matching tickers…")
    t0 = time.perf_counter()

    universe = set(full_mapping.keys())
    sym_regex = _extract_symbols_regex(question, universe)
    sym_alias = _extract_by_alias(question, alias_index)
    sym_sector = _extract_sector(question, full_mapping, alias_index)
    question_symbols = _merge_unique(sym_regex, sym_alias, sym_sector)

    # If this turn has no tickers in the question, carry over from recent context.
    ctx_symbols: list[str] = []
    if not question_symbols:
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

    # Single-primary-symbol default: one ticker in the question → fetch only that
    # ticker; ignore context carry-over so the prompt stays focused.
    if question_symbols:
        if len(question_symbols) == 1:
            symbols = question_symbols[:1]
        else:
            symbols = question_symbols[:MAX_TICKERS_PER_QUESTION]
    else:
        symbols = ctx_symbols[:MAX_TICKERS_PER_QUESTION]

    timings["match"] = round(time.perf_counter() - t0, 4)

    match_parts: list[str] = []
    if sym_regex:
        match_parts.append(f"regex: {', '.join(sym_regex)}")
    if sym_alias:
        match_parts.append(f"alias: {', '.join(sym_alias)}")
    if sym_sector:
        match_parts.append(f"sector: {', '.join(sym_sector)}")
    if ctx_symbols and not question_symbols:
        match_parts.append(f"context: {', '.join(ctx_symbols)}")
    elif ctx_symbols and question_symbols:
        match_parts.append(f"context_ignored: {', '.join(ctx_symbols)}")
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

            economic_ctx: dict[str, Any] | None = None
            if use_fred and config.fred_api_key():
                try:
                    reg = load_fred_series_registry()
                    fred_ids = select_fred_series_ids(
                        question,
                        reg,
                        recent_messages=recent_messages,
                        conversation_summary=conversation_summary,
                    )
                    if fred_ids:
                        _step(step_callback, label="Fetching macro (FRED)…")
                        t_f = time.perf_counter()
                        economic_ctx = build_economic_context(
                            fred_ids, config.fred_api_key(),
                        )
                        if economic_ctx is not None:
                            economic_ctx = {
                                **economic_ctx,
                                "meta": {
                                    "mode": "keyword",
                                    "series_requested": list(fred_ids),
                                    "sectors_used_for_extras": [],
                                },
                            }
                        timings["fred"] = round(time.perf_counter() - t_f, 3)
                        _step(
                            step_callback,
                            detail=f"FRED: {len(fred_ids)} series ({timings['fred']}s)",
                        )
                except Exception as e:  # noqa: BLE001
                    log.warning("[fred]     %s", e)

            sys_prompt = SYSTEM_PROMPT_PROMPT_EXPORT if skip_llm else SYSTEM_PROMPT
            chat_msgs: list[dict[str, str]] = [
                {"role": "system", "content": sys_prompt},
            ]
            if recent_messages:
                chat_msgs.extend(recent_messages[-MAX_RECENT_MESSAGES:])
            general_content = build_structured_general_user_content(
                question=question,
                conversation_summary=conversation_summary,
                recent_messages=recent_messages,
                prior_message_count=prior_message_count,
                economic_context=economic_ctx,
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
                    economic_context=economic_ctx,
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
                    economic_context=economic_ctx,
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
                economic_context=economic_ctx,
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

    economic_ctx_stock: dict[str, Any] | None = None
    if use_fred and config.fred_api_key():
        try:
            reg_s = load_fred_series_registry()
            inferred_raw = _sectors_for_symbols(
                list(sub.keys()),
                full_mapping,
                alias_index,
                ticker_fred_sectors=ticker_fred_sectors,
            )
            # Sector-specific FRED series only for sectors that appear in registry (lean + confident).
            allowed_sector_tags = frozenset(
                s for entry in reg_s for s in entry.sectors
            )
            inferred_sec = frozenset(s for s in inferred_raw if s in allowed_sector_tags)
            fred_ids_s = select_fred_series_ids(
                question,
                reg_s,
                recent_messages=recent_messages,
                conversation_summary=conversation_summary,
                ticker_macro=True,
                inferred_sectors=inferred_sec,
            )
            if fred_ids_s:
                _step(step_callback, label="Fetching macro (FRED)…")
                t_fs = time.perf_counter()
                economic_ctx_stock = build_economic_context(
                    fred_ids_s, config.fred_api_key(),
                )
                if economic_ctx_stock is not None:
                    economic_ctx_stock = {
                        **economic_ctx_stock,
                        "meta": {
                            "mode": "ticker_macro",
                            "series_requested": list(fred_ids_s),
                            "sectors_used_for_extras": sorted(inferred_sec),
                        },
                    }
                timings["fred"] = round(time.perf_counter() - t_fs, 3)
                _step(
                    step_callback,
                    detail=f"FRED: {len(fred_ids_s)} series ({timings['fred']}s)",
                )
        except Exception as e:  # noqa: BLE001
            log.warning("[fred]     %s", e)

    # --- 4. Build prompt and call LLM ---
    _step(step_callback, label="Generating response…")
    rag_block = rag_text.strip() if rag_text else "(none)"

    symbol_resolution: dict[str, Any] = {
        "question_tickers": question_symbols,
        "tickers_in_prompt": list(sub.keys()),
        "source": (
            "single_question_ticker"
            if len(question_symbols) == 1
            else (
                "multi_question_ticker"
                if len(question_symbols) > 1
                else ("context_carryover" if ctx_symbols else "sector_or_unknown")
            )
        ),
    }
    user_content = build_structured_stock_user_content(
        question=question,
        context_json=ctx,
        rag_text=rag_text,
        symbols=list(sub.keys()),
        conversation_summary=conversation_summary,
        recent_messages=recent_messages,
        prior_message_count=prior_message_count,
        idx_err=idx_err,
        economic_context=economic_ctx_stock,
        symbol_resolution=symbol_resolution,
    )

    sys_prompt_stock = SYSTEM_PROMPT_PROMPT_EXPORT if skip_llm else SYSTEM_PROMPT
    chat_msgs = [{"role": "system", "content": sys_prompt_stock}]
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
            economic_context=economic_ctx_stock,
            symbol_resolution=symbol_resolution,
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
            economic_context=economic_ctx_stock,
            symbol_resolution=symbol_resolution,
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
        economic_context=economic_ctx_stock,
        symbol_resolution=symbol_resolution,
        export_messages=list(chat_msgs),
    )
