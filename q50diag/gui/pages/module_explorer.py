"""Identifier explorer for one module: scan a 0x21 or 0x22 range, then watch
the identifiers that answered and highlight the bytes that change.

Watching uses session polling under the "explorer" owner with ad-hoc
parameters whose decode returns the payload as hex text. Byte positions are
counted in the payload after the service echo and identifier bytes, the same
layout scan_identifiers returns and ParameterDef.decode receives."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QLineEdit,
    QSpinBox,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from q50diag.core.types import ModuleAddress, ParameterDef, Reading
from q50diag.gui.catalog import CatalogEntry
from q50diag.gui.context import AppContext
from q50diag.gui.widgets.common import button, hbox, hex_bytes, label, make_table, table_item
from q50diag.gui.widgets.hex_delegate import CHANGED_ROLE, HexBytesDelegate
from q50diag.session import IdentifierScan

OWNER = "explorer"
SERVICE_LABELS = {0x21: "0x21  local id", 0x22: "0x22  data id"}
ID_COL, LEN_COL, CHANGES_COL, BYTES_COL = range(4)
HINT = (
    "Scan a range, then press Watch and turn the steering wheel. Bytes that change are "
    "highlighted; bytes that track the wheel are candidates for EPS motor current or "
    "torque. Pin a byte to chart it on the Steering page."
)
GENERIC_HINT = (
    "Scan a range of identifiers, then press Watch to re-read the ones that answered. "
    "Bytes that change between reads are highlighted."
)


def id_width(service: int) -> int:
    """Identifier width in bytes: one for 0x21 local ids, two for 0x22."""
    return 1 if service == 0x21 else 2


def request_for(service: int, ident: int) -> bytes:
    if service == 0x21:
        return bytes([0x21, ident & 0xFF])
    return bytes([0x22, (ident >> 8) & 0xFF, ident & 0xFF])


def format_id(service: int, ident: int) -> str:
    return f"{ident:0{id_width(service) * 2}X}"


def _hex_payload(payload: bytes) -> str:
    return payload.hex(" ").upper()


def watch_param(module: ModuleAddress, service: int, ident: int) -> ParameterDef:
    return ParameterDef(
        key=f"explore.{module.name}.{service:02X}.{format_id(service, ident)}",
        name=f"{module.name} {service:02X} {format_id(service, ident)}",
        unit="",
        module=module.name,
        category="Explorer",
        request=request_for(service, ident),
        decode=_hex_payload,
    )


def pinned_byte_entry(module: ModuleAddress, service: int, ident: int, index: int) -> CatalogEntry:
    def decode(payload: bytes, position: int = index):
        return payload[position] if len(payload) > position else None

    ident_text = format_id(service, ident)
    param = ParameterDef(
        key=f"pin.{module.name}.{service:02X}.{ident:X}.b{index}",
        name=f"{module.name} {service:02X} {ident_text} byte {index}",
        unit="raw",
        module=module.name,
        category="Pinned",
        request=request_for(service, ident),
        decode=decode,
        min_value=0,
        max_value=255,
        description=f"Byte {index} of {module.name} service {service:02X} id {ident_text}, pinned from the explorer",
    )
    return CatalogEntry(module, param)


def parse_hex_id(text: str, service: int) -> int | None:
    cleaned = text.strip().upper().removeprefix("0X")
    try:
        value = int(cleaned, 16)
    except ValueError:
        return None
    return value if 0 <= value < (1 << (8 * id_width(service))) else None


class ExplorerTab(QWidget):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.module: ModuleAddress | None = None
        self.service = 0x21
        self.results: dict[int, bytes] = {}
        self._reset_tracking()
        self._items: dict[int, tuple[QTableWidgetItem, ...]] = {}
        self._watch_keys: dict[str, int] = {}
        self._build_ui()
        ctx.session.identifiers_scanned.connect(self._on_scanned)
        ctx.session.readings.connect(self._on_readings)
        ctx.session.connection_changed.connect(self._on_connection)
        ctx.vehicle_changed.connect(self._apply_vehicle)
        self._apply_vehicle(ctx.vehicle)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        hint = self.hint = label(HINT, "dim")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addLayout(self._build_scan_row())
        self.table = make_table(["ID", "Len", "Changes", "Bytes"], stretch_column=BYTES_COL)
        self.table.setItemDelegateForColumn(BYTES_COL, HexBytesDelegate(self.table))
        self.table.setColumnWidth(ID_COL, 90)
        self.table.setColumnWidth(LEN_COL, 60)
        self.table.setColumnWidth(CHANGES_COL, 90)
        layout.addWidget(self.table, 1)
        layout.addLayout(self._build_pin_row())

    def _build_scan_row(self):
        self.service_box = QComboBox()
        self.service_box.currentIndexChanged.connect(self._on_service_changed)
        self.start_edit = self._hex_edit("00")
        self.end_edit = self._hex_edit("FF")
        self.scan_button = button("Scan", "primary", "Read every identifier in the range")
        self.scan_button.clicked.connect(self.start_scan)
        self.watch_button = button("Watch", None, "Re-read the found identifiers repeatedly")
        self.watch_button.setCheckable(True)
        self.watch_button.toggled.connect(self._on_watch_toggled)
        self.reset_button = button("Reset changes", "ghost", "Clear the change highlighting and counts")
        self.reset_button.clicked.connect(self.reset_changes)
        self.status_label = label("No scan yet", "faint")
        return hbox(
            label("Service", "dim"),
            self.service_box,
            label("From", "dim"),
            self.start_edit,
            label("To", "dim"),
            self.end_edit,
            self.scan_button,
            self.watch_button,
            self.reset_button,
            None,
            self.status_label,
        )

    def _build_pin_row(self):
        self.byte_spin = QSpinBox()
        self.byte_spin.setRange(0, 63)
        self.byte_spin.setProperty("role", "mono")
        self.pin_button = button("Pin byte", None, "Chart this byte of the selected identifier on the Steering page")
        self.pin_button.clicked.connect(self.pin_selected)
        self.pin_caption = label("Selected id, byte index", "dim")
        return hbox(self.pin_caption, self.byte_spin, self.pin_button, None)

    @staticmethod
    def _hex_edit(text: str) -> QLineEdit:
        edit = QLineEdit(text)
        edit.setProperty("role", "mono")
        edit.setFixedWidth(80)
        return edit

    # Module and service --------------------------------------------------
    def set_module(self, module: ModuleAddress | None) -> None:
        if module == self.module:
            return
        self.stop_watch()
        self.module = module
        self.results = {}
        self._reset_tracking()
        self._rebuild_table()
        self.status_label.setText("No scan yet")

    def _apply_vehicle(self, vehicle) -> None:
        """Services and default ranges come from the vehicle; pinning feeds the
        Steering page, so it is hidden where that page is unavailable."""
        self.set_module(None)
        self.stop_watch()
        self.service = vehicle.explorer_services[0][0]
        self._ranges = {service: (start, end) for service, start, end in vehicle.explorer_services}
        self.service_box.blockSignals(True)
        self.service_box.clear()
        for service in self._ranges:
            self.service_box.addItem(SERVICE_LABELS[service], service)
        self.service_box.blockSignals(False)
        self.service_box.setCurrentIndex(0)
        self._on_service_changed(0)
        for widget in (self.pin_caption, self.byte_spin, self.pin_button):
            widget.setVisible(vehicle.has_steering_page)
        self.hint.setText(HINT if vehicle.has_steering_page else GENERIC_HINT)

    def _on_service_changed(self, _index: int) -> None:
        service = self.service_box.currentData()
        if service is None:
            return
        start, end = self._ranges[service]
        self.start_edit.setText(start)
        self.end_edit.setText(end)

    # Scan ----------------------------------------------------------------
    def start_scan(self) -> None:
        if self.module is None:
            self.ctx.notify("Select a module first", "warn")
            return
        service = self.service_box.currentData()
        start = parse_hex_id(self.start_edit.text(), service)
        end = parse_hex_id(self.end_edit.text(), service)
        if start is None or end is None or start > end:
            limit = "FF" if service == 0x21 else "FFFF"
            self.ctx.notify(f"Invalid range: use hex ids from 00 to {limit}, start not above end", "warn")
            return
        self.stop_watch()
        self.status_label.setText(f"Scanning {format_id(service, start)}..{format_id(service, end)}")
        self.ctx.session.scan_identifiers(self.module, service, start, end)

    def _on_scanned(self, scan: IdentifierScan) -> None:
        if self.module is None or scan.module.name != self.module.name:
            return
        self.service = scan.service
        self.results = dict(scan.results)
        self._reset_tracking()
        self._rebuild_table()
        state = "" if scan.complete else " (cancelled, partial)"
        self.status_label.setText(f"{len(self.results)} identifiers answered{state}")

    def _reset_tracking(self) -> None:
        self.ever_changed: dict[int, set[int]] = {}
        self.change_counts: dict[int, int] = {}
        self._last: dict[int, bytes] = {}

    def reset_changes(self) -> None:
        self.ever_changed = {}
        self.change_counts = {}
        for ident in self._items:
            self._update_row(ident)

    def _rebuild_table(self) -> None:
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self._items = {}
        for ident in sorted(self.results):
            row = self.table.rowCount()
            self.table.insertRow(row)
            items = (
                table_item(format_id(self.service, ident), mono=True),
                table_item("", mono=True, align_right=True),
                table_item("", mono=True, align_right=True),
                table_item("", mono=True),
            )
            items[ID_COL].setData(Qt.ItemDataRole.UserRole, ident)
            for column, item in enumerate(items):
                self.table.setItem(row, column, item)
            self._items[ident] = items
            self._update_row(ident)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(ID_COL, Qt.SortOrder.AscendingOrder)

    def _update_row(self, ident: int) -> None:
        items = self._items.get(ident)
        if items is None:
            return
        data = self._last.get(ident, self.results.get(ident, b""))
        items[LEN_COL].setData(Qt.ItemDataRole.DisplayRole, len(data))
        items[CHANGES_COL].setData(Qt.ItemDataRole.DisplayRole, self.change_counts.get(ident, 0))
        items[BYTES_COL].setText(hex_bytes(data))
        items[BYTES_COL].setData(CHANGED_ROLE, sorted(self.ever_changed.get(ident, set())))

    # Watch ---------------------------------------------------------------
    def _on_watch_toggled(self, checked: bool) -> None:
        if checked:
            self.start_watch()
        else:
            self.stop_watch()

    def start_watch(self) -> None:
        if self.module is None or not self.results:
            self.ctx.notify("Scan first: Watch re-reads the identifiers that answered", "warn")
            self._set_watch_button(False)
            return
        params = [watch_param(self.module, self.service, ident) for ident in sorted(self.results)]
        self._watch_keys = {p.key: ident for p, ident in zip(params, sorted(self.results))}
        self.ctx.session.start_polling(OWNER, [(self.module, p) for p in params])
        self._set_watch_button(True)
        self.status_label.setText(f"Watching {len(params)} identifiers")

    def stop_watch(self) -> None:
        if self._watch_keys:
            self.ctx.session.stop_polling(OWNER)
            self._watch_keys = {}
            self.status_label.setText(f"{len(self.results)} identifiers answered")
        self._set_watch_button(False)

    @property
    def watching(self) -> bool:
        return bool(self._watch_keys)

    def _set_watch_button(self, checked: bool) -> None:
        self.watch_button.blockSignals(True)
        self.watch_button.setChecked(checked)
        self.watch_button.setText("Stop watch" if checked else "Watch")
        self.watch_button.blockSignals(False)

    def _on_readings(self, readings: list[Reading]) -> None:
        if not self._watch_keys:
            return
        for reading in readings:
            ident = self._watch_keys.get(reading.key)
            if ident is None or reading.error or not isinstance(reading.value, str):
                continue
            try:
                data = bytes.fromhex(reading.value)
            except ValueError:
                continue
            self._apply_snapshot(ident, data)

    def _apply_snapshot(self, ident: int, data: bytes) -> None:
        previous = self._last.get(ident)
        self._last[ident] = data
        if previous is not None:
            changed = self.ctx.backend.diff_snapshots({ident: previous}, {ident: data}).get(ident, [])
            if changed:
                self.ever_changed.setdefault(ident, set()).update(changed)
                self.change_counts[ident] = self.change_counts.get(ident, 0) + 1
        self._update_row(ident)

    def _on_connection(self, connected: bool, _info) -> None:
        if not connected:
            self._watch_keys = {}
            self._set_watch_button(False)

    # Pin -----------------------------------------------------------------
    def selected_ident(self) -> int | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, ID_COL)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def pin_selected(self) -> None:
        ident = self.selected_ident()
        if self.module is None or ident is None:
            self.ctx.notify("Select an identifier row to pin a byte from", "warn")
            return
        index = self.byte_spin.value()
        data = self._last.get(ident, self.results.get(ident, b""))
        if index >= len(data):
            self.ctx.notify(f"Identifier {format_id(self.service, ident)} has only {len(data)} bytes", "warn")
            return
        self.ctx.pin(pinned_byte_entry(self.module, self.service, ident, index))
