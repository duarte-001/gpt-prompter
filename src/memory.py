"""Rolling conversation memory via periodic LLM summaries."""

from __future__ import annotations

import logging
import time

from src.llm import ollama_chat

log = logging.getLogger("stock_qa")

SUMMARY_INTERVAL = 3

_SUMMARIZE_SYSTEM = (
    "You are a conversation summarizer. Produce a concise summary (under 200 "
    "words) of the stock-related conversation so far. Focus on: which stocks "
    "or sectors were discussed, key data points or conclusions, and any user "
    "preferences expressed. Be factual and brief."
)


def should_summarize(user_message_count: int) -> bool:
    """True when it is time to update the rolling summary."""
    return user_message_count > 0 and user_message_count % SUMMARY_INTERVAL == 0


def build_summary(
    messages: list[dict[str, str]],
    previous_summary: str = "",
    *,
    model: str | None = None,
    ollama_base_url: str | None = None,
) -> str:
    """Compress conversation history into a rolling summary via LLM."""
    if not messages:
        return previous_summary

    convo_lines: list[str] = []
    for m in messages:
        role = m["role"].upper()
        content = m["content"]
        if len(content) > 800:
            content = content[:800] + "…"
        convo_lines.append(f"{role}: {content}")
    convo_text = "\n".join(convo_lines)

    parts: list[str] = []
    if previous_summary:
        parts.append(f"Previous summary:\n{previous_summary}\n")
    parts.append(f"Recent messages:\n{convo_text}\n")
    parts.append(
        "Produce an updated summary combining the previous summary (if any) "
        "with these new messages."
    )

    log.info("[memory]   Compressing conversation (%d messages)…", len(messages))
    t0 = time.perf_counter()
    try:
        result = ollama_chat(
            [
                {"role": "system", "content": _SUMMARIZE_SYSTEM},
                {"role": "user", "content": "\n".join(parts)},
            ],
            model=model,
            base_url=ollama_base_url,
            timeout_s=30.0,
            log_context="memory_summary",
        )
        elapsed = time.perf_counter() - t0
        log.info(
            "[memory]   Summary ready: %d words (wall %.1fs incl. logs above)",
            len(result.split()),
            elapsed,
        )
        return result
    except Exception as exc:  # noqa: BLE001
        elapsed = time.perf_counter() - t0
        log.warning(
            "[memory]   Summary failed (%.1fs), keeping previous: %s",
            elapsed, exc,
        )
        return previous_summary
