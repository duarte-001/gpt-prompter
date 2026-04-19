# PyInstaller runtime hook — runs before the entry script (launcher.py).
# Writes a one-line boot stamp so diagnostics exist even if launcher never reaches __main__.

from __future__ import annotations

import os
import sys
import time
from pathlib import Path


def _append_line(path: Path, line: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
    except OSError:
        pass


def _is_embedded_streamlit_worker() -> bool:
    return len(sys.argv) > 1 and sys.argv[1] == "--streamlit-worker"


if getattr(sys, "frozen", False) and not _is_embedded_streamlit_worker():
    exe = Path(sys.executable).resolve()
    stamp = (
        f"{time.strftime('%Y-%m-%d %H:%M:%S')} pyi_rth_boot pid={os.getpid()} "
        f"exe={exe}\n"
    )
    tmp = os.environ.get("TEMP") or os.environ.get("TMP") or str(Path.home())
    paths = [
        exe.with_name("StockAssistant.log"),
        Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        / "StockAssistant"
        / "StockAssistant.log",
        Path(tmp) / "StockAssistant_last_boot.log",
    ]
    for p in paths:
        _append_line(p, stamp)
