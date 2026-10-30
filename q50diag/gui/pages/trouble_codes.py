"""Trouble Codes page: one table of DTCs across every module, filters, the
OBD-II freeze frame, and per-module clearing behind a confirmation."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QMessageBox, QSplitter, QVBoxLayout, QWidget

from q50diag.core.types import Dtc, ModuleAddress, Reading
from q50diag.gui import theme
from q50diag.gui.context import AppContext
from q50diag.gui.pages.base import Page
from q50diag.gui.widgets.common import Card, button, format_value, hbox, label, make_table, table_item
from q50diag.session import DtcScanResult

OBD_TARGET = "OBD-II"
ALL = "All"
KINDS = ("stored", "pending", "permanent", "current", "history")
KIND_COLORS = {
    "stored": theme.FAULT,
    "current": theme.FAULT,
    "permanent": theme.FAULT,
    "pending": theme.WARN,
    "history": theme.TEXT_DIM,
}


def dtc_description(dtc: Dtc, describe) -> str:
    if dtc.description:
        return dtc.description
    try:
        return describe(dtc.code) or ""
    except Exception:  # noqa: BLE001 (a missing description is not an error worth surfacing)
        return ""


def freeze_frame_key(key: str) -> str:
    """Freeze frame readings use mode 02 keys; the catalog names mode 01."""
    return key.replace("obd.02.", "obd.01.", 1)


class TroubleCodesPage(Page):
    title = "Trouble Codes"
    subtitle = "Every module's diagnostic trouble codes. Clearing erases freeze frame data."

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(ctx, parent)
        self._result: DtcScanResult | None = None
        self._busy = False
        self.scan_button = button(
            "Scan all modules", "primary", "Read DTCs from OBD-II and every known or discovered module"
        )
        self.scan_button.clicked.connect(self._scan_all)
        self.header.add_action(self.scan_button)
        self._build_filters()
        self._build_body()
        self._wire()
        if ctx.dtc_result:
            self._on_dtcs(ctx.dtc_result)
        self._update_buttons()

    # UI -----------------------------------------------------------------
    def _build_filters(self) -> None:
        self.module_filter = QComboBox()
        self.module_filter.addItem(ALL)
        self.module_filter.currentIndexChanged.connect(self._fill_table)
        self.kind_filter = QComboBox()
        self.kind_filter.addItem(ALL)
        for kind in KINDS:
            self.kind_filter.addItem(kind)
        self.kind_filter.currentIndexChanged.connect(self._fill_table)
        self.summary = label("No scan yet", "dim")
        self.root.addLayout(
            hbox(label("Module", "dim"), self.module_filter, label("Kind", "dim"), self.kind_filter, None, self.summary)
        )

    def _build_body(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        self.table = make_table(["Module", "Code", "Description", "Kind", "Status"], stretch_column=2)
        self.table.setSortingEnabled(True)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        splitter.addWidget(self.table)
        side = QWidget()
        column = QVBoxLayout(side)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(16)
        column.addWidget(self._build_clear_card())
        column.addWidget(self._build_freeze_card(), 1)
        splitter.addWidget(side)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        self.root.addWidget(splitter, 1)

    def _build_clear_card(self) -> Card:
        card = Card("Clear codes")
        self.clear_target = QComboBox()
        self.clear_target.addItem(f"{OBD_TARGET} (mode 04, emission codes)", OBD_TARGET)
        self.clear_button = button("Clear", "danger", "Erase codes on the selected module after confirmation")
        self.clear_button.clicked.connect(self._on_clear_clicked)
        note = label("Clearing erases freeze frame data. Readiness monitors reset after an OBD-II clear.", "faint")
        note.setWordWrap(True)
        row = hbox(self.clear_target, self.clear_button)
        row.setStretch(0, 1)
        card.body.addLayout(row)
        card.body.addWidget(note)
        return card

    def _build_freeze_card(self) -> Card:
        card = Card("Freeze frame (OBD-II)")
        self.freeze_table = make_table(["Parameter", "Value", "Unit"], stretch_column=0)
        self.freeze_empty = label("No freeze frame stored", "faint")
        card.body.addWidget(self.freeze_table, 1)
        card.body.addWidget(self.freeze_empty)
        return card

    def _wire(self) -> None:
        session = self.ctx.session
        session.dtcs_ready.connect(self._on_dtcs)
        session.dtcs_cleared.connect(self._on_cleared)
        session.busy_changed.connect(self._on_busy)
        session.connection_changed.connect(self._on_connection)
        self.ctx.vehicle_state_cleared.connect(self._reset)

    # Data ---------------------------------------------------------------
    def _on_dtcs(self, result: DtcScanResult) -> None:
        self._result = result
        self._refresh_module_filter(result)
        self._refresh_clear_targets(result)
        self._fill_table()
        self._fill_freeze(result.freeze_frame)
        scanned = len(result.module_status)
        silent = sum(1 for status in result.module_status.values() if status != "ok")
        text = f"{len(result.dtcs)} codes, {scanned} modules scanned, {silent} not answering"
        if not result.complete:
            text += ", scan cancelled"
        self.summary.setText(text)

    def _reset(self) -> None:
        """Drop the previous vehicle's or bus's codes and clear targets."""
        self._on_dtcs(DtcScanResult([], {}))
        self._result = None
        self.summary.setText("No scan yet")

    def _refresh_module_filter(self, result: DtcScanResult) -> None:
        current = self.module_filter.currentText()
        names = sorted({d.module for d in result.dtcs} | (set(result.module_status) - {OBD_TARGET}))
        self.module_filter.blockSignals(True)
        self.module_filter.clear()
        self.module_filter.addItem(ALL)
        self.module_filter.addItems(names)
        index = self.module_filter.findText(current)
        self.module_filter.setCurrentIndex(max(index, 0))
        self.module_filter.blockSignals(False)

    def _refresh_clear_targets(self, result: DtcScanResult) -> None:
        current = self.clear_target.currentData()
        self.clear_target.clear()
        self.clear_target.addItem(f"{OBD_TARGET} (mode 04, emission codes)", OBD_TARGET)
        modules = {m.name: m for m in self.ctx.all_modules()}
        names = sorted({d.module for d in result.dtcs} | set(result.module_status))
        for name in names:
            module = modules.get(name)
            if module is not None and module.protocol != "obd2":
                self.clear_target.addItem(f"{name}  ({module.tx_id:03X})", name)
        index = self.clear_target.findData(current)
        self.clear_target.setCurrentIndex(max(index, 0))

    def visible_dtcs(self) -> list[Dtc]:
        if self._result is None:
            return []
        module = self.module_filter.currentText()
        kind = self.kind_filter.currentText()
        return [
            d for d in self._result.dtcs if (module == ALL or d.module == module) and (kind == ALL or d.kind == kind)
        ]

    def _fill_table(self) -> None:
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for dtc in self.visible_dtcs():
            row = self.table.rowCount()
            self.table.insertRow(row)
            color = KIND_COLORS.get(dtc.kind, theme.TEXT)
            highlight = theme.ACCENT if dtc.code.startswith("C16") else color
            status = f"0x{dtc.status:02X}" if dtc.status is not None else "--"
            self.table.setItem(row, 0, table_item(dtc.module, mono=True))
            self.table.setItem(row, 1, table_item(dtc.code, mono=True, color=highlight))
            self.table.setItem(row, 2, table_item(dtc_description(dtc, self.ctx.backend.describe) or "Unknown code"))
            self.table.setItem(row, 3, table_item(dtc.kind, color=color))
            self.table.setItem(row, 4, table_item(status, mono=True))
        self.table.setSortingEnabled(True)
        self.table.resizeColumnToContents(0)
        self.table.resizeColumnToContents(1)

    def _fill_freeze(self, readings: list[Reading]) -> None:
        self.freeze_table.setRowCount(0)
        for reading in readings:
            row = self.freeze_table.rowCount()
            self.freeze_table.insertRow(row)
            name = self.ctx.display_name(freeze_frame_key(reading.key))
            value = reading.error or format_value(reading.value)
            self.freeze_table.setItem(row, 0, table_item(name))
            self.freeze_table.setItem(row, 1, table_item(value, mono=True, align_right=True))
            self.freeze_table.setItem(row, 2, table_item(reading.unit, color=theme.TEXT_DIM))
        self.freeze_empty.setVisible(not readings)

    def _on_row_selected(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        module = self.table.item(rows[0].row(), 0).text()
        index = self.clear_target.findData(module)
        if index >= 0:
            self.clear_target.setCurrentIndex(index)

    # Clearing -----------------------------------------------------------
    def _module_for(self, name: str) -> ModuleAddress | None:
        return next((m for m in self.ctx.all_modules() if m.name == name), None)

    def _on_clear_clicked(self) -> None:
        name = self.clear_target.currentData() or OBD_TARGET
        if not self._confirm_clear(name):
            return
        if name == OBD_TARGET:
            self.ctx.session.clear_dtcs(None)
            return
        module = self._module_for(name)
        if module is None:
            self.ctx.notify(f"Unknown module {name}; cannot clear", "error")
            return
        self.ctx.session.clear_dtcs(module)

    def _confirm_clear(self, name: str) -> bool:
        extra = (
            " Readiness monitors also reset to incomplete, so an emissions inspection will fail "
            "until the drive cycles complete."
            if name == OBD_TARGET
            else ""
        )
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(f"Clear codes on {name}")
        box.setText(f"Clear all trouble codes on {name}?")
        box.setInformativeText(
            f"Freeze frame data for {name} will be lost and cannot be recovered.{extra} "
            "A fault that is still present will set its code again."
        )
        box.setStandardButtons(QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        return box.exec() == QMessageBox.StandardButton.Yes

    def _on_cleared(self, name: str) -> None:
        self.ctx.notify(f"Cleared codes on {name}. Rescanning.", "ok")
        self.ctx.session.scan_all_dtcs()

    def _scan_all(self) -> None:
        self.ctx.session.scan_all_dtcs()

    def _on_connection(self, _connected: bool, _info) -> None:
        self._update_buttons()

    def _on_busy(self, busy: bool, _label: str) -> None:
        self._busy = busy
        self._update_buttons()

    def _update_buttons(self) -> None:
        enabled = self.ctx.session.connected and not self._busy
        self.scan_button.setEnabled(enabled)
        self.clear_button.setEnabled(enabled)
