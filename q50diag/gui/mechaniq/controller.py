"""MechanIQ controller: the one object the panel and the page talk to.

Forwards user input to the runner and runner events to the model, applies
code changes through the change log (tests, hot reload, commit) off the GUI
thread, and applies stray edits left uncommitted when a turn ends."""

from __future__ import annotations

import inspect
import logging
import threading
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QEventLoop, QObject, Qt, QThread, Signal
from PySide6.QtWidgets import QApplication, QDialog, QWidget

from q50diag.gui.mechaniq.model import MechaniqModel

log = logging.getLogger(__name__)

RELOAD_TIMEOUT_S = 60.0
STRAY_SUMMARY = "apply edits left at the end of a MechanIQ turn"
MISSING_KEY_NOTICE = "Not sent. MechanIQ needs an Anthropic API key. Click Add API key to enter one."


class MainThreadReloader:
    """Wraps a reloader so `apply(plan)` runs on the Qt main thread when the
    change log calls it from a worker thread."""

    def __init__(self, reloader) -> None:
        self._reloader = reloader

    def __getattr__(self, name: str):
        return getattr(self._reloader, name)

    def apply(self, plan, *args, **kwargs):
        from q50diag.mechaniq.mainthread import call_in_main_thread

        return call_in_main_thread(lambda: self._reloader.apply(plan, *args, **kwargs), timeout_s=RELOAD_TIMEOUT_S)


def describe_entry(entry) -> str:
    """Report text for the apply_change and undo_change tools."""
    if callable(getattr(entry, "report", None)):
        return entry.report()
    lines = [
        f"change {getattr(entry, 'id', '')}: {getattr(entry, 'status', '')}",
        f"reload: {getattr(entry, 'reload_kind', '') or 'none'}",
        f"commit: {getattr(entry, 'commit', '') or 'none'}",
    ]
    tail = getattr(entry, "test_tail", "")
    if tail and getattr(entry, "status", "") == "tests_failed":
        lines.append("test output (tail):\n" + tail)
    return "\n".join(lines)


def _accepts_reloader(method: Callable) -> bool:
    try:
        return "reloader" in inspect.signature(method).parameters
    except (TypeError, ValueError):
        return False


def _on_main_thread() -> bool:
    app = QApplication.instance()
    return app is not None and QThread.currentThread() == app.thread()


