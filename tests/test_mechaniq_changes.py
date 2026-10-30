"""Tests for the MechanIQ change log. Every test runs in a throwaway git
repository under tmp_path with a local identity; the project repository and
its git config are never touched. The test command is injected so a change
runs one tiny test file, not the whole suite."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from q50diag.mechaniq import changes
from q50diag.mechaniq.changes import (
    APPLIED,
    COMMIT_FAILED,
    NO_GIT,
    REJECTED,
    RELOAD_FAILED,
    TESTS_FAILED,
    UNDO,
    UNDO_FAILED,
    UNDO_RELOAD_FAILED,
    ChangeEntry,
    ChangeLog,
    clean_summary,
    git_available,
    pytest_command,
)
from q50diag.mechaniq.reload import ReloadPlan, ReloadReport

FORBIDDEN_TRAILERS = ("co-authored-by", "claude", "generated with")
# A packaged build runs this suite on the user's computer, which may have no git.
# The ChangeLog fixture runs nested pytest, which needs the launcher flag when frozen.
pytestmark = [pytest.mark.skipif(not git_available(), reason="git is not installed"), pytest.mark.keep_frozen]


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, stdin=subprocess.DEVNULL, capture_output=True, text=True, check=True
    )
    return result.stdout


class FakeReloader:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[ReloadPlan, str]] = []
        self.fail = fail

    def apply(self, plan: ReloadPlan, label: str = "") -> ReloadReport:
        self.calls.append((plan, label))
        if self.fail:
            raise RuntimeError("reload exploded")
        return ReloadReport(kind=plan.kind, reloaded=plan.live_modules)


@pytest.fixture
def checks(tmp_path) -> dict[str, list[str]]:
    """Test commands: one passing and one failing test file outside the repo."""
    folder = tmp_path / "checks"
    folder.mkdir()
    (folder / "test_pass.py").write_text("def test_pass():\n    assert True\n")
    (folder / "test_fail.py").write_text("def test_fail():\n    assert 1 == 2, 'deliberate failure'\n")
    base = [*pytest_command(), "-q", "-x", "-p", "no:cacheprovider"]
    return {"pass": [*base, str(folder / "test_pass.py")], "fail": [*base, str(folder / "test_fail.py")]}


def _make_repo(root: Path, commit: bool) -> Path:
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.name", "MechanIQ Test")
    git(root, "config", "user.email", "mechaniq-test@example.invalid")
    git(root, "config", "commit.gpgsign", "false")
    files = {
        "q50diag/__init__.py": "",
        "q50diag/data/__init__.py": "",
        "q50diag/data/table.py": "VALUE = 1\n",
        "q50diag/data/other.py": "OTHER = 1\n",
        "q50diag/session.py": "SESSION = 1\n",
        "tests/test_table.py": "def test_table():\n    assert True\n",
        "docs/notes.md": "notes\n",
    }
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    if commit:
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "initial")
    return root


@pytest.fixture
def repo(tmp_path) -> Path:
    return _make_repo(tmp_path / "repo", commit=True)


@pytest.fixture
def empty_repo(tmp_path) -> Path:
    return _make_repo(tmp_path / "empty", commit=False)


def _change_log(root: Path, command: list[str]) -> ChangeLog:
    return ChangeLog(root, root / "logs" / "mechaniq-changes.jsonl", test_command=command)


def _assert_clean_message(root: Path, rev: str = "HEAD") -> str:
    message = git(root, "log", "-1", "--format=%B", rev).strip()
    assert not any(word in message.lower() for word in FORBIDDEN_TRAILERS), message
    return message


def test_apply_on_repo_without_commits(empty_repo, checks):
    log = _change_log(empty_repo, checks["pass"])
    reloader = FakeReloader()
    (empty_repo / "q50diag/data/table.py").write_text("VALUE = 2\n")

    entry = log.apply("bump table value", ["q50diag/data/table.py"], reloader)

    assert entry.status == APPLIED, entry.report()
    assert len(entry.commit) == 40
    assert entry.reload_kind == "live"
    assert _assert_clean_message(empty_repo) == "fix(mechaniq): bump table value"
    assert git(empty_repo, "show", "--name-only", "--format=", "HEAD").split() == ["q50diag/data/table.py"]
    untracked = git(empty_repo, "status", "--porcelain", "--untracked-files=all")
    assert "?? q50diag/data/other.py" in untracked and "?? q50diag/session.py" in untracked
    plan, label = reloader.calls[0]
    assert plan.live_modules == ("q50diag.data.table",)
    assert label == f"Code change {entry.id}: bump table value"
    lines = (empty_repo / "logs/mechaniq-changes.jsonl").read_text().splitlines()
    assert [json.loads(line)["id"] for line in lines] == [entry.id]
    assert log.entries() == [entry]


def test_failing_tests_stop_reload_and_commit(repo, checks):
    log = _change_log(repo, checks["fail"])
    reloader = FakeReloader()
    head = git(repo, "rev-parse", "HEAD")
    (repo / "q50diag/data/table.py").write_text("VALUE = 3\n")

    entry = log.apply("break things", ["q50diag/data/table.py"], reloader)

    assert entry.status == TESTS_FAILED
    assert entry.commit == ""
    assert "deliberate failure" in entry.test_tail
    assert "1 failed" in entry.test_tail
    assert "deliberate failure" in entry.report()
    assert reloader.calls == []
    assert git(repo, "rev-parse", "HEAD") == head
    assert log.entries()[-1].status == TESTS_FAILED


def test_failing_tests_on_repo_without_commits(empty_repo, checks):
    log = _change_log(empty_repo, checks["fail"])
    entry = log.apply("nope", ["q50diag/data/table.py"], FakeReloader())
    assert entry.status == TESTS_FAILED
    head = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"], cwd=empty_repo, stdin=subprocess.DEVNULL, capture_output=True
    )
    assert head.returncode != 0


def test_commit_contains_only_listed_files(repo, checks):
    log = _change_log(repo, checks["pass"])
    (repo / "q50diag/data/table.py").write_text("VALUE = 4\n")
    (repo / "q50diag/data/other.py").write_text("OTHER = 4\n")
    (repo / "q50diag/session.py").write_text("SESSION = 4\n")
    git(repo, "add", "q50diag/session.py")  # staged by someone else; must stay out of the commit

    entry = log.apply("only the table", ["q50diag/data/table.py"], FakeReloader())

    assert entry.status == APPLIED
    assert git(repo, "show", "--name-only", "--format=", "HEAD").split() == ["q50diag/data/table.py"]
    status = git(repo, "status", "--porcelain")
    assert " M q50diag/data/other.py" in status
    assert "M  q50diag/session.py" in status


def test_restart_required_change_is_still_committed(repo, checks):
    log = _change_log(repo, checks["pass"])
    (repo / "q50diag/session.py").write_text("SESSION = 5\n")
    entry = log.apply("session tweak", ["q50diag/session.py"], FakeReloader())
    assert entry.status == APPLIED
    assert entry.reload_kind == "restart"
    assert entry.commit


def test_reload_failure_still_commits(repo, checks):
    log = _change_log(repo, checks["pass"])
    (repo / "q50diag/data/table.py").write_text("VALUE = 6\n")
    entry = log.apply("table", ["q50diag/data/table.py"], FakeReloader(fail=True))
    assert entry.status == RELOAD_FAILED
    assert entry.reload_kind == "restart"
    assert entry.commit
    assert "reload exploded" in entry.detail


def test_without_git_change_is_reloaded_but_not_committed(repo, checks, monkeypatch):
    monkeypatch.setattr(changes, "git_available", lambda: False)
    log = _change_log(repo, checks["pass"])
    reloader = FakeReloader()
    head = git(repo, "rev-parse", "HEAD")
    (repo / "q50diag/data/table.py").write_text("VALUE = 8\n")

    entry = log.apply("no git here", ["q50diag/data/table.py"], reloader)

    assert entry.status == COMMIT_FAILED
    assert NO_GIT in entry.detail
    assert len(reloader.calls) == 1
    assert git(repo, "rev-parse", "HEAD") == head


def test_git_available_is_false_without_git(monkeypatch):
    monkeypatch.setattr(changes.shutil, "which", lambda name: None)
    assert not git_available()


def test_pytest_command_uses_the_launcher_flag_when_frozen(monkeypatch):
    assert pytest_command()[:1] == [sys.executable]
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert pytest_command() == [sys.executable, "--run-pytest"]


def test_undo_reverts_and_reloads(repo, checks):
    reloader = FakeReloader()
    log = ChangeLog(repo, test_command=checks["pass"], reloader=reloader)
    (repo / "q50diag/data/table.py").write_text("VALUE = 7\n")
    applied = log.apply("seven", ["q50diag/data/table.py"])
    git(repo, "add", "q50diag/session.py")  # unchanged, harmless
    (repo / "q50diag/data/other.py").write_text("OTHER = 7\n")
    git(repo, "add", "q50diag/data/other.py")  # unrelated staged change: git revert would refuse

    undo = log.undo(applied.id)

    assert undo.status == UNDO, undo.report()
    assert undo.undoes == applied.id
    assert (repo / "q50diag/data/table.py").read_text() == "VALUE = 1\n"
    message = _assert_clean_message(repo)
    assert message.startswith('Revert "fix(mechaniq): seven"')
    assert applied.commit in message
    assert git(repo, "show", "--name-only", "--format=", "HEAD").split() == ["q50diag/data/table.py"]
    assert "M  q50diag/data/other.py" in git(repo, "status", "--porcelain")
    assert [label for _plan, label in reloader.calls] == [
        f"Code change {applied.id}: seven",
        f"Undo code change {applied.id}",
    ]
    assert reloader.calls[1][0].live_modules == ("q50diag.data.table",)
    assert [e.status for e in log.entries()] == [APPLIED, UNDO]
    assert log.undo(applied.id).status == REJECTED


def test_undo_of_root_commit_removes_added_file(empty_repo, checks):
    log = _change_log(empty_repo, checks["pass"])
    applied = log.apply("add table", ["q50diag/data/table.py"], FakeReloader())
    undo = log.undo(applied.id, FakeReloader())
    assert undo.status == UNDO, undo.report()
    assert not (empty_repo / "q50diag/data/table.py").exists()


def test_undo_refuses_dirty_files_and_unknown_ids(repo, checks):
    log = _change_log(repo, checks["pass"])
    (repo / "q50diag/data/table.py").write_text("VALUE = 8\n")
    applied = log.apply("eight", ["q50diag/data/table.py"], FakeReloader())
    (repo / "q50diag/data/table.py").write_text("VALUE = 9\n")
    assert log.undo(applied.id, FakeReloader()).status == "undo_failed"
    assert (repo / "q50diag/data/table.py").read_text() == "VALUE = 9\n"
    assert log.undo("missing", FakeReloader()).status == REJECTED


def test_invalid_input_is_rejected_before_tests(repo, checks, tmp_path):
    log = _change_log(repo, checks["fail"])  # a failing command proves the tests never ran
    outside = tmp_path / "elsewhere.py"
    outside.write_text("")
    cases = {
        "outside": (["../elsewhere.py"], "outside the project root"),
        "absolute outside": ([str(outside)], "outside the project root"),
        "missing": (["q50diag/data/missing.py"], "does not exist"),
        "none": ([], "No files listed"),
    }
    for name, (files, reason) in cases.items():
        entry = log.apply("summary", files, FakeReloader())
        assert entry.status == REJECTED, name
        assert reason in entry.detail, name
    assert log.apply("   ", ["q50diag/data/table.py"], FakeReloader()).status == REJECTED


def test_nothing_to_commit_is_reported(repo, checks):
    log = _change_log(repo, checks["pass"])
    entry = log.apply("no edit", ["q50diag/data/table.py"], FakeReloader())
    assert entry.status == APPLIED
    assert entry.commit == ""
    assert "nothing committed" in entry.detail


def test_commit_failure_is_reported(repo, checks):
    (repo / ".git" / "hooks" / "pre-commit").write_text("#!/bin/sh\necho hook says no >&2\nexit 1\n")
    (repo / ".git" / "hooks" / "pre-commit").chmod(0o755)
    log = _change_log(repo, checks["pass"])
    (repo / "q50diag/data/table.py").write_text("VALUE = 10\n")
    entry = log.apply("blocked", ["q50diag/data/table.py"], FakeReloader())
    assert entry.status == COMMIT_FAILED
    assert "hook says no" in entry.detail


def test_commit_and_undo_without_a_configured_identity(repo, checks, monkeypatch, tmp_path):
    """A packaged build may run where git has no user.name or user.email."""
    git(repo, "config", "--unset", "user.name")
    git(repo, "config", "--unset", "user.email")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-global-config"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name in ("EMAIL", "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        monkeypatch.delenv(name, raising=False)
    log = _change_log(repo, checks["pass"])
    (repo / "q50diag/data/table.py").write_text("VALUE = 11\n")

    entry = log.apply("no identity", ["q50diag/data/table.py"], FakeReloader())
    assert entry.status == APPLIED, entry.report()
    assert git(repo, "log", "-1", "--format=%an <%ae>").strip() == "q50diag <q50diag@localhost>"
    undo = log.undo(entry.id, FakeReloader())
    assert undo.status == UNDO and len(undo.commit) == 40, undo.report()
    assert (repo / "q50diag/data/table.py").read_text() == "VALUE = 1\n"


def test_commit_keeps_the_configured_identity(repo, checks):
    log = _change_log(repo, checks["pass"])
    (repo / "q50diag/data/table.py").write_text("VALUE = 12\n")
    entry = log.apply("own identity", ["q50diag/data/table.py"], FakeReloader())
    assert entry.status == APPLIED, entry.report()
    assert git(repo, "log", "-1", "--format=%an <%ae>").strip() == "MechanIQ Test <mechaniq-test@example.invalid>"


def test_pending_edits_ignores_baseline_and_failed_versions(repo, checks):
    (repo / "q50diag/data/other.py").write_text("OTHER = 'before mechaniq'\n")
    log = _change_log(repo, checks["pass"])
    assert log.pending_edits() == []

    (repo / "q50diag/data/table.py").write_text("VALUE = 11\n")
    (repo / "tests/test_new.py").write_text("def test_new():\n    assert True\n")
    (repo / "docs/notes.md").write_text("edited\n")
    assert log.pending_edits() == ["q50diag/data/table.py", "tests/test_new.py"]

    log.apply("stray edits", log.pending_edits(), FakeReloader())
    assert log.pending_edits() == []

    (repo / "q50diag/data/other.py").write_text("OTHER = 'edited by mechaniq'\n")
    assert log.pending_edits() == ["q50diag/data/other.py"]

    failing = _change_log(repo, checks["fail"])
    (repo / "q50diag/data/table.py").write_text("VALUE = 12\n")
    assert "q50diag/data/table.py" in failing.pending_edits()
    failing.apply("fails", ["q50diag/data/table.py"], FakeReloader())
    assert "q50diag/data/table.py" not in failing.pending_edits()
    (repo / "q50diag/data/table.py").write_text("VALUE = 13\n")
    assert "q50diag/data/table.py" in failing.pending_edits()


def test_pending_edits_on_repo_without_commits(empty_repo, checks):
    log = _change_log(empty_repo, checks["pass"])
    assert log.pending_edits() == []  # everything is untracked, but it predates the log
    (empty_repo / "q50diag/data/new_module.py").write_text("NEW = 1\n")
    (empty_repo / "q50diag/data/__pycache__").mkdir()
    (empty_repo / "q50diag/data/__pycache__/new_module.cpython-312.pyc").write_bytes(b"\0")
    assert log.pending_edits() == ["q50diag/data/new_module.py"]


def test_pending_edits_outside_a_git_repo(tmp_path, checks):
    assert _change_log(tmp_path, checks["pass"]).pending_edits() == []


def test_summary_cleaning_and_entry_round_trip():
    assert clean_summary("fix(mechaniq):  decode\nEPS   angle ") == "decode EPS angle"
    assert len(clean_summary("x" * 300)) == 100
    entry = ChangeEntry("abc", "2026-10-23T10:00:00", "s", ("a.py",), APPLIED, "live", "f" * 40, "tail")
    assert ChangeEntry.from_json(entry.to_json()) == entry


def test_related_tests_selects_tests_that_name_the_changed_module(tmp_path):
    from q50diag.mechaniq.changes import related_tests

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_nissan.py").write_text("from q50diag.protocol.nissan import decode\n")
    (tests_dir / "test_other.py").write_text("from q50diag.protocol import obd2\n")
    (tests_dir / "test_unrelated.py").write_text("import json\n")

    assert related_tests(["q50diag/protocol/nissan.py"], tmp_path) == ["tests/test_nissan.py"]
    assert related_tests(["q50diag/protocol/obd2.py"], tmp_path) == ["tests/test_other.py"]
    assert related_tests(["tests/test_unrelated.py"], tmp_path) == ["tests/test_unrelated.py"]
    assert related_tests(["README.md"], tmp_path) == []


def test_related_tests_follow_modules_that_import_the_changed_module(tmp_path):
    from q50diag.mechaniq.changes import related_tests

    package = tmp_path / "q50diag" / "protocol"
    package.mkdir(parents=True)
    (package / "obd_fields.py").write_text("class Field: ...\n")
    (package / "obd2.py").write_text("from q50diag.protocol.obd_fields import Field\n")
    (package / "reader.py").write_text("from q50diag.protocol import obd2\n")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_reader.py").write_text("from q50diag.protocol.reader import read\n")
    (tests_dir / "test_broadcast.py").write_text("import json\n")

    changed = ["q50diag/protocol/obd_fields.py", "tests/test_broadcast.py"]
    assert related_tests(changed, tmp_path) == ["tests/test_broadcast.py", "tests/test_reader.py"]


def test_related_tests_run_everything_when_a_changed_module_has_no_test(tmp_path):
    from q50diag.mechaniq.changes import related_tests

    (tmp_path / "q50diag").mkdir()
    (tmp_path / "q50diag" / "orphan.py").write_text("VALUE = 1\n")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_broadcast.py").write_text("import json\n")

    assert related_tests(["q50diag/orphan.py", "tests/test_broadcast.py"], tmp_path) == []


def test_undo_that_fails_the_tests_restores_the_files(repo, checks):
    log = ChangeLog(repo, test_command=checks["pass"], reloader=FakeReloader())
    (repo / "q50diag/data/table.py").write_text("VALUE = 9\n")
    applied = log.apply("nine", ["q50diag/data/table.py"])
    head = git(repo, "rev-parse", "HEAD")
    log.test_command = checks["fail"]

    undo = log.undo(applied.id)

    assert undo.status == UNDO_FAILED, undo.report()
    assert "nothing was undone" in undo.detail
    assert "deliberate failure" in undo.report()
    assert (repo / "q50diag/data/table.py").read_text() == "VALUE = 9\n"
    assert git(repo, "rev-parse", "HEAD") == head
    assert git(repo, "status", "--porcelain", "--", "q50diag/data/table.py") == ""
    log.test_command = checks["pass"]
    assert log.undo(applied.id).status == UNDO


def test_undo_reports_reload_failure_and_counts_as_undone(repo, checks):
    log = ChangeLog(repo, test_command=checks["pass"])
    (repo / "q50diag/data/table.py").write_text("VALUE = 10\n")
    applied = log.apply("ten", ["q50diag/data/table.py"], FakeReloader())

    undo = log.undo(applied.id, FakeReloader(fail=True))

    assert undo.status == UNDO_RELOAD_FAILED
    assert undo.commit and "reload exploded" in undo.detail
    assert (repo / "q50diag/data/table.py").read_text() == "VALUE = 1\n"
    assert log.undo(applied.id).status == REJECTED
