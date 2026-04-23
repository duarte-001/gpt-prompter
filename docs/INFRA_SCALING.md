# Infra & Scaling Notes (1–2 users)

This repo is intentionally optimized for **single-machine** usage and **low concurrency**. For a prompt-generator app used by **1–2 people at a time**, the current setup (Streamlit + local persistence + optional Ollama + local Chroma) is a good fit.

## Recommended default (today)

- **App**: one Streamlit process (or the existing launcher/`StockAssistant.exe` flow).
- **Persistence**:
  - file-backed app data under `data/`
  - Chroma persistent store under `data/chroma`
  - optional yfinance cache under `data/yfinance_cache`
- **Deployment**: keep it simple (Python venv for dev; packaged `.exe` for non-dev).

## When to use Docker (trigger-based)

Docker is optional here. Consider it when any of these become true:

- **Reproducibility**: onboarding other machines is painful (dependency drift, Python version conflicts).
- **Hosting**: you want to deploy on a VPS/cloud host with a predictable runtime artifact.
- **Isolation**: you want to avoid host-level Python tooling and keep the runtime sandboxed.

If none of the above apply, Docker is mostly extra moving parts.

## When to add a relational database

### Add SQLite when you need *queryable* durable data

SQLite is the right next step if you want any of:
- **Session/chat history** that survives restarts and can be searched/filtered
- **Audit log** (e.g., what prompt was generated, which tickers, when)
- **User prefs** (saved watchlists, defaults, prompt templates)

SQLite keeps operations local, has near-zero ops overhead, and works well for a few concurrent users when writes are light.

### Move to Postgres later (only with clear need)

Postgres becomes worth it when you need:
- **Remote/multi-client** access to the same DB across machines
- **Higher write concurrency** or longer-running background jobs
- **Operational reliability** beyond “single machine with local disk”
- **Analytics** / reporting / growth beyond hobby scale

## Optional future path (design only): Docker + SQLite

This is a *design sketch* to keep the upgrade path clear without changing the current behavior.

### Design goals
- Keep **Streamlit UI** unchanged.
- Add a small persistence layer behind an interface so you can swap:
  - `NoDB` (today) → `SQLite` (later) → `Postgres` (much later)
- Keep Chroma as-is unless you later need a hosted vector DB.

### Data model sketch (SQLite)
- `sessions`: `id`, `created_at`, `title`, `last_active_at`
- `turns`: `id`, `session_id`, `role`, `content`, `created_at`
- `runs`: `id`, `session_id`, `question`, `symbols_json`, `live_market_data_json`, `export_messages_json`, `created_at`
- `settings`: `key`, `value_json`, `updated_at`

### Container layout sketch
- **app** container runs Streamlit (`streamlit run src/streamlit_app.py`)
- **volume** for:
  - `data/` (exports, cache)
  - `data/chroma/` (vector store)
  - `data/app.sqlite` (SQLite DB)
- Optional: run Ollama on the host (common on Windows) and point `OLLAMA_BASE_URL` to it.

### Notes for Windows
If you rely on the current `.exe` distribution, Docker is usually not the right primary distribution channel. Docker is more useful for server hosting or dev reproducibility.