class MechaniqController(QObject):
    _posted = Signal(object)  # zero-argument callable to run on the main thread
    needs_key_changed = Signal(bool)

    def __init__(
        self, model: MechaniqModel, ctx=None, project_root: Path | None = None, parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self.model = model
        self.ctx = ctx
        self.project_root = project_root
        self.runner = None
        self.panel = None
        self.change_log = None
        self.reloader = None
        self.settings = None
        self.settings_path: Path | None = None
        self.unavailable_reason = ""
        self._change_lock = threading.Lock()
        self._turn_snapshot: dict[str, float] | None = None
        self._posted.connect(lambda job: job(), Qt.ConnectionType.QueuedConnection)

    # Wiring ----------------------------------------------------------------
    def set_runner(self, runner) -> None:
        self.runner = runner
        runner.event.connect(self._on_event)

    def set_change_tools(self, change_log, reloader) -> None:
        self.change_log = change_log
        self.reloader = reloader
        self.model.set_changes(change_log.entries())

    def set_settings(self, settings, path: Path | None = None) -> None:
        self.settings = settings
        self.settings_path = path
        self.model.set_config(settings.auth_mode, settings.model)
        self.needs_key_changed.emit(self.needs_api_key)

    def mark_unavailable(self, reason: str) -> None:
        self.unavailable_reason = reason
        self.model.add_notice(f"MechanIQ runtime is not available: {reason}", error=True)

    @property
    def available(self) -> bool:
        return self.runner is not None and not self.unavailable_reason

    @property
    def needs_api_key(self) -> bool:
        """True when settings are loaded, API key mode is in effect and no key is set."""
        if self.settings is None:
            return False
        from q50diag.mechaniq.settings import needs_api_key

        return needs_api_key(self.settings)

    # User actions ------------------------------------------------------------
    def send(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        self.model.add_user_text(text)
        if not self.available:
            self.model.add_notice(f"Not sent. {self.unavailable_reason or 'The runtime is not running.'}", error=True)
            return
        if self.needs_api_key:
            self.model.add_notice(MISSING_KEY_NOTICE, error=True)
            return
        if self._turn_snapshot is None:
            self._turn_snapshot = self._pending_snapshot()
        self.runner.send(text)

    def interrupt(self) -> None:
        if self.runner is not None:
            self.runner.interrupt()

    def reset(self) -> None:
        if self.runner is not None:
            self.runner.reset()
        self.model.reset()

    def undo(self, change_id: str) -> None:
        if self.change_log is None:
            self.model.add_notice("Undo unavailable: the change log is not loaded.", error=True)
            return
        self._in_background(lambda: self._undo_blocking(change_id))

    def open_settings(self, parent: QWidget | None = None) -> None:
        from q50diag.gui.mechaniq.settings_dialog import MechaniqSettingsDialog
        from q50diag.mechaniq.settings import MODELS, MechaniqSettings, save_settings

        dialog = MechaniqSettingsDialog(self.settings or MechaniqSettings(), MODELS, parent)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.save_and_apply(dialog.result_settings(), save_settings)

    def save_and_apply(self, settings, save: Callable) -> None:
        if self._settings_inside_project():
            self.model.add_notice(
                f"Settings not saved: {self.settings_path} is inside the project folder, "
                "which MechanIQ commits to git.",
                error=True,
            )
            return
        try:
            save(settings, self.settings_path)
        except OSError as exc:
            self.model.add_notice(f"Settings not saved: {exc}", error=True)
            return
        self.set_settings(settings, self.settings_path)
        if self.runner is not None:
            self.runner.apply_settings(settings)
        self.model.add_notice(
            f"Settings saved: {settings.model}, {self.model.auth_source}. They apply from the next message."
        )

    def _settings_inside_project(self) -> bool:
        if self.settings_path is None or self.project_root is None:
            return False
        return Path(self.settings_path).resolve().is_relative_to(Path(self.project_root).resolve())

    def shutdown(self) -> None:
        if self.runner is not None:
            self.runner.shutdown()

    # Code changes --------------------------------------------------------------
    def apply_change(self, summary: str, files: list[str]) -> str:
        """change_applier for the apply_change tool. Blocks the caller until the
        change is tested, reloaded and committed; the GUI keeps running."""
        if self.change_log is None:
            return "apply_change unavailable: the change log is not loaded. Tell the user to restart q50diag."
        if not _on_main_thread():
            return describe_entry(self._apply_blocking(summary, list(files)))
        result: dict = {}
        loop = QEventLoop()

        def work() -> None:
            try:
                result["entry"] = self._apply_blocking(summary, list(files))
            except Exception as exc:  # noqa: BLE001 (reported to the agent below)
                result["error"] = exc
            self._posted.emit(loop.quit)

        threading.Thread(target=work, name="mechaniq-apply", daemon=True).start()
        loop.exec()
        if "error" in result:
            raise result["error"]
        return describe_entry(result["entry"])

    def _apply_blocking(self, summary: str, files: list[str]):
        with self._change_lock:
            entry = self.change_log.apply(summary, files, MainThreadReloader(self.reloader))
        self._posted.emit(lambda: self._record_change(entry))
        return entry

    def undo_change(self, change_id: str) -> str:
        """change_undoer for the undo_change tool; called on a worker thread."""
        if self.change_log is None:
            return "undo_change unavailable: the change log is not loaded. Tell the user to restart q50diag."
        return describe_entry(self._undo_blocking(change_id))

    def _undo_blocking(self, change_id: str):
        with self._change_lock:
            if _accepts_reloader(self.change_log.undo):
                entry = self.change_log.undo(change_id, reloader=MainThreadReloader(self.reloader))
            else:
                entry = self.change_log.undo(change_id)
        self._posted.emit(lambda: self._record_change(entry))
        return entry

    def _record_change(self, entry) -> None:
        self.model.add_change(entry)
        recorder = getattr(self.ctx, "recorder", None)
        if recorder is not None and recorder.is_recording and getattr(entry, "status", "") != "tests_failed":
            self.ctx.mark_event(f"MechanIQ change: {getattr(entry, 'summary', '')}")

    def _in_background(self, job: Callable[[], None]) -> None:
        def run() -> None:
            try:
                job()
            except Exception as exc:  # noqa: BLE001 (shown in the transcript)
                log.exception("MechanIQ background job failed")
                notice = f"Change failed: {exc}"
                self._posted.emit(lambda: self.model.add_notice(notice, error=True))

        threading.Thread(target=run, name="mechaniq-change", daemon=True).start()

    # Runtime events ----------------------------------------------------------------
    def _on_event(self, event) -> None:
        self.model.handle_event(event)
        if event.kind == "idle":
            self._apply_stray_edits()

    def _pending_snapshot(self) -> dict[str, float] | None:
        if self.change_log is None:
            return None
        try:
            return {str(path): self._mtime(path) for path in self.change_log.pending_edits()}
        except Exception:  # noqa: BLE001 (a git failure must not block sending)
            log.exception("pending_edits failed")
            return None

    def _mtime(self, path) -> float:
        target = Path(path)
        if not target.is_absolute() and self.project_root is not None:
            target = self.project_root / target
        try:
            return target.stat().st_mtime
        except OSError:
            return -1.0

    def stray_edits(self) -> list[str]:
        """Files pending now that were not pending, or have changed, since the turn started."""
        before = self._turn_snapshot
        now = self._pending_snapshot()
        if before is None or now is None:
            return []
        return [path for path, mtime in now.items() if before.get(path) != mtime]

    def _apply_stray_edits(self) -> None:
        files = self.stray_edits()
        self._turn_snapshot = None
        if not files:
            return
        self.model.add_notice(f"Applying {len(files)} uncommitted file(s) left by the turn: {', '.join(files)}")
        self._in_background(lambda: self._apply_blocking(STRAY_SUMMARY, files))
