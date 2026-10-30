# PyInstaller spec for q50diag: one-folder build on every OS, plus a .app on macOS.
#
#   uv run --group package pyinstaller --noconfirm --clean packaging/q50diag.spec
#
# Output: dist/q50diag/ (Windows: q50diag.exe inside; Linux: q50diag inside)
#         dist/Q50 Diag.app (macOS)
#
# The q50diag package is analysed (so every dependency it imports is collected)
# and then removed from the frozen archive; its .py source ships under
# app_src/ and packaging/launcher.py runs it from a per-user writable copy.
#
# Build on the target OS and CPU (Windows x64, macOS arm64, macOS x86_64):
# PyInstaller does not cross-compile, and claude-agent-sdk ships one CLI
# binary per platform wheel (no universal2 wheel), so there is no universal2 build.
#
# Environment:
#   Q50DIAG_BUILD_ID  overrides the build stamp (default: git short hash + UTC time)
#   Q50DIAG_NO_PYTEST set to 1 to leave pytest out (MechanIQ then cannot apply changes)

import importlib.util
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

ROOT = Path(SPECPATH).resolve().parent
APP_NAME = "q50diag"
DISPLAY_NAME = "Q50 Diag"
BUNDLE_ID = "io.github.darblinko.q50diag"
SOURCE_DIRS = ("q50diag", "tests")
SDK_DIR = Path(importlib.util.find_spec("claude_agent_sdk").origin).parent
CLI_NAME = "claude.exe" if sys.platform == "win32" else "claude"
WITH_PYTEST = os.environ.get("Q50DIAG_NO_PYTEST") != "1"

# Qt modules nothing in the app imports; excluding them keeps them out even if
# an optional import in a dependency would otherwise pull them in.
QT_EXCLUDES = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.QtQml", "PySide6.QtQuick",
    "PySide6.QtQuickWidgets", "PySide6.QtQuick3D", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.Qt3DCore", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtGraphs", "PySide6.QtDesigner", "PySide6.QtUiTools",
    "PySide6.QtLocation", "PySide6.QtPositioning", "PySide6.QtBluetooth", "PySide6.QtSql",
]


def source_datas():
    """(file, destination folder) for the plain-source copy under app_src/:
    the package, its tests, the Markdown docs the MechanIQ prompt cites,
    pyproject.toml (pytest settings), .gitignore (keeps logs/ out of git) and the
    licence files, which must travel with the OBDb-derived tables."""
    files = [path for top in SOURCE_DIRS for path in (ROOT / top).rglob("*")
             if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"]
    files += list((ROOT / "docs").rglob("*.md"))
    files += [ROOT / "pyproject.toml", ROOT / ".gitignore", ROOT / "LICENSE", ROOT / "THIRD_PARTY_NOTICES.md"]
    files += list((ROOT / "LICENSES").glob("*.txt"))
    return [(str(path), str(Path("app_src") / path.parent.relative_to(ROOT))) for path in sorted(files)]


def build_id_file():
    """Write the build stamp the launcher compares to decide whether to refresh the workspace."""
    stamp = os.environ.get("Q50DIAG_BUILD_ID")
    if not stamp:
        result = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                                capture_output=True, text=True)
        revision = result.stdout.strip() if result.returncode == 0 else "nogit"
        stamp = f"{revision}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    target = Path(workpath) / "BUILD_ID"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(stamp + "\n", encoding="utf-8")
    return (str(target), "app_src")


def pytest_bundle():
    """pytest, run in-process through `<exe> --run-pytest`, for MechanIQ's test gate."""
    if not WITH_PYTEST:
        return [], []
    hidden = ["pytest", *collect_submodules("_pytest"), "pluggy", "iniconfig", "packaging"]
    datas = []
    for dist in ("pytest", "pluggy", "iniconfig", "packaging"):
        datas += copy_metadata(dist)
    return hidden, datas


def install_cli(meipass_dir):
    """Copy the Claude Code CLI to where claude_agent_sdk looks for it
    (<sys._MEIPASS>/claude_agent_sdk/_bundled/), byte for byte.

    It is deliberately not given to Analysis: PyInstaller reclassifies any
    Mach-O or PE file as a binary, and on macOS rewrites its load commands and
    replaces Anthropic's Developer ID signature with an ad-hoc one."""
    source = SDK_DIR / "_bundled" / CLI_NAME
    if not source.is_file():
        raise SystemExit(f"{source} missing: install the claude-agent-sdk wheel for this platform")
    target = Path(meipass_dir) / "claude_agent_sdk" / "_bundled" / CLI_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


pytest_hidden, pytest_datas = pytest_bundle()

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=[],
    # The Claude Code CLI is added after the build by install_cli().
    datas=[*source_datas(), build_id_file(), *pytest_datas],
    hiddenimports=[*collect_submodules("q50diag"), *pytest_hidden],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", *QT_EXCLUDES],
    noarchive=False,
)

# Keep q50diag's dependencies but not q50diag itself: it must load from the
# editable source copy, and a frozen copy in the archive would shadow it.
a.pure = [entry for entry in a.pure if entry[0] != "q50diag" and not entry[0].startswith("q50diag.")]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    # Windows: a console-subsystem exe whose console the bootloader hides at
    # once. Child console programs (claude.exe, git, pytest runs) inherit
    # that hidden console instead of each opening a visible window, which a
    # windowed (console=False) parent would cause. macOS needs console=False
    # for the .app.
    console=sys.platform != "darwin",
    hide_console="hide-early" if sys.platform == "win32" else None,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    upx=False,
)

coll = COLLECT(exe, a.binaries, a.datas, name=APP_NAME, upx=False)

# PyInstaller targets are assembled when constructed, so the code below runs
# on the finished output.
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{DISPLAY_NAME}.app",
        bundle_identifier=BUNDLE_ID,
        info_plist={
            "CFBundleDisplayName": DISPLAY_NAME,
            "CFBundleShortVersionString": "0.1.0",
            "NSHighResolutionCapable": True,
            # PySide6 6.12 wheels are tagged macosx_14_0.
            "LSMinimumSystemVersion": "14.0",
        },
    )
    app_path = Path(DISTPATH) / f"{DISPLAY_NAME}.app"
    install_cli(app_path / "Contents" / "Frameworks")
    # Re-seal the outer bundle (ad hoc) so it covers the added file; without
    # --deep, the CLI keeps its own Anthropic signature.
    subprocess.run(["codesign", "--force", "--sign", "-", str(app_path)], check=True)
else:
    install_cli(Path(DISTPATH) / APP_NAME / "_internal")
