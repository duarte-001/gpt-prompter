# Stock Assistant

**Positioning:** an intelligent prompt-generation system that maximizes LLM capabilities for financial reasoning—grounded on reliable data, efficient retrieval, and professional-grade outputs. The app ships as a **React UI served by a FastAPI backend**, with one-click prompt export to paste into ChatGPT.

Ships as a deterministic pipeline plus UI: optional local **Ollama** answers, or prompt-only mode with **Export to GPT** for ChatGPT and similar models.

## What you see in the app (plain-language pillars)

1. **Clear answers** — Straight explanations without drowning you in jargon.
2. **Live numbers** — Prices and key figures from up-to-date feeds, not guesswork.
3. **Smart context** — Background notes when they help; live data always comes first.
4. **Works with your AI** — Export the full prompt to ChatGPT or another assistant in one step.
5. **Careful tone** — No hype; not personal financial advice.

See `docs/BRANDING.md` for Option A visuals, tone rules, and the technical mapping of these pillars to the pipeline.

## Infra / scaling (when to add Docker or a DB)

This repo is designed to work well as a **single-machine prompt generator**. For the trigger-based guidance on when to introduce Docker, SQLite, or Postgres, see `docs/INFRA_SCALING.md`.

## Current Recommended Workflow

This repository is currently optimized for **prompt generation + external GPT final answer**:

1. Ask a question in the app.
2. Pipeline resolves symbols/sectors and fetches live metrics.
3. Pipeline builds structured user content with authoritative `live_market_data`.
4. Scroll to **Export to GPT (last reply)** → **Copy GPT prompt** → paste into ChatGPT.
5. Paste in ChatGPT for final response.

By default, **ChatGPT-style copy only** is enabled in the sidebar (`PROMPT_ONLY=1`): the on-device assistant is skipped for speed so you can export to ChatGPT quickly.

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

Timings and live JSON for the last reply are visible in **Dev mode** in the React UI.

## Auto-Updates

When launched via `python launcher.py`, the app checks GitHub (`origin/main`) for new commits before starting. If updates are found, a dialog asks whether to update now. Accepting triggers `git pull --ff-only` and `pip install -r requirements.txt`, then the app restarts with the new code.

To skip the check (e.g. offline), pass `--skip-update`:

```powershell
python launcher.py --skip-update
```

## Download (no Python required)

Pre-built Windows releases are available on GitHub:

1. Go to [**Releases**](https://github.com/duarte-001/gpt-prompter/releases).
2. Download the latest **`StockAssistant-windows.zip`** from Releases.
3. Extract the zip to any folder.
4. Double-click `StockAssistant.exe` to launch.

No Python, no terminal, no setup commands needed.

## Releasing a New Version (maintainer)

When you want to publish a new `.exe` build:

1. Bump **[VERSION](VERSION)** (this is what the frozen app reports as its build).
2. After the release is public, bump **`update/manifest.json`** `version` on `main` to match (or exceed) the new release so older installed builds can prompt to open the download page.
3. Commit, tag, push.

```powershell
git add -A && git commit -m "release v1.0.1"
git tag v1.0.1
git push origin main --tags
```

GitHub Actions builds the `.exe` automatically and publishes it as a release.

### Optional update prompt (frozen `StockAssistant.exe`)

On launch, the `.exe` fetches **`update/manifest.json`** (default: raw URL on `main`). If the manifest **`version`** is **greater** than the number in the bundled **`VERSION`** file, a dialog asks whether to open **`download_url`** in the browser. The dialog does not show version strings; numbers stay in `VERSION` / logs only.

- Disable: env **`STOCK_ASSISTANT_DISABLE_UPDATE_CHECK=1`**, or run **`StockAssistant.exe --skip-update`**.
- Custom manifest: **`STOCK_ASSISTANT_UPDATE_MANIFEST_URL`** (HTTPS JSON with `version`, `download_url`, optional `notes_url`).
- “No” dismisses that manifest version until it changes (state under `%LOCALAPPDATA%\StockAssistant\`).

**How to confirm it is working**

1. **Silent success (already up to date):** Run the `.exe` with a **`VERSION`** that matches or exceeds `update/manifest.json` on GitHub. You should get **no** dialog; check `StockAssistant.log` next to the exe for lines like `Up to date` / no update errors.
2. **Dialog path:** Temporarily set **`STOCK_ASSISTANT_UPDATE_MANIFEST_URL`** to a small JSON URL where `"version"` is higher than your bundled **`VERSION`** (e.g. a gist raw URL, or any static host). Run the `.exe` — you should see the **Stock Assistant — Update** prompt. **Yes** opens the URL; **No** dismisses for that manifest version.
3. **Production path:** After you push a higher **`version`** in **`update/manifest.json`** on `main`, older installed builds (lower **`VERSION`**) should prompt on next launch (unless disabled or dismissed for that version).

## Building Locally (optional)

You can also build the `.exe` on your own machine:

```powershell
pip install pyinstaller
python build.py
```

The output is `dist/StockAssistant/StockAssistant.exe`.

**Note:** the `.exe` does not auto-install a new build. **Git-based** pull+pip only runs for `python launcher.py` from a checkout. The **frozen** app can only **prompt** and open the download page (see above).

## Architecture (High Level)

- `launcher.py`
  - Desktop entry point: dev git pull (non-frozen), optional frozen manifest check, starts Streamlit, opens Edge/Chrome `--app` window.
- `src/updater.py`
  - Dev-only: git fetch/compare, prompt, `git pull` + `pip install`.
- `src/frozen_update_check.py`
  - Frozen `.exe`: HTTPS manifest vs bundled `VERSION`, optional “open download page” dialog.
- React UI (`frontend/`) served by FastAPI (`src/api/app.py`)
  - Chat-first prompt generator, export-first workflow, optional dev details.
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

### Desktop app (recommended)

Launches the FastAPI backend (serving the React UI) and opens it in a chromeless Edge/Chrome window (looks like a native app — no address bar, no tabs). Install deps first:

```powershell
pip install -r requirements.txt
python launcher.py
```

### Dev server (browser)

```powershell
python -m src.api.server
```

Open `http://127.0.0.1:8787/`.

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
- **Desktop: no window appears**
  - The launcher needs **Microsoft Edge** or **Google Chrome** installed. Edge is pre-installed on all Windows 10/11 machines. If neither is found the app falls back to your default browser.

## Notes

- This project is for educational analysis and workflow automation.
- Not financial advice.
