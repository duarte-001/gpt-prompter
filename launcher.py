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


def _ensure_dotnet_root_for_pythonnet() -> None:
    """clr_loader/coreclr needs DOTNET_ROOT; frozen GUI apps may not inherit a full shell PATH."""
    if sys.platform != "win32":
        return
    if os.environ.get("DOTNET_ROOT"):
        return
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pfx86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    for base in (
        Path(pf) / "dotnet",
        Path(pfx86) / "dotnet",
        Path(r"C:\Program Files\dotnet"),
        Path(r"C:\Program Files (x86)\dotnet"),
    ):
        if (base / "dotnet.exe").exists():
            os.environ["DOTNET_ROOT"] = str(base.resolve())
            return


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
            "Logs: StockAssistant.log next to the .exe, "
            "%LOCALAPPDATA%\\StockAssistant\\StockAssistant.log, or "
            "%TEMP%\\StockAssistant_last_boot.log.\n\n"
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


def _start_streamlit_frozen_worker() -> subprocess.Popen:
    """Re-launch same .exe with --streamlit-worker (Streamlit cannot run in a daemon thread)."""
    _apply_streamlit_env()
    _log.info("Starting Streamlit worker subprocess …")
    return subprocess.Popen(
        [sys.executable, "--streamlit-worker"],
        cwd=str(Path(sys.executable).resolve().parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


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
        _ensure_dotnet_root_for_pythonnet()
        if os.environ.get("DOTNET_ROOT"):
            os.environ.setdefault("PYTHONNET_RUNTIME", "coreclr")
        else:
            os.environ.pop("PYTHONNET_RUNTIME", None)
        _log.info(
            "DOTNET_ROOT=%r PYTHONNET_RUNTIME=%r",
            os.environ.get("DOTNET_ROOT"),
            os.environ.get("PYTHONNET_RUNTIME"),
        )

    proc = None
    if _is_frozen():
        try:
            proc = _start_streamlit_frozen_worker()
            atexit.register(_kill_proc, proc)
        except Exception:
            _log.error("Streamlit worker failed to start:\n%s", traceback.format_exc())
            _open_browser_and_block(reason="Streamlit worker failed — see StockAssistant.log")
            return
    else:
        proc = _start_streamlit_subprocess()
        atexit.register(_kill_proc, proc)

    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))

    if not _wait_for_server(_HOST, _PORT, _STARTUP_TIMEOUT):
        reason = "Streamlit did not respond within 60 s"
        if proc is not None and proc.poll() is not None:
            reason = f"Streamlit process exited early (code {proc.returncode})"
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
