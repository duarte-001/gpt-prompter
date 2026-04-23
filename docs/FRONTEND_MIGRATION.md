# Frontend Migration (Streamlit → React + FastAPI + Electron)

This repo now contains a migration path away from Streamlit while **reusing the existing Python core** (`src/pipeline.py`, `src/fetcher.py`, `src/rag.py`, `src/memory.py`).

## What exists now

### FastAPI backend
- Entrypoint: `python -m src.api.server`
- Endpoints:
  - `GET /api/health`
  - `POST /api/ask` (wraps `src.pipeline.answer_question(...)`)
- Serves UI:
  - If `frontend/dist` exists, serves it
  - Otherwise serves `frontend/` (lightweight no-build React via CDN)
- Serves repo icons under `GET /assets/*`

### React parity UI (no build step)
- Location: `frontend/`
- Goal: match Streamlit flows (ask → answer → technical details → export prompt)
- Export parity:
  - The API returns `export_messages` (system + user payload, and optionally recent messages)
  - UI can copy the `export_messages` JSON or just the last **user** payload text

### Electron shell (packaging scaffold)
- Location: `electron/`
- Starts `python -m src.api.server` and opens a desktop window pointed at `http://127.0.0.1:8787/`
- Building requires Node + **npm** to install Electron dependencies.

## Parity notes vs Streamlit

- **Prompt-only mode**: matches Streamlit’s default “Prompt-only mode (skip local answer)” behavior (`skip_llm=True`). You still get `export_messages` for external GPT.\n- **RAG**: Streamlit uses `USE_RAG=True`. The API supports `use_rag` as well.\n- **Ollama dependency**:\n  - Prompt-only mode skips **chat** calls, but **RAG embeddings** still require Ollama for `nomic-embed-text`.\n  - If Ollama is not running, use `use_rag=false` (or start Ollama).\n\n## Known risks / follow-ups\n+- **npm missing**: if the machine has Node but not npm, Vite/React build and Electron packaging can’t be executed yet. The repo still provides a no-build UI and a packaging scaffold.\n+- **Desktop distribution**: Electron packaging (installer) is a separate step from the current PyInstaller `.exe` flow; decide whether to keep PyInstaller for Python-only distribution or switch fully to Electron for UI + runtime.\n+- **Port collisions**: FastAPI defaults to `127.0.0.1:8787` (env `STOCK_ASSISTANT_PORT`). If another service uses it, change the port.\n+
