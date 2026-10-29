"""Small shared widgets: cards, readouts, headers, tables and helpers."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from q50diag.gui import theme


def label(text: str = "", role: str | None = None, state: str | None = None) -> QLabel:
    widget = QLabel(text)
    if role:
        widget.setProperty("role", role)
    if state:
        widget.setProperty("state", state)
    return widget


def button(text: str, variant: str | None = None, tooltip: str = "") -> QPushButton:
    widget = QPushButton(text)
    if variant:
        widget.setProperty("variant", variant)
    if tooltip:
        widget.setToolTip(tooltip)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    return widget


def separator() -> QFrame:
    line = QFrame()
    line.setObjectName("Separator")
    return line


def hbox(*widgets, spacing: int = 8, margins: tuple[int, int, int, int] = (0, 0, 0, 0)) -> QHBoxLayout:
    layout = QHBoxLayout()
    layout.setSpacing(spacing)
    layout.setContentsMargins(*margins)
    for widget in widgets:
        if widget is None:
            layout.addStretch(1)
        elif isinstance(widget, QWidget):
            layout.addWidget(widget)
        else:
            layout.addLayout(widget)
    return layout


class Card(QFrame):
    """Panel with an optional uppercase section title and a body layout."""

    def __init__(self, title: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(16, 14, 16, 14)
        self.body.setSpacing(8)
        self.title_label: QLabel | None = None
        if title:
            self.title_label = label(title.upper(), "section")
            self.body.addWidget(self.title_label)

    def set_state(self, state: str | None) -> None:
        theme.set_state(self, state)


class StatCard(Card):
    """Title, one large monospace value and a dim detail line."""

    def __init__(self, title: str, value: str = "--", detail: str = "", parent: QWidget | None = None) -> None:
        super().__init__(title, parent)
        self.value_label = label(value, "value")
        self.value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.detail_label = label(detail, "dim")
        self.detail_label.setWordWrap(True)
        self.body.addWidget(self.value_label)
        self.body.addWidget(self.detail_label)
        self.body.addStretch(1)

    def set_value(self, value: str, state: str | None = None, detail: str | None = None) -> None:
        self.value_label.setText(value)
        theme.set_state(self.value_label, state)
        self.set_state(state if state in ("ok", "warn", "fault") else None)
        if detail is not None:
            self.detail_label.setText(detail)


class BigReadout(Card):
    """Large live value for the Steering page: title, number, unit, sub line."""

    def __init__(self, title: str, unit: str = "", parent: QWidget | None = None) -> None:
        super().__init__(title, parent)
        self.value_label = label("--", "big")
        self.unit_label = label(unit, "dim")
        row = hbox(self.value_label, self.unit_label, None, spacing=6)
        row.setAlignment(self.unit_label, Qt.AlignmentFlag.AlignBottom)
        self.body.addLayout(row)
        self.sub_label = label("", "faint")
        self.body.addWidget(self.sub_label)

    def set_value(self, text: str, state: str | None = None, sub: str | None = None) -> None:
        self.value_label.setText(text)
        theme.set_state(self.value_label, state)
        if sub is not None:
            self.sub_label.setText(sub)


class PageHeader(QWidget):
    """Page title, subtitle and a right-aligned action area (`actions`)."""

    def __init__(self, title: str, subtitle: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 4)
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(label(title, "title"))
        self.subtitle = label(subtitle, "subtitle")
        self.subtitle.setWordWrap(True)
        text.addWidget(self.subtitle)
        layout.addLayout(text, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        layout.addLayout(self.actions)

    def add_action(self, widget: QWidget) -> QWidget:
        self.actions.addWidget(widget)
        return widget


def make_table(
    headers: list[str], stretch_column: int | None = None, mono_columns: tuple[int, ...] = ()
) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(26)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setAlternatingRowColors(True)
    table.setShowGrid(False)
    table.setWordWrap(False)
    header = table.horizontalHeader()
    header.setHighlightSections(False)
    header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    if stretch_column is not None:
        header.setSectionResizeMode(stretch_column, QHeaderView.ResizeMode.Stretch)
    else:
        header.setStretchLastSection(True)
    table.setProperty("mono_columns", list(mono_columns))
    return table


def table_item(text: str, mono: bool = False, color: str | None = None, align_right: bool = False) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    if mono:
        item.setFont(theme.mono_font(9.5))
    if color:
        item.setForeground(QColor(color))
    if align_right:
        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return item


def format_value(value, digits: int = 2) -> str:
    if value is None:
        return "--"
    if isinstance(value, bool):
        return "ON" if value else "OFF"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def hex_bytes(data: bytes) -> str:
    return data.hex(" ").upper()


def state_dot(state: str | None) -> str:
    """HTML for a colored status dot, for rich-text labels."""
    return f'<span style="color:{theme.state_color(state)}">&#9679;</span>'
