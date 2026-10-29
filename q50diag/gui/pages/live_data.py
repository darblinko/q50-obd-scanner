"""Live Data page: pick any catalog parameter, poll it, tabulate and chart it."""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QShowEvent
from PySide6.QtWidgets import (
    QHeaderView,
    QLineEdit,
    QMenu,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from q50diag.gui import theme
from q50diag.gui.catalog import CatalogEntry, group_by_category
from q50diag.gui.context import AppContext, Marker
from q50diag.gui.dashboard_layouts import Preferences
from q50diag.gui.favorites import load_favorites, save_favorites, with_key, without_key
from q50diag.gui.pages.base import Page
from q50diag.gui.widgets.charts import StackedCharts
from q50diag.gui.widgets.common import (
    Card,
    button,
    format_value,
    hbox,
    label,
    make_table,
    table_item,
)

OWNER = "live"
TABLE_REFRESH_MS = 200
KEY_ROLE = Qt.ItemDataRole.UserRole + 1
TABLE_HEADERS = ["Parameter", "Value", "Unit", "Min", "Max", "Upd/s", "Module"]


class LiveDataPage(Page):
    title = "Live Data"
    subtitle = "Every supported OBD-II PID, manufacturer module parameter and decoded broadcast signal."

    def __init__(self, ctx: AppContext, parent: QWidget | None = None, favorites_path: Path | None = None) -> None:
        super().__init__(ctx, parent)
        self._checked: list[str] = []
        self._favorites_path = favorites_path
        self._favorites: Preferences = load_favorites(favorites_path)
        self._rebuilding = False
        self._build_header()
        self._build_body()
        self._wire()
        self._rebuild_tree()
        self.charts.set_markers([(m.timestamp, m.label) for m in ctx.markers])
        self._timer = QTimer(self)
        self._timer.setInterval(TABLE_REFRESH_MS)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()

    # Construction -------------------------------------------------------
    def _build_header(self) -> None:
        self.rate_label = label("0.0 reads/s", "mono", "idle")
        self.rate_label.setToolTip("Parameter reads per second across all polling owners")
        self.reset_button = button("Reset min/max", "ghost", "Clear the min, max and rate statistics")
        self.poll_button = button("Start polling", "primary")
        self.poll_button.setCheckable(True)
        self.favorite_button = button(
            "Favorite checked", "ghost", "Add every checked parameter to the dashboard favorites"
        )
        self.header.add_action(self.rate_label)
        self.header.add_action(self.favorite_button)
        self.header.add_action(self.reset_button)
        self.header.add_action(self.poll_button)

    def _build_body(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_picker())
        splitter.addWidget(self._build_values())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([360, 900])
        self.root.addWidget(splitter, 1)

    def _build_picker(self) -> QWidget:
        card = Card("Parameters")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search name, key or module")
        self.search.setClearButtonEnabled(True)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Parameter", "Module"])
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setStretchLastSection(False)
        self.count_label = label("", "faint")
        self.clear_button = button("Uncheck all", "ghost")
        card.body.addWidget(self.search)
        card.body.addWidget(self.tree, 1)
        card.body.addLayout(hbox(self.count_label, None, self.clear_button))
        card.setMinimumWidth(280)
        return card

    def _build_values(self) -> QWidget:
        splitter = QSplitter(Qt.Orientation.Vertical)
        table_card = Card("Values")
        self.table = make_table(TABLE_HEADERS, stretch_column=0)
        for column, width in enumerate([0, 120, 60, 90, 90, 70, 100]):
            if width:
                self.table.setColumnWidth(column, width)
        table_card.body.addWidget(self.table)
        chart_card = Card("Charts")
        self.charts = StackedCharts(self.ctx.store, self._meta)
        chart_card.body.addWidget(self.charts, 1)
        splitter.addWidget(table_card)
        splitter.addWidget(chart_card)
        splitter.setSizes([260, 520])
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)
        return holder

    def _wire(self) -> None:
        self.search.textChanged.connect(self._apply_filter)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.clear_button.clicked.connect(self._uncheck_all)
        self.tree.customContextMenuRequested.connect(self._show_tree_menu)
        self.favorite_button.clicked.connect(self.favorite_checked)
        self.poll_button.toggled.connect(self._on_poll_toggled)
        self.reset_button.clicked.connect(self.ctx.store.reset_stats)
        self.ctx.catalog_changed.connect(self._rebuild_tree)
        self.ctx.marker_added.connect(self._on_marker)
        self.ctx.markers_cleared.connect(lambda: self.charts.set_markers([]))
        session = self.ctx.session
        session.poll_rate.connect(self._on_poll_rate)
        session.activity_changed.connect(self._sync_poll_button)
        session.connection_changed.connect(self._sync_poll_button)

    # Catalog tree -------------------------------------------------------
    def _rebuild_tree(self) -> None:
        self._rebuilding = True
        self.tree.clear()
        known = {entry.key for entry in self.ctx.catalog}
        self._checked = [key for key in self._checked if key in known]
        for category, entries in group_by_category(self.ctx.catalog).items():
            group = QTreeWidgetItem([category, ""])
            group.setFlags(Qt.ItemFlag.ItemIsEnabled)
            group.setForeground(0, theme_color(theme.TEXT_DIM))
            for entry in entries:
                group.addChild(self._make_leaf(entry))
            self.tree.addTopLevelItem(group)
            group.setExpanded(True)
        self._rebuilding = False
        self._apply_filter(self.search.text())
        self._selection_changed()

    def _make_leaf(self, entry: CatalogEntry) -> QTreeWidgetItem:
        item = QTreeWidgetItem([entry.param.name, entry.module.name])
        item.setData(0, KEY_ROLE, entry.key)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable)
        state = Qt.CheckState.Checked if entry.key in self._checked else Qt.CheckState.Unchecked
        item.setCheckState(0, state)
        item.setFont(1, theme.mono_font(9))
        tip = [entry.key, entry.param.description] if entry.param.description else [entry.key]
        if not entry.polled:
            tip.append("Broadcast signal: captured in short passive monitor windows while polling.")
        item.setToolTip(0, "\n".join(tip))
        self._mark_favorite(item, entry.key in self._favorites.favorites)
        return item

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for index in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(index)
            visible = 0
            for child_index in range(group.childCount()):
                child = group.child(child_index)
                haystack = f"{child.text(0)} {child.text(1)} {child.data(0, KEY_ROLE)}".lower()
                hidden = bool(needle) and needle not in haystack
                child.setHidden(hidden)
                visible += 0 if hidden else 1
            group.setHidden(visible == 0)

    def _leaves(self) -> list[QTreeWidgetItem]:
        items = []
        for index in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(index)
            items += [group.child(i) for i in range(group.childCount())]
        return items

    def set_checked(self, keys: list[str]) -> None:
        """Check exactly these keys (used by tests and other pages)."""
        self._rebuilding = True
        wanted = set(keys)
        for item in self._leaves():
            key = item.data(0, KEY_ROLE)
            item.setCheckState(0, Qt.CheckState.Checked if key in wanted else Qt.CheckState.Unchecked)
        self._checked = [k for k in keys if self.ctx.entry(k) is not None]
        self._rebuilding = False
        self._selection_changed()

    def _uncheck_all(self) -> None:
        self.set_checked([])

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._rebuilding or column != 0:
            return
        key = item.data(0, KEY_ROLE)
        if not key:
            return
        checked = item.checkState(0) == Qt.CheckState.Checked
        if checked and key not in self._checked:
            self._checked = [*self._checked, key]
        elif not checked and key in self._checked:
            self._checked = [k for k in self._checked if k != key]
        self._selection_changed()

    def _selection_changed(self) -> None:
        self.count_label.setText(f"{len(self._checked)} selected of {len(self.ctx.catalog)}")
        self._rebuild_table()
        self._update_chart_keys()
        if self.poll_button.isChecked():
            self._subscribe()

    def checked_keys(self) -> list[str]:
        return list(self._checked)

    # Favorites ----------------------------------------------------------
    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.reload_favorites()

    def favorite_keys(self) -> tuple[str, ...]:
        return self._favorites.favorites

    def reload_favorites(self) -> None:
        self._favorites = load_favorites(self._favorites_path)
        self._refresh_favorite_marks()

    def _refresh_favorite_marks(self) -> None:
        self._rebuilding = True
        for item in self._leaves():
            self._mark_favorite(item, item.data(0, KEY_ROLE) in self._favorites.favorites)
        self._rebuilding = False

    def _mark_favorite(self, item: QTreeWidgetItem, favorite: bool) -> None:
        font = item.font(0)
        font.setBold(favorite)
        item.setFont(0, font)
        item.setData(0, Qt.ItemDataRole.ForegroundRole, theme_color(theme.ACCENT) if favorite else None)

    def _show_tree_menu(self, position) -> None:
        item = self.tree.itemAt(position)
        key = item.data(0, KEY_ROLE) if item is not None else None
        if not key:
            return
        menu = QMenu(self.tree)
        if key in self._favorites.favorites:
            menu.addAction("Remove from favorites", lambda: self.remove_favorite(key))
        else:
            menu.addAction("Add to favorites", lambda: self.add_favorite(key))
        menu.exec(self.tree.viewport().mapToGlobal(position))

    def _update_favorites(self, keys: list[str], add: bool) -> Preferences:
        favorites = load_favorites(self._favorites_path)
        for key in keys:
            favorites = with_key(favorites, key) if add else without_key(favorites, key)
        save_favorites(favorites, self._favorites_path)
        self._favorites = favorites
        self._refresh_favorite_marks()
        return favorites

    def _favorite_name(self, key: str) -> str:
        entry = self.ctx.entry(key)
        return entry.param.name if entry else key

    def add_favorite(self, key: str) -> None:
        self._update_favorites([key], add=True)
        self.ctx.notify(f"Added {self._favorite_name(key)} to favorites")

    def remove_favorite(self, key: str) -> None:
        self._update_favorites([key], add=False)
        self.ctx.notify(f"Removed {self._favorite_name(key)} from favorites")

    def favorite_checked(self) -> None:
        if not self._checked:
            self.ctx.notify("Live Data: check at least one parameter to favorite", "warn")
            return
        self._update_favorites(self._checked, add=True)
        self.ctx.notify(f"Added {len(self._checked)} checked parameter(s) to favorites")

    # Polling ------------------------------------------------------------
    def _subscriptions(self) -> list:
        entries = [self.ctx.entry(key) for key in self._checked]
        return [entry.subscription() for entry in entries if entry is not None]

    def _subscribe(self) -> None:
        subscriptions = self._subscriptions()
        if subscriptions:
            self.ctx.session.start_polling(OWNER, subscriptions)
        else:
            self.ctx.session.stop_polling(OWNER)

    def _on_poll_toggled(self, running: bool) -> None:
        self.poll_button.setText("Stop polling" if running else "Start polling")
        if not running:
            self.ctx.session.stop_polling(OWNER)
            return
        if not self.ctx.session.connected:
            self.ctx.notify("Live Data: connect to an adapter or the simulator first", "warn")
            self.poll_button.setChecked(False)
            return
        if not self._checked:
            self.ctx.notify("Live Data: check at least one parameter to poll", "warn")
            self.poll_button.setChecked(False)
            return
        self._subscribe()

    def start_polling(self) -> None:
        self.poll_button.setChecked(True)

    def stop_polling(self) -> None:
        self.poll_button.setChecked(False)

    def _sync_poll_button(self, *_args) -> None:
        running = OWNER in self.ctx.session.polling_owners()
        if self.poll_button.isChecked() and not running and not self.ctx.session.connected:
            self.poll_button.blockSignals(True)
            self.poll_button.setChecked(False)
            self.poll_button.setText("Start polling")
            self.poll_button.blockSignals(False)

    def _on_poll_rate(self, rate: float) -> None:
        self.rate_label.setText(f"{rate:5.1f} reads/s")
        theme.set_state(self.rate_label, "accent" if rate > 0 else "idle")

    # Table and charts ---------------------------------------------------
    def _meta(self, key: str) -> tuple[str, str]:
        entry = self.ctx.entry(key)
        if entry is None:
            return key, ""
        return entry.param.name, entry.param.unit

    def _is_text(self, key: str) -> bool:
        latest = self.ctx.store.latest(key)
        return latest is not None and not latest.error and isinstance(latest.value, str)

    def _update_chart_keys(self) -> None:
        self.charts.set_keys([key for key in self._checked if not self._is_text(key)])

    def _rebuild_table(self) -> None:
        self.table.setRowCount(len(self._checked))
        for row, key in enumerate(self._checked):
            entry = self.ctx.entry(key)
            name = entry.param.name if entry else key
            module = entry.module.name if entry else ""
            unit = entry.param.unit if entry else ""
            cells = [table_item(name), table_item("--", mono=True), table_item(unit, color=theme.TEXT_DIM)]
            cells += [table_item("--", mono=True, align_right=True) for _ in range(3)]
            cells.append(table_item(module, mono=True, color=theme.TEXT_DIM))
            cells[0].setToolTip(key)
            for column, cell in enumerate(cells):
                self.table.setItem(row, column, cell)
        self._refresh_table()

    def _refresh(self) -> None:
        if not self.isVisible():
            return
        self._refresh_table()
        self._update_chart_keys()

    def _refresh_table(self) -> None:
        now = time.time()
        store = self.ctx.store
        for row, key in enumerate(self._checked):
            value_item = self.table.item(row, 1)
            if value_item is None:
                continue
            latest = store.latest(key)
            stats = store.stats(key)
            if latest is not None and latest.error and (stats is None or stats.count == 0):
                value_item.setText("error")
                value_item.setForeground(theme_color(theme.FAULT))
                value_item.setToolTip(latest.error)
            elif latest is not None:
                value_item.setText(format_value(latest.value))
                value_item.setForeground(theme_color(theme.ACCENT))
                value_item.setToolTip(stats.last_error if stats and stats.last_error else "")
            minimum = stats.minimum if stats else None
            maximum = stats.maximum if stats else None
            self.table.item(row, 3).setText(format_value(minimum))
            self.table.item(row, 4).setText(format_value(maximum))
            self.table.item(row, 5).setText(f"{store.rate(key, now):.1f}")

    def _on_marker(self, marker: Marker) -> None:
        self.charts.add_marker(marker.timestamp, marker.label)


def theme_color(hex_color: str) -> QBrush:
    return QBrush(QColor(hex_color))
