"""
Build a standalone StockAssistant.exe using PyInstaller.

Usage:
    python build.py            # windowed (no console)
    python build.py --debug    # console visible for diagnostics

A dedicated clean venv (.venv-build) is used automatically so that only
the app's actual dependencies end up in the bundle (not any globally
installed heavy packages like torch that would crash PyInstaller).

Output:  dist/StockAssistant/StockAssistant.exe  (one-dir mode)
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

_DEBUG_BUILD = "--debug" in sys.argv

_ROOT = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Build venv management
# ---------------------------------------------------------------------------
_BUILD_VENV = _ROOT / ".venv-build"
_BUILD_PYTHON = _BUILD_VENV / "Scripts" / "python.exe"


def _ensure_build_venv() -> None:
    """Create .venv-build and install deps if it doesn't exist yet."""
    if _BUILD_PYTHON.exists():
        return
    print("Creating clean build venv (.venv-build)…")
    subprocess.run([sys.executable, "-m", "venv", str(_BUILD_VENV)], check=True)
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
    hidden_imports = ["src"] + src_modules

    hidden_str = repr(hidden_imports)

    datas = [
        (str(_ROOT / "some_tickers.json"), "."),
        (str(_ROOT / "requirements.txt"), "."),
        (str(_ROOT / "assets"), "assets"),
        (str(_ROOT / ".streamlit"), ".streamlit"),
    ]
    datas_str = repr([(s.replace("\\", "/"), d) for s, d in datas])

    spec_content = textwrap.dedent(f"""\
        import sys
        sys.setrecursionlimit(sys.getrecursionlimit() * 5)

        from PyInstaller.utils.hooks import collect_all, collect_data_files

        st_datas, st_binaries, st_hiddenimports = collect_all('streamlit')
        wv_datas, wv_binaries, wv_hiddenimports = collect_all('webview')
        pn_datas, pn_binaries, pn_hiddenimports = collect_all('pythonnet')
        cl_datas, cl_binaries, cl_hiddenimports = collect_all('clr_loader')

        all_datas = st_datas + wv_datas + pn_datas + cl_datas
        all_binaries = st_binaries + wv_binaries + pn_binaries + cl_binaries
        all_hiddenimports = st_hiddenimports + wv_hiddenimports + pn_hiddenimports + cl_hiddenimports

        block_cipher = None

        a = Analysis(
            ['{_q(_ROOT / "launcher.py")}'],
            pathex=['{_q(_ROOT)}'],
            binaries=all_binaries,
            datas={datas_str} + all_datas,
            hiddenimports={hidden_str} + all_hiddenimports,
            hookspath=[],
            hooksconfig={{}},
            runtime_hooks=[],
            excludes=[],
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

    import shutil
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
        if final_dist.exists():
            shutil.rmtree(final_dist, ignore_errors=True)
        (_ROOT / "dist").mkdir(exist_ok=True)
        shutil.copytree(dist_dir / "StockAssistant", final_dist)
        exe = final_dist / "StockAssistant.exe"
        print(f"\nBuild succeeded.\nExecutable: {exe}")
    else:
        print(f"\nBuild failed (exit code {result.returncode}).", file=sys.stderr)
        sys.exit(result.returncode)


if __name__ == "__main__":
    main()
