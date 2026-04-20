"""
Streamlit: chat-first stock assistant (Ollama + live Yahoo metrics + RAG).

Run: streamlit run src/streamlit_app.py
"""

from __future__ import annotations

import base64
import logging
import os
import json
import re
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


def _favicon_data_uri(path: Path, mime: str = "image/png") -> str | None:
    """Encode an icon file as a base64 data URI for inline <link> injection."""
    if not path.exists():
        return None
    try:
        raw = path.read_bytes()
        b64 = base64.b64encode(raw).decode("ascii")
        return f"data:{mime};base64,{b64}"
    except OSError:
        return None


_favicon_png_uri = _favicon_data_uri(_icon_png)
_favicon_ico_uri = _favicon_data_uri(_icon_ico, "image/x-icon")

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
BRAND_PRIMARY = "#3B82F6"
BRAND_ACCENT = "#22D3EE"
BRAND_TEXT = "#E5E7EB"
BRAND_BASE = "#0F172A"
BRAND_SURFACE = "#111C33"
BRAND_BORDER = "#1E293B"
BRAND_DANGER = "#F87171"


def _env_prompt_only_default_on() -> bool:
    """True when local Ollama chat is off (default: PROMPT_ONLY unset or ``1``)."""
    return os.environ.get("PROMPT_ONLY", "1").strip() != "0"


def _skip_ollama_boot() -> bool:
    """Skip starting/reachability checks for Ollama when only prompt export is used."""
    if os.environ.get("SKIP_OLLAMA_BOOT", "").strip() == "1":
        return True
    return _env_prompt_only_default_on()

_page_cfg: dict = {
    "page_title": "Stock Assistant",
    "layout": "wide",
    "initial_sidebar_state": "collapsed",
}
if _page_icon:
    _page_cfg["page_icon"] = _page_icon
st.set_page_config(**_page_cfg)

st.markdown(
    f"""
    <style>
    /* Option A slate — enforced here so the UI stays dark if .streamlit/config.toml
       is not picked up (e.g. wrong cwd before launcher fix) or the host forces light chrome. */
    html, body, .stApp, [data-testid="stAppViewContainer"], [data-testid="stAppViewContainer"] > section {{
      background-color: {BRAND_BASE} !important;
    }}
    [data-testid="stHeader"] {{
      background-color: rgba(15, 23, 42, 0.92) !important;
    }}
    .stApp a:link, .stApp a:visited {{ color: #3B82F6 !important; }}
    .stApp a:hover {{ color: #22D3EE !important; }}
    .welcome-card {{
      border: 1px solid #1E293B;
      border-radius: 12px;
      padding: 0.9rem 1rem;
      background: #111C33;
      margin-bottom: 0.5rem;
    }}
    .welcome-card ul {{
      margin: 0.35rem 0 0.25rem 1rem;
      padding: 0;
    }}

    /* Chat avatars / icons (Streamlit chrome) */
    div[data-testid="stChatMessageAvatar"] {{
      background-color: {BRAND_SURFACE} !important;
      border: 1px solid {BRAND_BORDER} !important;
    }}
    /* Streamlit sets the colored badge on an inner div; override that too. */
    div[data-testid="stChatMessageAvatar"] > div {{
      background-color: {BRAND_SURFACE} !important;
      border: 1px solid {BRAND_BORDER} !important;
    }}
    /* Streamlit uses different SVGs/strategies (fill vs stroke vs currentColor) per role. */
    div[data-testid="stChatMessageAvatar"] svg,
    div[data-testid="stChatMessageAvatar"] svg * {{
      color: {BRAND_ACCENT} !important;
      fill: {BRAND_ACCENT} !important;
      stroke: {BRAND_ACCENT} !important;
    }}
    </style>
    """,
    unsafe_allow_html=True,
)

# Override Streamlit's single-size favicon with proper multi-size <link> tags so the
# browser / app-mode window picks the sharpest match for its DPI instead of scaling.
_fav_links: list[str] = []
if _favicon_ico_uri:
    _fav_links.append(f'<link rel="icon" type="image/x-icon" href="{_favicon_ico_uri}">')
if _favicon_png_uri:
    _fav_links.append(f'<link rel="icon" type="image/png" sizes="256x256" href="{_favicon_png_uri}">')
    _fav_links.append(f'<link rel="apple-touch-icon" sizes="256x256" href="{_favicon_png_uri}">')
if _fav_links:
    _fav_js = "".join(_fav_links).replace('"', '\\"')
    st.markdown(
        f"""<script>
        (function() {{
            document.querySelectorAll('link[rel*="icon"]').forEach(function(el) {{ el.remove(); }});
            document.head.insertAdjacentHTML('beforeend', "{_fav_js}");
        }})();
        </script>""",
        unsafe_allow_html=True,
    )

