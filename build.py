"""
Build a standalone StockAssistant.exe using PyInstaller.

Usage:
    pip install pyinstaller
    python build.py

Output:  dist/StockAssistant/StockAssistant.exe  (one-dir mode)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_SEP = ";"  # Windows path separator for --add-data


def _add_data(src: str, dest: str) -> str:
    return f"{src}{_SEP}{dest}"


def main() -> None:
    data_args: list[str] = []

    pairs = [
        (str(_ROOT / "some_tickers.json"), "."),
        (str(_ROOT / "requirements.txt"), "."),
        (str(_ROOT / "assets"), "assets"),
        (str(_ROOT / ".streamlit"), ".streamlit"),
        (str(_ROOT / "src"), "src"),
    ]
    for src, dest in pairs:
        data_args += ["--add-data", _add_data(src, dest)]

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", "StockAssistant",
        "--icon", str(_ROOT / "assets" / "icon.ico"),
        "--windowed",
        "--noconfirm",
        *data_args,
        str(_ROOT / "launcher.py"),
    ]

    print("Running:", " ".join(cmd))
    result = subprocess.run(cmd, cwd=str(_ROOT))
    if result.returncode == 0:
        exe = _ROOT / "dist" / "StockAssistant" / "StockAssistant.exe"
        print(f"\nBuild succeeded.\nExecutable: {exe}")
    else:
        print(f"\nBuild failed (exit code {result.returncode}).", file=sys.stderr)
        sys.exit(result.returncode)


if __name__ == "__main__":
    main()
