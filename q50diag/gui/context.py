"""Application context shared by the main window and every page.

Holds the session, recorder, series store, parameter catalog, event markers
and parameters pinned from the module explorer. Pages receive one AppContext
and talk to the car only through `ctx.session`."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

from q50diag.core.types import DiscoveredModule, Dtc, ModuleAddress, Reading
from q50diag.gui import catalog as catalog_module
from q50diag.gui.app_settings import AppSettings, save_app_settings
from q50diag.gui.backend import Backend
from q50diag.gui.store import SeriesStore
from q50diag.recorder import Recorder
from q50diag.session import DtcScanResult, ModuleReport, Session, VehicleOverview

if TYPE_CHECKING:
    from q50diag.gui.catalog import CatalogEntry
    from q50diag.vehicles import VehicleProfile

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Marker:
    timestamp: float
    label: str


class AppContext(QObject):
    toast = Signal(str, str)  # message, level: "info", "ok", "warn", "error"
    marker_added = Signal(object)  # Marker
    markers_cleared = Signal()
    catalog_changed = Signal()
    pinned_changed = Signal()
    recording_changed = Signal(bool)
    module_state_changed = Signal()  # responding map or discovered list changed
    navigate = Signal(str)  # page title to show
    vehicle_changed = Signal(object)  # VehicleProfile
    adapter_changed = Signal(str, str)  # adapter key, bus key

    def __init__(
        self,
        session: Session,
        backend: Backend | None = None,
        recorder: Recorder | None = None,
        parent: QObject | None = None,
        settings_path: Path | None = None,
    ) -> None:
        """`settings_path` is where vehicle and adapter choices are saved; None saves nothing."""
        super().__init__(parent)
        self.session = session
        self.backend = backend or session.backend
        self.settings_path = settings_path
        self.recorder = recorder or Recorder()
        self.store = SeriesStore()
        self.catalog: list[CatalogEntry] = []
        self.markers: list[Marker] = []
        self.pinned: list[CatalogEntry] = []
        self.overview: VehicleOverview | None = None
        self.dtc_result: DtcScanResult | None = None
        self.discovered: list[DiscoveredModule] = []
        self.module_responding: dict[str, bool] = {}
        self.module_reports: dict[str, ModuleReport] = {}
        self._catalog_index: dict[str, CatalogEntry] = {}
        self._wire_session()
        self.rebuild_catalog()

    def _wire_session(self) -> None:
        s = self.session
        s.readings.connect(self._on_readings)
        s.overview_ready.connect(self._on_overview)
        s.modules_discovered.connect(self._on_discovered)
        s.module_read.connect(self._on_module_read)
        s.dtcs_ready.connect(self._on_dtcs)
        s.error.connect(lambda text: self.notify(text, "error"))
        s.connection_changed.connect(self._on_connection)

    # Catalog -----------------------------------------------------------
    def rebuild_catalog(self) -> None:
        pids = self.overview.supported_pids if self.overview else None
        extra = [d.address for d in self.discovered]
        try:
            self.catalog = catalog_module.build_catalog(self.backend, pids, extra)
        except Exception as exc:  # noqa: BLE001; a broken backend must not stop the GUI
            log.exception("catalog build failed")
            self.notify(f"Parameter catalog unavailable: {exc}", "error")
            self.catalog = []
        self._catalog_index = {entry.key: entry for entry in self.catalog}
        for entry in self.pinned:
            self._catalog_index.setdefault(entry.key, entry)
        self.catalog_changed.emit()

    def entry(self, key: str) -> CatalogEntry | None:
        return self._catalog_index.get(key) or catalog_module.find_by_key(self.pinned, key)

    def display_name(self, key: str) -> str:
        entry = self.entry(key)
        return entry.param.name if entry else key

    def all_modules(self) -> list[ModuleAddress]:
        """Known modules plus discovered ones not in the known list."""
        try:
            modules = list(self.backend.known_modules())
        except ImportError:
            modules = []
        seen = {(m.tx_id, m.rx_id) for m in modules}
        for found in self.discovered:
            key = (found.address.tx_id, found.address.rx_id)
            if key not in seen:
                modules.append(found.address)
                seen.add(key)
        return modules

    # Vehicle and adapter selection ---------------------------------------
    @property
    def vehicle(self) -> VehicleProfile:
        return self.backend.vehicle

    def set_vehicle(self, vehicle: VehicleProfile) -> bool:
        """Switch the active vehicle. Refused while connected."""
        if vehicle is self.backend.vehicle:
            return True
        if not self._can_change("vehicle"):
            return False
        self.backend.vehicle = vehicle
        self.pinned = []
        self._forget_vehicle_state()
        self.pinned_changed.emit()
        self.vehicle_changed.emit(vehicle)
        self._save_settings()
        return True

    def set_adapter(self, adapter_key: str, bus: str) -> bool:
        """Select the adapter profile and CAN bus used for the next connection."""
        if (adapter_key, bus) == (self.backend.adapter_key, self.backend.bus):
            return True
        if not self._can_change("adapter"):
            return False
        bus_changed = bus != self.backend.bus
        self.backend.adapter_key = adapter_key
        self.backend.bus = bus
        if bus_changed:
            self._forget_vehicle_state()
        self.adapter_changed.emit(adapter_key, bus)
        self._save_settings()
        return True

    def _can_change(self, what: str) -> bool:
        if self.session.connected:
            self.notify(f"Disconnect before changing the {what}", "warn")
            return False
        return True

    def _forget_vehicle_state(self) -> None:
        """Drop results that belong to the previous vehicle or bus."""
        self.session.discovered = []
        self.overview = None
        self.dtc_result = None
        self.discovered = []
        self.module_reports = {}
        self.module_responding = {}
        self.rebuild_catalog()
        self.module_state_changed.emit()

    def _save_settings(self) -> None:
        if self.settings_path is None:
            return
        settings = AppSettings(self.backend.vehicle.key, self.backend.adapter_key, self.backend.bus)
        try:
            save_app_settings(settings, self.settings_path)
        except OSError as exc:
            self.notify(f"Could not save settings: {exc}", "warn")

    # Pinned parameters (from the module explorer) ----------------------
    def pin(self, entry: CatalogEntry) -> None:
        if any(p.key == entry.key for p in self.pinned):
            return
        self.pinned = [*self.pinned, entry]
        self._catalog_index.setdefault(entry.key, entry)
        self.pinned_changed.emit()
        self.notify(f"Pinned {entry.param.name} to the Steering page", "ok")

    def unpin(self, key: str) -> None:
        self.pinned = [p for p in self.pinned if p.key != key]
        self.pinned_changed.emit()

    # Markers and recording -----------------------------------------------
    def mark_event(self, label: str = "Steering felt heavy") -> Marker:
        stamp = self.recorder.mark_event(label, time.time())
        marker = Marker(stamp, label)
        self.markers = [*self.markers, marker]
        self.marker_added.emit(marker)
        suffix = "" if self.recorder.is_recording else " (not recording: marker shown on charts only)"
        self.session.status.emit(f"Event marked: {label}{suffix}")
        return marker

    def start_recording(self, folder: Path | None = None) -> Path | None:
        try:
            path = self.recorder.start(folder)
        except OSError as exc:
            self.notify(f"Could not start recording: {exc}", "error")
            return None
        self.recording_changed.emit(True)
        self.notify(f"Recording to {path}", "ok")
        return path

    def stop_recording(self) -> None:
        path = self.recorder.stop()
        self.recording_changed.emit(False)
        if path:
            self.notify(f"Recording saved: {path.name}", "info")

    # Notifications -------------------------------------------------------
    def notify(self, text: str, level: str = "info") -> None:
        if level == "error":
            log.warning("%s", text)
        self.toast.emit(text, level)

    # Session signal handlers ---------------------------------------------
    def _on_readings(self, readings: list[Reading]) -> None:
        self.store.add_many(readings)
        if not self.recorder.is_recording:
            return
        for reading in readings:
            entry = self.entry(reading.key)
            name = entry.param.name if entry else reading.key
            module = entry.module.name if entry else ""
            try:
                self.recorder.write_reading(reading, name, module)
            except OSError as exc:
                self.stop_recording()
                self.notify(f"Recording stopped, write failed: {exc}", "error")
                return

    def _on_overview(self, overview: VehicleOverview) -> None:
        self.overview = overview
        self.rebuild_catalog()

    def _on_discovered(self, found: list[DiscoveredModule]) -> None:
        self.discovered = list(found)
        responding = {d.address.name for d in found}
        for module in self.all_modules():
            self.module_responding[module.name] = module.name in responding
        self.rebuild_catalog()
        self.module_state_changed.emit()

    def _on_module_read(self, report: ModuleReport) -> None:
        self.module_reports[report.module.name] = report
        self.module_responding[report.module.name] = report.responding
        self.module_state_changed.emit()

    def _on_dtcs(self, result: DtcScanResult) -> None:
        self.dtc_result = result
        for name, state in result.module_status.items():
            if name != "OBD-II":
                self.module_responding.setdefault(name, state == "ok")
        self.module_state_changed.emit()

    def _on_connection(self, connected: bool, _info) -> None:
        if not connected:
            self.module_responding.clear()
            self.module_state_changed.emit()

    def dtcs_for(self, module_name: str) -> list[Dtc]:
        if not self.dtc_result:
            return []
        return [d for d in self.dtc_result.dtcs if d.module == module_name]
