"""
Desktop launcher: checks for updates, then runs the Streamlit app inside
a native OS window via pywebview.

Usage:  python launcher.py
        python launcher.py --skip-update
"""

from __future__ import annotations

import atexit
import logging
import os
import signal
import socket
import subprocess
import sys
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
# Diagnostic log (file-based, written next to the .exe in frozen builds)
# ---------------------------------------------------------------------------
_log = logging.getLogger("launcher")


def _setup_logging() -> None:
    _log.setLevel(logging.DEBUG)
    if _is_frozen():
        log_path = Path(sys.executable).with_name("StockAssistant.log")
        handler: logging.Handler = logging.FileHandler(str(log_path), encoding="utf-8")
    else:
        handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    _log.addHandler(handler)


def _is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def _port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port)) == 0


def _wait_for_server(host: str, port: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _port_open(host, port):
            return True
        time.sleep(0.25)
    return False


def _kill_proc(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _check_for_updates() -> bool:
    """Run the updater. Returns True if code was updated (caller should restart)."""
    try:
        from src.updater import check_and_update
        return check_and_update()
    except Exception:
        return False


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


def _open_browser_and_block(reason: str = "") -> None:
    """Last resort: open in default browser and keep Streamlit alive."""
    import ctypes
    import webbrowser

    _log.info("Falling back to default browser. Reason: %s", reason)
    webbrowser.open(_URL)
    if _is_frozen() and sys.platform == "win32":
        msg = (
            "The native window could not be started, so the app was opened in your default browser.\n\n"
        )
        if reason:
            msg += f"Cause: {reason}\n\n"
        msg += (
            "A log file (StockAssistant.log) has been written next to the .exe.\n\n"
            "When you are finished, close this dialog to exit."
        )
        ctypes.windll.user32.MessageBoxW(0, msg, "Stock Assistant", 0x40)
        return
    stop = threading.Event()

    def _on_sigint(*_: object) -> None:
        stop.set()

    signal.signal(signal.SIGINT, _on_sigint)
    while not stop.is_set():
        time.sleep(1)


_streamlit_error: str | None = None


def _start_streamlit_inprocess() -> threading.Thread:
    """Run the Streamlit server in a daemon thread (used in frozen .exe builds).

    In a PyInstaller bundle sys.executable is the .exe itself, so spawning
    ``sys.executable -m streamlit`` would re-launch the whole app in a loop.
    Instead we call Streamlit's bootstrap.run() directly in-process.
    """
    os.environ["STREAMLIT_SERVER_HEADLESS"] = "true"
    os.environ["STREAMLIT_SERVER_PORT"] = str(_PORT)
    os.environ["STREAMLIT_SERVER_ADDRESS"] = _HOST
    os.environ["STREAMLIT_GLOBAL_DEVELOPMENT_MODE"] = "false"

    flag_options = {
        "server.headless": True,
        "server.port": _PORT,
        "server.address": _HOST,
        "global.developmentMode": False,
    }

    _log.info("Importing streamlit.web.bootstrap …")
    try:
        from streamlit.web.bootstrap import run as st_run
    except Exception:
        _log.error("Failed to import Streamlit:\n%s", traceback.format_exc())
        raise

    def _run_streamlit() -> None:
        global _streamlit_error
        try:
            st_run(str(_STREAMLIT_SCRIPT), False, [], flag_options)
        except Exception:
            _streamlit_error = traceback.format_exc()
            _log.error("Streamlit crashed:\n%s", _streamlit_error)

    _log.info("Starting Streamlit thread …")
    thread = threading.Thread(target=_run_streamlit, daemon=True)
    thread.start()
    return thread


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

    if _is_frozen():
        os.environ.setdefault("PYTHONNET_RUNTIME", "coreclr")
        _log.info("PYTHONNET_RUNTIME=%s", os.environ.get("PYTHONNET_RUNTIME"))

    proc = None
    if _is_frozen():
        try:
            _start_streamlit_inprocess()
        except Exception:
            _log.error("Streamlit failed to start:\n%s", traceback.format_exc())
            _open_browser_and_block(reason=f"Streamlit import failed — see StockAssistant.log")
            return
    else:
        proc = _start_streamlit_subprocess()
        atexit.register(_kill_proc, proc)

    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))

    if not _wait_for_server(_HOST, _PORT, _STARTUP_TIMEOUT):
        reason = _streamlit_error or "Streamlit did not respond within 60 s"
        _log.error("Server wait failed: %s", reason)
        if proc:
            _kill_proc(proc)
        if _is_frozen():
            _open_browser_and_block(reason=reason)
            return
        print(f"ERROR: {reason}", file=sys.stderr)
        sys.exit(1)

    _log.info("Streamlit is listening — launching native window")
    icon_path = str(_ICON) if _ICON.exists() else None

    try:
        import webview
        _log.info("pywebview imported successfully")

        webview.create_window(
            "Stock Assistant",
            _URL,
            width=1200,
            height=820,
            min_size=(800, 500),
        )
        webview.start(icon=icon_path)
    except Exception:
        wv_err = traceback.format_exc()
        _log.error("pywebview failed:\n%s", wv_err)
        if _is_frozen():
            _open_browser_and_block(reason="Native window failed — see StockAssistant.log")
        else:
            raise

    if proc:
        _kill_proc(proc)


if __name__ == "__main__":
    main()
