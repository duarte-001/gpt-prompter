# Desktop `.exe` deployment — problem summary

## What went wrong (and how it was fixed)

### 1. Native window (pywebview) failed in the frozen app — **REMOVED**

- **Symptom:** Tracebacks around `pythonnet` / `clr` / `Python.Runtime.Loader.Initialize` when opening the UI from **PyInstaller** builds.
- **Root cause:** pywebview on Windows uses **.NET** via **pythonnet** for *all* its backends (`winforms` and `edgechromium` both `import clr`). In a frozen process the default **.NET Framework** loader (`netfx`) consistently fails to resolve `Python.Runtime.dll`, and machines without a .NET Core/5+/6+ SDK can't use `coreclr` either. There is no pywebview backend on Windows that avoids pythonnet.
- **Resolution:** **pywebview, pythonnet, and clr_loader were removed entirely.** The launcher now opens Edge or Chrome in `--app` mode (a chromeless window with no address bar or tabs) — zero .NET dependency, looks native, works on every Windows 10/11 machine that has Edge (all of them) or Chrome.

### 2. "Browser fallback" still looked broken

- **Symptom:** Users said both native and browser paths failed.
- **Cause:** The browser path only ran **after** Streamlit was listening. If Streamlit never started (or crashed first), the app exited **before** any fallback.
- **Mitigation:** Fall back to the browser on server timeout as well, and run Streamlit in a **separate process** (see below) so the server can actually stay up.

### 3. Streamlit inside the same `.exe` on a background thread

- **Symptom:** `ValueError: signal only works in main thread of the main interpreter` from Streamlit's bootstrap.
- **Cause:** Streamlit registers **SIGTERM** (and similar) from `bootstrap.run()`. That must run on the process **main thread**, not a **daemon** thread.
- **Mitigation:** The frozen build relaunches the same `StockAssistant.exe` with **`--streamlit-worker`** so Streamlit runs in a **child process** on its own main thread; the parent opens the browser window only.

### 4. "No log file" after building

- **Symptom:** `StockAssistant.log` missing even though the user "ran" the app.
- **Causes:**
  - **Build ≠ run:** Logs are written when **`StockAssistant.exe` runs**, not when `python build.py` runs.
  - Writes **next to the `.exe`** can fail (OneDrive, permissions); logs were duplicated under **`%LOCALAPPDATA%\StockAssistant\`** and **`%TEMP%\StockAssistant_last_boot.log`**.
  - Crashes **before** `main()` could hide logs — addressed with top-level crash handling and a **PyInstaller runtime hook** that writes a boot line **before** `launcher.py` runs.

### 5. "Works locally, fails when downloaded from GitHub"

- **Symptom:** Local `dist\StockAssistant\` works; the zip from Releases does not.
- **Typical causes:**
  - The **release zip was built from an older commit** (tag still pointed at old code while local tree had fixes).
  - Releases ship **`StockAssistant-windows.zip`** (same filename each tag) — always check the **release tag** and **`BUILD_INFO.txt`** inside the zip (`github_sha`, run URL, `built_at`) so you know which build you have.
  - **Different machine** than the dev box (antivirus, "run inside zip" without extracting).
- **Mitigation:** Push commits, **bump `VERSION`**, push a **new tag** (`v1.0.1`, …), confirm the **GitHub Actions** run succeeded, then verify **`BUILD_INFO.txt`** matches the run you expect.

---

## How the UI window works now

1. `launcher.py` starts the FastAPI server (serving the React UI).
2. Once the server is listening, it looks for **`msedge.exe`** or **`chrome.exe`** in standard install paths.
3. Launches the browser in **`--app=http://127.0.0.1:8787/`** mode — a chromeless window.
4. Waits for the browser window to close, then shuts down the server and exits.
5. If no Edge/Chrome is found, falls back to the **default browser** (`webbrowser.open`).

No .NET, no pythonnet, no CLR, no pywebview.

---

## Quick verification checklist

1. **Rebuild** (`python build.py` or CI).
2. **Run** `StockAssistant.exe` (extracted folder, not "run from zip" only).
3. Check logs: exe directory, `%LOCALAPPDATA%\StockAssistant\`, `%TEMP%\StockAssistant_last_boot.log`.
4. For a **GitHub** build: open **`BUILD_INFO.txt`** and match **`github_sha`** to the commit you intended to ship.

---

## References in this repo

- Entry: `launcher.py` (starts FastAPI, opens Edge/Chrome `--app` window, logging, fallbacks).
- Bundle: `build.py` / generated `StockAssistant.spec` (PyInstaller, optional `python build.py --debug` for a console).
- CI: `.github/workflows/build.yml` (tag-triggered build, zip, `BUILD_INFO.txt`).
