"""Offscreen tests for the MechanIQ GUI: model, transcript view, panel, page,
changes list, controller and the main window integration. The runtime is
replaced by local stubs (a fake runner with an `event` signal and a fake
change log)."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

import pytest
from PySide6.QtCore import QEvent, QObject, Qt, QThread, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from q50diag.gui.main_window import PAGE_ROLE, MainWindow
from q50diag.gui.mechaniq import attach_views, build_mechaniq
from q50diag.gui.mechaniq.controller import MechaniqController
from q50diag.gui.mechaniq.model import LEAD, MechaniqModel, summarize_tool_input
from q50diag.gui.mechaniq.page import MechaniqPage
from q50diag.gui.mechaniq.panel import PANEL_WIDTH, STRIP_WIDTH, MechaniqPanel
from q50diag.gui.mechaniq.settings_dialog import MechaniqSettingsDialog
from q50diag.gui.mechaniq.views import TranscriptView
from tests.test_gui_fakes import app, ctx, wait_until  # noqa: F401


@dataclass(frozen=True)
class Event:
    kind: str
    agent_id: str = LEAD
    text: str = ""
    data: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Settings:
    auth_mode: str = "subscription"
    api_key: str = ""
    model: str = "claude-opus-5-5"


@dataclass(frozen=True)
class Change:
    id: str
    time: float
    summary: str
    files: tuple
    status: str = "committed"
    reload_kind: str = "live"
    commit: str = "abcdef1234567"
    test_tail: str = ""


class FakeRunner(QObject):
    event = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[str] = []
        self.interrupts = 0
        self.resets = 0
        self.shutdowns = 0
        self.settings = None

    def send(self, text: str) -> None:
        self.sent.append(text)

    def interrupt(self) -> None:
        self.interrupts += 1

    def reset(self) -> None:
        self.resets += 1

    def apply_settings(self, settings) -> None:
        self.settings = settings

    def shutdown(self) -> None:
        self.shutdowns += 1


class FakeReloader:
    def __init__(self) -> None:
        self.threads: list[bool] = []

    def apply(self, plan):
        self.threads.append(QThread.currentThread() == QApplication.instance().thread())
        return "reloaded"


class FakeChangeLog:
    def __init__(self, entries=()) -> None:
        self._entries = list(entries)
        self.pending: list[str] = []
        self.applied: list[tuple[str, list[str]]] = []
        self.undone: list[str] = []

    def entries(self):
        return list(self._entries)

    def pending_edits(self):
        return list(self.pending)

    def apply(self, summary, files, reloader):
        reloader.apply(("plan", tuple(files)))
        entry = Change(f"c{len(self._entries) + 1}", 1_760_000_000.0, summary, tuple(files))
        self._entries.append(entry)
        self.applied.append((summary, list(files)))
        return entry

    def undo(self, entry_id, reloader=None):
        if reloader is not None:
            reloader.apply(("undo", entry_id))
        self.undone.append(entry_id)
        entry = Change(f"u{entry_id}", 1_760_000_100.0, f"undo {entry_id}", (), "undo", "live", "fedcba9")
        self._entries.append(entry)
        return entry


SCRIPT = [
    Event("init", data={"model": "claude-opus-5-5", "session_id": "s1", "api_key_source": "none"}),
    Event("busy"),
    Event("delta", text="Checking the "),
    Event("delta", text="decoder."),
    Event("text", text="Checking the decoder."),
    Event(
        "tool_use",
        data={
            "id": "t1",
            "name": "Task",
            "input": {"description": "Fix decoder", "prompt": "Fix the 21 01 decoder", "subagent_type": "code-fixer"},
        },
    ),
    Event("agent_started", "t1", data={"title": "Fix decoder", "subagent_type": "code-fixer"}),
    Event("delta", "t1", text="Reading nissan.py"),
    Event("tool_use", "t1", data={"id": "r1", "name": "Read", "input": {"file_path": "/x/q50diag/protocol/nissan.py"}}),
    Event("tool_result", "t1", data={"tool_use_id": "r1", "is_error": False}),
    Event("tool_use", "t1", data={"id": "b1", "name": "Bash", "input": {"command": "pytest -q"}}),
    Event("tool_result", "t1", data={"tool_use_id": "b1", "is_error": True}),
    Event("text", "t1", text="Fixed the offset."),
    Event("agent_finished", "t1"),
    Event("tool_result", LEAD, data={"tool_use_id": "t1", "is_error": False}),
    Event("delta", text="The helper fixed it."),
    Event("result", data={"cost_usd": 0.05, "duration_ms": 12_300, "num_turns": 3, "is_error": False}),
    Event("idle"),
]


def make_controller(change_log=None, ctx=None):
    model = MechaniqModel()
    controller = MechaniqController(model, ctx=ctx)
    runner = FakeRunner()
    controller.set_runner(runner)
    controller.set_settings(Settings())
    if change_log is not None:
        controller.set_change_tools(change_log, FakeReloader())
    return controller, runner


def key(widget, key_code, modifiers=Qt.KeyboardModifier.NoModifier, text=""):
    event = QKeyEvent(QEvent.Type.KeyPress, key_code, modifiers, text)
    return QApplication.sendEvent(widget, event)


# Model -----------------------------------------------------------------------
def test_model_builds_transcripts_and_agents_from_events(app):
    model = MechaniqModel()
    busy: list[bool] = []
    model.busy_changed.connect(busy.append)
    model.add_user_text("Fix the decoder")
    for event in SCRIPT:
        model.handle_event(event)
    lead = model.transcript(LEAD)
    assert [e.kind for e in lead] == ["user", "assistant", "tool", "assistant", "result"]
    assert lead[1].text == "Checking the decoder." and not lead[1].streaming
    assert (lead[2].name, lead[2].text, lead[2].status) == ("Task", "Fix decoder", "done")
    assert lead[3].text == "The helper fixed it." and not lead[3].streaming
    assert lead[4].text == "Done in 12.3 s, 3 turns, $0.0500"
    helper = model.transcript("t1")
    assert [e.kind for e in helper] == ["task", "assistant", "tool", "tool", "assistant"]
    assert helper[0].text == "Fix the 21 01 decoder"
    assert helper[1].text == "Reading nissan.py"
    assert (helper[2].name, helper[2].status) == ("Read", "done")
    assert (helper[3].text, helper[3].status) == ("pytest -q", "failed")
    agents = model.agents()
    assert [(a.agent_id, a.title, a.state) for a in agents] == [("lead", "Lead", "idle"), ("t1", "Fix decoder", "done")]
    assert busy == [True, False] and not model.busy
    assert (model.model_name, model.auth_source, model.turns) == ("claude-opus-5-5", "subscription", 3)
    assert model.total_cost == pytest.approx(0.05)


def test_model_cost_is_running_total_per_connection(app):
    model = MechaniqModel()
    model.handle_event(Event("result", data={"cost_usd": 0.05, "duration_ms": 1000, "num_turns": 1}))
    model.handle_event(Event("result", data={"cost_usd": 0.08, "duration_ms": 1000, "num_turns": 1}))
    assert model.transcript()[-1].text.endswith("$0.0300")
    model.reset()
    model.handle_event(Event("result", data={"cost_usd": 0.02, "duration_ms": 1000, "num_turns": 1}))
    assert model.total_cost == pytest.approx(0.10)
    assert model.transcript()[-1].text.endswith("$0.0200")
    assert [a.agent_id for a in model.agents()] == [LEAD]


def test_tool_input_summary():
    assert summarize_tool_input({"command": "git status\n  --short"}) == "git status --short"
    assert summarize_tool_input({"keys": ["EPS.21.01.supply"]}) == '["EPS.21.01.supply"]'
    assert len(summarize_tool_input({"prompt": "x" * 300})) == 90
    assert summarize_tool_input(None) == ""


# Transcript view ---------------------------------------------------------------
def test_transcript_view_throttles_streamed_deltas(app):
    model = MechaniqModel()
    view = TranscriptView(model, LEAD)
    view.resize(380, 400)
    model.add_user_text("hello")
    for index in range(60):
        model.handle_event(Event("delta", text=f"chunk{index} "))
    assert wait_until(lambda: "chunk59" in view.toPlainText(), 2)
    assert view.render_count <= 3
    model.handle_event(Event("tool_use", data={"id": "x", "name": "mcp__q50__get_readings", "input": {"keys": ["v"]}}))
    assert wait_until(lambda: "running" in view.toPlainText(), 2)
    model.handle_event(Event("tool_result", data={"tool_use_id": "x"}))
    assert wait_until(lambda: "get_readings" in view.toPlainText() and "done" in view.toPlainText(), 2)
    text = view.toPlainText()
    assert text.count("chunk0 ") == 1 and "running" not in text and text.count("YOU") == 1


def test_transcript_renders_markdown(app):
    model = MechaniqModel()
    view = TranscriptView(model, LEAD)
    model.handle_event(Event("text", text="**Step 1**\n\n- turn left\n- hold `3 s`"))
    view.render_now()
    assert "Step 1" in view.toPlainText() and "**" not in view.toPlainText()
    assert "turn left" in view.toPlainText()


# Panel -------------------------------------------------------------------------
def test_panel_sends_on_enter_and_stop_interrupts(app):
    controller, runner = make_controller()
    panel = MechaniqPanel(controller)
    panel.show()
    panel.input.setPlainText("why is steering heavy")
    key(panel.input, Qt.Key.Key_Return)
    assert runner.sent == ["why is steering heavy"] and panel.input.toPlainText() == ""
    assert controller.model.transcript()[0].text == "why is steering heavy"
    panel.input.setPlainText("line one")
    panel.input.moveCursor(panel.input.textCursor().MoveOperation.End)
    key(panel.input, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    assert runner.sent == ["why is steering heavy"] and "\n" in panel.input.toPlainText()
    assert not panel.stop_button.isEnabled()
    runner.event.emit(Event("busy"))
    assert panel.stop_button.isEnabled()
    panel.stop_button.click()
    assert runner.interrupts == 1
    runner.event.emit(Event("init", data={"model": "claude-sonnet-5-5", "api_key_source": "ANTHROPIC_API_KEY"}))
    assert panel.info_label.text().startswith("claude-sonnet-5-5, API key")
    panel.collapse()
    assert panel.collapsed and panel.width() == STRIP_WIDTH
    panel.strip.click()
    assert not panel.collapsed and panel.width() == PANEL_WIDTH
    panel.new_button.click()
    assert runner.resets == 1 and controller.model.transcript() == []


def test_changes_tab_lists_newest_first_and_undo(app):
    log = FakeChangeLog(
        [
            Change("c1", 1_760_000_000.0, "first fix", ("q50diag/protocol/nissan.py",)),
            Change("c2", 1_760_000_050.0, "second fix", ("q50diag/gui/pages/steering.py",), "tests_failed", "", ""),
        ]
    )
    controller, _runner = make_controller(log)
    panel = MechaniqPanel(controller)
    rows = panel.changes.rows
    assert [row.entry.id for row in rows] == ["c2", "c1"]
    assert not rows[0].undo_button.isEnabled() and rows[1].undo_button.isEnabled()
    rows[1].undo_button.click()
    assert wait_until(lambda: log.undone == ["c1"] and len(panel.changes.rows) == 3, 3)
    assert panel.changes.rows[0].entry.id == "uc1"
    assert controller.reloader.threads == [True]
    assert "undo c1" in controller.model.transcript()[-1].text


# Controller ----------------------------------------------------------------------
def test_apply_change_runs_reload_on_main_thread(app):
    log = FakeChangeLog()
    controller, _runner = make_controller(log)
    report = controller.apply_change("fix offset", ["q50diag/protocol/nissan.py"])
    assert "committed" in report and "abcdef1234567" in report
    assert log.applied == [("fix offset", ["q50diag/protocol/nissan.py"])]
    assert wait_until(lambda: len(controller.model.changes()) == 1, 2)
    assert controller.reloader.threads == [True]
    reports: list[str] = []
    worker = threading.Thread(target=lambda: reports.append(controller.apply_change("again", ["a.py"])))
    worker.start()
    assert wait_until(lambda: not worker.is_alive(), 5)
    assert reports and "committed" in reports[0] and controller.reloader.threads == [True, True]


def test_undo_change_tool_runs_on_worker_thread(app):
    log = FakeChangeLog([Change("c1", 1_760_000_000.0, "first fix", ("a.py",))])
    controller, _runner = make_controller(log)
    reports: list[str] = []
    worker = threading.Thread(target=lambda: reports.append(controller.undo_change("c1")))
    worker.start()
    assert wait_until(lambda: not worker.is_alive(), 5)
    assert log.undone == ["c1"] and "change uc1: undo" in reports[0]
    assert controller.reloader.threads == [True]
    assert wait_until(lambda: controller.model.changes()[0].id == "uc1", 2)


def test_failed_background_job_reports_its_error(app):
    controller, _runner = make_controller()

    def fail() -> None:
        raise RuntimeError("disk full")

    controller._in_background(fail)
    assert wait_until(lambda: any(e.text == "Change failed: disk full" for e in controller.model.transcript()), 5)


def test_agent_finished_twice_is_idempotent(app):
    model = MechaniqModel()
    model.handle_event(Event("agent_started", "t1", data={"title": "Helper", "subagent_type": "data-analyst"}))
    model.handle_event(Event("agent_finished", "t1", data={"status": "failed", "is_error": True}))
    model.handle_event(Event("agent_finished", "t1", data={"status": "completed", "is_error": False}))
    assert model.agent("t1").state == "failed" and len(model.agents()) == 2


def test_stray_edits_are_applied_when_turn_ends(app):
    log = FakeChangeLog()
    log.pending = ["notes.txt"]
    controller, runner = make_controller(log)
    controller.send("fix it")
    log.pending = ["notes.txt", "q50diag/protocol/nissan.py"]
    runner.event.emit(Event("idle"))
    assert wait_until(lambda: log.applied, 3)
    assert log.applied[0][1] == ["q50diag/protocol/nissan.py"]
    runner.event.emit(Event("idle"))  # no turn in progress: nothing more is applied
    wait_until(lambda: False, 0.1)
    assert len(log.applied) == 1


def test_unavailable_runtime_reports_instead_of_sending(app):
    controller = MechaniqController(MechaniqModel())
    controller.mark_unavailable("ImportError: no module named tools")
    controller.send("hello")
    kinds = [e.kind for e in controller.model.transcript()]
    assert kinds == ["error", "user", "error"]
    assert "apply_change unavailable" in controller.apply_change("x", [])


def test_settings_dialog_and_save(app):
    dialog = MechaniqSettingsDialog(Settings(), ("claude-opus-5-5", "claude-sonnet-5-5"))
    assert dialog.subscription_radio.isChecked() and not dialog.key_edit.isEnabled()
    dialog.api_radio.setChecked(True)
    assert not dialog.save_button.isEnabled()
    dialog.key_edit.setText("sk-ant-test")
    dialog.model_combo.setCurrentText("claude-sonnet-5-5")
    assert dialog.save_button.isEnabled()
    result = dialog.result_settings()
    assert result == Settings("api_key", "sk-ant-test", "claude-sonnet-5-5")
    controller, runner = make_controller()
    saved = []
    controller.save_and_apply(result, lambda settings, path: saved.append(settings))
    assert saved == [result] and runner.settings == result and controller.model.auth_source == "API key"


# Page ---------------------------------------------------------------------------
def test_page_has_one_column_per_agent(ctx):
    controller, runner = make_controller()
    page = MechaniqPage(ctx)
    page.attach(controller)
    page.resize(1200, 700)
    assert list(page.columns) == [LEAD]
    for event in SCRIPT[:8]:
        runner.event.emit(event)
    assert list(page.columns) == [LEAD, "t1"]
    column = page.columns["t1"]
    assert column.title_label.text() == "FIX DECODER" and column.state_label.text() == "working"
    assert wait_until(lambda: "Reading nissan.py" in column.transcript.toPlainText(), 2)
    runner.event.emit(Event("agent_finished", "t1"))
    assert column.state_label.text() == "done"
    page.input.setPlainText("next step")
    page.send_button.click()
    assert runner.sent == ["next step"]
    page.new_button.click()
    assert list(page.columns) == [LEAD]


# Main window --------------------------------------------------------------------
def _fake_builder(store):
    def build(window, ctx):
        controller, runner = make_controller(FakeChangeLog(), ctx)
        attach_views(controller, MechaniqPanel(controller), window, ctx)
        store["runner"] = runner
        return controller

    return build


@pytest.fixture
def window(ctx, tmp_path):
    store: dict = {}
    win = MainWindow(ctx, mechaniq_builder=_fake_builder(store))
    win.fake_runner = store["runner"]
    win.show()
    yield win
    win.close()


def nav_rows(win) -> list[str]:
    return [win.nav.item(row).text() for row in range(win.nav.count())]


def test_main_window_nav_and_panel_visibility(window):
    rows = nav_rows(window)
    assert rows.index("MechanIQ") > rows.index("TOOLS") and rows[-1] == "MechanIQ"
    panel = window.mechaniq.panel
    assert panel.parent() is not None and panel.isVisible()
    window.show_page("MechanIQ")
    assert window.current_page().title == "MechanIQ" and not panel.isVisible()
    window.show_page("Steering")
    assert panel.isVisible()
    window.fake_runner.event.emit(Event("text", text="Step 1 of 3: hold the wheel straight."))
    assert wait_until(lambda: "Step 1 of 3" in panel.transcript.toPlainText(), 2)
    assert wait_until(lambda: "Step 1 of 3" in window.pages["MechanIQ"].columns[LEAD].transcript.toPlainText(), 2)


def test_main_window_shuts_runner_down_on_close(ctx):
    store: dict = {}
    win = MainWindow(ctx, mechaniq_builder=_fake_builder(store))
    win.show()
    win.close()
    assert store["runner"].shutdowns == 1


def test_rebuild_page_replaces_widget_and_keeps_selection(window):
    window.show_page("Steering")
    old_console = window.pages["Console"]
    index = window.stack.indexOf(window.frames["Console"])
    window.rebuild_page("Console")
    new_console = window.pages["Console"]
    assert new_console is not old_console and window.frames["Console"].widget() is new_console
    assert window.stack.indexOf(window.frames["Console"]) == index
    assert window.current_page().title == "Steering"
    assert window.nav.currentItem().data(PAGE_ROLE) == "Steering"
    old_steering = window.current_page()
    window.rebuild_page("Steering")
    assert window.current_page() is window.pages["Steering"] is not old_steering
    assert window.current_page().isVisible()
    assert window.nav.currentItem().data(PAGE_ROLE) == "Steering"
    window.show_page("MechanIQ")
    window.rebuild_page("MechanIQ")
    assert window.pages["MechanIQ"].controller is window.mechaniq
    assert not window.mechaniq.panel.isVisible()
    assert list(window.pages)[-1] == "MechanIQ"


def test_space_types_in_mechaniq_input_without_marking(window, ctx):
    window.activateWindow()
    if not wait_until(window.isActiveWindow, 1):
        pytest.skip("offscreen platform did not activate the window")
    window.show_page("Overview")
    field_ = window.mechaniq.panel.input
    field_.setFocus()
    assert wait_until(lambda: QApplication.focusWidget() is field_, 1)
    key(field_, Qt.Key.Key_A, text="a")
    key(field_, Qt.Key.Key_Space, text=" ")
    key(field_, Qt.Key.Key_B, text="b")
    assert field_.toPlainText() == "a b" and ctx.markers == []
    window.current_page().setFocus()
    key(window.current_page(), Qt.Key.Key_Space, text=" ")
    assert len(ctx.markers) == 1


def test_build_mechaniq_survives_a_broken_runtime(ctx, monkeypatch):
    import q50diag.gui.mechaniq as mechaniq_gui

    def broken(*_args):
        raise ImportError("no module named q50diag.mechaniq.tools")

    monkeypatch.setattr(mechaniq_gui, "_wire_runtime", broken)
    win = MainWindow(ctx, mechaniq_builder=build_mechaniq)
    try:
        assert not win.mechaniq.available and win.mechaniq.panel is not None
        assert "not available" in win.mechaniq.model.transcript()[0].text
    finally:
        win.close()
