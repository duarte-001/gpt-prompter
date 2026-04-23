"""
Desktop launcher: checks for updates, then runs the FastAPI server (which serves
the React frontend) and opens a native-feeling window (Edge/Chrome --app mode)
or the default browser.

Usage:  python launcher.py
        python launcher.py --skip-update
"""

from __future__ import annotations

import atexit
import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path


def _get_root() -> Path:
    """Project root: works both from source checkout and PyInstaller bundle."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


_ROOT = _get_root()

_HOST = "127.0.0.1"
_API_PORT = 8787
# Health checks and server wait use the origin only.
_API_SERVER_BASE_URL = f"http://{_HOST}:{_API_PORT}"
_API_BROWSER_APP_URL = f"{_API_SERVER_BASE_URL}/"
_STARTUP_TIMEOUT = 60

# ---------------------------------------------------------------------------
# Diagnostic log (frozen: next to .exe, with fallback under %LOCALAPPDATA%)
# ---------------------------------------------------------------------------
_log = logging.getLogger("launcher")
_ACTIVE_LOG_PATH: str | None = None


def _is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def _win_local_appdata_dir() -> Path:
    la = os.environ.get("LOCALAPPDATA")
    if not la:
        la = str(Path.home() / "AppData" / "Local")
    d = Path(la) / "StockAssistant"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _frozen_fallback_log() -> Path:
    if sys.platform == "win32":
        return _win_local_appdata_dir() / "StockAssistant.log"
    base = Path.home() / ".cache" / "StockAssistant"
    base.mkdir(parents=True, exist_ok=True)
    return base / "StockAssistant.log"


def _frozen_primary_log() -> Path:
    return Path(sys.executable).resolve().with_name("StockAssistant.log")


def _boot_temp_stamp_path() -> Path:
    return Path(tempfile.gettempdir()) / "StockAssistant_last_boot.log"


def _is_streamlit_worker_argv() -> bool:
    return _is_frozen() and len(sys.argv) > 1 and sys.argv[1] == "--streamlit-worker"


def _boot_frozen_traces() -> None:
    """Append a boot line as soon as the frozen process starts (before main).

    The Streamlit worker is a second process by design; skip duplicate boot lines
    there so logs reflect one user-facing app session.
    """
    if not _is_frozen() or _is_streamlit_worker_argv():
        return
    stamp = (
        f"{time.strftime('%Y-%m-%d %H:%M:%S')} launcher_boot pid={os.getpid()} "
        f"exe={sys.executable}\n"
    )
    paths = (_frozen_primary_log(), _frozen_fallback_log(), _boot_temp_stamp_path())
    for path in paths:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(stamp)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
        except OSError:
            continue


def _setup_logging() -> None:
    global _ACTIVE_LOG_PATH
    if _log.handlers:
        return
    _log.setLevel(logging.DEBUG)
    if _is_frozen():
        log_path = _frozen_primary_log()
        try:
            handler: logging.Handler = logging.FileHandler(str(log_path), encoding="utf-8")
            _ACTIVE_LOG_PATH = str(log_path)
        except OSError:
            log_path = _frozen_fallback_log()
            handler = logging.FileHandler(str(log_path), encoding="utf-8")
            _ACTIVE_LOG_PATH = str(log_path)
    else:
        handler = logging.StreamHandler(sys.stderr)
        _ACTIVE_LOG_PATH = None
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    _log.addHandler(handler)


def _server_ready(url: str) -> bool:
    """Return True when FastAPI health responds, with fallback to root HTTP reachability."""
    from urllib.request import urlopen
    from urllib.error import HTTPError, URLError

    api_health = f"{url.rstrip('/')}/api/health"
    try:
        with urlopen(api_health, timeout=2) as resp:
            return 200 <= resp.status < 500
    except (HTTPError, URLError, OSError, ValueError):
        pass

    try:
        with urlopen(url, timeout=2) as resp:
            return 200 <= resp.status < 500
    except HTTPError as exc:
        return 200 <= exc.code < 500
    except (URLError, OSError, ValueError):
        return False


def _wait_for_server(url: str, timeout: float, proc: subprocess.Popen | None = None) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            return False
        if _server_ready(url):
            return True
        time.sleep(0.5)
    return False


def _kill_proc(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _kill_stale_on_port(host: str, port: int) -> None:
    """Kill any process still listening on *host*:*port* (leftover from a previous run)."""
    if sys.platform != "win32":
        return
    try:
        out = subprocess.check_output(
            ["netstat", "-ano", "-p", "TCP"],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            text=True,
            timeout=5,
        )
    except Exception:
        return
    target = f"{host}:{port}"
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and target in parts[1] and parts[3] == "LISTENING":
            pid = int(parts[4])
            if pid == os.getpid():
                continue
            _log.info("Killing stale process on %s (pid %d)", target, pid)
            try:
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)],
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    capture_output=True,
                    timeout=5,
                )
            except Exception:
                pass


def _check_for_updates() -> bool:
    """Run the updater. Returns True if code was updated (caller should restart)."""
    try:
        from src.updater import check_and_update
        return check_and_update()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Native-feeling window via Edge / Chrome --app mode
# ---------------------------------------------------------------------------

def _find_chrome_or_edge_executable() -> str | None:
    """Return chrome.exe or msedge.exe path. Chrome is preferred when both exist."""
    if sys.platform != "win32":
        return shutil.which("google-chrome") or shutil.which("chromium-browser")

    candidates: list[Path] = []
    for env_var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(env_var)
        if not base:
            continue
        candidates.append(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")
        candidates.append(Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe")

    for p in candidates:
        if p.exists():
            return str(p)

    return shutil.which("chrome") or shutil.which("google-chrome") or shutil.which("msedge")


def _launch_app_mode_window(url: str) -> tuple[subprocess.Popen | None, Path | None]:
    """Open *url* in a chromeless Edge/Chrome window (--app mode).

    Returns ``(popen, profile_dir)``. On Windows, *profile_dir* is the ``--user-data-dir``
    passed to the browser (used to wait for detached Chrome/Edge children). Else *profile_dir* is None.
    """
    browser = _find_chrome_or_edge_executable()
    if not browser:
        _log.warning("No Edge or Chrome found for --app mode")
        return None, None

    user_data = _win_local_appdata_dir() / "browser-profile" if sys.platform == "win32" else None
    cmd = [
        browser,
        f"--app={url}",
        "--no-first-run",
        "--disable-extensions",
        "--start-maximized",
    ]
    if user_data:
        cmd.append(f"--user-data-dir={user_data}")
    _log.info("Launching app-mode window: %s", " ".join(cmd))
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return proc, user_data
    except OSError as exc:
        _log.warning("Failed to launch app-mode window: %s", exc)
        return None, None


def _win_browser_process_count_using_profile(profile_dir: Path) -> int | None:
    """Count chrome.exe/msedge.exe whose command line references *profile_dir*.

    Uses both the resolved path and a short path fragment because WMI command lines
    may omit or shorten ``--user-data-dir=`` paths, and Chrome may use ``/`` or ``\\``.

    Returns None if the query failed (caller should not rely on the result).
    """
    if sys.platform != "win32":
        return 0
    m1 = str(profile_dir.resolve()).lower().replace("'", "''")
    m2 = "stockassistant\\browser-profile"
    script = (
        f"$m1 = '{m1}'; $m2 = '{m2}'; "
        "$n = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | "
        "Where-Object { $_.Name -and $_.CommandLine -and ($_.Name.ToLower() -in @('chrome.exe','msedge.exe')) "
        "  -and (($cl = $_.CommandLine.ToLower().Replace([char]0x2F,[char]0x5C)) -and "
        "         ($cl.Contains($m1) -or $cl.Contains($m2))) }); "
        "$n.Count"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if out.returncode != 0:
            _log.warning("Profile process query failed (rc=%s): %s", out.returncode, (out.stderr or "").strip()[:500])
            return None
        line = (out.stdout or "").strip().splitlines()
        last = line[-1].strip() if line else ""
        return int(last)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        _log.warning("Profile process query error: %s", exc)
        return None


def _wait_for_app_mode_browser_exit(browser_proc: subprocess.Popen, profile_dir: Path | None) -> None:
    """Block until the user-facing browser session is gone.

    On Windows, ``chrome.exe`` / ``msedge.exe`` often exit immediately after spawning a
    long-lived child; ``wait()`` on the parent therefore must not be treated as the user
    having closed the app. If the starter PID is long-lived instead, children may already
    be gone when ``wait()`` returns, so we record profile-tagged processes *while* waiting.
    """
    stop_monitor = threading.Event()
    profile_seen = threading.Event()

    def _monitor_profile() -> None:
        if profile_dir is None:
            return
        while not stop_monitor.wait(0.5):
            n = _win_browser_process_count_using_profile(profile_dir)
            if n is not None and n > 0:
                profile_seen.set()

    monitor_th: threading.Thread | None = None
    if sys.platform == "win32" and profile_dir is not None:
        monitor_th = threading.Thread(target=_monitor_profile, name="browser-profile-monitor", daemon=True)
        monitor_th.start()

    browser_proc.wait()

    if monitor_th is not None:
        stop_monitor.set()
        monitor_th.join(timeout=2.0)

    if sys.platform != "win32" or profile_dir is None:
        return

    if not profile_seen.is_set():
        _log.debug(
            "Browser starter exited without observing profile-tagged chrome/msedge children "
            "(single long-lived process or WMI did not expose command lines)."
        )
        return

    _log.info("Waiting for browser processes using profile %s to exit …", profile_dir)
    while True:
        n = _win_browser_process_count_using_profile(profile_dir)
        if n is None:
            _log.warning("Profile process query failed while waiting for browser shutdown.")
            return
        if n == 0:
            break
        time.sleep(1.0)


def _open_url_with_installed_browser(url: str) -> None:
    """Open *url* using Chrome or Edge if installed; avoids Windows ``webbrowser`` defaulting to Edge."""
    if sys.platform == "win32":
        exe = _find_chrome_or_edge_executable()
        if exe:
            try:
                subprocess.Popen(
                    [exe, url],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                return
            except OSError as exc:
                _log.warning("Could not start browser %s: %s", exe, exc)
    import webbrowser

    webbrowser.open(url)


def _open_browser_fallback(reason: str = "") -> None:
    """Open in Chrome/Edge when possible, else the default browser (no blocking dialog)."""
    _log.info("Opening in default browser. Reason: %s", reason)
    _open_url_with_installed_browser(_API_BROWSER_APP_URL)


def _ui_mode() -> str:
    """Legacy compatibility shim (always react now)."""
    return "react"


def _start_fastapi_server_thread(host: str, port: int) -> tuple[threading.Thread, object]:
    """
    Start uvicorn server in a background thread and return (thread, server).
    The returned server object exposes ``should_exit`` to request shutdown.
    """
    # In PyInstaller windowed mode, sys.stdout/sys.stderr can be None. Uvicorn's
    # logging setup expects file-like streams (uses .isatty()).
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115

    import uvicorn

    # Import app lazily so frozen boot stays fast and errors are logged clearly.
    from src.api.app import app as fastapi_app

    cfg = uvicorn.Config(
        fastapi_app,
        host=host,
        port=port,
        reload=False,
        log_level="info",
        proxy_headers=True,
        # Avoid color/TTY assumptions in frozen builds.
        use_colors=False,
    )
    server = uvicorn.Server(cfg)

    def _run() -> None:
        try:
            server.run()
        except Exception:
            _log.exception("FastAPI server thread crashed")

    th = threading.Thread(target=_run, name="fastapi-server", daemon=True)
    th.start()
    return th, server


def main() -> None:
    _setup_logging()
    _log.info("launcher start  frozen=%s  python=%s  root=%s", _is_frozen(), sys.version, _ROOT)

    if not _is_frozen() and "--skip-update" not in sys.argv:
        if _check_for_updates():
            subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--skip-update"])
            sys.exit(0)

    if _is_frozen() and "--skip-update" not in sys.argv:
        if os.environ.get("STOCK_ASSISTANT_DISABLE_UPDATE_CHECK", "").strip() != "1":
            try:
                from src.frozen_update_check import maybe_prompt_frozen_update

                maybe_prompt_frozen_update()
            except Exception:
                _log.exception("Frozen update check failed")

    _kill_stale_on_port(_HOST, _API_PORT)

    proc = None
    api_server = None
    _log.info("Starting FastAPI + React UI on %s:%d", _HOST, _API_PORT)
    _, api_server = _start_fastapi_server_thread(_HOST, _API_PORT)
    atexit.register(lambda: setattr(api_server, "should_exit", True))

    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))

    if not _wait_for_server(_API_SERVER_BASE_URL, _STARTUP_TIMEOUT, proc):
        reason = f"FastAPI did not respond with HTTP within {_STARTUP_TIMEOUT:.0f} s"
        _log.error("Server wait failed: %s", reason)
        if proc:
            _kill_proc(proc)
        if api_server is not None:
            try:
                api_server.should_exit = True
            except Exception:
                pass
        if _is_frozen():
            _open_browser_fallback(reason=reason)
            return
        print(f"ERROR: {reason}", file=sys.stderr)
        sys.exit(1)

    _log.info("Server is listening — launching native window")

    browser_proc, browser_profile = _launch_app_mode_window(_API_BROWSER_APP_URL)
    if browser_proc:
        _log.info("App-mode window launched (pid %d), waiting for it to close …", browser_proc.pid)
        _wait_for_app_mode_browser_exit(browser_proc, browser_profile)
        _log.info("App-mode window closed")
    else:
        _open_browser_fallback(reason="Edge/Chrome not found for app-mode window")
        if _is_frozen() and sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0,
                "Stock Assistant is running in your browser.\n\n"
                "When you are finished, close this dialog to exit.",
                "Stock Assistant",
                0x40,
            )
        else:
            stop = threading.Event()
            signal.signal(signal.SIGINT, lambda *_: stop.set())
            while not stop.is_set():
                time.sleep(1)

    if proc:
        _kill_proc(proc)
    if api_server is not None:
        try:
            api_server.should_exit = True
        except Exception:
            pass


def _write_crash_report(text: str) -> None:
    paths = []
    if getattr(sys, "frozen", False):
        paths.append(Path(sys.executable).resolve().with_name("StockAssistant_crash.txt"))
        paths.append(_frozen_fallback_log().with_name("StockAssistant_crash.txt"))
        paths.append(Path(tempfile.gettempdir()) / "StockAssistant_last_crash.txt")
    for p in paths:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
            return
        except OSError:
            continue


if __name__ == "__main__":
    _boot_frozen_traces()
    try:
        main()
    except Exception:
        crash = traceback.format_exc()
        _write_crash_report(crash)
        if getattr(sys, "frozen", False) and sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0,
                f"StockAssistant crashed:\n\n{crash[:800]}\n\n"
                "Details: StockAssistant_crash.txt next to the .exe, "
                "%LOCALAPPDATA%\\StockAssistant\\, or %TEMP%\\StockAssistant_last_crash.txt.",
                "Stock Assistant — Fatal Error",
                0x10,
            )
        raise
    finally:
        logging.shutdown()
