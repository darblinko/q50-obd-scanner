"""Non-modal toast notifications stacked in the bottom-right of a window."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from q50diag.gui import theme

LEVEL_COLORS = {"info": theme.ACCENT, "ok": theme.OK, "warn": theme.WARN, "error": theme.FAULT}
LEVEL_TITLES = {"info": "INFO", "ok": "DONE", "warn": "WARNING", "error": "ERROR"}
DURATION_MS = {"info": 5000, "ok": 4000, "warn": 8000, "error": 10000}
MAX_TOASTS = 4
TOAST_WIDTH = 380


class Toast(QFrame):
    def __init__(self, text: str, level: str, parent: QWidget) -> None:
        super().__init__(parent)
        color = LEVEL_COLORS.get(level, theme.ACCENT)
        self.setStyleSheet(
            f"Toast {{ background: {theme.PANEL_ALT}; border: 1px solid {theme.BORDER_STRONG};"
            f" border-left: 3px solid {color}; border-radius: 6px; }}"
        )
        self.setFixedWidth(TOAST_WIDTH)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click to dismiss")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        column = QVBoxLayout()
        column.setSpacing(2)
        title = QLabel(LEVEL_TITLES.get(level, "INFO"))
        title.setStyleSheet(f"color: {color}; font-size: 8pt; font-weight: 700;")
        body = QLabel(text)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        column.addWidget(title)
        column.addWidget(body)
        layout.addLayout(column)
        QTimer.singleShot(DURATION_MS.get(level, 5000), self.dismiss)

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self.dismiss()

    def dismiss(self) -> None:
        host = self.parent()
        self.hide()
        self.deleteLater()
        if isinstance(host, ToastHost):
            host.forget(self)


class ToastHost(QWidget):
    """Transparent overlay that owns toasts and keeps them anchored to the
    bottom-right corner of `window` as it resizes."""

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self._window = window
        self._toasts: list[Toast] = []
        window.installEventFilter(self)
        self.hide()

    def show_toast(self, text: str, level: str = "info") -> None:
        toast = Toast(text, level, self)
        self._toasts.append(toast)
        while len(self._toasts) > MAX_TOASTS:
            self._toasts[0].dismiss()
        toast.show()
        self._layout()

    def forget(self, toast: Toast) -> None:
        if toast in self._toasts:
            self._toasts.remove(toast)
        self._layout()

    def visible_texts(self) -> list[str]:
        texts = []
        for toast in self._toasts:
            labels = toast.findChildren(QLabel)
            texts.append(labels[-1].text() if labels else "")
        return texts

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 (Qt override)
        if watched is self._window and event.type() == QEvent.Type.Resize:
            self._layout()
        return False

    def _layout(self) -> None:
        if not self._toasts:
            self.hide()
            return
        margin, gap = 16, 8
        heights = [t.sizeHint().height() for t in self._toasts]
        total = sum(heights) + gap * (len(heights) - 1)
        x = self._window.width() - TOAST_WIDTH - margin
        y = self._window.height() - total - margin - 28  # clear the status bar
        self.setGeometry(x, y, TOAST_WIDTH, total)
        offset = 0
        for toast, height in zip(self._toasts, heights):
            toast.setGeometry(0, offset, TOAST_WIDTH, height)
            offset += height + gap
        self.show()
        self.raise_()