if "boot_ready" not in st.session_state:
    st.session_state["boot_ready"] = False
if "boot_stage" not in st.session_state:
    st.session_state["boot_stage"] = "init"
if "boot_started_at" not in st.session_state:
    st.session_state["boot_started_at"] = _time.perf_counter()
if "boot_notes" not in st.session_state:
    st.session_state["boot_notes"] = []


def _warm_fetch_hint(msg: str) -> str:
    """Append actionable context when Yahoo / TLS fails (often AV or corporate proxy)."""
    low = msg.lower()
    hint = ""
    if any(
        x in low
        for x in (
            "ssl",
            "certificate",
            "cert",
            "tls",
            "connection",
            "timed out",
            "timeout",
            "10054",
            "10060",
            "refused",
            "403",
            "401",
            "blocked",
        )
    ):
        hint = (
            " If this persists, allow this app (or Python) through the firewall/antivirus HTTPS scan, "
            "or try another network / mobile hotspot — Yahoo Finance must be reachable for live data."
        )
    return f"market fetch: {msg}{hint}"


def _render_welcome_boot() -> None:
    st.title("Stock Assistant")
    st.caption(
        "Intelligent prompt-generation for financial reasoning, grounded in reliable live data and retrieval."
    )
    st.markdown(
        """
        <div class="welcome-card">
          <strong>Preparing your workspace</strong>
          <ul>
            <li>Loading tickers and market data</li>
            <li>Computing retrieval context (RAG)</li>
            <li>Preparing prompt generator</li>
          </ul>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.caption("Not financial advice.")


def _run_boot_sequence() -> None:
    notes: list[str] = []
    warm_msgs: list[str] = []
    # Keep boot UI non-interactive (no expanders/clickable status panels).
    progress = st.progress(0)
    label = st.empty()
    details = st.empty()

    def _set(step: str, pct: int, detail: str | None = None) -> None:
        label.markdown(f"**{step}…**")
        progress.progress(max(0, min(100, pct)))
        if detail:
            details.caption(detail)

    st.session_state["boot_stage"] = "starting_ollama"
    _set("Starting model runtime", 15)
    if "ollama_ready" not in st.session_state:
        if _skip_ollama_boot():
            st.session_state["ollama_ready"] = False
            st.session_state["ollama_boot_skipped"] = True
            _log.info("[boot]     Skipping Ollama (prompt-only / SKIP_OLLAMA_BOOT=1)")
        else:
            st.session_state["ollama_boot_skipped"] = False
            st.session_state["ollama_ready"] = ensure_ollama_running(OLLAMA_URL)
    if st.session_state.get("ollama_boot_skipped"):
        _set("Starting model runtime", 25, "Using prompt export path — local model runtime not required.")
    elif st.session_state["ollama_ready"]:
        _set("Starting model runtime", 25, "Model runtime is ready.")
    else:
        notes.append(
            "Ollama is not reachable and could not be started automatically. "
            "Install it from https://ollama.com or run `ollama serve` in a terminal."
        )
        _set("Starting model runtime", 25, "Model runtime is not reachable. You can still use prompt export mode.")

    # Warm-up scope for the welcome page: ticker universe load + RAG computation/index.
    if os.environ.get("SKIP_YF_WARM") == "1":
        st.session_state["yf_warm_done"] = True
        _set("Warm-up", 35, "Warm-up skipped by environment setting.")
    elif "yf_warm_done" not in st.session_state:
        st.session_state["boot_stage"] = "loading_tickers"
        _set("Loading tickers and market data", 45)
        _t_warm = _time.perf_counter()
        _log.info("[warm-up]  Pre-loading market data for all tickers (2y)…")
        _warm_results = None
        try:
            _warm_map = load_ticker_mapping(TICKERS_JSON)
            _warm_results = fetch_all_tickers(_warm_map, period=DEFAULT_YF_PERIOD)
            _log.info("[warm-up]  Fetch done (%.1fs)", _time.perf_counter() - _t_warm)
        except Exception as e:  # noqa: BLE001
            _log.error("[warm-up]  Fetch failed (%.1fs): %s", _time.perf_counter() - _t_warm, e)
            warm_msgs.append(_warm_fetch_hint(str(e)))

        if _warm_results is not None and st.session_state.get("ollama_ready"):
            st.session_state["boot_stage"] = "computing_rag"
            _set("Computing RAG context", 70)
            _log.info("[warm-up]  Indexing to Chroma…")
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
        elif _warm_results is not None:
            _set("Computing RAG context", 70, "Skipped — Ollama not used for this session.")
            _log.info("[warm-up]  Skipping RAG indexing (Ollama not available / skipped at boot)")
        if _warm_results is not None:
            _log.info("[warm-up]  Done (%.1fs)", _time.perf_counter() - _t_warm)
        st.session_state["yf_warm_done"] = True

    if warm_msgs:
        st.session_state["yf_warm_error"] = "; ".join(warm_msgs)
    st.session_state["boot_notes"] = notes
    st.session_state["boot_stage"] = "ready"
    st.session_state["boot_ready"] = True
    _set("Workspace ready", 100)


if not st.session_state["boot_ready"]:
    _render_welcome_boot()
    _run_boot_sequence()
    st.rerun()
    st.stop()

if boot_notes := st.session_state.get("boot_notes"):
    st.warning("Startup note: " + " ".join(boot_notes))
    st.session_state["boot_notes"] = []

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
        "Prompt-only mode (skip local answer)",
        value=os.environ.get("PROMPT_ONLY", "1").strip() == "1",
        help="Fetch + RAG only; no Ollama chat. Fastest path to **Export to GPT**.",
    )

USE_RAG = True
INDEX_METRICS_TO_RAG = os.environ.get("INDEX_RAG_EACH_ASK", "0").strip() == "1"

c1, c2 = st.columns([4, 1])
with c1:
    st.title("Stock Assistant")
with c2:
    if st.button("Clear chat", use_container_width=True, type="secondary"):
        st.session_state.messages = []
        st.session_state.pop("qa_result", None)
        st.session_state.conversation_summary = ""
        st.rerun()

st.caption("Ask in plain language for a clear, data-grounded market read. Not financial advice.")

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])


def _friendly_status_label(label: str) -> str:
    raw = (label or "").strip()
    low = raw.lower()
    if "fetch" in low or "market" in low:
        return "Fetching live market data"
    if "rag" in low or "retriev" in low or "index" in low:
        return "Preparing retrieval context"
    if "llm" in low or "answer" in low or "draft" in low:
        return "Drafting your answer"
    return raw or "Processing your question"


prompt = st.chat_input(placeholder="Ask your question.")
if prompt:
    prompt_text = prompt.strip()

    st.session_state.messages.append({"role": "user", "content": prompt_text})
    with st.chat_message("user"):
        st.markdown(prompt_text)

    prior = st.session_state.messages[:-1]
    recent = prior[-4:] if prior else []

    assistant_text = ""
    if not prompt_only and not st.session_state.get("ollama_ready"):
        with st.spinner("Connecting to Ollama…"):
            st.session_state["ollama_ready"] = ensure_ollama_running(OLLAMA_URL)
        if not st.session_state["ollama_ready"]:
            assistant_text = (
                "**Ollama is not reachable.** Install from [ollama.com](https://ollama.com), "
                "start the Ollama app, or turn on **Prompt-only mode** in the sidebar to use "
                "export without a local model."
            )

    with st.chat_message("assistant"):
        if assistant_text:
            st.markdown(assistant_text)
        else:
            try:
                with st.status("Working on your question…", expanded=True) as status:
                    def on_step(label: str, detail: str) -> None:
                        if label:
                            status.update(label=_friendly_status_label(label))
                        if detail:
                            status.write(detail)

                    res = answer_question(
                        prompt_text,
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
    if should_summarize(user_count) and not prompt_only and st.session_state.get("ollama_ready"):
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
            timing_items = list(res.timings.items())
            max_cols = 3
            for start in range(0, len(timing_items), max_cols):
                cols = st.columns(max_cols)
                for col, (step_name, secs) in zip(cols, timing_items[start : start + max_cols]):
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
            style="padding:0.45rem 1rem; border-radius:8px; cursor:pointer;
            font-family:system-ui,sans-serif; font-size:14px; color:{BRAND_TEXT};
            background:{BRAND_SURFACE}; border:1px solid {BRAND_BORDER};">
            Copy GPT prompt
          </button>
          <span id="gpt-copy-feedback" style="margin-left:10px; color:{BRAND_PRIMARY}; font-size:13px;"></span>
          <script>
            const txt = {js_str};
            const btn = document.getElementById("gpt-copy-btn");
            const fb = document.getElementById("gpt-copy-feedback");
            btn.addEventListener("click", () => {{
              navigator.clipboard.writeText(txt).then(() => {{
                fb.textContent = "Copied!";
                fb.style.color = "{BRAND_ACCENT}";
                setTimeout(() => {{ fb.textContent = ""; }}, 2500);
              }}).catch(() => {{
                fb.textContent = "Copy failed — select the text in the box above.";
                fb.style.color = "{BRAND_DANGER}";
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
