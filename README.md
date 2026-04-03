# Stock Q&A Prompt Generator

Deterministic stock-data pipeline that prepares a high-quality prompt for external GPT (ChatGPT), with optional local Ollama answering and optional RAG enrichment.

## What This App Does

- Matches symbols from user questions using deterministic logic (ticker regex, aliases, sector keywords, context carry-over).
- Fetches live market metrics from Yahoo Finance for matched symbols.
- Optionally retrieves background context from Chroma (RAG).
- Builds a structured prompt payload (`json` block) that is designed for reliable GPT responses.
- Exposes an **Export to GPT** UI section with a **Copy GPT prompt** button.

## Current Recommended Workflow

This repository is currently optimized for **prompt generation + external GPT final answer**:

1. Ask a question in the Streamlit app.
2. Pipeline resolves symbols/sectors and fetches live metrics.
3. Pipeline builds structured user content with authoritative `live_market_data`.
4. Use **Export to GPT (last reply)** -> **Copy GPT prompt**.
5. Paste in ChatGPT for final response.

By default, **Prompt generator mode is enabled** in the sidebar (`PROMPT_ONLY=1`), so local LLM answer generation is skipped for speed.

## How RAG And Intermediate Steps Help

This app is intentionally multi-step. Each step reduces ambiguity before the final GPT response.

1. **Deterministic symbol resolution**
   - The pipeline first detects assets using:
     - exact ticker matching (regex),
     - company alias matching,
     - sector keyword matching,
     - context carry-over from recent turns + summary.
   - Why it helps: GPT gets the correct universe of assets even when the user asks a vague follow-up.

2. **Live metrics fetch (authoritative layer)**
   - For matched symbols, the app fetches Yahoo-derived metrics and serializes them into `live_market_data`.
   - Why it helps: all numeric claims are grounded in explicit data (price, returns, RSI, dates), not model memory.

3. **RAG retrieval (background layer)**
   - Chroma stores/retrieves previously indexed context chunks.
   - Retrieved text is attached as `retrieval_background` and treated as secondary context.
   - Why it helps: adds explanatory context (company/background/history) that may not be present in live metric rows.
   - Guardrail: if RAG conflicts with live metrics, live metrics win.

4. **Deduplicated conversation context**
   - The app includes recent turns directly and only adds `session_summary` when older history is needed.
   - Why it helps: keeps continuity for GPT without bloating prompt tokens with duplicated context.

5. **Structured payload assembly**
   - The user turn is emitted as a fenced `json` payload with stable keys (`current_question`, `live_market_data`, etc.).
   - Why it helps: consistent schema improves instruction-following and makes GPT outputs more reproducible.

6. **System guardrails**
   - System instructions enforce: no invented numbers, explicit missing-data handling, risk-aware framing, and trend classification.
   - Why it helps: GPT output stays decisive but grounded.

7. **Export to GPT**
   - The app exports the exact built prompt so ChatGPT receives the same context stack.
   - Why it helps: no manual copy mistakes and no hidden transformation between UI and final model.

### Practical interpretation of RAG in this project

- Think of RAG as a **context enhancer**, not a source of truth.
- The final answer quality is usually best when:
  - `live_market_data` drives numbers,
  - RAG adds qualitative background,
  - GPT handles final reasoning and writing style.

## Architecture (High Level)

- `src/streamlit_app.py`
  - Main UI.
  - Runs warm-up fetch + optional indexing once per session.
  - Calls `answer_question(...)`.
  - Exports the exact built prompt to clipboard.
- `src/pipeline.py`
  - Core orchestration:
    - deterministic symbol matching
    - yfinance fetch
    - optional RAG retrieval
    - prompt construction
    - optional local Ollama answer
  - Produces `export_messages` for direct GPT export.
- `src/fetcher.py`
  - Batch fetch + metric summarization.
- `src/rag.py`
  - Chroma indexing/retrieval.
- `src/memory.py`
  - Rolling conversation summary (used when local-answer mode is enabled).
- `src/config.py`
  - Defaults for model, paths, RAG, cache, yfinance window, etc.

## Setup

## 1) Create and activate virtual environment

### Windows (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

## 2) Install dependencies

```powershell
pip install -r requirements.txt
```

## 3) Optional: install and run Ollama

Required for local model answering and embedding-based RAG paths.

- Install: [https://ollama.com](https://ollama.com)
- Start server:

```powershell
ollama serve
```

- Pull default models (if needed):

```powershell
ollama pull llama3.2:latest
ollama pull nomic-embed-text
```

## Run the App

```powershell
streamlit run src/streamlit_app.py
```

Open the local URL shown by Streamlit.

## Environment Flags

Useful runtime controls:

- `PROMPT_ONLY` (default in UI: `1`)
  - `1`: skip local Ollama chat answer; build/export prompt only.
  - `0`: allow local answer generation in addition to export.
- `INDEX_RAG_EACH_ASK` (default `0`)
  - `1`: index fetched metrics into Chroma on every question (slower).
- `SKIP_YF_WARM` (default `0`)
  - `1`: skip startup warm-up fetch/index.
- `OLLAMA_BASE_URL`
  - Override Ollama endpoint (default `http://127.0.0.1:11434`).
- `OLLAMA_USE_GPU` / `OLLAMA_OPTIONS_JSON`
  - Control Ollama runtime options (see `src/config.py`).

### Example (PowerShell)

```powershell
$env:PROMPT_ONLY="1"
$env:SKIP_YF_WARM="1"
streamlit run src/streamlit_app.py
```

## Prompt Payload Shape

The generated user content is a fenced `json` payload with fields like:

- `schema_version`
- `current_question`
- `symbols_universe`
- `live_market_data` (authoritative metrics; do not alter)
- `retrieval_background`
- `session_summary` (included only when needed to avoid duplication)
- `indexing_note` (when applicable)

The system prompt enforces:

- numbers must come from `live_market_data`
- missing data must be explicitly acknowledged
- risk-aware, non-guaranteed language
- concise source-style attribution
- trend classification using momentum + RSI when available

## CLI (Optional)

`src/app.py` offers quick commands:

```powershell
python src/app.py fetch --period 2y --output-json metrics.json
python src/app.py ask "Is NVDA trending up?"
```

## Troubleshooting

- **Ollama unreachable**
  - Start `ollama serve`, verify `OLLAMA_BASE_URL`.
- **Slow first load**
  - Warm-up fetch/index may take time; set `SKIP_YF_WARM=1` if needed.
- **No symbols matched**
  - Ask with ticker/company/sector words, or rely on context from prior turns.
- **Copy button blocked**
  - Browser clipboard permission may be restricted; use the prompt preview box manually.

## Notes

- This project is for educational analysis and workflow automation.
- Not financial advice.
