"""Instrument panel settings: choose the layout preset, assign sensors and
range overrides per slot, and manage named layouts (Save As, Rename, Delete).

Edits apply to a working copy of the preferences; `result_preferences()` returns
it, with the layout shown in the selector made active."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QGridLayout,
    QInputDialog,
    QRadioButton,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from q50diag.gui import theme
from q50diag.gui.catalog import CatalogEntry
from q50diag.gui.dashboard_layouts import (
    PRESETS,
    Layout,
    Preferences,
    Preset,
    SlotAssignment,
    active_layout,
    find_layout,
    unique_layout_id,
    valid_range,
    visible_slots,
    with_layout,
    with_preset,
    with_slot,
    without_layout,
)
from q50diag.gui.dialogs.sensor_picker import SensorPickerDialog
from q50diag.gui.widgets.common import button, hbox, label, make_table, table_item

SENSOR_COLUMN, DEFAULT_COLUMN, MIN_COLUMN, MAX_COLUMN = 1, 2, 3, 4
# The app stylesheet styles check boxes only; give radio buttons a visible indicator.
RADIO_STYLE = (
    f"QRadioButton::indicator {{ width: 12px; height: 12px; border-radius: 7px;"
    f" border: 1px solid {theme.BORDER_STRONG}; background: {theme.BG_DEEP}; }}"
    f"QRadioButton::indicator:checked {{ background: {theme.ACCENT}; border-color: {theme.ACCENT}; }}"
)
PRESET_ICON_SIZE = QSize(44, 30)
RANGE_ERROR = "Slot {}: enter both min and max as numbers with max greater than min, or leave both blank."


def preset_icon(preset: Preset, size: QSize = PRESET_ICON_SIZE) -> QIcon:
    """A thumbnail of the preset's grid cells."""
    pixmap = QPixmap(size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    cell_w, cell_h = size.width() / preset.columns, size.height() / preset.rows
    painter.setPen(QColor(theme.ACCENT))
    painter.setBrush(QColor(theme.ACCENT_DIM))
    for row, column, row_span, column_span in preset.cells:
        rect = QRectF(column * cell_w + 1, row * cell_h + 1, column_span * cell_w - 2, row_span * cell_h - 2)
        painter.drawRoundedRect(rect, 2, 2)
    painter.end()
    return QIcon(pixmap)


class DashboardSettingsDialog(QDialog):
    def __init__(self, prefs: Preferences, catalog: Sequence[CatalogEntry], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Instrument panel settings")
        self.setStyleSheet(RADIO_STYLE)
        self.resize(820, 600)
        self._prefs = prefs
        self._catalog = list(catalog)
        self._entries = {entry.key: entry for entry in self._catalog}
        self._editing = active_layout(prefs).id
        self._invalid_rows: set[int] = set()
        self._filling = False
        self.layout_combo = QComboBox()
        self.layout_combo.setMinimumWidth(220)
        self.preset_group = QButtonGroup(self)
        self.preset_buttons: dict[str, QRadioButton] = {}
        self.table = make_table(["Slot", "Sensor", "Default range", "Min", "Max"], stretch_column=SENSOR_COLUMN)
        self.error_label = label("", state="fault")
        self.error_label.setWordWrap(True)
        self.apply_button = button("Apply", "primary")
        self._build_layout()
        self._fill_layout_combo()
        self._load_layout()
        self.layout_combo.currentIndexChanged.connect(self._on_layout_selected)
        self.preset_group.idClicked.connect(self._on_preset_clicked)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)

    def _build_layout(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        save_as, rename, self.delete_button = button("Save As..."), button("Rename..."), button("Delete", "danger")
        save_as.clicked.connect(lambda: self._prompt("Save layout as", "Layout name", self.save_as))
        rename.clicked.connect(lambda: self._prompt("Rename layout", "Layout name", self.rename, self.current_layout().name))
        self.delete_button.clicked.connect(self.delete_layout)
        layout.addWidget(label("SAVED LAYOUT", "section"))
        layout.addLayout(hbox(self.layout_combo, save_as, rename, self.delete_button, None))
        layout.addWidget(label("GAUGE ARRANGEMENT", "section"))
        layout.addLayout(self._build_presets())
        layout.addWidget(label("SLOTS", "section"))
        layout.addWidget(self.table, 1)
        change, clear = button("Change sensor..."), button("Clear slot", "ghost")
        change.clicked.connect(lambda: self.pick_sensor(self.table.currentRow()))
        clear.clicked.connect(lambda: self.assign_slot(self.table.currentRow(), ""))
        hint = label("Leave Min and Max blank to use the sensor's default range.", "faint")
        layout.addLayout(hbox(change, clear, None, hint))
        layout.addWidget(self.error_label)
        cancel = button("Cancel", "ghost")
        cancel.clicked.connect(self.reject)
        self.apply_button.clicked.connect(self.accept)
        layout.addLayout(hbox(None, cancel, self.apply_button))

    def _build_presets(self) -> QGridLayout:
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        for index, preset in enumerate(PRESETS.values()):
            radio = QRadioButton(preset.label)
            radio.setIcon(preset_icon(preset))
            radio.setIconSize(QSize(44, 30))
            self.preset_group.addButton(radio, index)
            self.preset_buttons[preset.name] = radio
            grid.addWidget(radio, 0, index)
        grid.setColumnStretch(len(PRESETS), 1)
        return grid

    # Layout selection ---------------------------------------------------
    def current_layout(self) -> Layout:
        return find_layout(self._prefs, self._editing) or active_layout(self._prefs)

    def _fill_layout_combo(self) -> None:
        self.layout_combo.blockSignals(True)
        self.layout_combo.clear()
        for layout in self._prefs.layouts:
            self.layout_combo.addItem(layout.name, layout.id)
        self.layout_combo.setCurrentIndex(max(self.layout_combo.findData(self._editing), 0))
        self.layout_combo.blockSignals(False)
        self.delete_button.setEnabled(len(self._prefs.layouts) > 1)

    def _on_layout_selected(self, index: int) -> None:
        self._editing = self.layout_combo.itemData(index)
        self._load_layout()

    def select_layout(self, layout_id: str) -> None:
        self.layout_combo.setCurrentIndex(self.layout_combo.findData(layout_id))

    def _load_layout(self) -> None:
        layout = self.current_layout()
        self.preset_buttons[layout.preset].setChecked(True)
        self._fill_table()

    def _replace_current(self, layout: Layout) -> None:
        self._prefs = with_layout(self._prefs, layout)

    # Named layouts ------------------------------------------------------
    def _prompt(self, title: str, prompt: str, action, text: str = "") -> None:
        name, ok = QInputDialog.getText(self, title, prompt, text=text)
        if ok and name.strip():
            action(name.strip())

    def save_as(self, name: str) -> None:
        """Copy the layout being edited under a new name and edit the copy."""
        copy = replace(self.current_layout(), id=unique_layout_id(self._prefs, name), name=name)
        self._prefs = with_layout(self._prefs, copy)
        self._editing = copy.id
        self._fill_layout_combo()
        self._load_layout()

    def rename(self, name: str) -> None:
        self._replace_current(replace(self.current_layout(), name=name))
        self._fill_layout_combo()

    def delete_layout(self) -> None:
        remaining = without_layout(self._prefs, self._editing)
        if remaining is self._prefs:
            return
        self._prefs = remaining
        self._editing = active_layout(remaining).id
        self._fill_layout_combo()
        self._load_layout()

    # Preset and slots ---------------------------------------------------
    def _on_preset_clicked(self, index: int) -> None:
        self.set_preset(list(PRESETS)[index])

    def set_preset(self, preset: str) -> None:
        self.preset_buttons[preset].setChecked(True)
        self._replace_current(with_preset(self.current_layout(), preset))
        self._fill_table()

    def _fill_table(self) -> None:
        self._filling = True
        slots = visible_slots(self.current_layout())
        self.table.setRowCount(len(slots))
        for row, slot in enumerate(slots):
            self._fill_row(row, slot)
        self._filling = False
        self._invalid_rows.clear()
        self._sync_errors()

    def _fill_row(self, row: int, slot: SlotAssignment | None) -> None:
        entry = self._entries.get(slot.key) if slot else None
        self.table.setItem(row, 0, _fixed(table_item(str(row + 1), color=theme.TEXT_DIM)))
        if slot is None:
            sensor = table_item("(empty)", color=theme.TEXT_FAINT)
        elif entry is None:
            sensor = table_item(f"{slot.key} (unavailable)", color=theme.WARN)
        else:
            sensor = table_item(f"{entry.param.name} ({entry.param.unit})" if entry.param.unit else entry.param.name)
        self.table.setItem(row, SENSOR_COLUMN, _fixed(sensor))
        self.table.setItem(row, DEFAULT_COLUMN, _fixed(table_item(_default_range(entry), color=theme.TEXT_DIM)))
        low, high = slot.range if slot and slot.range else ("", "")
        for column, value in ((MIN_COLUMN, low), (MAX_COLUMN, high)):
            item = table_item(_number(value) if value != "" else "", mono=True)
            if slot is None:
                _fixed(item)
            self.table.setItem(row, column, item)

    def _on_cell_double_clicked(self, row: int, column: int) -> None:
        if column in (SENSOR_COLUMN, 0, DEFAULT_COLUMN):
            self.pick_sensor(row)

    def pick_sensor(self, row: int) -> None:
        if row < 0:
            return
        slot = visible_slots(self.current_layout())[row]
        picker = SensorPickerDialog(self._catalog, self._prefs.favorites, slot.key if slot else "", self)
        accepted = picker.exec() == QDialog.DialogCode.Accepted
        self._prefs = replace(self._prefs, favorites=picker.favorites())
        if accepted:
            self.assign_slot(row, picker.selected_key())

    def assign_slot(self, row: int, key: str) -> None:
        """Put `key` in slot `row` ("" empties it); a new key drops any range override."""
        if row < 0:
            return
        current = visible_slots(self.current_layout())[row]
        if current is not None and current.key == key:
            return
        assignment = SlotAssignment(key) if key else None
        self._replace_current(with_slot(self.current_layout(), row, assignment))
        self._filling = True
        self._fill_row(row, assignment)
        self._filling = False
        self._invalid_rows.discard(row)
        self._sync_errors()

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._filling or item.column() not in (MIN_COLUMN, MAX_COLUMN):
            return
        self.set_range_text(item.row(), self.table.item(item.row(), MIN_COLUMN).text(),
                            self.table.item(item.row(), MAX_COLUMN).text())

    def set_range_text(self, row: int, low_text: str, high_text: str) -> None:
        """Parse a range override for slot `row`; both blank clears it."""
        slot = visible_slots(self.current_layout())[row]
        if slot is None:
            return
        parsed = _parse_range(low_text, high_text)
        if parsed is False:
            self._invalid_rows.add(row)
        else:
            self._invalid_rows.discard(row)
            self._replace_current(with_slot(self.current_layout(), row, replace(slot, range=parsed)))
        self._sync_errors()

    def _sync_errors(self) -> None:
        first = min(self._invalid_rows) + 1 if self._invalid_rows else None
        self.error_label.setText(RANGE_ERROR.format(first) if first else "")
        self.apply_button.setEnabled(first is None)

    def result_preferences(self) -> Preferences:
        return replace(self._prefs, active=self._editing)


def _fixed(item: QTableWidgetItem) -> QTableWidgetItem:
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return item


def _default_range(entry: CatalogEntry | None) -> str:
    if entry is None or entry.param.min_value is None or entry.param.max_value is None:
        return "none"
    return f"{_number(entry.param.min_value)} to {_number(entry.param.max_value)}"


def _number(value: float) -> str:
    return f"{value:g}"


def _parse_range(low_text: str, high_text: str):
    """(low, high), None when both are blank, or False when invalid."""
    if not low_text.strip() and not high_text.strip():
        return None
    try:
        parsed = valid_range((float(low_text), float(high_text)))
    except ValueError:
        return False
    return parsed if parsed is not None else False
