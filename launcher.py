"""
Desktop launcher: checks for updates, then runs the Streamlit app inside
a native OS window via pywebview.

Usage:  python launcher.py
        python launcher.py --skip-update
"""

from __future__ import annotations

import atexit
import os
import signal
import socket
import subprocess
import sys
import threading
import time
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

    from streamlit.web.bootstrap import run as st_run

    thread = threading.Thread(
        target=st_run,
        args=(str(_STREAMLIT_SCRIPT), False, [], flag_options),
        daemon=True,
    )
    thread.start()
    return thread


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if not _is_frozen() and "--skip-update" not in sys.argv:
        if _check_for_updates():
            subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--skip-update"])
            sys.exit(0)

    import webview

    proc = None
    if _is_frozen():
        _start_streamlit_inprocess()
    else:
        proc = _start_streamlit_subprocess()
        atexit.register(_kill_proc, proc)

    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))

    if not _wait_for_server(_HOST, _PORT, _STARTUP_TIMEOUT):
        print("ERROR: Streamlit did not start within the timeout.", file=sys.stderr)
        if proc:
            _kill_proc(proc)
        sys.exit(1)

    icon_path = str(_ICON) if _ICON.exists() else None

    webview.create_window(
        "Stock Assistant",
        _URL,
        width=1200,
        height=820,
        min_size=(800, 500),
    )
    webview.start(icon=icon_path)

    if proc:
        _kill_proc(proc)


if __name__ == "__main__":
    main()
