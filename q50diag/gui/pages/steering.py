"""Steering page: purpose-built view for the intermittent heavy steering.

Steering watch polls voltage, speed, RPM, the ABS steering angle and the EPS
21 01 record (supply voltage, angle, driver torque, assist load, assist limit
and temperature). It uses no broadcast frames: the 2017 Q50 OBD port is
gatewayed and showed none in testing. Only the EPS supply voltage and angle
match independent readings; the other EPS fields are provisional, inferred
from captures at rest and during wheel turns. The watch starts recording automatically.
Marked events get a summary of the 5 s before and after the mark.

The page is Q50-specific. For a vehicle without a steering profile it shows
an "not available for this vehicle" panel and its actions are hidden."""

from __future__ import annotations

import time
from dataclasses import dataclass

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFrame, QGridLayout, QSplitter, QVBoxLayout, QWidget

from q50diag.core.types import Dtc
from q50diag.gui import theme
from q50diag.gui.catalog import find_by_key, find_by_request, steering_entries
from q50diag.gui.context import AppContext, Marker
from q50diag.gui.pages.base import Page
from q50diag.gui.widgets.charts import StackedCharts
from q50diag.gui.widgets.common import BigReadout, Card, button, format_value, hbox, label, make_table, table_item
from q50diag.session import VOLTAGE_KEY

EPS_READOUT_KEYS = {
    "eps_supply": "EPS.21.01.supply",
    "eps_angle": "EPS.21.01.angle",
    "eps_torque": "EPS.21.01.torque",
    "eps_assist": "EPS.21.01.assist",
    "eps_limit": "EPS.21.01.b11",
}
SUMMARY_SPAN_S = 5.0
STALE_AFTER_S = 5.0

# Generic Nissan EPS codes, not Q50 confirmed (docs/research/q50-diagnostics.md, EPS follow-up).
EPS_CODE_NOTES = {
    "C1601": "Supply voltage out of range (below 8.5 V or above 18.5 V)",
    "C1604": "Torque sensor",
    "C1606": "EPS motor",
    "C1607": "EPS ECU internal",
    "C1608": "EPS ECU internal",
    "C1609": "Vehicle speed signal",
    "C160A": "Overheat protection active (not a fault)",
    "C1613": "Torque sensor calibration needed",
    "U1000": "CAN communication lost for about 2 s",
}
NOT_A_FAULT = {"C160A"}

# (readout id, title, unit, digits)
READOUTS = (
    ("voltage", "Battery voltage", "V", 2),
    ("speed", "Vehicle speed", "km/h", 0),
    ("rpm", "Engine RPM", "rpm", 0),
    ("angle_abs", "Steering angle (ABS)", "deg", 1),
    ("eps_supply", "EPS supply voltage", "V", 2),
    ("eps_angle", "Steering angle (EPS)", "deg", 1),
    ("eps_torque", "EPS driver torque (raw)", "", 0),
    ("eps_assist", "EPS assist load (raw)", "", 0),
    ("eps_limit", "EPS assist limit (raw, candidate)", "", 0),
    ("rate", "Watch poll rate", "reads/s", 1),
)
READOUT_COLUMNS = 5


def eps_note(code: str) -> str:
    return EPS_CODE_NOTES.get(code.split("-")[0].upper(), "")


def dtc_color(code: str) -> str | None:
    base = code.split("-")[0].upper()
    if base in NOT_A_FAULT:
        return theme.WARN
    if base.startswith("C16") or base == "U1000":
        return theme.FAULT
    return None


def voltage_state(volts: float) -> str:
    if volts < 11.5 or volts > 15.5:
        return "fault"
    if volts < 12.0:
        return "warn"
    return "ok"


@dataclass
class EventSummary:
    min_voltage: float | None
    peak_torque: float | None
    mean_speed: float | None


