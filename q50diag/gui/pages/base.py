"""Base class for the stacked pages."""

from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget

from q50diag.gui.context import AppContext
from q50diag.gui.widgets.common import PageHeader


class Page(QWidget):
    """A page has a title (also its navigation label), a subtitle, a header
    with an action area, and a vertical `root` layout for content."""

    title = ""
    subtitle = ""

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("Page")
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(28, 22, 28, 22)
        self.root.setSpacing(16)
        self.header = PageHeader(self.title, self.subtitle)
        self.root.addWidget(self.header)

    def on_shown(self) -> None:
        """Called each time the page becomes the visible page."""
