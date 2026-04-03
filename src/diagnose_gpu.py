"""
Print GPU + Ollama diagnostics (run from project root):

    python -m src.diagnose_gpu

Does not change your system; use this when the GPU stays idle during chat/embed.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _run(cmd: list[str]) -> tuple[int, str]:
    try:
        kwargs: dict = {
            "capture_output": True,
            "text": True,
            "timeout": 30,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        p = subprocess.run(cmd, **kwargs)
        out = (p.stdout or "") + (p.stderr or "")
        return p.returncode, out.strip()
    except FileNotFoundError:
        return -1, ""
    except Exception as e:  # noqa: BLE001
        return -1, str(e)


def main() -> None:
    # Avoid UnicodeEncodeError on Windows consoles (cp1252)
    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

    print("=== NVIDIA driver / GPU (nvidia-smi) ===\n")
    if shutil.which("nvidia-smi"):
        code, out = _run(["nvidia-smi"])
        print(out if out else "(no output)")
        if code != 0:
            print(f"(exit code {code})")
    else:
        print(
            "nvidia-smi not found on PATH.\n"
            "Install NVIDIA drivers from https://www.nvidia.com/drivers - "
            "then reopen the terminal and try again."
        )

    print("\n=== Ollama ===\n")
    base = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").strip().rstrip("/")
    try:
        import httpx

        r = httpx.get(f"{base}/api/version", timeout=3.0)
        print(f"Reachable: {base}")
        print(f"version: {r.json() if r.status_code == 200 else r.text}")
    except Exception as e:  # noqa: BLE001
        print(f"Not reachable at {base}: {e}")
        print("Start Ollama (tray app or: ollama serve) and retry.")

    ps_out = ""
    if shutil.which("ollama"):
        code, out = _run(["ollama", "ps"])
        ps_out = out or ""
        if ps_out:
            print("\nollama ps:\n", ps_out)

    if ps_out and re.search(r"\b100%\s+CPU\b", ps_out, re.IGNORECASE):
        print(
            "\n*** NOTE: At least one model shows '100% CPU' in ollama ps. ***\n"
            "Ollama is running that model on CPU (not GPU). On 4GB GPUs this often happens when\n"
            "VRAM is full: unload the chat model (stop chatting) before heavy RAG indexing,\n"
            "or use smaller models (e.g. llama3.2:3b for chat). Check server.log for 'offload' / GPU.\n"
        )

    log_hint = Path(os.environ.get("LOCALAPPDATA", "")) / "Ollama" / "server.log"
    print("\n=== Ollama GPU hints (read this) ===\n")
    print(
        "1) Embedding + chat both use Ollama. If the GPU is idle, Ollama may be running a model "
        "on CPU (VRAM too small, drivers, or Windows using the wrong GPU).\n"
    )
    print(
        "2) After a prompt, open server.log and search for GPU / CUDA / layer / offload:\n"
        f"   {log_hint}\n"
    )
    print(
        "3) Set env OLLAMA_DEBUG=1, restart Ollama, reproduce one prompt - more detail in the log.\n"
    )
    print(
        "4) Try a smaller chat model so it fits in VRAM, e.g. ollama pull llama3.2:3b "
        "(sidebar in the app) - oversized models spill to CPU.\n"
    )
    print(
        "5) Laptop: Settings - System - Display - Graphics - add ollama.exe - "
        "High performance (NVIDIA). Or run: scripts\\set_ollama_gpu_high_performance.ps1\n"
    )
    print(
        "6) Upstream issues: https://github.com/ollama/ollama/issues?q=windows+gpu\n"
    )


if __name__ == "__main__":
    main()
