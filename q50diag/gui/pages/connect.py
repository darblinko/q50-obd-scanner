"""Connect page: vehicle, adapter and CAN bus selection, serial port selection,
simulator entry, adapter details and a pre-flight checklist."""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QComboBox, QFrame, QGridLayout, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from q50diag.core.types import AdapterInfo, PortInfo
from q50diag.gui import theme
from q50diag.gui.backend import SIMULATOR
from q50diag.gui.context import AppContext
from q50diag.gui.pages.base import Page
from q50diag.gui.widgets.common import Card, button, hbox, label
from q50diag.transport.adapters import ADAPTERS, adapter_by_key
from q50diag.vehicles import VEHICLES, vehicle_by_key

log = logging.getLogger(__name__)

DEVICE_ROLE = Qt.ItemDataRole.UserRole + 1

WSL_HINT = (
    "No USB serial adapter found. Under WSL the USB adapter is not visible until it is attached "
    "from Windows with usbipd-win: run <span style='font-family:monospace'>usbipd list</span>, "
    "then <span style='font-family:monospace'>usbipd attach --wsl --busid &lt;id&gt;</span> "
    "in an administrator PowerShell, and press Refresh. Alternatively run q50diag with "
    "Windows Python, where the adapter appears as a COM port."
)
GENERIC_HINT = "No USB serial adapter found. Check the USB cable and that the adapter driver is installed."

CHECKLIST = (
    ("Ignition ON", "Ignition ON (engine off) is enough to read codes and identification."),
    ("Engine running", "Run the engine for live engine data and for steering work, which needs charging voltage."),
    ("Adapter firmware", "Update the adapter firmware with the vendor tool before first use."),
    ("CAN bus", "HS-CAN (pins 6/14) carries OBD-II and most modules. Ford MS-CAN (pins 3/11) needs an "
                "adapter and bus that support it."),
    ("One tool at a time", "Close other OBD apps that may hold the port."),
)


