"""
Streamlit: chat-first stock assistant (Ollama + live Yahoo metrics + RAG).

Run: streamlit run src/streamlit_app.py
"""

from __future__ import annotations

import logging
import os
import json
import sys
import time as _time
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components


def _bundle_root() -> Path:
    """Project root in dev; PyInstaller ``_MEIPASS`` when frozen (``assets/`` lives here)."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent


_ROOT = _bundle_root()
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_icon_png = _ROOT / "assets" / "icon.png"
_icon_ico = _ROOT / "assets" / "icon.ico"
_page_icon: str | None = None
if _icon_png.exists():
    _page_icon = str(_icon_png.resolve())
elif _icon_ico.exists():
    _page_icon = str(_icon_ico.resolve())

from src import config  # noqa: E402
from src.config import (  # noqa: E402
    DEFAULT_YF_PERIOD,
    TICKERS_JSON,
    load_ticker_mapping,
)
from src.fetcher import fetch_all_tickers  # noqa: E402
from src.logging_utils import log_qa_result  # noqa: E402
from src.memory import build_summary, should_summarize  # noqa: E402
from src.pipeline import (  # noqa: E402
    SKIP_LLM_PLACEHOLDER,
    SYSTEM_PROMPT,
    answer_question,
    build_structured_general_user_content,
    build_structured_stock_user_content,
)
from src.ollama_runtime import ensure_ollama_running  # noqa: E402
from src.rag import ingest_fetch_results  # noqa: E402

_log = logging.getLogger("stock_qa")

OLLAMA_URL = os.environ.get("OLLAMA_BASE_URL", config.OLLAMA_BASE_URL).strip()

_page_cfg: dict = {
    "page_title": "Stock assistant",
    "layout": "wide",
    "initial_sidebar_state": "collapsed",
}
if _page_icon:
    _page_cfg["page_icon"] = _page_icon
st.set_page_config(**_page_cfg)

st.markdown(
    """
    <style>
    .stApp a:link, .stApp a:visited { color: #3B82F6 !important; }
    .stApp a:hover { color: #22D3EE !important; }
    </style>
    """,
    unsafe_allow_html=True,
)

# Start Ollama once per session (same as original app).
if "ollama_ready" not in st.session_state:
    with st.spinner("Starting Ollama…"):
        st.session_state["ollama_ready"] = ensure_ollama_running(OLLAMA_URL)
    if not st.session_state["ollama_ready"]:
        st.warning(
            "Ollama is not reachable and could not be started automatically. "
            "Install it from https://ollama.com or run `ollama serve` in a terminal, then refresh this page."
        )

# One-time warm-up: fetch + index (original behaviour; robust errors like current main).
if os.environ.get("SKIP_YF_WARM") == "1":
    st.session_state["yf_warm_done"] = True
elif "yf_warm_done" not in st.session_state:
    with st.spinner("Pre-loading market data and indexing for RAG (once per session)…"):
        _t_warm = _time.perf_counter()
        _log.info("[warm-up]  Pre-loading market data for all tickers (2y)…")
        warm_msgs: list[str] = []
        try:
            _warm_map = load_ticker_mapping(TICKERS_JSON)
            _warm_results = fetch_all_tickers(_warm_map, period=DEFAULT_YF_PERIOD)
            _log.info("[warm-up]  Fetch done (%.1fs), indexing to Chroma…", _time.perf_counter() - _t_warm)
        except Exception as e:  # noqa: BLE001
            _log.error("[warm-up]  Fetch failed (%.1fs): %s", _time.perf_counter() - _t_warm, e)
            warm_msgs.append(f"market fetch: {e}")
        else:
            try:
                _indexed, _idx_err = ingest_fetch_results(
                    _warm_results,
                    period=DEFAULT_YF_PERIOD,
                    ollama_base_url=OLLAMA_URL,
                    embed_model=config.OLLAMA_EMBED_MODEL,
                )
                if _idx_err:
                    _log.warning("[warm-up]  RAG indexing issue: %s", _idx_err)
                    warm_msgs.append(f"RAG index: {_idx_err}")
                else:
                    _log.info("[warm-up]  Indexed %d chunk(s) into Chroma", _indexed)
            except Exception as e:  # noqa: BLE001
                _log.error("[warm-up]  RAG ingest failed: %s", e)
                warm_msgs.append(f"RAG: {e}")
            _log.info("[warm-up]  Done (%.1fs)", _time.perf_counter() - _t_warm)
        if warm_msgs:
            st.session_state["yf_warm_error"] = "; ".join(warm_msgs)
    st.session_state["yf_warm_done"] = True

if err := st.session_state.get("yf_warm_error"):
    st.warning(
        "Warm-up note (the app still works; Yahoo history uses the last session when there is no bar for today yet): "
        f"{err}"
    )
    del st.session_state["yf_warm_error"]

if "messages" not in st.session_state:
    st.session_state.messages = []
if "conversation_summary" not in st.session_state:
    st.session_state.conversation_summary = ""

with st.sidebar:
    st.header("Model")
    ollama_model = st.text_input(
        "Ollama model",
        value=config.OLLAMA_MODEL,
        help="Chat model, e.g. llama3.2",
    )
    prompt_only = st.checkbox(
        "Prompt generator (skip local answer)",
        value=os.environ.get("PROMPT_ONLY", "1").strip() == "1",
        help="Fetch + RAG only; no Ollama chat. Fastest path to **Export to GPT**.",
    )

USE_RAG = True
INDEX_METRICS_TO_RAG = os.environ.get("INDEX_RAG_EACH_ASK", "0").strip() == "1"

c1, c2 = st.columns([4, 1])
with c1:
    st.title("Stock assistant")
with c2:
    if st.button("Clear chat", use_container_width=True):
        st.session_state.messages = []
        st.session_state.pop("qa_result", None)
        st.session_state.conversation_summary = ""
        st.rerun()

st.caption(
    "Ask in plain language. The app uses **live Yahoo Finance metrics** and **Chroma RAG** "
    f"({DEFAULT_YF_PERIOD} window; cache ~1h). Sidebar: **Prompt generator** skips the local LLM "
    "and only builds data for **Export to GPT**. "
    "If **Ollama never uses your GPU** (CPU-only, very slow), run `python -m src.diagnose_gpu` in a terminal. "
    "Not financial advice."
)

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

if prompt := st.chat_input(
    placeholder="Ask about a stock — e.g. Is it a good time to look at NVDA? What should I watch?",
):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    prior = st.session_state.messages[:-1]
    recent = prior[-4:] if prior else []

    assistant_text = ""
    with st.chat_message("assistant"):
        try:
            with st.status("Processing your question…", expanded=True) as status:
                def on_step(label: str, detail: str) -> None:
                    if label:
                        status.update(label=label)
                    if detail:
                        status.write(detail)

                res = answer_question(
                    prompt.strip(),
                    period=DEFAULT_YF_PERIOD,
                    tickers_json_path=None,
                    model=ollama_model.strip() or None,
                    ollama_base_url=OLLAMA_URL,
                    embedding_model=config.OLLAMA_EMBED_MODEL,
                    use_rag=USE_RAG,
                    index_metrics_to_rag=INDEX_METRICS_TO_RAG,
                    conversation_summary=st.session_state.conversation_summary,
                    recent_messages=recent,
                    prior_message_count=len(prior),
                    step_callback=on_step,
                    skip_llm=prompt_only,
                )

                total = res.timings.get("total", 0)
                if res.error and not res.answer:
                    status.update(label=f"Completed with errors ({total}s)", state="error")
                else:
                    status.update(label=f"Done in {total}s", state="complete", expanded=False)

            st.session_state["qa_result"] = res
            try:
                log_qa_result(
                    prompt.strip(),
                    res,
                    period=DEFAULT_YF_PERIOD,
                    use_rag=USE_RAG,
                )
            except Exception:
                pass
            if res.error and not res.answer:
                assistant_text = f"**Could not complete the request.**\n\n{res.error}"
            else:
                assistant_text = res.answer or "_No text returned._"
                if prompt_only and assistant_text == SKIP_LLM_PLACEHOLDER:
                    assistant_text = (
                        "**Prompt ready.** Open **Export to GPT** below, copy the "
                        "Messages JSON or single prompt, then paste into ChatGPT."
                    )
                if res.symbols_used:
                    assistant_text += f"\n\n---\n_Symbols with live data: {', '.join(res.symbols_used)}._"
                if res.rag_error:
                    assistant_text += f"\n\n_Note (retrieval): {res.rag_error}_"
            st.markdown(assistant_text)
        except Exception as e:  # noqa: BLE001
            assistant_text = f"**Error:** {e}"
            st.markdown(assistant_text)

    st.session_state.messages.append({"role": "assistant", "content": assistant_text})

    user_count = sum(1 for m in st.session_state.messages if m["role"] == "user")
    if should_summarize(user_count) and not prompt_only:
        st.session_state.conversation_summary = build_summary(
            st.session_state.messages,
            previous_summary=st.session_state.conversation_summary,
            model=ollama_model.strip() or None,
            ollama_base_url=OLLAMA_URL,
        )

    st.rerun()

with st.expander("Technical details (last reply)", expanded=False):
    if st.session_state.get("qa_result"):
        res = st.session_state["qa_result"]
        if res.timings:
            cols = st.columns(len(res.timings))
            for col, (step_name, secs) in zip(cols, res.timings.items()):
                col.metric(step_name, f"{secs}s")
        if res.rag_context:
            st.text_area("RAG context", res.rag_context, height=120)
        st.code(res.context_json[:12000] + ("…" if len(res.context_json) > 12000 else ""), language="json")
    else:
        st.info("Ask a question to see live JSON and retrieval context here.")


def _messages_to_paste_text(messages: list[dict[str, str]]) -> str:
    chunks: list[str] = []
    for m in messages:
        role = (m.get("role") or "user").upper()
        chunks.append(f"{role}:\n{m.get('content', '')}")
    return "\n\n".join(chunks)


def _build_gpt_export_prompt() -> str:
    res = st.session_state.get("qa_result")
    if not res:
        return ""

    export = getattr(res, "export_messages", None)
    if export:
        return _messages_to_paste_text(export)

    msgs_hist = st.session_state.messages
    if len(msgs_hist) >= 2 and msgs_hist[-1].get("role") == "assistant":
        prior_messages = msgs_hist[:-2]
        question = msgs_hist[-2].get("content", "").strip()
    else:
        prior_messages = msgs_hist[:-1]
        question = (msgs_hist[-1].get("content", "") if msgs_hist else "").strip()
    recent_fb = prior_messages[-4:] if prior_messages else []
    prior_count = len(prior_messages)
    conv_summary = (st.session_state.get("conversation_summary") or "").strip()
    rag_block = (getattr(res, "rag_context", "") or "").strip()
    live_json = (getattr(res, "context_json", "") or "").strip() or "{}"

    if live_json.strip() in ("", "{}"):
        user = build_structured_general_user_content(
            question=question,
            conversation_summary=conv_summary,
            recent_messages=recent_fb,
            prior_message_count=prior_count,
        )
    else:
        user = build_structured_stock_user_content(
            question=question,
            context_json=live_json,
            rag_text=rag_block,
            symbols=list(getattr(res, "symbols_used", []) or []),
            conversation_summary=conv_summary,
            recent_messages=recent_fb,
            prior_message_count=prior_count,
            idx_err=None,
        )
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(recent_fb)
    messages.append({"role": "user", "content": user})
    return _messages_to_paste_text(messages)


def _render_copy_gpt_prompt_button(prompt_text: str) -> None:
    if not prompt_text:
        return
    js_str = json.dumps(prompt_text)
    components.html(
        f"""
        <div>
          <button id="gpt-copy-btn" type="button"
            style="padding:0.45rem 1rem; border-radius:6px; cursor:pointer;
            font-family:system-ui,sans-serif; font-size:14px;">
            Copy GPT prompt
          </button>
          <span id="gpt-copy-feedback" style="margin-left:10px; color:#2e7d32; font-size:13px;"></span>
          <script>
            const txt = {js_str};
            const btn = document.getElementById("gpt-copy-btn");
            const fb = document.getElementById("gpt-copy-feedback");
            btn.addEventListener("click", () => {{
              navigator.clipboard.writeText(txt).then(() => {{
                fb.textContent = "Copied!";
                fb.style.color = "#2e7d32";
                setTimeout(() => {{ fb.textContent = ""; }}, 2500);
              }}).catch(() => {{
                fb.textContent = "Copy failed — select the text in the box above.";
                fb.style.color = "#c62828";
              }});
            }});
          </script>
        </div>
        """,
        height=52,
    )


with st.expander("Export to GPT (last reply)", expanded=False):
    if st.session_state.get("qa_result"):
        prompt_text = _build_gpt_export_prompt()
        st.text_area(
            "Preview",
            prompt_text,
            height=260,
        )
        _render_copy_gpt_prompt_button(prompt_text)
    else:
        st.info("Ask a question first, then export it to ChatGPT here.")
