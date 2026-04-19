"""
Desktop launcher: checks for updates, then runs the Streamlit app inside
a native-feeling window (Edge/Chrome --app mode) or the default browser.

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
_ICON = _ROOT / "assets" / "icon.ico"
_STREAMLIT_SCRIPT = _ROOT / "src" / "streamlit_app.py"

_HOST = "127.0.0.1"
_PORT = 8501
_URL = f"http://{_HOST}:{_PORT}"
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


def _boot_frozen_traces() -> None:
    """Append a boot line as soon as the frozen process starts (before main)."""
    if not _is_frozen():
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
    """Return True when *url* responds with HTTP 200 (not just a TCP accept)."""
    from urllib.request import urlopen
    from urllib.error import URLError
    try:
        with urlopen(url, timeout=2) as resp:
            return resp.status == 200
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

def _find_edge_or_chrome() -> str | None:
    """Return the path to msedge.exe or chrome.exe, or None."""
    if sys.platform != "win32":
        return shutil.which("google-chrome") or shutil.which("chromium-browser")

    candidates: list[Path] = []
    for env_var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(env_var)
        if not base:
            continue
        candidates.append(Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe")
        candidates.append(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")

    for p in candidates:
        if p.exists():
            return str(p)

    return shutil.which("msedge") or shutil.which("chrome") or shutil.which("google-chrome")


def _launch_app_mode_window(url: str) -> subprocess.Popen | None:
    """Open *url* in a chromeless Edge/Chrome window (--app mode). Returns the process or None."""
    browser = _find_edge_or_chrome()
    if not browser:
        _log.warning("No Edge or Chrome found for --app mode")
        return None

    user_data = _win_local_appdata_dir() / "browser-profile" if sys.platform == "win32" else None
    cmd = [
        browser,
        f"--app={url}",
        "--no-first-run",
        "--disable-extensions",
        f"--window-size=1200,820",
    ]
    if user_data:
        cmd.append(f"--user-data-dir={user_data}")
    _log.info("Launching app-mode window: %s", " ".join(cmd))
    try:
        return subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as exc:
        _log.warning("Failed to launch app-mode window: %s", exc)
        return None


def _open_browser_fallback(reason: str = "") -> None:
    """Open in the default browser (no blocking dialog)."""
    import webbrowser

    _log.info("Opening in default browser. Reason: %s", reason)
    webbrowser.open(_URL)


# ---------------------------------------------------------------------------
# Streamlit launch strategies
# ---------------------------------------------------------------------------

def _start_streamlit_subprocess() -> subprocess.Popen:
    """Launch Streamlit as a child process (used when running from source)."""
    streamlit_cmd = [
        sys.executable, "-m", "streamlit", "run",
        str(_STREAMLIT_SCRIPT),
        "--server.headless=true",
        f"--server.port={_PORT}",
        f"--server.address={_HOST}",
        "--global.developmentMode=false",
    ]

    return subprocess.Popen(
        streamlit_cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(_ROOT),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _apply_streamlit_env() -> None:
    os.environ["STREAMLIT_SERVER_HEADLESS"] = "true"
    os.environ["STREAMLIT_SERVER_PORT"] = str(_PORT)
    os.environ["STREAMLIT_SERVER_ADDRESS"] = _HOST
    os.environ["STREAMLIT_GLOBAL_DEVELOPMENT_MODE"] = "false"


def _streamlit_flag_options() -> dict:
    return {
        "server.headless": True,
        "server.port": _PORT,
        "server.address": _HOST,
        "global.developmentMode": False,
    }


def _streamlit_worker_entry() -> None:
    """Second process: Streamlit bootstrap on the real main thread (signal handlers work)."""
    _apply_streamlit_env()
    _log.info("streamlit-worker: importing bootstrap …")
    from streamlit.web.bootstrap import run as st_run

    st_run(str(_STREAMLIT_SCRIPT), False, [], _streamlit_flag_options())


def _worker_log_path() -> Path:
    return _win_local_appdata_dir() / "streamlit_worker.log" if sys.platform == "win32" else _frozen_fallback_log().with_name("streamlit_worker.log")


def _start_streamlit_frozen_worker() -> subprocess.Popen:
    """Re-launch same .exe with --streamlit-worker (Streamlit cannot run in a daemon thread)."""
    _apply_streamlit_env()
    _log.info("Starting Streamlit worker subprocess …")
    wlog = _worker_log_path()
    wlog.parent.mkdir(parents=True, exist_ok=True)
    wlog_fh = open(wlog, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "--streamlit-worker"],
        cwd=str(Path(sys.executable).resolve().parent),
        stdout=wlog_fh,
        stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    _log.info("Worker log: %s", wlog)
    return proc


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    _setup_logging()
    _log.info("launcher start  frozen=%s  python=%s  root=%s", _is_frozen(), sys.version, _ROOT)

    if not _is_frozen() and "--skip-update" not in sys.argv:
        if _check_for_updates():
            subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--skip-update"])
            sys.exit(0)

    _kill_stale_on_port(_HOST, _PORT)

    proc = None
    if _is_frozen():
        try:
            proc = _start_streamlit_frozen_worker()
            atexit.register(_kill_proc, proc)
        except Exception:
            _log.error("Streamlit worker failed to start:\n%s", traceback.format_exc())
            _open_browser_fallback(reason="Streamlit worker failed")
            return
    else:
        proc = _start_streamlit_subprocess()
        atexit.register(_kill_proc, proc)

    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))

    if not _wait_for_server(_URL, _STARTUP_TIMEOUT, proc):
        reason = "Streamlit did not respond with HTTP 200 within 60 s"
        if proc is not None and proc.poll() is not None:
            reason = f"Streamlit process exited early (code {proc.returncode})"
            wlog = _worker_log_path() if _is_frozen() else None
            if wlog and wlog.exists():
                try:
                    tail = wlog.read_text(encoding="utf-8", errors="replace")[-2000:]
                    reason += f"\nWorker log tail:\n{tail}"
                except OSError:
                    pass
        _log.error("Server wait failed: %s", reason)
        if proc:
            _kill_proc(proc)
        if _is_frozen():
            _open_browser_fallback(reason=reason)
            return
        print(f"ERROR: {reason}", file=sys.stderr)
        sys.exit(1)

    _log.info("Streamlit is listening — launching native window")

    browser_proc = _launch_app_mode_window(_URL)
    if browser_proc:
        _log.info("App-mode window launched (pid %d), waiting for it to close …", browser_proc.pid)
        browser_proc.wait()
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
    if _is_frozen() and len(sys.argv) > 1 and sys.argv[1] == "--streamlit-worker":
        _setup_logging()
        try:
            _streamlit_worker_entry()
        finally:
            logging.shutdown()
        sys.exit(0)
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
