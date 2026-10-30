"""Change log for the code changes MechanIQ applies to the running app.

`ChangeLog.apply()` runs the test suite, hot reloads the changed modules,
commits only the listed files as `fix(mechaniq): <summary>` and appends one
JSON line to `logs/mechaniq-changes.jsonl`. Failing tests stop it before the
reload and the commit. `undo()` reverts the commit for those files and reloads them.

The calls block on subprocesses (pytest up to TEST_TIMEOUT_S, 300 s), so call them from a
worker thread; the reloader moves its own work to the Qt main thread."""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import uuid
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from q50diag.mechaniq.reload import ReloadReport, classify

log = logging.getLogger(__name__)

LOG_NAME = Path("logs") / "mechaniq-changes.jsonl"
COMMIT_PREFIX = "fix(mechaniq): "
WATCHED_DIRS = ("q50diag", "tests")
TEST_TIMEOUT_S = 300.0  # the full suite takes about 240 s; related-test selection keeps most runs short
# Matches packaging/launcher.py GIT_IDENTITY, used for the workspace's first commit.
MANAGED_IDENTITY = ("-c", "user.name=q50diag", "-c", "user.email=q50diag@localhost")
GIT_TIMEOUT_S = 60.0
TAIL_LINES = 40
TAIL_CHARS = 4000
SUMMARY_CHARS = 100
NO_GIT = "git is not installed on this computer"
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # git's empty tree, the parent of a root commit

APPLIED = "applied"
TESTS_FAILED = "tests_failed"
RELOAD_FAILED = "reload_failed"  # committed, but the running app needs a restart
COMMIT_FAILED = "commit_failed"
REJECTED = "rejected"
UNDO = "undo"
UNDO_FAILED = "undo_failed"
UNDO_RELOAD_FAILED = "undo_reload_failed"  # reverted and committed, but the running app needs a restart
UNDONE_STATUSES = (UNDO, UNDO_RELOAD_FAILED)


