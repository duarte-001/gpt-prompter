"""
Build a standalone StockAssistant.exe using PyInstaller.

Usage:
    python build.py            # windowed (no console)
    python build.py --debug    # console visible for diagnostics

A dedicated clean venv (.venv-build) is used automatically so that only
the app's actual dependencies end up in the bundle (not any globally
installed heavy packages like torch that would crash PyInstaller).

Output: ``dist/StockAssistant/StockAssistant.exe`` (one-dir mode).

Intermediate publish copies live under ``%TEMP%/StockAssistant_build/`` only — never a
second app folder inside ``dist/`` (avoids a leftover ``StockAssistant_staging``).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

_DEBUG_BUILD = "--debug" in sys.argv

_ROOT = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Build venv management
# ---------------------------------------------------------------------------
_BUILD_VENV = _ROOT / ".venv-build"
_BUILD_PYTHON = _BUILD_VENV / "Scripts" / "python.exe"


def _bootstrap_pip_into_venv(py_exe: Path) -> None:
    """Install pip when `python -m venv --without-pip` was used (ensurepip often fails under OneDrive)."""
    import tempfile
    import urllib.request

    url = "https://bootstrap.pypa.io/get-pip.py"
    with tempfile.NamedTemporaryFile(suffix="_get-pip.py", delete=False) as f:
        tmp = Path(f.name)
    try:
        with urllib.request.urlopen(url, timeout=120) as resp:
            tmp.write_bytes(resp.read())
        subprocess.run([str(py_exe), str(tmp)], check=True)
    finally:
        tmp.unlink(missing_ok=True)


def _ensure_build_venv() -> None:
    """Create .venv-build and install deps if it doesn't exist yet."""
    if _BUILD_PYTHON.exists():
        chk = subprocess.run(
            [str(_BUILD_PYTHON), "-m", "pip", "--version"],
            capture_output=True,
        )
        if chk.returncode == 0:
            return
        print("Existing .venv-build is incomplete; recreating …")
        shutil.rmtree(_BUILD_VENV, ignore_errors=True)
    elif _BUILD_VENV.exists():
        shutil.rmtree(_BUILD_VENV, ignore_errors=True)

    print("Creating clean build venv (.venv-build)…")
    venv_cmd = [sys.executable, "-m", "venv", str(_BUILD_VENV)]
    if sys.platform == "win32":
        venv_cmd.append("--copies")

    try:
        subprocess.run(venv_cmd, check=True)
    except subprocess.CalledProcessError:
        print("Standard venv failed (often OneDrive / ensurepip). Retrying with --without-pip …")
        shutil.rmtree(_BUILD_VENV, ignore_errors=True)
        subprocess.run(venv_cmd + ["--without-pip"], check=True)
        _bootstrap_pip_into_venv(_BUILD_PYTHON)

    subprocess.run(
        [str(_BUILD_PYTHON), "-m", "pip", "install", "-r", str(_ROOT / "requirements.txt"), "pyinstaller", "-q"],
        check=True,
        cwd=str(_ROOT),
    )
_SEP = ";"  # Windows path separator for --add-data
_SPEC = _ROOT / "StockAssistant.spec"


def _q(p: Path) -> str:
    """Forward-slash quoted path string safe for use inside the .spec file."""
    return str(p).replace("\\", "/")


