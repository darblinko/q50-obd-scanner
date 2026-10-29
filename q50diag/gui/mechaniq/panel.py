"""Docked MechanIQ panel on the right edge of every page: header with model
and auth source, agent chips, Chat and Changes tabs, and the message input.
Collapses to a thin vertical strip."""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPaintEvent
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QStackedLayout, QTabWidget, QVBoxLayout, QWidget

from q50diag.gui import theme
from q50diag.gui.mechaniq.model import LEAD
from q50diag.gui.mechaniq.views import AgentChips, ChangesView, ChatInput, KeyPrompt, TranscriptView, header_info
from q50diag.gui.widgets.common import button, hbox, label

PANEL_WIDTH = 380
STRIP_WIDTH = 30


class VerticalStripButton(QPushButton):
    """Tall narrow button with its text drawn bottom to top."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setFixedWidth(STRIP_WIDTH)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Show the MechanIQ panel")
        self.busy = False

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802; Qt override
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(theme.PANEL_HOVER if self.underMouse() else theme.BG_DEEP))
        painter.setPen(QColor(theme.BORDER))
        painter.drawLine(0, 0, 0, self.height())
        painter.setPen(QColor(theme.ACCENT if self.busy else theme.TEXT_DIM))
        painter.translate(0, self.height())
        painter.rotate(-90)
        area = QRect(0, 0, self.height(), self.width())
        painter.drawText(area.adjusted(0, 0, -16, 0), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                         self.text())
        painter.end()


class MechaniqPanel(QWidget):
    """Lead conversation in a docked column. `controller` provides `model`,
    `send`, `interrupt`, `reset`, `undo`, `open_settings`, `needs_api_key`
    and the `needs_key_changed` signal."""

    open_page_requested = Signal()

    def __init__(self, controller, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("MechaniqPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"QWidget#MechaniqPanel {{ background: {theme.BG_DEEP}; "
                           f"border-left: 1px solid {theme.BORDER}; }}")
        self.controller = controller
        self.model = controller.model
        self.collapsed = False
        self._stack = QStackedLayout(self)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self.expanded_view = self._build_expanded()
        self.strip = VerticalStripButton("MECHANIQ")
        self.strip.clicked.connect(self.expand)
        self._stack.addWidget(self.expanded_view)
        self._stack.addWidget(self.strip)
        self.setFixedWidth(PANEL_WIDTH)
        self.model.info_changed.connect(self._update_info)
        self.model.busy_changed.connect(self._on_busy)
        controller.needs_key_changed.connect(self._on_needs_key)
        self._update_info()
        self._on_busy(self.model.busy)
        self._on_needs_key(controller.needs_api_key)

    def _build_expanded(self) -> QWidget:
        view = QWidget()
        column = QVBoxLayout(view)
        column.setContentsMargins(12, 10, 12, 12)
        column.setSpacing(8)
        column.addLayout(self._build_header())
        self.chips = AgentChips(self.model)
        self.chips.clicked.connect(lambda _agent: self.open_page_requested.emit())
        column.addWidget(self.chips)
        self.key_prompt = KeyPrompt()
        self.key_prompt.add_key_requested.connect(lambda: self.controller.open_settings(self.window()))
        column.addWidget(self.key_prompt)
        self.tabs = QTabWidget()
        self.transcript = TranscriptView(self.model, LEAD)
        self.changes = ChangesView(self.model)
        self.changes.undo_requested.connect(self.controller.undo)
        self.tabs.addTab(self.transcript, "Chat")
        self.tabs.addTab(self.changes, "Changes")
        column.addWidget(self.tabs, 1)
        self.status_label = label("", "faint")
        column.addWidget(self.status_label)
        self.input = ChatInput()
        self.input.submitted.connect(self.controller.send)
        column.addWidget(self.input)
        self.send_button = button("Send", "primary")
        self.send_button.clicked.connect(self.input.submit)
        self.stop_button = button("Stop", "danger", "Interrupt the current turn")
        self.stop_button.clicked.connect(self.controller.interrupt)
        self.new_button = button("New", "ghost", "Start a new conversation")
        self.new_button.clicked.connect(self.controller.reset)
        column.addLayout(hbox(self.new_button, None, self.stop_button, self.send_button))
        return view

    def _build_header(self) -> QVBoxLayout:
        title = label("MechanIQ", "section")
        title.setStyleSheet(f"color: {theme.ACCENT}; font-size: 9pt;")
        self.info_label = label("", "faint")
        self.settings_button = button("Settings", "ghost", "Auth mode and model")
        self.settings_button.clicked.connect(lambda: self.controller.open_settings(self.window()))
        self.page_button = button("Agents", "ghost", "Open the MechanIQ page with one column per agent")
        self.page_button.clicked.connect(self.open_page_requested.emit)
        self.collapse_button = button(">>", "ghost", "Collapse the panel to a strip")
        self.collapse_button.clicked.connect(self.collapse)
        row = QHBoxLayout()
        row.setSpacing(2)
        row.addWidget(title, 1)
        row.addWidget(self.page_button)
        row.addWidget(self.settings_button)
        row.addWidget(self.collapse_button)
        header = QVBoxLayout()
        header.setSpacing(0)
        header.addLayout(row)
        header.addWidget(self.info_label)
        return header

    def collapse(self) -> None:
        self.collapsed = True
        self._stack.setCurrentWidget(self.strip)
        self.setFixedWidth(STRIP_WIDTH)

    def expand(self) -> None:
        self.collapsed = False
        self._stack.setCurrentWidget(self.expanded_view)
        self.setFixedWidth(PANEL_WIDTH)
        self.input.setFocus()

    def _update_info(self) -> None:
        self.info_label.setText(header_info(self.model))

    def _on_needs_key(self, needs_key: bool) -> None:
        self.key_prompt.setVisible(needs_key)
        self.input.setEnabled(not needs_key)
        self.send_button.setEnabled(not needs_key)

    def _on_busy(self, busy: bool) -> None:
        self.stop_button.setEnabled(busy)
        self.status_label.setText("Working" if busy else "")
        self.strip.busy = busy
        self.strip.update()
