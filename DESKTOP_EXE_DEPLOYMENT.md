# Desktop `.exe` deployment — problem summary

## What went wrong

### 1. Native window (pywebview) failed in the frozen app

- **Symptom:** Tracebacks around `pythonnet` / `clr` / `Python.Runtime.Loader.Initialize`, or `clr_loader` / `netfx.py`, when opening the UI from **PyInstaller** builds.
- **Cause:** pywebview on Windows uses **.NET** via **pythonnet**. In a frozen process, the default **.NET Framework** path (`netfx`) often misbehaves (including `Failed to resolve Python.Runtime.Loader.Initialize` from `Python.Runtime.dll`). Forcing **CoreCLR** avoids that, but **`DOTNET_ROOT`** must point at a real host. Many PCs only have the **user** install `%LOCALAPPDATA%\Microsoft\dotnet` or **`dotnet` on PATH**, not `C:\Program Files\dotnet`, so the launcher used to leave `DOTNET_ROOT` unset and stuck on `netfx`. **UPX**-compressed `Python.Runtime.dll` can also break CLR load; the build excludes that DLL from UPX.
- **Mitigation:** Before loading CLR, resolve `DOTNET_ROOT` via `PATH` (`where dotnet`), `%LOCALAPPDATA%\Microsoft\dotnet`, then Program Files; set `PYTHONNET_RUNTIME=coreclr` when a root is found. If there is still no .NET host, install the **[.NET desktop/runtime](https://dotnet.microsoft.com/download)** (or rely on browser fallback).

### 2. “Browser fallback” still looked broken

- **Symptom:** Users said both native and browser paths failed.
- **Cause:** The browser path only ran **after** Streamlit was listening. If Streamlit never started (or crashed first), the app exited **before** any fallback.
- **Mitigation:** Fall back to the browser on server timeout as well, and run Streamlit in a **separate process** (see below) so the server can actually stay up.

### 3. Streamlit inside the same `.exe` on a background thread

- **Symptom:** `ValueError: signal only works in main thread of the main interpreter` from Streamlit’s bootstrap.
- **Cause:** Streamlit registers **SIGTERM** (and similar) from `bootstrap.run()`. That must run on the process **main thread**, not a **daemon** thread.
- **Mitigation:** The frozen build relaunches the same `StockAssistant.exe` with **`--streamlit-worker`** so Streamlit runs in a **child process** on its own main thread; the parent hosts pywebview only.

### 4. “No log file” after building

- **Symptom:** `StockAssistant.log` missing even though the user “ran” the app.
- **Causes:**
  - **Build ≠ run:** Logs are written when **`StockAssistant.exe` runs**, not when `python build.py` runs.
  - Writes **next to the `.exe`** can fail (OneDrive, permissions); logs were duplicated under **`%LOCALAPPDATA%\StockAssistant\`** and **`%TEMP%\StockAssistant_last_boot.log`**.
  - Crashes **before** `main()` could hide logs — addressed with top-level crash handling and a **PyInstaller runtime hook** that writes a boot line **before** `launcher.py` runs.

### 5. “Works locally, fails when downloaded from GitHub”

- **Symptom:** Local `dist\StockAssistant\` works; the zip from Releases does not.
- **Typical causes:**
  - The **release zip was built from an older commit** (tag still pointed at old code while local tree had fixes).
  - Reusing **`VERSION` = 1.0.0** and tag **`v1.0.0`** produces the **same zip name** every time → easy to download an **old** artifact by mistake.
  - **Different machine** than the dev box (missing .NET / WebView2, antivirus, “run inside zip” without extracting).
- **Mitigation:** Push commits, **bump `VERSION`**, push a **new tag** (`v1.0.1`, …), confirm the **GitHub Actions** run succeeded. CI now drops **`BUILD_INFO.txt`** next to the exe (`github_sha`, run URL, `built_at`) so you can prove which build a zip came from.

---

## Quick verification checklist

1. **Rebuild** (`python build.py` or CI).
2. **Run** `StockAssistant.exe` (extracted folder, not “run from zip” only).
3. Check logs: exe directory, `%LOCALAPPDATA%\StockAssistant\`, `%TEMP%\StockAssistant_last_boot.log`.
4. For a **GitHub** build: open **`BUILD_INFO.txt`** and match **`github_sha`** to the commit you intended to ship.

---

## References in this repo

- Entry: `launcher.py` (webview parent, `--streamlit-worker` child, logging, fallbacks).
- Bundle: `build.py` / generated `StockAssistant.spec` (PyInstaller, `collect_all` for heavy deps, optional `python build.py --debug` for a console).
- CI: `.github/workflows/build.yml` (tag-triggered build, zip, `BUILD_INFO.txt`).
