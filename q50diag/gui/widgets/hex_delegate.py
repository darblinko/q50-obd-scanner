"""Table delegate that paints hex bytes in monospace and highlights the byte
positions that changed.

Set the cell's DisplayRole to the bytes as text ("62 12 08 00 1F") and
CHANGED_ROLE to a list of changed byte indices. Changed bytes are drawn in
the accent color on a tinted background."""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QRect, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from q50diag.gui import theme

CHANGED_ROLE = Qt.ItemDataRole.UserRole + 41


class HexBytesDelegate(QStyledItemDelegate):
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        self.initStyleOption(option, index)
        text = option.text
        option.text = ""
        style = option.widget.style() if option.widget else None
        if style:
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, option, painter, option.widget)
        changed = set(index.data(CHANGED_ROLE) or [])
        painter.save()
        font = theme.mono_font(9.5)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        x = option.rect.x() + 6
        y = option.rect.y()
        height = option.rect.height()
        space = metrics.horizontalAdvance(" ")
        for position, token in enumerate(text.split()):
            width = metrics.horizontalAdvance(token)
            rect = QRect(x, y, width, height)
            if position in changed:
                painter.fillRect(rect.adjusted(-2, 3, 2, -3), QColor(theme.ACCENT_DIM))
                painter.setPen(QColor(theme.ACCENT))
            else:
                painter.setPen(QColor(theme.TEXT))
            painter.drawText(rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, token)
            x += width + space
        painter.restore()
