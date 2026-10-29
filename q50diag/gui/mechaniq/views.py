"""Widgets shared by the MechanIQ panel and page: the transcript view, the
chat input, agent chips, the change log list and the API key prompt."""

from __future__ import annotations

import time
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QKeyEvent,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocumentFragment,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from q50diag.gui import theme
from q50diag.gui.mechaniq.model import LEAD, MechaniqModel, TranscriptEntry
from q50diag.gui.widgets.common import Card, button, hbox, label

RENDER_INTERVAL_MS = 100  # at most about 10 renders per second while text streams
STATUS_COLORS = {"running": theme.WARN, "done": theme.OK, "failed": theme.FAULT}
AGENT_STATE_LABELS = {"idle": "idle", "running": "working", "done": "done", "failed": "failed"}
CHANGE_STATUS_STATES = {"tests_failed": "fault", "committed": "ok", "applied": "ok", "undone": "idle"}
NOT_UNDOABLE = ("tests_failed", "undo", "undone", "reverted")
CHIP_TITLE_LIMIT = 16
KEY_PROMPT_TEXT = (
    "MechanIQ needs an Anthropic API key before it can answer. Click Add API key and paste "
    "your key. The settings window explains where to get one."
)


HEADED_KINDS = ("assistant", "user", "task")


def _block_format(top_margin: float) -> QTextBlockFormat:
    block = QTextBlockFormat()
    block.setTopMargin(top_margin)
    return block


def _char_format(
    color: str, bold: bool = False, point_size: float | None = None, mono: bool = False
) -> QTextCharFormat:
    char = QTextCharFormat()
    char.setForeground(QColor(color))
    if bold:
        char.setFontWeight(QFont.Weight.Bold)
    if point_size:
        char.setFontPointSize(point_size)
    if mono:
        char.setFontFamilies([theme.mono_family()])
    return char


