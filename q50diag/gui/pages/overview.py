"""Overview page: vehicle summary cards and the full scan (discovery, all DTCs,
identification)."""

from __future__ import annotations

from collections import Counter

from PySide6.QtWidgets import QGridLayout, QWidget

from q50diag.core.types import AdapterInfo, MonitorStatus
from q50diag.gui import theme
from q50diag.gui.context import AppContext
from q50diag.gui.pages.base import Page
from q50diag.gui.widgets.common import Card, StatCard, button, make_table, table_item
from q50diag.gui.widgets.task_strip import TaskStrip
from q50diag.session import DtcScanResult, VehicleOverview

MONITOR_COLORS = {"complete": theme.OK, "incomplete": theme.WARN}


def voltage_state(volts: float | None) -> str | None:
    """Battery voltage band: under 11.8 V fault, under 12.2 V or over 15.0 V warn."""
    if volts is None:
        return None
    if volts < 11.8:
        return "fault"
    if volts < 12.2 or volts > 15.0:
        return "warn"
    return "ok"


LOW_VOLTAGE_NOTE = "Low: EPS assist may be limited below about 9 to 10 V"


def voltage_detail(volts: float | None, low_note: str = LOW_VOLTAGE_NOTE) -> str:
    if volts is None:
        return "Adapter did not report voltage"
    if volts >= 13.2:
        return "Charging (engine running)"
    if volts >= 12.2:
        return "Battery at rest"
    return low_note


