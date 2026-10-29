"""Progress strip for the running session command, with a Cancel button.

It follows Session.busy_changed and Session.progress, so every long command
shows progress here without page-specific code. Hidden while idle."""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QProgressBar, QWidget

from q50diag.gui.widgets.common import button, label
from q50diag.session import Session


class TaskStrip(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None, always_visible: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("TaskStrip")
        self._session = session
        self._always_visible = always_visible
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 6, 16, 6)
        layout.setSpacing(12)
        self.text = label("Idle", "dim")
        self.text.setMinimumWidth(260)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        self.count = label("", "mono")
        self.cancel_button = button("Cancel", "ghost", "Stop the running operation")
        self.cancel_button.clicked.connect(self._cancel)
        layout.addWidget(self.text)
        layout.addWidget(self.bar, 1)
        layout.addWidget(self.count)
        layout.addWidget(self.cancel_button)
        session.busy_changed.connect(self._on_busy)
        session.progress.connect(self._on_progress)
        self._set_idle()

    def _cancel(self) -> None:
        self._session.cancel()
        self.text.setText("Cancelling")

    def _on_busy(self, busy: bool, text: str) -> None:
        if busy:
            self.text.setText(text)
            self.bar.setRange(0, 0)  # indeterminate until progress arrives
            self.count.setText("")
            self.cancel_button.setEnabled(True)
            self.setVisible(True)
        else:
            self._set_idle()

    def _on_progress(self, text: str, done: int, total: int) -> None:
        self.setVisible(True)
        self.text.setText(text)
        if total > 0:
            self.bar.setRange(0, total)
            self.bar.setValue(min(done, total))
            self.count.setText(f"{done}/{total}")
        else:
            self.bar.setRange(0, 0)

    def _set_idle(self) -> None:
        self.text.setText("Idle")
        self.bar.setRange(0, 1)
        self.bar.setValue(0)
        self.count.setText("")
        self.cancel_button.setEnabled(False)
        self.setVisible(self._always_visible)
