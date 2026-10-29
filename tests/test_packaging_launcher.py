"""packaging/launcher.py: workspace install, upgrade and data folder choice."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

LAUNCHER_PATH = Path(__file__).resolve().parent.parent / "packaging" / "launcher.py"
# A packaged build ships tests/ but not packaging/.
pytestmark = pytest.mark.skipif(not LAUNCHER_PATH.is_file(), reason="packaging/ is not shipped in builds")


@pytest.fixture(scope="module")
def launcher():
    spec = importlib.util.spec_from_file_location("q50diag_launcher", LAUNCHER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bundle(root: Path, build: str, value: str) -> Path:
    (root / "q50diag").mkdir(parents=True)
    (root / "q50diag" / "__init__.py").write_text(value)
    (root / "BUILD_ID").write_text(build + "\n")
    return root


def test_first_run_copies_source(launcher, tmp_path):
    source = _bundle(tmp_path / "bundle", "b1", "A = 1\n")
    workspace = tmp_path / "data" / "app"
    launcher.install_source(source, workspace)
    assert (workspace / "q50diag" / "__init__.py").read_text() == "A = 1\n"
    assert launcher.build_id(workspace) == "b1"


def test_same_build_keeps_edits(launcher, tmp_path):
    source = _bundle(tmp_path / "bundle", "b1", "A = 1\n")
    workspace = tmp_path / "data" / "app"
    launcher.install_source(source, workspace)
    (workspace / "q50diag" / "__init__.py").write_text("A = 2\n")
    launcher.install_source(source, workspace)
    assert (workspace / "q50diag" / "__init__.py").read_text() == "A = 2\n"


def test_new_build_moves_old_source_aside_and_keeps_logs(launcher, tmp_path):
    workspace = tmp_path / "data" / "app"
    launcher.install_source(_bundle(tmp_path / "old", "b1", "A = 1\n"), workspace)
    (workspace / "q50diag" / "__init__.py").write_text("A = edited\n")
    (workspace / "logs").mkdir()
    (workspace / "logs" / "run.csv").write_text("x\n")

    launcher.install_source(_bundle(tmp_path / "new", "b2", "A = 3\n"), workspace)

    assert (workspace / "q50diag" / "__init__.py").read_text() == "A = 3\n"
    assert (workspace / "logs" / "run.csv").is_file()
    backups = list(workspace.parent.glob("app-backup-b1-*"))
    assert len(backups) == 1
    assert (backups[0] / "q50diag" / "__init__.py").read_text() == "A = edited\n"


def test_workspace_override(launcher, tmp_path, monkeypatch):
    monkeypatch.setenv(launcher.WORKSPACE_ENV, str(tmp_path / "custom"))
    assert launcher.user_data_root() == tmp_path / "custom"


def test_run_pytest_exits_with_pytest_code_before_teardown(launcher, monkeypatch, tmp_path):
    exits = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pytest, "main", lambda args: pytest.ExitCode.TESTS_FAILED)
    monkeypatch.setattr(launcher.os, "_exit", lambda code: exits.append(code))
    monkeypatch.setattr(launcher.sys, "path", list(launcher.sys.path))
    launcher.run_pytest(["-q"])
    assert exits == [1]