class OverviewPage(Page):
    title = "Overview"
    subtitle = "Vehicle summary. Full scan runs module discovery, every DTC read and identification."

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(ctx, parent)
        self._busy = False
        self._build_header_actions()
        self.task_strip = TaskStrip(ctx.session, always_visible=True)
        self.root.addWidget(self.task_strip)
        self._build_cards()
        self._wire()
        self._refresh_all()

    def _build_header_actions(self) -> None:
        self.overview_button = button("Read overview", "ghost", "Read VIN, monitor status and voltage")
        self.overview_button.clicked.connect(self.ctx.session.read_vehicle_overview)
        self.scan_button = button("Full scan", "primary", "Discovery, DTCs on every module, identification")
        self.scan_button.clicked.connect(self.ctx.session.full_scan)
        self.header.add_action(self.overview_button)
        self.header.add_action(self.scan_button)

    def _build_cards(self) -> None:
        grid = QGridLayout()
        grid.setSpacing(16)
        self.vin_card = StatCard("VIN", "--", "Read with mode 09")
        self.mil_card = StatCard("Check engine lamp", "--", "Mode 01 PID 01")
        self.voltage_card = StatCard("Battery voltage", "--", "ATRV at the OBD port")
        self.adapter_card = StatCard("Adapter", "--", "")
        self.modules_card = StatCard("Modules found", "--", "Run a full scan to discover modules")
        self.dtc_card = StatCard("Trouble codes", "--", "Run a full scan to read every module")
        for index, card in enumerate(
            (self.vin_card, self.mil_card, self.voltage_card,
             self.adapter_card, self.modules_card, self.dtc_card)
        ):
            grid.addWidget(card, index // 3, index % 3)
        self.monitor_table = make_table(["Monitor", "Status"], stretch_column=0)
        monitor_card = Card("Readiness monitors")
        monitor_card.body.addWidget(self.monitor_table)
        self.module_table = make_table(["Module", "Codes", "Status"], stretch_column=2)
        module_card = Card("DTC count per module")
        module_card.body.addWidget(self.module_table)
        grid.addWidget(monitor_card, 2, 0)
        grid.addWidget(module_card, 2, 1, 1, 2)
        grid.setRowStretch(2, 1)
        for column in range(3):
            grid.setColumnStretch(column, 1)
        self.root.addLayout(grid, 1)

    def _wire(self) -> None:
        session = self.ctx.session
        session.overview_ready.connect(self._on_overview)
        session.dtcs_ready.connect(self._on_dtcs)
        session.voltage_changed.connect(self._on_voltage)
        session.connection_changed.connect(self._on_connection)
        session.busy_changed.connect(self._on_busy)
        self.ctx.module_state_changed.connect(self._show_modules)
        self.ctx.vehicle_changed.connect(lambda _vehicle: self.vin_card.set_value("--", "idle"))

    def _refresh_all(self) -> None:
        self._on_connection(self.ctx.session.connected, self.ctx.session.adapter)
        if self.ctx.overview:
            self._on_overview(self.ctx.overview)
        if self.ctx.dtc_result:
            self._on_dtcs(self.ctx.dtc_result)
        self._show_modules()

    def on_shown(self) -> None:
        self._refresh_all()

    # Handlers -----------------------------------------------------------
    def _on_overview(self, overview: VehicleOverview) -> None:
        self.vin_card.set_value(overview.vin or "not reported", "accent" if overview.vin else "idle")
        self._show_monitor(overview.monitor)
        self._on_voltage(overview.voltage)
        if overview.errors:
            self.vin_card.detail_label.setText(f"{len(overview.errors)} read errors: " + "; ".join(overview.errors[:2]))
        else:
            self.vin_card.detail_label.setText("Read with mode 09")

    def _show_monitor(self, monitor: MonitorStatus | None) -> None:
        self.monitor_table.setRowCount(0)
        if monitor is None:
            self.mil_card.set_value("--", "idle", "Monitor status not available")
            return
        state = "fault" if monitor.mil_on else "ok"
        detail = f"{monitor.dtc_count} emission DTCs reported by the {self.ctx.vehicle.obd_module}"
        self.mil_card.set_value("ON" if monitor.mil_on else "OFF", state, detail)
        for name, status in monitor.monitors.items():
            row = self.monitor_table.rowCount()
            self.monitor_table.insertRow(row)
            self.monitor_table.setItem(row, 0, table_item(name))
            color = MONITOR_COLORS.get(status, theme.TEXT_FAINT)
            self.monitor_table.setItem(row, 1, table_item(status, mono=True, color=color))

    def _on_voltage(self, volts) -> None:
        text = f"{volts:.2f} V" if volts is not None else "--"
        self.voltage_card.set_value(text, voltage_state(volts), voltage_detail(volts, self.ctx.vehicle.low_voltage_note))

    def _on_connection(self, connected: bool, info: AdapterInfo | None) -> None:
        if connected and info is not None:
            self.adapter_card.set_value(info.identity or "connected", "ok", info.protocol)
        else:
            self.adapter_card.set_value("offline", "idle", "Connect on the Connect page")
        self._update_buttons()

    def _on_busy(self, busy: bool, _label: str) -> None:
        self._busy = busy
        self._update_buttons()

    def _update_buttons(self) -> None:
        enabled = self.ctx.session.connected and not self._busy
        self.overview_button.setEnabled(enabled)
        self.scan_button.setEnabled(enabled)

    def _show_modules(self) -> None:
        responding = sorted(name for name, ok in self.ctx.module_responding.items() if ok)
        if not responding and not self.ctx.discovered:
            return
        names = ", ".join(responding) if responding else "none"
        state = "ok" if responding else "warn"
        self.modules_card.set_value(str(len(responding)), state, f"Responding: {names}")

    def _on_dtcs(self, result: DtcScanResult) -> None:
        counts = Counter(dtc.module for dtc in result.dtcs)
        total = len(result.dtcs)
        modules_with = len(counts)
        detail = f"{modules_with} modules with codes, {len(result.module_status)} scanned"
        if not result.complete:
            detail += " (scan cancelled, partial)"
        self.dtc_card.set_value(str(total), "fault" if total else "ok", detail)
        self._fill_module_table(result, counts)

    def _fill_module_table(self, result: DtcScanResult, counts: Counter) -> None:
        # Codes from modules without their own scan entry came from the OBD-II read.
        obd_count = sum(n for name, n in counts.items() if name not in result.module_status)
        self.module_table.setRowCount(0)
        for name in sorted(result.module_status):
            row = self.module_table.rowCount()
            self.module_table.insertRow(row)
            count = obd_count if name == "OBD-II" else counts.get(name, 0)
            status = result.module_status[name]
            count_color = theme.FAULT if count else theme.TEXT_DIM
            status_color = theme.OK if status == "ok" else theme.TEXT_FAINT
            self.module_table.setItem(row, 0, table_item(name, mono=True))
            self.module_table.setItem(row, 1, table_item(str(count), mono=True, color=count_color, align_right=True))
            self.module_table.setItem(row, 2, table_item(status, color=status_color))
