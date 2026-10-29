"""Persistent top status bar: selected vehicle, connection, adapter, protocol, voltage, VIN,
recording state with elapsed time, Record toggle and Mark event."""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from q50diag.core.types import AdapterInfo
from q50diag.gui import theme
from q50diag.gui.backend import SIMULATOR
from q50diag.gui.context import AppContext
from q50diag.gui.widgets.common import button, label
from q50diag.session import VehicleOverview

LOW_VOLTAGE_ENGINE_OFF = 12.2
LOW_VOLTAGE_FAULT = 11.5


def voltage_state(volts: float | None) -> str | None:
    if volts is None:
        return "idle"
    if volts < LOW_VOLTAGE_FAULT:
        return "fault"
    if volts < LOW_VOLTAGE_ENGINE_OFF:
        return "warn"
    return "ok"


def format_elapsed(seconds: float) -> str:
    whole = int(seconds)
    return f"{whole // 3600:02d}:{whole % 3600 // 60:02d}:{whole % 60:02d}"


class Field(QWidget):
    """Small caption above a monospace value."""

    def __init__(self, caption: str, value: str = "--", min_width: int = 0) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)
        layout.addWidget(label(caption.upper(), "section"))
        self.value = label(value, "mono")
        if min_width:
            self.value.setMinimumWidth(min_width)
        layout.addWidget(self.value)

    def set(self, text: str, state: str | None = None) -> None:
        self.value.setText(text)
        theme.set_state(self.value, state)


class TopBar(QWidget):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("TopBar")
        self._build()
        session = ctx.session
        session.connection_changed.connect(self._on_connection)
        session.voltage_changed.connect(self._on_voltage)
        session.overview_ready.connect(self._on_overview)
        ctx.recording_changed.connect(self._on_recording)
        ctx.vehicle_changed.connect(self._on_vehicle)
        self._clock = QTimer(self)
        self._clock.setInterval(500)
        self._clock.timeout.connect(self._tick)
        self._clock.start()
        self._on_connection(session.connected, session.adapter)
        if session.connected and ctx.overview is not None:
            self._on_overview(ctx.overview)
        self._on_recording(ctx.recorder.is_recording)

    def _build(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 8, 14, 8)
        layout.setSpacing(26)
        brand = label("Q50DIAG", "section")
        theme.set_state(brand, "accent")
        self.vehicle = Field("Vehicle", "--", 120)
        self._on_vehicle(self.ctx.vehicle)
        self.connection = Field("Link", "OFFLINE", 110)
        self.adapter = Field("Adapter", "--", 120)
        self.protocol = Field("Protocol", "--", 120)
        self.voltage = Field("Battery", "--", 70)
        self.vin = Field("VIN", "--", 160)
        self.recording = Field("Recording", "OFF", 90)
        for widget in (brand, self.vehicle, self.connection, self.adapter, self.protocol, self.voltage, self.vin):
            layout.addWidget(widget)
        layout.addStretch(1)
        layout.addWidget(self.recording)
        self.record_button = button("Record", None, "Start or stop the CSV session recording")
        self.record_button.setObjectName("RecordButton")
        self.record_button.setCheckable(True)
        self.record_button.toggled.connect(self._toggle_recording)
        self.mark_button = button("Mark event", "primary", "Write an EVENT row and mark every chart (Space or F9)")
        self.mark_button.clicked.connect(lambda: self.ctx.mark_event())
        layout.addWidget(self.record_button)
        layout.addWidget(self.mark_button)

    def _on_connection(self, connected: bool, info: AdapterInfo | None) -> None:
        if connected and info is not None:
            simulated = self.ctx.session.port == SIMULATOR
            self.connection.set("SIMULATOR" if simulated else "ONLINE", "accent" if simulated else "ok")
            self.connection.setToolTip(info.port)
            self.adapter.set(info.identity or "--")
            self.adapter.setToolTip(info.description)
            self.protocol.set(_short_protocol(info.protocol))
            self.protocol.setToolTip(info.protocol)
        else:
            self.connection.set("OFFLINE", "idle")
            self.adapter.set("--")
            self.protocol.set("--")
            self.vin.set("--")
            self._on_voltage(None)

    def _on_vehicle(self, vehicle) -> None:
        self.vehicle.set(vehicle.short_name)
        self.vehicle.setToolTip(vehicle.name)

    def _on_voltage(self, volts: float | None) -> None:
        text = f"{volts:.2f} V" if volts is not None else "--"
        self.voltage.set(text, voltage_state(volts) if volts is not None else None)

    def _on_overview(self, overview: VehicleOverview) -> None:
        self.vin.set(overview.vin or "not reported", None if overview.vin else "idle")

    def _toggle_recording(self, checked: bool) -> None:
        if checked == self.ctx.recorder.is_recording:
            return
        if checked:
            if self.ctx.start_recording() is None:
                self.record_button.setChecked(False)
        else:
            self.ctx.stop_recording()

    def _on_recording(self, recording: bool) -> None:
        self.record_button.blockSignals(True)
        self.record_button.setChecked(recording)
        self.record_button.setText("Stop recording" if recording else "Record")
        self.record_button.blockSignals(False)
        self._tick()

    def _tick(self) -> None:
        recorder = self.ctx.recorder
        if recorder.is_recording:
            self.recording.set(f"REC {format_elapsed(recorder.elapsed())}", "fault")
            self.recording.setToolTip(str(recorder.path))
        else:
            self.recording.set("OFF", "idle")


def _short_protocol(protocol: str) -> str:
    text = protocol.replace("ISO 15765-4", "").strip(" ()")
    return text or protocol or "--"