@dataclass(frozen=True)
class ChangeEntry:
    id: str
    time: str
    summary: str
    files: tuple[str, ...]
    status: str
    reload_kind: str
    commit: str
    test_tail: str
    detail: str = ""  # reload report, git error or rejection reason
    undoes: str = ""  # id of the change an undo entry reverted

    def to_json(self) -> str:
        data = asdict(self)
        data["files"] = list(self.files)
        return json.dumps(data, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> ChangeEntry:
        data = json.loads(text)
        data["files"] = tuple(data.get("files", ()))
        known = cls.__dataclass_fields__
        return cls(**{key: value for key, value in data.items() if key in known})

    def report(self) -> str:
        """Plain text for the agent and the Changes tab."""
        lines = [f"Change {self.id}: {self.status}", f"Summary: {self.summary}", "Files: " + ", ".join(self.files)]
        if self.commit:
            lines.append(f"Commit: {self.commit}")
        if not self.detail.startswith("Reload:"):
            lines.append(f"Reload: {self.reload_kind}")
        if self.detail:
            lines.append(self.detail)
        if self.status in (TESTS_FAILED, UNDO_FAILED) and self.test_tail:
            lines += ["Test output (tail):", self.test_tail]
        return "\n".join(lines)


def clean_summary(summary: str) -> str:
    """One line, no repeated prefix, at most SUMMARY_CHARS characters."""
    text = " ".join(summary.split())
    if text.lower().startswith(COMMIT_PREFIX.strip()):
        text = text[len(COMMIT_PREFIX.strip()) :].strip()
    return text[:SUMMARY_CHARS].rstrip()


def tail(text: str, lines: int = TAIL_LINES, chars: int = TAIL_CHARS) -> str:
    return "\n".join(text.rstrip().splitlines()[-lines:])[-chars:]


def _digest(path: Path) -> str:
    try:
        return hashlib.sha1(path.read_bytes()).hexdigest()
    except OSError:
        return ""  # deleted or unreadable


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def pytest_command() -> list[str]:
    """Command prefix that runs pytest with this interpreter. A frozen build's
    executable has no `-m`; its launcher runs pytest for `--run-pytest`."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--run-pytest"]
    return [sys.executable, "-m", "pytest"]


def git_available() -> bool:
    """False when git is missing, or on macOS when /usr/bin/git is the shim
    that opens an "install developer tools" dialog because they are absent."""
    git = shutil.which("git")
    if git is None:
        return False
    if sys.platform == "darwin" and git == "/usr/bin/git":
        try:
            return (
                subprocess.run(
                    ["xcode-select", "-p"],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=GIT_TIMEOUT_S,
                ).returncode
                == 0
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
    return True


class ChangeLog:
    """Applies, records and undoes MechanIQ code changes in one git repository.

    `test_command` defaults to `pytest -q -x` run by this interpreter (pytest_command());
    tests inject a narrower command. `reloader` (a HotReloader) is used when
    apply() or undo() is not given one."""

    def __init__(
        self,
        project_root: Path,
        log_path: Path | None = None,
        test_command: Sequence[str] | None = None,
        test_timeout_s: float = TEST_TIMEOUT_S,
        reloader=None,
        test_selector: Callable[[Sequence[str], Path], list[str]] | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.log_path = Path(log_path) if log_path else self.project_root / LOG_NAME
        self.test_command = list(test_command or (*pytest_command(), "-q", "-x"))
        self.test_timeout_s = test_timeout_s
        self.reloader = reloader
        self.test_selector = test_selector
        self._lock = threading.Lock()
        self._git_ok = git_available()
        self._baseline = self._dirty_digests()  # edits present before MechanIQ started
        self._failed: dict[str, str] = {}  # path -> digest of a version whose tests failed

    # Public API -------------------------------------------------------------
    def apply(self, summary: str, files: Sequence[str], reloader=None) -> ChangeEntry:
        with self._lock:
            return self._apply(clean_summary(summary), files, reloader or self.reloader)

    def undo(self, entry_id: str, reloader=None) -> ChangeEntry:
        with self._lock:
            return self._undo(entry_id, reloader or self.reloader)

    def entries(self) -> list[ChangeEntry]:
        try:
            lines = self.log_path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        found: list[ChangeEntry] = []
        for number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                found.append(ChangeEntry.from_json(line))
            except (ValueError, TypeError) as exc:
                log.warning("%s line %d unreadable: %s", self.log_path, number, exc)
        return found

    def find(self, entry_id: str) -> ChangeEntry | None:
        return next((e for e in self.entries() if e.id == entry_id), None)

    def pending_edits(self) -> list[str]:
        """Modified, added or deleted files under q50diag/ and tests/ that are
        not committed, excluding edits present when this log was created and
        versions whose tests already failed."""
        pending: list[str] = []
        for path, digest in sorted(self._dirty_digests().items()):
            if self._baseline.get(path) == digest or self._failed.get(path) == digest:
                continue
            pending.append(path)
        return pending

    # Apply ------------------------------------------------------------------
    def _apply(self, summary: str, files: Sequence[str], reloader) -> ChangeEntry:
        entry_id = uuid.uuid4().hex[:8]
        relative, problem = self._check_input(summary, files)
        if problem:
            return self._record(self._entry(entry_id, summary, relative, REJECTED, detail=problem))
        passed, output = self._run_tests(relative)
        if not passed:
            self._failed.update({path: _digest(self.project_root / path) for path in relative})
            detail = "Tests failed: nothing was reloaded or committed. Fix the failure and call apply_change again."
            return self._record(self._entry(entry_id, summary, relative, TESTS_FAILED, test_tail=output, detail=detail))
        reload_kind, report, reload_error = self._reload(relative, reloader, f"Code change {entry_id}: {summary}")
        commit, git_error = self._commit(COMMIT_PREFIX + summary, relative)
        if git_error:
            status = COMMIT_FAILED
        else:
            status = RELOAD_FAILED if reload_error else APPLIED
            self._forget(relative)
        notes = [
            report,
            git_error,
            "" if commit or git_error else "No uncommitted changes in these files; nothing committed.",
        ]
        detail = "\n".join(note for note in notes if note)
        entry = self._entry(entry_id, summary, relative, status, reload_kind, commit, tail(output), detail)
        return self._record(entry)

    def _check_input(self, summary: str, files: Sequence[str]) -> tuple[tuple[str, ...], str]:
        if isinstance(files, (str, Path)):
            files = [files]
        relative: list[str] = []
        for item in files:
            path = Path(item)
            absolute = (path if path.is_absolute() else self.project_root / path).resolve()
            try:
                text = absolute.relative_to(self.project_root).as_posix()
            except ValueError:
                return tuple(
                    relative
                ), f"{item} is outside the project root {self.project_root}; list project files only."
            if not absolute.exists() and not self._tracked(text):
                return tuple(relative), f"{text} does not exist and is not tracked by git; check the path."
            if text not in relative:
                relative.append(text)
        if not relative:
            return (), "No files listed; pass the files you changed."
        if not summary:
            return tuple(relative), "Empty summary; describe the change in one line."
        return tuple(relative), ""

    def _run_tests(self, files: Sequence[str] = ()) -> tuple[bool, str]:
        env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
        selected = self.test_selector(files, self.project_root) if self.test_selector else []
        try:
            result = subprocess.run(
                self.test_command + selected,
                cwd=self.project_root,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=self.test_timeout_s,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            partial = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            return False, tail(partial + f"\nTests timed out after {self.test_timeout_s:.0f} s")
        except OSError as exc:
            return False, f"Could not run the tests: {exc}"
        return result.returncode == 0, tail(result.stdout + result.stderr)

    def _reload(self, files: tuple[str, ...], reloader, label: str) -> tuple[str, str, str]:
        """Returns (reload kind, report text, error text)."""
        plan = classify(files, self.project_root)
        fallback = "none" if plan.kind == "none" else "restart"
        if reloader is None:
            return fallback, "No reloader attached: restart the app to load the change.", ""
        try:
            report: ReloadReport = reloader.apply(plan, label)
        except Exception as exc:  # noqa: BLE001 (the commit still records the change)
            log.exception("reload failed")
            return fallback, f"Reload failed: {exc}. Restart the app to load the change.", str(exc)
        return (fallback if report.error else report.kind), report.text(), report.error

    # Undo -------------------------------------------------------------------
    def _undo(self, entry_id: str, reloader) -> ChangeEntry:
        new_id = uuid.uuid4().hex[:8]
        entries = self.entries()
        target = next((e for e in entries if e.id == entry_id), None)
        if target is None or not target.commit:
            detail = f"No committed change with id {entry_id}; list the change log for valid ids."
            return self._record(self._entry(new_id, f"undo {entry_id}", (), REJECTED, detail=detail, undoes=entry_id))
        summary = f"undo {entry_id}: {target.summary}"
        if any(e.undoes == entry_id and e.status in UNDONE_STATUSES for e in entries):
            return self._record(
                self._entry(
                    new_id,
                    summary,
                    target.files,
                    REJECTED,
                    detail=f"Change {entry_id} was already undone.",
                    undoes=entry_id,
                )
            )
        commit, error, output = self._revert(target)
        if error:
            entry = self._entry(
                new_id, summary, target.files, UNDO_FAILED, test_tail=output, detail=error, undoes=entry_id
            )
            return self._record(entry)
        self._forget(target.files)
        kind, report, reload_error = self._reload(target.files, reloader, f"Undo code change {entry_id}")
        status = UNDO_RELOAD_FAILED if reload_error else UNDO
        return self._record(self._entry(new_id, summary, target.files, status, kind, commit, output, report, entry_id))

    def _revert(self, target: ChangeEntry) -> tuple[str, str, str]:
        """Revert target.commit for its files only. `git revert` refuses to run
        while unrelated changes are staged, so the commit's diff is reverse
        applied to the working tree, tested, and committed with a pathspec.
        When the tests fail the patch is applied forward again, so the files
        are as they were. Returns (new commit hash, error text, test output)."""
        files = target.files
        if self._git("status", "--porcelain", "--", *files).stdout.strip():
            return "", "The files have uncommitted changes; apply or discard them before undoing.", ""
        parent = self._git("rev-parse", "--verify", "--quiet", f"{target.commit}^")
        base = parent.stdout.strip() if parent.returncode == 0 else EMPTY_TREE
        patch = self._git_bytes("diff", "--binary", base, target.commit, "--", *files)
        if patch.returncode != 0:
            return "", f"git diff failed: {patch.stderr.decode(errors='replace').strip()}", ""
        if not patch.stdout:
            return "", "The commit changed none of these files; nothing to revert.", ""
        applied = self._git_bytes("apply", "-R", "-", stdin=patch.stdout)
        if applied.returncode != 0:
            stderr = applied.stderr.decode(errors="replace").strip()
            return "", f"Reverse patch did not apply, nothing changed: {stderr}", ""
        passed, output = self._run_tests(files)
        if not passed:
            return "", self._restore_after_failed_undo(patch.stdout), output
        message = f'Revert "{COMMIT_PREFIX}{target.summary}"\n\nThis reverts commit {target.commit}.'
        commit, error = self._commit(message, files)
        if not commit and not error:
            error = "The commit changed none of these files; nothing to revert."
        return commit, error, tail(output)

    def _restore_after_failed_undo(self, patch: bytes) -> str:
        restored = self._git_bytes("apply", "-", stdin=patch)
        if restored.returncode != 0:
            stderr = restored.stderr.decode(errors="replace").strip()
            return (
                "Tests failed with the change reverted, and restoring the files also failed: "
                f"{stderr}. Run git restore on the listed files."
            )
        return (
            "Tests failed with the change reverted, so nothing was undone; the files are unchanged. "
            "A later change probably depends on this one: undo that change first."
        )

    # Git --------------------------------------------------------------------
    def _git(self, *args: str) -> subprocess.CompletedProcess:
        if not self._git_ok:
            return subprocess.CompletedProcess(["git", *args], 1, "", NO_GIT)
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_EDITOR": "true"}
        try:
            return subprocess.run(
                ["git", *args],
                cwd=self.project_root,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=GIT_TIMEOUT_S,
                env=env,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return subprocess.CompletedProcess(["git", *args], 1, "", f"git {args[0]} failed: {exc}")

    def _git_bytes(self, *args: str, stdin: bytes | None = None) -> subprocess.CompletedProcess:
        if not self._git_ok:
            return subprocess.CompletedProcess(["git", *args], 1, b"", NO_GIT.encode())
        try:
            source = {"input": stdin} if stdin is not None else {"stdin": subprocess.DEVNULL}
            return subprocess.run(
                ["git", *args], cwd=self.project_root, capture_output=True, timeout=GIT_TIMEOUT_S, **source
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return subprocess.CompletedProcess(["git", *args], 1, b"", f"git {args[0]} failed: {exc}".encode())

    def _commit(self, message: str, files: tuple[str, ...]) -> tuple[str, str]:
        """Stage and commit exactly `files`. Returns (commit hash, error text);
        both empty when the files have nothing to commit."""
        added = self._git("add", "-A", "--", *files)
        if added.returncode != 0:
            return "", f"git add failed: {added.stderr.strip()}"
        if self._git("diff", "--cached", "--quiet", "--", *files).returncode == 0:
            return "", ""
        # A pathspec commits only these files even when other changes are staged.
        committed = self._git(*self._identity(), "commit", "-q", "-m", message, "--", *files)
        if committed.returncode != 0:
            return "", f"git commit failed: {(committed.stderr or committed.stdout).strip()}"
        return self._head(), ""

    def _identity(self) -> tuple[str, ...]:
        """The configured identity when git has one, otherwise the managed one.
        `git var` applies the same strict check as `git commit`."""
        known = all(self._git("var", name).returncode == 0 for name in ("GIT_AUTHOR_IDENT", "GIT_COMMITTER_IDENT"))
        return () if known else MANAGED_IDENTITY

    def _head(self) -> str:
        result = self._git("rev-parse", "HEAD")
        return result.stdout.strip() if result.returncode == 0 else ""

    def _tracked(self, path: str) -> bool:
        return bool(self._git("ls-files", "--", path).stdout.strip())

    def _dirty_digests(self) -> dict[str, str]:
        """Uncommitted files under the watched directories -> content digest."""
        result = self._git("status", "--porcelain=v1", "-z", "--untracked-files=all", "--", *WATCHED_DIRS)
        if result.returncode != 0:
            log.warning("git status failed in %s: %s", self.project_root, result.stderr.strip())
            return {}
        paths: list[str] = []
        records = result.stdout.split("\0")
        index = 0
        while index < len(records):
            record = records[index]
            index += 1
            if len(record) < 4:
                continue
            paths.append(record[3:])
            if record[0] in "RC":
                index += 1  # the next record is the rename source
        return {
            path: _digest(self.project_root / path)
            for path in paths
            if "__pycache__" not in Path(path).parts and not path.endswith(".pyc")
        }

    def _forget(self, files: Sequence[str]) -> None:
        for path in files:
            self._baseline.pop(path, None)
            self._failed.pop(path, None)

    # Records ----------------------------------------------------------------
    def _entry(
        self,
        entry_id: str,
        summary: str,
        files: Sequence[str],
        status: str,
        reload_kind: str = "none",
        commit: str = "",
        test_tail: str = "",
        detail: str = "",
        undoes: str = "",
    ) -> ChangeEntry:
        return ChangeEntry(
            entry_id, _now(), summary, tuple(files), status, reload_kind, commit, test_tail, detail, undoes
        )

    def _record(self, entry: ChangeEntry) -> ChangeEntry:
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(entry.to_json() + "\n")
        except OSError as exc:
            log.error("could not write %s: %s", self.log_path, exc)
        return entry


def related_tests(files: Sequence[str], project_root: Path) -> list[str]:
    """Test files that exercise the changed files: changed test files plus any
    test module that names a changed q50diag module or a module that imports
    one, directly or transitively. Empty means run all, which is also the
    answer when a changed module is reached by no test file."""
    root = Path(project_root)
    test_files = sorted((root / "tests").glob("test_*.py"))
    texts = {path: path.read_text(encoding="utf-8", errors="replace") for path in test_files}
    importers = _importers(root)
    selected: set[str] = set()
    for item in files:
        path = Path(item)
        if path.parts[:1] == ("tests",) and path.name.startswith("test_") and path.suffix == ".py":
            selected.add(path.as_posix())
        elif path.parts[:1] == ("q50diag",) and path.suffix == ".py":
            affected = _with_dependents(_module_name(path), importers)
            matched = [test for test, text in texts.items() if any(_names_module(text, m) for m in affected)]
            if not matched:
                return []
            selected.update(test.relative_to(root).as_posix() for test in matched)
    return sorted(selected)


def _module_name(path: Path) -> str:
    parts = list(path.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _importers(root: Path) -> dict[str, set[str]]:
    """{q50diag module: modules under q50diag/ that import it}."""
    importers: dict[str, set[str]] = {}
    for source in sorted((root / "q50diag").rglob("*.py")):
        importer = _module_name(source.relative_to(root))
        try:
            tree = ast.parse(source.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for imported in _imported_modules(tree):
            importers.setdefault(imported, set()).add(importer)
    return importers


def _imported_modules(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return {name for name in names if name == "q50diag" or name.startswith("q50diag.")}


def _with_dependents(module: str, importers: dict[str, set[str]]) -> set[str]:
    affected = {module}
    queue = [module]
    while queue:
        for importer in importers.get(queue.pop(), ()):
            if importer not in affected:
                affected.add(importer)
                queue.append(importer)
    return affected


def _names_module(text: str, module: str) -> bool:
    package, _, name = module.rpartition(".")
    return module in text or (bool(package) and f"from {package} import" in text and f" {name}" in text)
