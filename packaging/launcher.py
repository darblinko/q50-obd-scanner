"""Entry point of the packaged app (PyInstaller one-folder build).

The q50diag package is shipped as plain .py source under `app_src/` inside the
bundle, not as frozen bytecode, so MechanIQ can edit and hot reload it. The
bundle itself may be read-only (macOS App Translocation, a protected folder on
Windows), so on start the source is copied once per build into a per-user,
writable workspace and imported from there. Everything q50diag derives from
`__file__` (logs/, recordings, the MechanIQ change log and working directory)
then lands in that workspace.

`<exe> --run-pytest ARGS` runs pytest inside the frozen interpreter; a frozen
`sys.executable` is this program and has no `-m pytest`."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import NoReturn

APP_DIR_NAME = "q50diag"
SOURCE_ITEMS = (
    "q50diag",
    "tests",
    "docs",
    "pyproject.toml",
    ".gitignore",
    "BUILD_ID",
    "LICENSE",
    "LICENSES",
    "THIRD_PARTY_NOTICES.md",
)
PYTEST_FLAG = "--run-pytest"
WORKSPACE_ENV = "Q50DIAG_HOME"
GIT_IDENTITY = ("-c", "user.name=q50diag", "-c", "user.email=q50diag@localhost")
GIT_TIMEOUT_S = 60


def bundled_source() -> Path:
    base = getattr(sys, "_MEIPASS", None) or Path(__file__).resolve().parent
    return Path(base) / "app_src"


def user_data_root() -> Path:
    """Per-user writable folder, following each OS convention."""
    override = os.environ.get(WORKSPACE_ENV)
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / APP_DIR_NAME


def build_id(folder: Path) -> str:
    try:
        return (folder / "BUILD_ID").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _move_aside(workspace: Path, old_build: str) -> None:
    """Keep an earlier build's source (possibly edited by MechanIQ) instead of deleting it."""
    backup = workspace.parent / f"app-backup-{old_build or 'unknown'}-{time.strftime('%Y%m%d-%H%M%S')}"
    backup.mkdir(parents=True)
    for name in (*SOURCE_ITEMS, ".git"):
        item = workspace / name
        if item.exists():
            shutil.move(str(item), str(backup / name))


def install_source(source: Path, workspace: Path) -> None:
    """Copy the bundled source into `workspace` unless this build is already there.
    logs/ and other user files in the workspace are left in place."""
    wanted = build_id(source)
    current = build_id(workspace)
    if current == wanted and (workspace / "q50diag").is_dir():
        return
    workspace.mkdir(parents=True, exist_ok=True)
    if (workspace / "q50diag").exists():
        _move_aside(workspace, current)
    for name in SOURCE_ITEMS:
        item = source / name
        if item.is_dir():
            shutil.copytree(item, workspace / name)
        elif item.is_file():
            shutil.copy2(item, workspace / name)


def usable_git() -> str | None:
    """Path of a git that runs without prompting. On macOS /usr/bin/git is a
    shim that opens an "install developer tools" dialog when they are missing."""
    git = shutil.which("git")
    if not git:
        return None
    if sys.platform == "darwin" and git == "/usr/bin/git":
        probe = subprocess.run(
            ["xcode-select", "-p"], stdin=subprocess.DEVNULL, capture_output=True, timeout=GIT_TIMEOUT_S
        )
        if probe.returncode != 0:
            return None
    return git


def init_git_repo(workspace: Path) -> None:
    """MechanIQ commits each applied change and undoes by commit, so the
    workspace must be a git repository. Without git, edits still apply but
    are not recorded or undoable."""
    git = usable_git()
    if git is None or (workspace / ".git").exists():
        return
    for args in (
        ["init", "-q"],
        ["add", "-A"],
        [*GIT_IDENTITY, "commit", "-q", "-m", f"q50diag {build_id(workspace)}"],
    ):
        result = subprocess.run(
            [git, *args],
            cwd=workspace,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_S,
        )
        if result.returncode != 0:
            print(f"q50diag: git {args[-1]} failed in {workspace}: {result.stderr.strip()}", file=sys.stderr)
            return


def run_pytest(args: list[str]) -> NoReturn:
    """Exits as soon as pytest has reported. Interpreter teardown can crash in
    PySide (Qt objects destroyed out of order), which on Windows replaced the
    exit code and lost the buffered summary on its way to a pipe."""
    sys.path.insert(0, os.getcwd())
    import pytest

    code = int(pytest.main(args))
    for stream in (sys.stdout, sys.stderr):
        if stream is not None:
            stream.flush()
    os._exit(code)


def prepare_workspace(workspace: Path) -> None:
    install_source(bundled_source(), workspace)
    try:
        init_git_repo(workspace)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"q50diag: git setup skipped: {exc}", file=sys.stderr)


def import_app(workspace: Path):
    """Import q50diag from the workspace. If an edit left it unimportable,
    move that copy aside, restore the shipped source and import again."""
    try:
        from q50diag.__main__ import main as app_main
    except Exception as exc:  # noqa: BLE001 (any import failure means a broken edit)
        print(f"q50diag: workspace source failed to import ({exc!r}); restoring the shipped copy", file=sys.stderr)
        for name in [name for name in sys.modules if name == "q50diag" or name.startswith("q50diag.")]:
            del sys.modules[name]
        _move_aside(workspace, build_id(workspace) + "-broken")
        prepare_workspace(workspace)
        from q50diag.__main__ import main as app_main
    return app_main


def run_from_bundle(args: list[str]) -> int:
    """Read-only fallback: the app runs; MechanIQ edits do not."""
    sys.path.insert(0, str(bundled_source()))
    from q50diag.__main__ import main as app_main

    if "--log-dir" not in args:  # the default logs/ beside the source is read-only here
        args = [*args, "--log-dir", str(Path(tempfile.gettempdir()) / "q50diag-logs")]
    return app_main(args)


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == PYTEST_FLAG:
        run_pytest(sys.argv[2:])
    workspace = user_data_root() / "app"
    try:
        prepare_workspace(workspace)
    except OSError as exc:
        print(f"q50diag: cannot prepare {workspace}: {exc}; running from the bundle", file=sys.stderr)
        return run_from_bundle(sys.argv[1:])
    sys.path.insert(0, str(workspace))
    return import_app(workspace)(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
