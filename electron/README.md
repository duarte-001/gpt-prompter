# Electron wrapper

This folder contains an Electron shell that launches the Python FastAPI backend and loads the UI from it.

## Prereqs
- Node + **npm** (or pnpm/yarn)
- Python environment with `fastapi` + `uvicorn` installed (see repo `requirements.txt`)

## Dev run (once npm exists)

```powershell
cd electron
npm install
npm run dev
```

## Notes
- The Electron app starts `python -m src.api.server` in the repo root.
- You can override Python via `STOCK_ASSISTANT_PYTHON` (e.g. absolute path to `.venv\\Scripts\\python.exe`).
- Backend listens on `127.0.0.1:8787` by default (env `STOCK_ASSISTANT_PORT`).