class ConnectPage(Page):
    title = "Connect"
    subtitle = "Choose the vehicle, adapter and CAN bus, then a serial port or the simulator, and connect."

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(ctx, parent)
        self._connected = False
        self._busy = False
        self._build_ui()
        session = ctx.session
        session.connection_changed.connect(self._on_connection_changed)
        session.busy_changed.connect(self._on_busy)
        session.voltage_changed.connect(self._on_voltage)
        ctx.vehicle_changed.connect(self._sync_selection)
        ctx.adapter_changed.connect(self._sync_selection)
        self._connected = session.connected
        self._sync_selection()
        self._show_adapter(session.adapter)
        self.refresh_ports()

    # UI -----------------------------------------------------------------
    def _build_ui(self) -> None:
        columns = hbox(spacing=16)
        columns.addWidget(self._build_port_card(), 3)
        right = QVBoxLayout()
        right.setSpacing(16)
        right.addWidget(self._build_selection_card())
        right.addWidget(self._build_adapter_card())
        right.addWidget(self._build_checklist_card())
        right.addStretch(1)
        columns.addLayout(right, 2)
        self.root.addLayout(columns, 1)

    def _build_port_card(self) -> Card:
        card = Card("Serial ports")
        self.port_list = QListWidget()
        self.port_list.setStyleSheet(f'QListWidget {{ font-family: "{theme.mono_family()}"; }}')
        self.port_list.setSpacing(2)
        self.port_list.itemSelectionChanged.connect(self._update_buttons)
        self.port_list.itemDoubleClicked.connect(self._on_item_activated)
        self.hint = QFrame()
        self.hint.setObjectName("Hint")
        hint_layout = QVBoxLayout(self.hint)
        hint_layout.setContentsMargins(12, 10, 12, 10)
        self.hint_label = label("")
        self.hint_label.setWordWrap(True)
        self.hint_label.setTextFormat(Qt.TextFormat.RichText)
        hint_layout.addWidget(self.hint_label)
        self.hint.setVisible(False)
        self.refresh_button = button("Refresh", "ghost", "Scan for serial ports again")
        self.refresh_button.clicked.connect(self.refresh_ports)
        self.connect_button = button("Connect", "primary")
        self.connect_button.clicked.connect(self.connect_selected)
        self.disconnect_button = button("Disconnect", "danger")
        self.disconnect_button.clicked.connect(self.ctx.session.disconnect_port)
        card.body.addWidget(self.port_list, 1)
        card.body.addWidget(self.hint)
        card.body.addLayout(hbox(self.refresh_button, None, self.disconnect_button, self.connect_button))
        return card

    def _build_selection_card(self) -> Card:
        card = Card("Vehicle & adapter")
        self.vehicle_box = QComboBox()
        for vehicle in VEHICLES:
            self.vehicle_box.addItem(vehicle.name, vehicle.key)
        self.adapter_box = QComboBox()
        for adapter in ADAPTERS:
            self.adapter_box.addItem(adapter.name, adapter.key)
        self.bus_box = QComboBox()
        self.vehicle_box.currentIndexChanged.connect(self._on_vehicle_chosen)
        self.adapter_box.currentIndexChanged.connect(self._on_adapter_chosen)
        self.bus_box.currentIndexChanged.connect(self._on_bus_chosen)
        self.vehicle_note = label("", "faint")
        self.adapter_note = label("", "faint")
        self.lock_note = label("Disconnect to change the vehicle, adapter or bus.", "faint")
        for note in (self.vehicle_note, self.adapter_note, self.lock_note):
            note.setWordWrap(True)
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(6)
        rows = (("Vehicle", self.vehicle_box), (None, self.vehicle_note), ("Adapter", self.adapter_box),
                ("CAN bus", self.bus_box), (None, self.adapter_note), (None, self.lock_note))
        for row, (title, widget) in enumerate(rows):
            if title:
                grid.addWidget(label(title, "dim"), row, 0)
            grid.addWidget(widget, row, 1)
        grid.setColumnStretch(1, 1)
        card.body.addLayout(grid)
        return card

    def _build_adapter_card(self) -> Card:
        card = Card("Adapter")
        self.state_label = label("Not connected", "value", "idle")
        card.body.addWidget(self.state_label)
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(6)
        self.detail_labels: dict[str, object] = {}
        for row, (key, title) in enumerate(
            (("port", "Port"), ("identity", "Identity"), ("description", "Description"),
             ("protocol", "Protocol"), ("voltage", "Voltage"))
        ):
            grid.addWidget(label(title, "dim"), row, 0)
            value = label("--", "mono")
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(value, row, 1)
            self.detail_labels[key] = value
        grid.setColumnStretch(1, 1)
        card.body.addLayout(grid)
        return card

    def _build_checklist_card(self) -> Card:
        card = Card("Before connecting")
        for heading, text in CHECKLIST:
            item = label(f"<b>{heading}</b><br><span style='color:{theme.TEXT_DIM}'>{text}</span>")
            item.setTextFormat(Qt.TextFormat.RichText)
            item.setWordWrap(True)
            card.body.addWidget(item)
        return card

    # Vehicle, adapter and bus ---------------------------------------------
    def _sync_selection(self, *_args) -> None:
        """Show the backend's current choices without re-triggering the handlers."""
        backend = self.ctx.backend
        adapter = adapter_by_key(backend.adapter_key)
        for box in (self.vehicle_box, self.adapter_box, self.bus_box):
            box.blockSignals(True)
        self.vehicle_box.setCurrentIndex(self.vehicle_box.findData(backend.vehicle.key))
        self.adapter_box.setCurrentIndex(self.adapter_box.findData(adapter.key))
        self.bus_box.clear()
        for bus in adapter.buses:
            self.bus_box.addItem(bus.label, bus.key)
        self.bus_box.setCurrentIndex(max(0, self.bus_box.findData(backend.bus)))
        for box in (self.vehicle_box, self.adapter_box, self.bus_box):
            box.blockSignals(False)
        self.vehicle_note.setText(backend.vehicle.connect_note)
        self.adapter_note.setText(adapter.notes)
        self._set_simulator_label()
        self._update_buttons()

    def _on_vehicle_chosen(self, _index: int) -> None:
        self.ctx.set_vehicle(vehicle_by_key(self.vehicle_box.currentData()))
        self._sync_selection()

    def _on_adapter_chosen(self, _index: int) -> None:
        adapter = adapter_by_key(self.adapter_box.currentData())
        keys = [bus.key for bus in adapter.buses]
        bus = self.ctx.backend.bus if self.ctx.backend.bus in keys else keys[0]
        self.ctx.set_adapter(adapter.key, bus)
        self._sync_selection()

    def _on_bus_chosen(self, _index: int) -> None:
        bus = self.bus_box.currentData()
        if bus:
            self.ctx.set_adapter(self.adapter_box.currentData(), bus)
            self._sync_selection()

    def _set_simulator_label(self) -> None:
        for row in range(self.port_list.count()):
            item = self.port_list.item(row)
            if item.data(DEVICE_ROLE) == SIMULATOR:
                item.setText(self._simulator_text())

    def _simulator_text(self) -> str:
        return f"SIMULATOR      Simulated {self.ctx.vehicle.short_name} (no car, no adapter)"

    # Ports --------------------------------------------------------------
    def refresh_ports(self) -> None:
        try:
            ports = self.ctx.backend.list_ports()
        except Exception as exc:  # noqa: BLE001; reported to the user
            log.exception("port listing failed")
            self.ctx.notify(f"Could not list serial ports: {exc}", "error")
            ports = []
        self._populate(ports)

    def _populate(self, ports: list[PortInfo]) -> None:
        self.port_list.clear()
        # Ports without a USB VID are legacy UARTs (WSL lists /dev/ttyS0..7);
        # they are listed last and dimmed because the supported adapters are USB.
        usb = sorted((p for p in ports if p.vid is not None), key=lambda p: (not p.is_likely_vlinker, p.device))
        legacy = sorted((p for p in ports if p.vid is None), key=lambda p: p.device)
        for port in usb:
            self.port_list.addItem(self._port_item(port))
        simulator = QListWidgetItem(self._simulator_text())
        simulator.setData(DEVICE_ROLE, SIMULATOR)
        self.port_list.addItem(simulator)
        for port in legacy:
            item = self._port_item(port)
            item.setForeground(QColor(theme.TEXT_FAINT))
            item.setToolTip("Not a USB device; the adapter is not on this port")
            self.port_list.addItem(item)
        self.port_list.setCurrentRow(0)
        self._show_hint(not usb)
        self._update_buttons()

    @staticmethod
    def _port_item(port: PortInfo) -> QListWidgetItem:
        ids = ""
        if port.vid is not None:
            pid = f"{port.pid:04X}" if port.pid is not None else "----"
            ids = f"  [{port.vid:04X}:{pid}]"
        tag = "  likely vLinker" if port.is_likely_vlinker else ""
        description = port.description if port.description not in ("", "n/a") else "serial port"
        item = QListWidgetItem(f"{port.device:<14} {description}{ids}{tag}")
        item.setData(DEVICE_ROLE, port.device)
        if port.is_likely_vlinker:
            item.setForeground(QColor(theme.ACCENT))
            item.setToolTip("FTDI or vLinker descriptor: probably a USB OBD adapter")
        return item

    def _show_hint(self, no_ports: bool) -> None:
        if not no_ports:
            self.hint.setVisible(False)
            return
        self.hint_label.setText(WSL_HINT if sys.platform.startswith("linux") else GENERIC_HINT)
        self.hint.setVisible(True)

    def selected_device(self) -> str | None:
        item = self.port_list.currentItem()
        return item.data(DEVICE_ROLE) if item else None

    def _on_item_activated(self, _item: QListWidgetItem) -> None:
        self.connect_selected()

    def connect_selected(self) -> None:
        device = self.selected_device()
        if not device:
            self.ctx.notify("Select a port or the simulator first", "warn")
            return
        self.ctx.session.connect_port(device)

    # Session state ------------------------------------------------------
    def _on_connection_changed(self, connected: bool, info: AdapterInfo | None) -> None:
        self._connected = connected
        self._show_adapter(info if connected else None)
        self._update_buttons()

    def _on_busy(self, busy: bool, _label: str) -> None:
        self._busy = busy
        self._update_buttons()

    def _on_voltage(self, value) -> None:
        if self._connected:
            self.detail_labels["voltage"].setText(f"{value:.2f} V" if value is not None else "--")

    def _show_adapter(self, info: AdapterInfo | None) -> None:
        if info is None:
            self.state_label.setText("Not connected")
            theme.set_state(self.state_label, "idle")
            for value in self.detail_labels.values():
                value.setText("--")
            return
        self.state_label.setText("Connected")
        theme.set_state(self.state_label, "ok")
        port = "Simulator" if info.port in (SIMULATOR, "SIM") else info.port
        self.detail_labels["port"].setText(port)
        self.detail_labels["identity"].setText(info.identity or "--")
        self.detail_labels["description"].setText(info.description or "--")
        self.detail_labels["protocol"].setText(info.protocol or "--")
        voltage = f"{info.voltage:.2f} V" if info.voltage is not None else "--"
        self.detail_labels["voltage"].setText(voltage)

    def _update_buttons(self) -> None:
        has_selection = self.selected_device() is not None
        self.connect_button.setEnabled(has_selection and not self._busy)
        self.connect_button.setText("Reconnect" if self._connected else "Connect")
        self.disconnect_button.setEnabled(self._connected)
        unlocked = not self._connected and not self._busy
        for box in (self.vehicle_box, self.adapter_box, self.bus_box):
            box.setEnabled(unlocked)
        self.lock_note.setVisible(not unlocked)

    def on_shown(self) -> None:
        if not self._connected:
            self.refresh_ports()

