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
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_ICON = _ROOT / "assets" / "icon.ico"
_STREAMLIT_SCRIPT = _ROOT / "src" / "streamlit_app.py"

_HOST = "127.0.0.1"
_PORT = 8501
_URL = f"http://{_HOST}:{_PORT}"
_STARTUP_TIMEOUT = 30  # seconds


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


def _check_for_updates() -> None:
    """Run the updater; if it pulled new code, re-launch this script and exit."""
    try:
        from src.updater import check_and_update

        if check_and_update():
            os.execv(sys.executable, [sys.executable, str(_ROOT / "launcher.py"), "--skip-update"])
    except Exception:
        pass


def main() -> None:
    if "--skip-update" not in sys.argv:
        _check_for_updates()

    import webview  # imported after potential update so new code is picked up

    streamlit_cmd = [
        sys.executable, "-m", "streamlit", "run",
        str(_STREAMLIT_SCRIPT),
        "--server.headless=true",
        f"--server.port={_PORT}",
        f"--server.address={_HOST}",
        "--global.developmentMode=false",
    ]

    proc = subprocess.Popen(
        streamlit_cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    atexit.register(_kill_proc, proc)
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))

    if not _wait_for_server(_HOST, _PORT, _STARTUP_TIMEOUT):
        print("ERROR: Streamlit did not start within the timeout.", file=sys.stderr)
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
    webview.start(icon=icon_path, gui="edgechromium")

    _kill_proc(proc)


if __name__ == "__main__":
    main()
