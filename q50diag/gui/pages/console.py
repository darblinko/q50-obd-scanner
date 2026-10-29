"""Console page: raw AT and hex terminal over the session, with command
history and the TX/RX traffic stream."""

from __future__ import annotations

import html
import time

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QCheckBox, QFrame, QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget

from q50diag.gui import theme
from q50diag.gui.context import AppContext
from q50diag.gui.pages.base import Page
from q50diag.gui.widgets.common import button, hbox, label

MAX_LINES = 5000
MAX_HISTORY = 200
QUICK_COMMANDS = ("ATI", "AT@1", "ATRV", "ATDP", "ATDPN", "0100", "0902")
DIRECTION_COLORS = {"TX": theme.ACCENT, "RX": theme.TEXT, "INFO": theme.TEXT_DIM, "ERR": theme.FAULT}

SAFETY_NOTE = (
    "AT, ST and VT lines go to the adapter; hex lines are sent to the current header. "
    "Every hex request passes the read-only safety gate: writes, ECU reset, security access, "
    "routine control, I/O control and programming services are blocked. DTC clear is only "
    "available from the Trouble Codes page."
)


class CommandHistory:
    """Shell-style history: up walks back, down walks forward to an empty line."""

    def __init__(self, limit: int = MAX_HISTORY) -> None:
        self._items: list[str] = []
        self._limit = limit
        self._index = 0

    def add(self, line: str) -> None:
        if line and (not self._items or self._items[-1] != line):
            self._items = [*self._items, line][-self._limit:]
        self._index = len(self._items)

    def previous(self) -> str | None:
        if not self._items:
            return None
        self._index = max(0, self._index - 1)
        return self._items[self._index]

    def next(self) -> str:
        self._index = min(len(self._items), self._index + 1)
        return self._items[self._index] if self._index < len(self._items) else ""

    def items(self) -> list[str]:
        return list(self._items)


class ConsolePage(Page):
    title = "Console"
    subtitle = "Raw adapter terminal. Up and Down recall previous commands."

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(ctx, parent)
        self.history = CommandHistory()
        self._build_ui()
        session = ctx.session
        session.raw_log.connect(self.append_line)
        session.error.connect(self._on_error)
        session.connection_changed.connect(self._on_connection)

    def _build_ui(self) -> None:
        self.traffic_box = QCheckBox("Show polling traffic")
        self.traffic_box.setToolTip("Log every request the session sends, including live data polling")
        self.traffic_box.setChecked(self.ctx.session.log_traffic)
        self.traffic_box.toggled.connect(self._set_traffic)
        clear = button("Clear output", "ghost")
        self.header.add_action(self.traffic_box)
        self.header.add_action(clear)
        self.root.addWidget(self._build_note())
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setProperty("role", "mono")
        self.output.setFont(theme.mono_font(10))
        self.output.setMaximumBlockCount(MAX_LINES)
        self.output.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        clear.clicked.connect(self.output.clear)
        self.root.addWidget(self.output, 1)
        self.root.addLayout(self._build_quick_row())
        self.root.addLayout(self._build_input_row())

    def _build_note(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("Hint")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 8, 12, 8)
        text = label(SAFETY_NOTE)
        text.setWordWrap(True)
        layout.addWidget(text)
        return frame

    def _build_quick_row(self):
        row = hbox(label("Quick", "dim"))
        for command in QUICK_COMMANDS:
            quick = button(command, "ghost", f"Send {command}")
            quick.setStyleSheet(f'font-family: "{theme.mono_family()}";')
            quick.setProperty("command", command)
            quick.clicked.connect(self._on_quick_clicked)
            row.addWidget(quick)
        row.addStretch(1)
        return row

    def _build_input_row(self):
        self.prompt = label(">", "mono", "accent")
        self.input = QLineEdit()
        self.input.setProperty("role", "mono")
        self.input.setFont(theme.mono_font(10.5))
        self.input.setPlaceholderText("ATI    or    22 12 08    (hex is sent to the current ATSH header)")
        self.input.returnPressed.connect(self._send_input)
        self.input.installEventFilter(self)
        self.send_button = button("Send", "primary")
        self.send_button.clicked.connect(self._send_input)
        return hbox(self.prompt, self.input, self.send_button)

    # Behaviour ----------------------------------------------------------
    def _on_quick_clicked(self) -> None:
        sender = self.sender()
        if sender is not None:
            self.send(str(sender.property("command")))

    def _on_error(self, text: str) -> None:
        self.append_line("ERR", text)

    def _set_traffic(self, enabled: bool) -> None:
        self.ctx.session.log_traffic = enabled

    def _send_input(self) -> None:
        line = self.input.text().strip()
        self.input.clear()
        self.send(line)

    def send(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        self.history.add(line)
        if not self.ctx.session.connected:
            self.append_line("ERR", "Not connected. Connect on the Connect page first.")
            return
        self.ctx.session.raw_command(line)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802; Qt override
        if watched is self.input and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Up:
                previous = self.history.previous()
                if previous is not None:
                    self.input.setText(previous)
                return True
            if event.key() == Qt.Key.Key_Down:
                self.input.setText(self.history.next())
                return True
        return super().eventFilter(watched, event)

    def append_line(self, direction: str, text: str) -> None:
        stamp = time.strftime("%H:%M:%S") + f".{int(time.time() * 1000) % 1000:03d}"
        color = DIRECTION_COLORS.get(direction, theme.TEXT)
        lines = [part for part in text.replace("\r", "\n").split("\n") if part.strip()] or [""]
        for part in lines:
            self.output.appendHtml(
                f'<span style="color:{theme.TEXT_FAINT}">{stamp}</span>&nbsp;&nbsp;'
                f'<span style="color:{color}"><b>{direction:<4}</b></span>&nbsp;'
                f'<span style="color:{color}">{html.escape(part)}</span>'
            )

    def _on_connection(self, connected: bool, info) -> None:
        if connected and info is not None:
            self.append_line("INFO", f"Connected: {info.identity}, {info.protocol}")
        else:
            self.append_line("INFO", "Disconnected")

    def on_shown(self) -> None:
        self.input.setFocus()