class TranscriptView(QTextBrowser):
    """Read-only transcript of one agent. Renders on a timer and only redraws
    the entries from the first one that changed, so streamed text does not
    re-render the whole conversation for every chunk."""

    def __init__(
        self,
        model: MechaniqModel | None = None,
        agent_id: str = LEAD,
        speaker: str = "MechanIQ",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setOpenExternalLinks(True)
        self.setObjectName("MechaniqTranscript")
        self.setStyleSheet(f"QTextBrowser#MechaniqTranscript {{ border: none; background: {theme.PANEL}; }}")
        self.document().setDocumentMargin(10)
        self.agent_id = agent_id
        self.speaker = speaker
        self.render_count = 0
        self._model: MechaniqModel | None = None
        self._rendered: list[TranscriptEntry] = []
        self._starts: list[int] = []
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(RENDER_INTERVAL_MS)
        self._timer.timeout.connect(self.render_now)
        if model is not None:
            self.set_model(model)

    def set_model(self, model: MechaniqModel) -> None:
        self._model = model
        model.transcript_changed.connect(self._on_changed)
        self.schedule()

    def _on_changed(self, agent_id: str) -> None:
        if agent_id == self.agent_id:
            self.schedule()

    def schedule(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def render_now(self) -> None:
        if self._model is None:
            return
        entries = self._model.transcript(self.agent_id)
        first = self._first_difference(entries)
        if first is None:
            return
        bar = self.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        cursor = QTextCursor(self.document())
        cursor.beginEditBlock()
        if first < len(self._starts):
            cursor.setPosition(self._starts[first])
            cursor.movePosition(QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor)
            cursor.removeSelectedText()
        else:
            cursor.movePosition(QTextCursor.MoveOperation.End)
        starts = self._starts[:first]
        for index, entry in enumerate(entries[first:], start=first):
            starts.append(cursor.position())
            block = _block_format(12 if entry.kind in HEADED_KINDS else 3)
            if index > 0:
                cursor.insertBlock(block, QTextCharFormat())
            else:
                cursor.setBlockFormat(block)
            self._insert_entry(cursor, entry)
        cursor.endEditBlock()
        self._rendered = list(entries)
        self._starts = starts
        self.render_count += 1
        if at_bottom:
            bar.setValue(bar.maximum())

    def _first_difference(self, entries: list[TranscriptEntry]) -> int | None:
        for index, entry in enumerate(entries):
            if index >= len(self._rendered) or self._rendered[index] != entry:
                return index
        return len(entries) if len(entries) < len(self._rendered) else None

    def _insert_entry(self, cursor: QTextCursor, entry: TranscriptEntry) -> None:
        if entry.kind == "tool":
            self._insert_tool_line(cursor, entry)
            return
        headings = {
            "assistant": (self.speaker.upper(), theme.ACCENT),
            "user": ("YOU", theme.TEXT_DIM),
            "task": ("TASK FROM LEAD", theme.TEXT_DIM),
        }
        if entry.kind in headings:
            heading, color = headings[entry.kind]
            cursor.insertText(heading, _char_format(color, bold=True, point_size=8))
            cursor.insertBlock(_block_format(2), QTextCharFormat())
        if entry.kind == "assistant":
            cursor.insertFragment(QTextDocumentFragment.fromMarkdown(entry.text))
            return
        colors = {"user": theme.TEXT, "task": theme.TEXT_DIM, "error": theme.FAULT}
        cursor.insertText(entry.text, _char_format(colors.get(entry.kind, theme.TEXT_FAINT)))

    def _insert_tool_line(self, cursor: QTextCursor, entry: TranscriptEntry) -> None:
        status_color = STATUS_COLORS.get(entry.status, theme.TEXT_DIM)
        cursor.insertText(entry.name, _char_format(theme.ACCENT, point_size=8.5, mono=True))
        cursor.insertText(f"  {entry.text}  ", _char_format(theme.TEXT_DIM, point_size=8.5, mono=True))
        cursor.insertText(entry.status, _char_format(status_color, point_size=8.5, mono=True))


class ChatInput(QPlainTextEdit):
    """Message box: Enter sends, Shift+Enter inserts a newline."""

    submitted = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("MechaniqInput")
        self.setPlaceholderText("Ask MechanIQ (Enter sends, Shift+Enter for a new line)")
        self.setTabChangesFocus(True)
        self.setFixedHeight(72)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 (Qt override)
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
            event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self.submit()
            return
        super().keyPressEvent(event)

    def submit(self) -> None:
        text = self.toPlainText().strip()
        if text:
            self.clear()
            self.submitted.emit(text)


class KeyPrompt(Card):
    """First-run prompt shown while MechanIQ has no usable API key."""

    add_key_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Set up MechanIQ", parent)
        self.set_state("accent")
        self.text_label = label(KEY_PROMPT_TEXT, "dim")
        self.text_label.setWordWrap(True)
        self.add_key_button = button("Add API key", "primary", "Open MechanIQ settings to enter an API key")
        self.add_key_button.clicked.connect(self.add_key_requested.emit)
        self.body.addWidget(self.text_label)
        self.body.addLayout(hbox(self.add_key_button, None))


class AgentChips(QWidget):
    """One small button per agent with its state; clicking emits the agent id."""

    clicked = Signal(str)

    def __init__(self, model: MechaniqModel, limit: int = 3, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.model = model
        self.limit = limit
        self.layout_ = QHBoxLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setSpacing(6)
        self.chips: list[QPushButton] = []
        model.agents_changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        for chip in self.chips:
            self.layout_.removeWidget(chip)
            chip.hide()
            chip.deleteLater()
        self.chips = []
        while self.layout_.count():
            self.layout_.takeAt(0)
        agents = self.model.agents()
        shown = [agents[0], *agents[1:][-(self.limit - 1) :]] if len(agents) > self.limit else agents
        for agent in shown:
            chip = self._chip(_short(agent.title), agent.state)
            chip.setToolTip(f"{agent.title}: {AGENT_STATE_LABELS.get(agent.state, agent.state)}")
            chip.clicked.connect(lambda _=False, a=agent.agent_id: self.clicked.emit(a))
            self.chips.append(chip)
            self.layout_.addWidget(chip)
        hidden = len(agents) - len(shown)
        if hidden:
            more = self._chip(f"+{hidden}", "idle")
            more.clicked.connect(lambda _=False: self.clicked.emit(LEAD))
            self.chips.append(more)
            self.layout_.addWidget(more)
        self.layout_.addStretch(1)

    def _chip(self, text: str, state: str) -> QPushButton:
        color = theme.ACCENT if state == "running" else theme.FAULT if state == "failed" else theme.TEXT_DIM
        border = color if state in ("running", "failed") else theme.BORDER_STRONG
        chip = QPushButton(text)
        chip.setCursor(Qt.CursorShape.PointingHandCursor)
        chip.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {color}; border: 1px solid {border}; "
            f"border-radius: 9px; padding: 1px 8px; font-size: 8.5pt; }}"
        )
        return chip


def _short(title: str, limit: int = CHIP_TITLE_LIMIT) -> str:
    return title if len(title) <= limit else title[: limit - 3].rstrip() + "..."


def format_change_time(value) -> str:
    if isinstance(value, (int, float)):
        return time.strftime("%H:%M:%S", time.localtime(value))
    try:
        return datetime.fromisoformat(str(value)).strftime("%H:%M:%S")
    except ValueError:
        return str(value)


def can_undo(entry) -> bool:
    return bool(getattr(entry, "commit", "")) and getattr(entry, "status", "") not in NOT_UNDOABLE


class ChangeRow(QFrame):
    """One change log entry: time, status, reload kind, commit, summary, files, Undo."""

    def __init__(self, entry, on_undo, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.entry = entry
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(3)
        status = getattr(entry, "status", "")
        reload_kind = getattr(entry, "reload_kind", "") or "no reload"
        commit = (getattr(entry, "commit", "") or "")[:7] or "no commit"
        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(label(format_change_time(getattr(entry, "time", "")), "mono"))
        top.addWidget(label(status, state=CHANGE_STATUS_STATES.get(status, "warn")))
        top.addWidget(label(reload_kind, "dim", "warn" if "restart" in reload_kind else None))
        top.addWidget(label(commit, "faint"))
        top.addStretch(1)
        self.undo_button = button("Undo", "ghost", "Revert this change with git revert and reload")
        self.undo_button.setEnabled(can_undo(entry))
        self.undo_button.clicked.connect(lambda: on_undo(str(getattr(entry, "id", ""))))
        top.addWidget(self.undo_button)
        layout.addLayout(top)
        summary = label(getattr(entry, "summary", ""))
        summary.setWordWrap(True)
        layout.addWidget(summary)
        files = ", ".join(str(f) for f in getattr(entry, "files", ()) or ())
        files_label = label(files or "no files", "faint")
        files_label.setWordWrap(True)
        files_label.setFont(theme.mono_font(8.5))
        layout.addWidget(files_label)


class ChangesView(QScrollArea):
    """Change log, newest first, rebuilt when the model's changes change."""

    undo_requested = Signal(str)

    def __init__(self, model: MechaniqModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.model = model
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.rows: list[ChangeRow] = []
        self._body = QWidget()
        self._layout = QVBoxLayout(self._body)
        self._layout.setContentsMargins(8, 8, 8, 8)
        self._layout.setSpacing(6)
        self.setWidget(self._body)
        model.changes_changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
        entries = self.model.changes()
        self.rows = [ChangeRow(entry, self.undo_requested.emit) for entry in entries]
        for row in self.rows:
            self._layout.addWidget(row)
        if not self.rows:
            empty = label(
                "No changes yet. Code changes MechanIQ makes are tested, reloaded, committed and listed here.", "dim"
            )
            empty.setWordWrap(True)
            self._layout.addWidget(empty)
        self._layout.addStretch(1)


def header_info(model: MechaniqModel) -> str:
    parts = [model.model_name or "model not set", model.auth_source or "auth not set"]
    if model.total_cost:
        parts.append(f"${model.total_cost:.2f}")
    return ", ".join(parts)
