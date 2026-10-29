"""Main window: navigation rail, stacked pages, the docked MechanIQ panel,
top status bar, task strip, toasts and the global Mark event hotkeys (Space, F9)."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, QSize, Qt
from PySide6.QtGui import QCloseEvent, QKeySequence, QResizeEvent, QShortcut
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPlainTextEdit,
    QScrollArea,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from q50diag.gui.context import AppContext
from q50diag.gui.mechaniq import build_mechaniq
from q50diag.gui.mechaniq.page import MechaniqPage
from q50diag.gui.pages.base import Page
from q50diag.gui.pages.bus_monitor import BusMonitorPage
from q50diag.gui.pages.connect import ConnectPage
from q50diag.gui.pages.console import ConsolePage
from q50diag.gui.pages.dashboard import DashboardPage
from q50diag.gui.pages.live_data import LiveDataPage
from q50diag.gui.pages.modules import ModulesPage
from q50diag.gui.pages.overview import OverviewPage
from q50diag.gui.pages.recordings import RecordingsPage
from q50diag.gui.pages.steering import SteeringPage
from q50diag.gui.pages.trouble_codes import TroubleCodesPage
from q50diag.gui.widgets.task_strip import TaskStrip
from q50diag.gui.widgets.toast import ToastHost
from q50diag.gui.widgets.top_bar import TopBar

NAVIGATION: tuple[tuple[str, tuple[type[Page], ...]], ...] = (
    ("Vehicle", (ConnectPage, OverviewPage, TroubleCodesPage, ModulesPage)),
    ("Live", (DashboardPage, LiveDataPage, SteeringPage, BusMonitorPage)),
    ("Tools", (ConsolePage, RecordingsPage, MechaniqPage)),
)
TEXT_INPUTS = (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)
PAGE_ROLE = Qt.ItemDataRole.UserRole + 1
MAX_PAGE_SHORTCUTS = 9  # Ctrl+1 to Ctrl+9


class MarkEventFilter(QObject):
    """Space marks an event unless focus is in a text input, so typing in
    search boxes and the console still works."""

    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self._window = window

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 (Qt override)
        if event.type() != QEvent.Type.KeyPress or event.key() != Qt.Key.Key_Space:
            return False
        if not self._window.isActiveWindow() or _typing():
            return False
        if not event.isAutoRepeat():
            self._window.ctx.mark_event()
        return True


def _typing() -> bool:
    focus = QApplication.focusWidget()
    if isinstance(focus, TEXT_INPUTS):
        return True
    return isinstance(focus, QComboBox) and focus.isEditable()


# A page squeezes sideways down to this width, as it did unframed, then scrolls.
PAGE_MIN_WIDTH = 720


class PageFrame(QScrollArea):
    """Hosts a page so a small window scrolls it instead of squeezing its
    widgets over each other. The page fills the frame like an unframed page
    would, down to PAGE_MIN_WIDTH and its own minimum height. QScrollArea's
    own resizing is not used: it keeps pages at their width hint, wider than
    they need, and lets word-wrapped labels make them taller."""

    def __init__(self, page: QWidget) -> None:
        super().__init__()
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setWidget(page)

    def replace_page(self, page: QWidget) -> None:
        self.takeWidget()
        self.setWidget(page)
        self._fit()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        self._fit()

    def _fit(self) -> None:
        page = self.widget()
        if page is None:
            return
        hint = page.minimumSizeHint()
        least = QSize(min(hint.width(), PAGE_MIN_WIDTH), hint.height())
        room = self.maximumViewportSize()
        bar_height = self.horizontalScrollBar().sizeHint().height()
        bar_width = self.verticalScrollBar().sizeHint().width()
        sideways = room.width() < least.width()
        vertical = room.height() - bar_height * sideways < least.height()
        sideways = sideways or room.width() - bar_width * vertical < least.width()
        width = room.width() - bar_width * vertical
        height = room.height() - bar_height * sideways
        page.resize(max(width, least.width()), max(height, least.height()))


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext, mechaniq_builder: Callable | None = build_mechaniq) -> None:
        """`mechaniq_builder(window, ctx)` returns the MechanIQ controller whose
        `panel` is docked on the right; None leaves MechanIQ out (tests)."""
        super().__init__()
        self.ctx = ctx
        self._show_vehicle_title(ctx.vehicle)
        ctx.vehicle_changed.connect(self._show_vehicle_title)
        self.resize(1480, 920)
        # Fits a 1366x768 laptop at 125% scaling; pages scroll instead of overlapping.
        self.setMinimumSize(900, 560)
        self.pages: dict[str, Page] = {}
        self.frames: dict[str, PageFrame] = {}
        self._build()
        self.mechaniq = mechaniq_builder(self, ctx) if mechaniq_builder is not None else None
        if self.mechaniq is not None and self.mechaniq.panel is not None:
            self.body.addWidget(self.mechaniq.panel)
        self.toasts = ToastHost(self)
        ctx.toast.connect(self.toasts.show_toast)
        ctx.navigate.connect(self.show_page)
        ctx.session.status.connect(lambda text: self.statusBar().showMessage(text, 15000))
        ctx.session.error.connect(lambda text: self.statusBar().showMessage(f"Error: {text}", 30000))
        self._install_hotkeys()
        self.show_page(ConnectPage.title)

    def _show_vehicle_title(self, vehicle) -> None:
        self.setWindowTitle(f"q50diag: {vehicle.name} diagnostic explorer")

    def _build(self) -> None:
        root = QWidget()
        root.setObjectName("Root")
        column = QVBoxLayout(root)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        self.top_bar = TopBar(self.ctx)
        self.task_strip = TaskStrip(self.ctx.session)
        column.addWidget(self.top_bar)
        column.addWidget(self.task_strip)
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.body = body
        self.nav = QListWidget()
        self.nav.setObjectName("NavRail")
        self.nav.setFixedWidth(196)
        self.nav.currentItemChanged.connect(self._on_nav)
        self.stack = QStackedWidget()
        body.addWidget(self.nav)
        body.addWidget(self.stack, 1)
        column.addLayout(body, 1)
        self.setCentralWidget(root)
        self._populate_navigation()
        self.poll_label = QLabel("")
        self.statusBar().addPermanentWidget(self.poll_label)
        self.ctx.session.poll_rate.connect(self._on_poll_rate)
        self.statusBar().showMessage("Not connected. Choose a port on the Connect page.")

    def _populate_navigation(self) -> None:
        for section, page_classes in NAVIGATION:
            header = QListWidgetItem(section.upper())
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            font = header.font()
            font.setPointSizeF(7.5)
            font.setBold(True)
            header.setFont(font)
            self.nav.addItem(header)
            for page_class in page_classes:
                page = page_class(self.ctx)
                self.pages[page.title] = page
                self.frames[page.title] = PageFrame(page)
                self.stack.addWidget(self.frames[page.title])
                item = QListWidgetItem(page.title)
                item.setData(PAGE_ROLE, page.title)
                self.nav.addItem(item)

    def _install_hotkeys(self) -> None:
        self._space_filter = MarkEventFilter(self)
        QApplication.instance().installEventFilter(self._space_filter)
        f9 = QShortcut(QKeySequence(Qt.Key.Key_F9), self)
        f9.setContext(Qt.ShortcutContext.ApplicationShortcut)
        f9.activated.connect(lambda: self.ctx.mark_event())
        for number, title in enumerate(list(self.pages)[:MAX_PAGE_SHORTCUTS], start=1):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{number}"), self)
            shortcut.activated.connect(lambda t=title: self.show_page(t))

    def show_page(self, title: str) -> None:
        for row in range(self.nav.count()):
            if self.nav.item(row).data(PAGE_ROLE) == title:
                self.nav.setCurrentRow(row)
                return

    def current_page(self) -> Page:
        return self.stack.currentWidget().widget()

    def _on_nav(self, current: QListWidgetItem | None, _previous) -> None:
        if current is None:
            return
        page = self.pages.get(current.data(PAGE_ROLE))
        if page is None:
            return
        self.stack.setCurrentWidget(self.frames[page.title])
        self._update_panel(page)
        page.on_shown()

    def _update_panel(self, page: Page) -> None:
        """The docked panel shows on every page except the full MechanIQ page."""
        panel = self.mechaniq.panel if self.mechaniq is not None else None
        if panel is not None:
            panel.setVisible(page.title != MechaniqPage.title)

    def rebuild_page(self, title: str) -> None:
        """Replace a page with a fresh instance of its class, looked up by name
        in its (possibly reloaded) module. Keeps the stack index, the nav row
        and the current selection."""
        old = self.pages[title]
        old_class = type(old)
        module = sys.modules.get(old_class.__module__) or importlib.import_module(old_class.__module__)
        page = getattr(module, old_class.__name__)(self.ctx)
        was_current = self.current_page() is old
        self.frames[title].replace_page(page)
        self.pages = {key: page if key == title else value for key, value in self.pages.items()}
        if hasattr(page, "attach") and self.mechaniq is not None:
            page.attach(self.mechaniq)
        old.deleteLater()
        if was_current:
            page.on_shown()

    def _on_poll_rate(self, rate: float) -> None:
        self.poll_label.setText(f"poll {rate:5.1f} reads/s" if rate > 0 else "")

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt override)
        QApplication.instance().removeEventFilter(self._space_filter)
        if self.mechaniq is not None:
            self.mechaniq.shutdown()
        if self.ctx.recorder.is_recording:
            self.ctx.stop_recording()
        self.ctx.session.shutdown()
        super().closeEvent(event)
