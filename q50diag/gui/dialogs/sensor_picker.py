"""Sensor picker: choose the catalog parameter a dashboard gauge shows.

Lists every catalog entry with a favorite star, a search box over name, key
and module, and a "Favorites only" filter. Clicking the star column toggles a
favorite; double-click or Select assigns; Clear slot empties the gauge."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QCheckBox, QDialog, QLineEdit, QTableWidgetItem, QVBoxLayout, QWidget

from q50diag.gui import theme
from q50diag.gui.catalog import CatalogEntry
from q50diag.gui.widgets.common import button, hbox, make_table, table_item

STAR_ON = "★"
STAR_OFF = "☆"
STAR_COLUMN = 0
KEY_ROLE = Qt.ItemDataRole.UserRole + 1


class SensorPickerDialog(QDialog):
    """After exec(): `selected_key()` is the chosen key ("" when Clear slot was
    pressed) and `favorites()` the favorites group with any star changes."""

    def __init__(
        self,
        entries: Sequence[CatalogEntry],
        favorites: Sequence[str],
        current_key: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Choose sensor")
        self.resize(760, 540)
        self._favorites = tuple(favorites)
        self._selected = current_key
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search name, key or module")
        self.search_edit.setClearButtonEnabled(True)
        self.favorites_only = QCheckBox("Favorites only")
        self.table = make_table(["", "Name", "Unit", "Module", "Key"], stretch_column=1, mono_columns=(4,))
        self.table.setColumnWidth(STAR_COLUMN, 32)
        self.table.setColumnWidth(2, 70)
        self.table.setColumnWidth(3, 90)
        self.table.setColumnWidth(4, 200)
        self.favorite_button = button("Add to favorites", "ghost")
        self.clear_button = button("Clear slot", "danger")
        self.select_button = button("Select", "primary")
        self._build_layout()
        self._fill(entries, current_key)
        self.search_edit.textChanged.connect(self.apply_filter)
        self.favorites_only.toggled.connect(self.apply_filter)
        self.table.cellClicked.connect(self._on_cell_clicked)
        self.table.cellDoubleClicked.connect(lambda row, column: self._select_row(row))
        self.table.itemSelectionChanged.connect(self._sync_buttons)
        self.favorite_button.clicked.connect(self._toggle_current_favorite)
        self.clear_button.clicked.connect(self.clear_slot)
        self.select_button.clicked.connect(lambda: self._select_row(self.table.currentRow()))
        self._sync_buttons()
        self.search_edit.setFocus()

    def _build_layout(self) -> None:
        layout = QVBoxLayout(self)
        layout.addLayout(hbox(self.search_edit, self.favorites_only))
        layout.addWidget(self.table, 1)
        cancel = button("Cancel", "ghost")
        cancel.clicked.connect(self.reject)
        layout.addLayout(hbox(self.favorite_button, None, self.clear_button, cancel, self.select_button))

    def _fill(self, entries: Sequence[CatalogEntry], current_key: str) -> None:
        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            star = QTableWidgetItem()
            star.setData(KEY_ROLE, entry.key)
            star.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            star.setToolTip("Toggle favorite")
            self.table.setItem(row, STAR_COLUMN, star)
            self._paint_star(row)
            self.table.setItem(row, 1, table_item(entry.param.name))
            self.table.setItem(row, 2, table_item(entry.param.unit, color=theme.TEXT_DIM))
            self.table.setItem(row, 3, table_item(entry.module.name, color=theme.TEXT_DIM))
            self.table.setItem(row, 4, table_item(entry.key, mono=True, color=theme.TEXT_DIM))
            if entry.key == current_key:
                self.table.selectRow(row)
        if self.table.currentRow() >= 0:
            self.table.scrollToItem(self.table.item(self.table.currentRow(), 1))

    def _row_key(self, row: int) -> str:
        item = self.table.item(row, STAR_COLUMN) if row >= 0 else None
        return item.data(KEY_ROLE) if item is not None else ""

    def _paint_star(self, row: int) -> None:
        item = self.table.item(row, STAR_COLUMN)
        favorite = item.data(KEY_ROLE) in self._favorites
        item.setText(STAR_ON if favorite else STAR_OFF)
        item.setForeground(QColor(theme.WARN if favorite else theme.TEXT_FAINT))

    def apply_filter(self) -> None:
        terms = self.search_edit.text().strip().lower().split()
        only_favorites = self.favorites_only.isChecked()
        for row in range(self.table.rowCount()):
            key = self._row_key(row)
            haystack = " ".join(self.table.item(row, column).text() for column in (1, 3, 4)).lower()
            hidden = (only_favorites and key not in self._favorites) or not all(term in haystack for term in terms)
            self.table.setRowHidden(row, hidden)

    def visible_keys(self) -> list[str]:
        return [self._row_key(row) for row in range(self.table.rowCount()) if not self.table.isRowHidden(row)]

    def toggle_favorite(self, key: str) -> None:
        if key in self._favorites:
            self._favorites = tuple(existing for existing in self._favorites if existing != key)
        else:
            self._favorites = (*self._favorites, key)
        for row in range(self.table.rowCount()):
            if self._row_key(row) == key:
                self._paint_star(row)
        self._sync_buttons()
        if self.favorites_only.isChecked():
            self.apply_filter()

    def _on_cell_clicked(self, row: int, column: int) -> None:
        if column == STAR_COLUMN:
            self.toggle_favorite(self._row_key(row))

    def _toggle_current_favorite(self) -> None:
        key = self._row_key(self.table.currentRow())
        if key:
            self.toggle_favorite(key)

    def _sync_buttons(self) -> None:
        key = self._row_key(self.table.currentRow())
        self.select_button.setEnabled(bool(key))
        self.favorite_button.setEnabled(bool(key))
        self.favorite_button.setText("Remove from favorites" if key in self._favorites else "Add to favorites")

    def _select_row(self, row: int) -> None:
        key = self._row_key(row)
        if key:
            self._selected = key
            self.accept()

    def select_key(self, key: str) -> None:
        """Assign `key` and close, as a double-click on its row would."""
        row = next((row for row in range(self.table.rowCount()) if self._row_key(row) == key), -1)
        self._select_row(row)

    def clear_slot(self) -> None:
        self._selected = ""
        self.accept()

    def selected_key(self) -> str:
        return self._selected

    def favorites(self) -> tuple[str, ...]:
        return self._favorites