class SteeringPage(Page):
    title = "Steering"
    subtitle = (
        "Live steering, EPS and supply data for the intermittent heavy steering. "
        "Start the watch, drive, and mark each heavy moment."
    )

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(ctx, parent)
        self.keys: dict[str, str | None] = {}
        self.readouts: dict[str, BigReadout] = {}
        self.summaries: dict[float, EventSummary] = {}
        self._poll_rate = 0.0
        self._build_header_actions()
        self.content = QWidget()
        content_layout = QVBoxLayout(self.content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(16)
        content_layout.addWidget(self._build_hint())
        content_layout.addLayout(self._build_readouts())
        content_layout.addWidget(self._build_body(), 1)
        self.root.addWidget(self.content, 1)
        self.unavailable = self._build_unavailable()
        self.root.addWidget(self.unavailable, 1, Qt.AlignmentFlag.AlignTop)
        self._resolve_keys()
        self._wire()
        self._readout_timer = QTimer(self)
        self._readout_timer.setInterval(100)
        self._readout_timer.timeout.connect(self.refresh_readouts)
        self._readout_timer.start()
        self._event_timer = QTimer(self)
        self._event_timer.setInterval(1000)
        self._event_timer.timeout.connect(self.refresh_events)
        self._event_timer.start()
        for marker in ctx.markers:
            self._on_marker(marker)
        self.refresh_eps()
        ctx.vehicle_changed.connect(self._apply_vehicle)
        self._apply_vehicle(ctx.vehicle)

    # Layout --------------------------------------------------------------
    def _build_header_actions(self) -> None:
        self.read_eps_button = button("Read EPS", None, "Read EPS identification and trouble codes")
        self.read_eps_button.clicked.connect(self.read_eps)
        self.watch_button = button("Start steering watch", "primary", "Poll steering data and start recording")
        self.watch_button.setCheckable(True)
        self.watch_button.toggled.connect(self._on_watch_toggled)
        self.header.add_action(self.read_eps_button)
        self.header.add_action(self.watch_button)

    def _build_hint(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("Hint")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 10, 16, 10)
        text = label("Press SPACE when the steering feels heavy", "title")
        theme.set_state(text, "accent")
        detail = label("F9 also works. Each mark writes an EVENT row and drops a marker on every chart.", "dim")
        eps_detail = label(
            "EPS readouts decode the EPS 21 01 record. Supply voltage and angle match independent readings; "
            "torque, assist load and assist limit are provisional, inferred from captures at rest and during turns.",
            "faint",
        )
        eps_detail.setWordWrap(True)
        self.mark_button = button("Mark event", "primary", "Mark the moment the steering felt heavy (Space or F9)")
        self.mark_button.clicked.connect(lambda: self.ctx.mark_event())
        self.watch_state = label("Watch stopped", "faint")
        column = QVBoxLayout()
        column.setSpacing(2)
        column.addWidget(text)
        column.addWidget(detail)
        column.addWidget(eps_detail)
        layout.addLayout(hbox(column, None, self.watch_state, self.mark_button, spacing=16))
        return frame

    def _build_unavailable(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("Hint")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 12, 16, 12)
        heading = label("Not available for this vehicle", "title")
        theme.set_state(heading, "warn")
        self.unavailable_detail = label("", "dim")
        self.unavailable_detail.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(self.unavailable_detail)
        return frame

    def _apply_vehicle(self, vehicle) -> None:
        available = vehicle.has_steering_page
        if not available and self.watching:
            self.stop_watch()
        self.content.setVisible(available)
        self.unavailable.setVisible(not available)
        self.read_eps_button.setVisible(available)
        self.watch_button.setVisible(available)
        self.unavailable_detail.setText(
            f"The steering watch decodes the 2017 Q50 rack EPS record (Nissan local ID 21 01). No steering "
            f"profile exists for the {vehicle.name}. Power steering module identification and trouble codes "
            f"are on the Modules and Trouble Codes pages."
        )

    @property
    def available(self) -> bool:
        return self.ctx.vehicle.has_steering_page

    def _build_readouts(self) -> QGridLayout:
        grid = QGridLayout()
        grid.setSpacing(12)
        for index, (key, title, unit, _digits) in enumerate(READOUTS):
            readout = BigReadout(title, unit)
            readout.value_label.setStyleSheet("font-size: 22pt;")
            self.readouts[key] = readout
            grid.addWidget(readout, index // READOUT_COLUMNS, index % READOUT_COLUMNS)
        return grid

    def _build_body(self) -> QSplitter:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.charts = StackedCharts(self.ctx.store, self._meta)
        splitter.addWidget(self.charts)
        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(12, 0, 0, 0)
        side_layout.setSpacing(12)
        side_layout.addWidget(self._build_eps_card(), 2)
        side_layout.addWidget(self._build_events_card(), 3)
        splitter.addWidget(side)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([800, 480])
        return splitter

    def _build_eps_card(self) -> Card:
        card = Card("EPS module")
        self.eps_card = card
        self.eps_state = label("NOT CHECKED", "mono", "idle")
        self.eps_ids = label("", "faint")
        card.body.addLayout(hbox(self.eps_state, None, self.eps_ids))
        self.eps_table = make_table(["Code", "Kind", "Meaning"], stretch_column=2)
        self.eps_table.setColumnWidth(0, 80)
        self.eps_table.setColumnWidth(1, 70)
        self.eps_note = label("", "faint")
        self.eps_note.setWordWrap(True)
        self.eps_table.setMaximumHeight(150)  # leave room for the marked events table
        card.body.addWidget(self.eps_table, 1)
        card.body.addWidget(self.eps_note)
        return card

    def _build_events_card(self) -> Card:
        card = Card("Marked events (5 s before and after)")
        self.events_table = make_table(["Time", "Label", "Min V", "Peak torque", "Speed"], stretch_column=1)
        for column, width in ((0, 88), (2, 60), (3, 84), (4, 56)):
            self.events_table.setColumnWidth(column, width)
        card.body.addWidget(self.events_table, 1)
        return card

    # Wiring and key resolution -------------------------------------------
    def _wire(self) -> None:
        self.ctx.catalog_changed.connect(self._resolve_keys)
        self.ctx.pinned_changed.connect(self._update_chart_keys)
        self.ctx.marker_added.connect(self._on_marker)
        self.ctx.module_state_changed.connect(self.refresh_eps)
        self.ctx.session.dtcs_ready.connect(lambda _result: self.refresh_eps())
        self.ctx.session.poll_rate.connect(self._on_poll_rate)
        self.ctx.session.activity_changed.connect(self._sync_watch_button)

    def _key_by_request(self, module: str, request: bytes) -> str | None:
        entry = find_by_request(self.ctx.catalog, module, request)
        return entry.key if entry else None

    def _key_if_present(self, key: str) -> str | None:
        return key if find_by_key(self.ctx.catalog, key) else None

    def _resolve_keys(self) -> None:
        self.keys = {
            "voltage": self._key_if_present(VOLTAGE_KEY),
            "speed": self._key_by_request("ECM", b"\x01\x0d"),
            "rpm": self._key_by_request("ECM", b"\x01\x0c"),
            "angle_abs": self._key_by_request("ABS", b"\x22\x12\x08"),
        }
        self.keys.update({readout_id: self._key_if_present(key) for readout_id, key in EPS_READOUT_KEYS.items()})
        self._update_chart_keys()
        self.refresh_readouts()

    def _meta(self, key: str) -> tuple[str, str]:
        entry = self.ctx.entry(key)
        return (entry.param.name, entry.param.unit) if entry else (key, "")

    def chart_keys(self) -> list[str]:
        keys = [entry.key for entry in steering_entries(self.ctx.catalog)]
        keys += [entry.key for entry in self.ctx.pinned if entry.key not in keys]
        return keys

    def _update_chart_keys(self) -> None:
        self.charts.set_keys(self.chart_keys())
        self.charts.set_markers([(m.timestamp, m.label) for m in self.ctx.markers])

    # Steering watch ------------------------------------------------------
    def subscriptions(self):
        entries = steering_entries(self.ctx.catalog) + list(self.ctx.pinned)
        return [entry.subscription() for entry in entries]

    def _on_watch_toggled(self, checked: bool) -> None:
        if checked:
            self.start_watch()
        else:
            self.ctx.session.stop_steering_watch()
            self._set_watch_ui(False)

    def start_watch(self) -> None:
        if not self.available:
            self.ctx.notify("The steering watch is not available for this vehicle", "warn")
            self._set_watch_ui(False)
            return
        if not self.ctx.session.connected:
            self.ctx.notify("Connect to the adapter or the simulator first", "warn")
            self._set_watch_ui(False)
            return
        subs = self.subscriptions()
        if not subs:
            self.ctx.notify("No steering parameters available from the backend", "error")
            self._set_watch_ui(False)
            return
        self.ctx.session.start_steering_watch(subs)
        if not self.ctx.recorder.is_recording:
            self.ctx.start_recording()
        self._set_watch_ui(True)

    def stop_watch(self) -> None:
        self._set_watch_ui(False)
        self.ctx.session.stop_steering_watch()

    def _set_watch_ui(self, active: bool) -> None:
        self.watch_button.blockSignals(True)
        self.watch_button.setChecked(active)
        self.watch_button.blockSignals(False)
        self.watch_button.setText("Stop steering watch" if active else "Start steering watch")
        self.watch_state.setText("WATCH RUNNING" if active else "Watch stopped")
        theme.set_state(self.watch_state, "ok" if active else "idle")

    @property
    def watching(self) -> bool:
        return "steering" in self.ctx.session.polling_owners()

    def _sync_watch_button(self) -> None:
        if self.watch_button.isChecked() != self.watching:
            self._set_watch_ui(self.watching)

    def _on_poll_rate(self, rate: float) -> None:
        self._poll_rate = rate

    # Readouts ------------------------------------------------------------
    def refresh_readouts(self) -> None:
        now = time.time()
        for readout_id, _title, _unit, digits in READOUTS:
            if readout_id == "rate":
                state = "accent" if self.watching else "idle"
                self.readouts["rate"].set_value(f"{self._poll_rate:.1f}", state, "")
            else:
                self._refresh_value(readout_id, digits, now)

    def _refresh_value(self, readout_id: str, digits: int, now: float) -> None:
        readout = self.readouts[readout_id]
        key = self.keys.get(readout_id)
        if key is None:
            readout.set_value("n/a", "idle", "not available from the backend")
            return
        reading = self.ctx.store.latest(key)
        if reading is None:
            readout.set_value("--", "idle", "no data yet")
            return
        if reading.error:
            readout.set_value("ERR", "fault", reading.error[:60])
            return
        state = None
        if readout_id in ("voltage", "eps_supply") and isinstance(reading.value, (int, float)):
            state = voltage_state(float(reading.value))
        stats = self.ctx.store.stats(key)
        sub = ""
        if stats and stats.minimum is not None:
            sub = f"min {stats.minimum:.{digits}f}  max {stats.maximum:.{digits}f}"
        if now - reading.timestamp > STALE_AFTER_S:
            state, sub = "idle", f"stale, {now - reading.timestamp:.0f} s old"
        readout.set_value(format_value(reading.value, digits), state, sub)

    # EPS card ------------------------------------------------------------
    def _eps_module(self):
        try:
            return self.ctx.backend.module_by_name("EPS")
        except Exception:  # noqa: BLE001 (shown as unavailable on the card)
            return None

    def read_eps(self) -> None:
        module = self._eps_module()
        if module is None:
            self.ctx.notify("The backend defines no EPS module address", "error")
            return
        if not self.ctx.session.connected:
            self.ctx.notify("Connect to the adapter first", "warn")
            return
        self.ctx.session.read_module(module, include_parameters=False)

    def _eps_dtcs(self) -> tuple[list[Dtc], str]:
        report = self.ctx.module_reports.get("EPS")
        if report is not None and report.dtcs is not None:
            return report.dtcs, "codes from the last EPS read"
        if self.ctx.dtc_result is not None:
            return self.ctx.dtcs_for("EPS"), "codes from the last DTC scan"
        return [], "not read yet"

    def refresh_eps(self) -> None:
        module = self._eps_module()
        self.eps_ids.setText(f"{module.tx_id:03X} / {module.rx_id:03X}" if module else "no address")
        responding = self.ctx.module_responding.get("EPS")
        text, state = {True: ("RESPONDING", "ok"), False: ("NO RESPONSE", "fault")}.get(
            responding, ("NOT CHECKED", "idle")
        )
        self.eps_state.setText(text)
        theme.set_state(self.eps_state, state)
        dtcs, source = self._eps_dtcs()
        self.eps_table.setRowCount(0)
        for dtc in dtcs:
            self._add_eps_row(dtc)
        has_fault = any(dtc_color(d.code) == theme.FAULT for d in dtcs)
        self.eps_card.set_state("fault" if has_fault else state if responding is not None else None)
        self.eps_note.setText(f"{len(dtcs)} {source}. Code meanings are generic Nissan EPS, not Q50 confirmed.")

    def _add_eps_row(self, dtc: Dtc) -> None:
        row = self.eps_table.rowCount()
        self.eps_table.insertRow(row)
        meaning = eps_note(dtc.code) or dtc.description or self.ctx.backend.describe(dtc.code)
        color = dtc_color(dtc.code)
        self.eps_table.setItem(row, 0, table_item(dtc.code, mono=True, color=color))
        self.eps_table.setItem(row, 1, table_item(dtc.kind, color=theme.TEXT_DIM))
        item = table_item(meaning or "No description", color=color)
        item.setToolTip(meaning)
        self.eps_table.setItem(row, 2, item)

    # Events --------------------------------------------------------------
    def _on_marker(self, marker: Marker) -> None:
        self.charts.add_marker(marker.timestamp, marker.label)
        row = self.events_table.rowCount()
        self.events_table.insertRow(row)
        stamp = time.strftime("%H:%M:%S", time.localtime(marker.timestamp))
        when = table_item(stamp, mono=True)
        when.setData(Qt.ItemDataRole.UserRole, marker.timestamp)
        self.events_table.setItem(row, 0, when)
        label_item = table_item(f"#{row + 1}  {marker.label}")
        label_item.setToolTip(marker.label)
        self.events_table.setItem(row, 1, label_item)
        for column in (2, 3, 4):
            self.events_table.setItem(row, column, table_item("pending", mono=True, color=theme.TEXT_FAINT))
        self.events_table.scrollToBottom()
        self.refresh_events()

    def summarize(self, timestamp: float) -> EventSummary:
        start, end = timestamp - SUMMARY_SPAN_S, timestamp + SUMMARY_SPAN_S

        def window(readout_id: str) -> list[float]:
            key = self.keys.get(readout_id)
            return self.ctx.store.window(key, start, end) if key else []

        volts, torque, speed = window("voltage"), window("eps_torque"), window("speed")
        return EventSummary(
            min(volts) if volts else None,
            max(torque, key=abs) if torque else None,
            sum(speed) / len(speed) if speed else None,
        )

    def refresh_events(self, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        for row in range(self.events_table.rowCount()):
            stamp = self.events_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            if stamp in self.summaries or now < stamp + SUMMARY_SPAN_S:
                continue
            summary = self.summarize(stamp)
            self.summaries[stamp] = summary
            self._fill_event_row(row, summary)

    def _fill_event_row(self, row: int, summary: EventSummary) -> None:
        volt_color = None
        if summary.min_voltage is not None:
            volt_color = theme.state_color(voltage_state(summary.min_voltage))
        cells = (
            (summary.min_voltage, 2, volt_color),
            (summary.peak_torque, 0, None),
            (summary.mean_speed, 0, None),
        )
        for column, (value, digits, color) in zip((2, 3, 4), cells):
            text = "no data" if value is None else f"{value:.{digits}f}"
            self.events_table.setItem(row, column, table_item(text, mono=True, color=color or theme.TEXT))