def _write_spec() -> None:
    src_modules = [
        f"src.{p.stem}"
        for p in sorted((_ROOT / "src").glob("*.py"))
        if p.stem != "__init__"
    ]
    # Chroma: ProductTelemetryClient via importlib; ServerAPI defaults to chromadb.api.rust.RustBindingsAPI
    # (string in Settings) so PyInstaller never traces it. chromadb_rust_bindings ships a large .pyd.
    hidden_imports = ["src"] + src_modules + [
        "chromadb.telemetry.product.posthog",
        "chromadb.api.rust",
        "chromadb_rust_bindings",
    ]

    hidden_str = repr(hidden_imports)

    datas = [
        (str(_ROOT / "some_tickers.json"), "."),
        (str(_ROOT / "requirements.txt"), "."),
        # Same icon as embedded .exe — beside launcher for desktop shortcuts (IconLocation)
        (str(_ROOT / "assets" / "icon.ico"), "."),
        (str(_ROOT / "assets"), "assets"),
        (str(_ROOT / ".streamlit"), ".streamlit"),
        # Streamlit bootstrap needs a real script path on disk; hiddenimports alone
        # only place modules in the archive (no _internal/src/streamlit_app.py).
        (str(_ROOT / "src"), "src"),
    ]
    datas_str = repr([(s.replace("\\", "/"), d) for s, d in datas])

    spec_content = textwrap.dedent(f"""\
        import sys
        sys.setrecursionlimit(sys.getrecursionlimit() * 5)

        from PyInstaller.utils.hooks import collect_all, collect_data_files

        st_datas, st_binaries, st_hiddenimports = collect_all('streamlit')
        chroma_rust_datas, chroma_rust_binaries, chroma_rust_hiddenimports = collect_all(
            'chromadb_rust_bindings'
        )

        all_datas = st_datas + chroma_rust_datas
        all_binaries = st_binaries + chroma_rust_binaries
        all_hiddenimports = st_hiddenimports + chroma_rust_hiddenimports

        block_cipher = None

        a = Analysis(
            ['{_q(_ROOT / "launcher.py")}'],
            pathex=['{_q(_ROOT)}'],
            binaries=all_binaries,
            datas={datas_str} + all_datas,
            hiddenimports={hidden_str} + all_hiddenimports,
            hookspath=[],
            hooksconfig={{}},
            runtime_hooks=['{_q(_ROOT / "scripts" / "pyi_rth_stockassistant_bootlog.py")}'],
            excludes=['webview', 'pywebview', 'pythonnet', 'clr', 'clr_loader'],
            win_no_prefer_redirects=False,
            win_private_assemblies=False,
            cipher=block_cipher,
            noarchive=False,
        )

        pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

        exe = EXE(
            pyz,
            a.scripts,
            [],
            exclude_binaries=True,
            name='StockAssistant',
            debug=False,
            bootloader_ignore_signals=False,
            strip=False,
            upx=True,
            console={_DEBUG_BUILD},
            icon='{_q(_ROOT / "assets" / "icon.ico")}',
        )

        coll = COLLECT(
            exe,
            a.binaries,
            a.zipfiles,
            a.datas,
            strip=False,
            upx=True,
            upx_exclude=[],
            name='StockAssistant',
        )
    """)

    _SPEC.write_text(spec_content, encoding="utf-8")
    print(f"Wrote spec: {_SPEC}")


def main() -> None:
    _ensure_build_venv()
    _write_spec()

    import tempfile

    build_base = Path(tempfile.gettempdir()) / "StockAssistant_build"
    work_dir = build_base / "build"
    dist_dir = build_base / "dist"

    cmd = [
        str(_BUILD_PYTHON), "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--workpath", str(work_dir),
        "--distpath", str(dist_dir),
        str(_SPEC),
    ]
    print("Running:", " ".join(cmd))
    result = subprocess.run(cmd, cwd=str(_ROOT))

    if result.returncode == 0:
        final_dist = _ROOT / "dist" / "StockAssistant"
        # Old builds used dist/StockAssistant_staging — remove so dist/ never looks like two apps.
        legacy_staging = _ROOT / "dist" / "StockAssistant_staging"
        if legacy_staging.exists():
            shutil.rmtree(legacy_staging, ignore_errors=True)
        if final_dist.exists():
            for _ in range(5):
                shutil.rmtree(final_dist, ignore_errors=True)
                if not final_dist.exists():
                    break
                time.sleep(0.4)
        (_ROOT / "dist").mkdir(exist_ok=True)
        publish_staging = build_base / "StockAssistant_publish_next"
        if publish_staging.exists():
            shutil.rmtree(publish_staging, ignore_errors=True)
        shutil.copytree(dist_dir / "StockAssistant", publish_staging)
        try:
            if final_dist.exists():
                shutil.rmtree(final_dist, ignore_errors=True)
                time.sleep(0.15)
            shutil.move(str(publish_staging), str(final_dist))
        except OSError:
            shutil.rmtree(final_dist, ignore_errors=True)
            time.sleep(0.15)
            shutil.copytree(publish_staging, final_dist)
            shutil.rmtree(publish_staging, ignore_errors=True)
        exe = final_dist / "StockAssistant.exe"
        print(f"\nBuild succeeded.\nExecutable: {exe}")
    else:
        print(f"\nBuild failed (exit code {result.returncode}).", file=sys.stderr)
        sys.exit(result.returncode)


if __name__ == "__main__":
    main()
