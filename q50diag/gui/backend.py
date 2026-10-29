"""Indirection over the transport and protocol packages.

The session and the pages call the backend only through this class so that
tests can substitute a fake. Protocol imports are lazy where they can be; the
vehicle profile is resolved at construction."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING

from q50diag.core.transport import Transport
from q50diag.core.types import (
    CanFrame,
    DiscoveredModule,
    Dtc,
    ModuleAddress,
    MonitorStatus,
    ParameterDef,
    PortInfo,
    Reading,
    Value,
)

if TYPE_CHECKING:
    from q50diag.vehicles import VehicleProfile

SIMULATOR = "SIMULATOR"
# A real adapter takes tens of ms per request. Without a comparable delay the
# poll loop floods the GUI thread with raw traffic signals and freezes it.
SIM_REQUEST_LATENCY_S = 0.02

ProgressFn = Callable[[int, int, str], None]


class Backend:
    """`vehicle` selects module tables, protocol and decoding; `adapter_key` and
    `bus` select the adapter profile and CAN bus for real ports."""

    def __init__(self, vehicle: VehicleProfile | None = None, adapter_key: str | None = None,
                 bus: str = "hs") -> None:
        from q50diag.transport.adapters import DEFAULT_ADAPTER_KEY
        from q50diag.vehicles import default_vehicle

        self.vehicle: VehicleProfile = vehicle or default_vehicle()
        self.adapter_key: str = adapter_key or DEFAULT_ADAPTER_KEY
        self.bus: str = bus

    # Transport -----------------------------------------------------------
    def list_ports(self) -> list[PortInfo]:
        from q50diag.transport.ports import list_ports

        return list_ports()

    def make_transport(self, port: str) -> Transport:
        if port == SIMULATOR:
            from q50diag.transport.sim import SimulatedTransport

            return SimulatedTransport(vehicle=self.vehicle, bus=self.bus, request_latency_s=SIM_REQUEST_LATENCY_S)
        from q50diag.transport.adapters import adapter_by_key
        from q50diag.transport.elm import ElmTransport

        return ElmTransport(port, adapter=adapter_by_key(self.adapter_key), bus=self.bus)

    # OBD-II --------------------------------------------------------------
    def mode01_pids(self) -> dict[int, ParameterDef]:
        from q50diag.protocol.obd2 import MODE01_PIDS

        return MODE01_PIDS

    def supported_pids(self, t: Transport) -> list[int]:
        from q50diag.protocol.obd2 import supported_pids

        return supported_pids(t)

    def read_monitor_status(self, t: Transport) -> MonitorStatus:
        from q50diag.protocol.obd2 import read_monitor_status

        return read_monitor_status(t)

    def read_dtcs(self, t: Transport) -> list[Dtc]:
        from q50diag.protocol.obd2 import read_dtcs

        return self._localize(read_dtcs(t))

    def clear_dtcs(self, t: Transport) -> None:
        from q50diag.protocol.obd2 import clear_dtcs

        clear_dtcs(t)

    def read_vin(self, t: Transport) -> str | None:
        from q50diag.protocol.obd2 import read_vin

        return read_vin(t)

    def read_freeze_frame(self, t: Transport) -> list[Reading]:
        from q50diag.protocol.obd2 import read_freeze_frame

        return read_freeze_frame(t)

    # Modules -------------------------------------------------------------
    def known_modules(self) -> tuple[ModuleAddress, ...]:
        return self.vehicle.modules

    def scan_modules(self) -> tuple[ModuleAddress, ...]:
        """Known modules on the selected bus: the ones scans and discovery address."""
        return tuple(m for m in self.known_modules() if m.bus == self.bus)

    def obd_module_name(self) -> str:
        return self.vehicle.obd_module

    def module_by_name(self, name: str) -> ModuleAddress | None:
        return self.vehicle.module(name)

    def module_for_response_id(self, rx_id: int) -> ModuleAddress | None:
        return self.vehicle.module_for_response_id(rx_id)

    # Manufacturer protocol (Nissan KWP or Ford UDS, from the vehicle) ----
    def ensure_session(self, t: Transport, m: ModuleAddress) -> None:
        self.vehicle.diag.ensure_session(t, m)

    def reset_sessions(self) -> None:
        self.vehicle.diag.reset_sessions()

    def read_identification(self, t: Transport, m: ModuleAddress) -> dict[str, str]:
        return self.vehicle.diag.read_identification(t, m)

    def read_module_dtcs(self, t: Transport, m: ModuleAddress) -> list[Dtc]:
        return self._localize(self.vehicle.diag.read_module_dtcs(t, m))

    def clear_module_dtcs(self, t: Transport, m: ModuleAddress) -> None:
        self.vehicle.diag.clear_module_dtcs(t, m)

    def module_parameters(self, m: ModuleAddress) -> list[ParameterDef]:
        return self.vehicle.module_parameters(m)

    def read_parameter(self, t: Transport, m: ModuleAddress, p: ParameterDef) -> Reading:
        from q50diag.protocol.reader import read_parameter

        return read_parameter(t, m, p, self.vehicle.diag.ensure_session)

    def decode_reply(self, p: ParameterDef, raw: bytes) -> Reading:
        from q50diag.protocol.reader import decode_reply

        return decode_reply(p, raw)

    # Discovery and exploration -------------------------------------------
    def discover_modules(
        self, t: Transport, progress: ProgressFn | None, stop: threading.Event | None
    ) -> list[DiscoveredModule]:
        from q50diag.protocol.discovery import discover_modules

        vehicle = self.vehicle
        on_bus = self.scan_modules()
        return discover_modules(
            t, progress, stop, modules=on_bus, probes=vehicle.discovery_probes,
            sweep_protocol=vehicle.sweep_protocol, response_offsets=vehicle.response_offsets,
            reserved=tuple(m for m in self.known_modules() if m not in on_bus),
        )

    def scan_identifiers(
        self,
        t: Transport,
        m: ModuleAddress,
        service: int,
        start: int,
        end: int,
        progress: ProgressFn | None = None,
        stop: threading.Event | None = None,
    ) -> dict[int, bytes]:
        from q50diag.protocol.explorer import scan_identifiers

        return scan_identifiers(t, m, service, start, end, progress=progress, stop=stop,
                                session=self.vehicle.diag.ensure_session)

    def diff_snapshots(self, a: dict[int, bytes], b: dict[int, bytes]) -> dict[int, list[int]]:
        from q50diag.protocol.explorer import diff_snapshots

        return diff_snapshots(a, b)

    # Broadcast -----------------------------------------------------------
    def decode_frame(self, frame: CanFrame) -> dict[str, Value]:
        return self.vehicle.decode_frame(frame)

    def broadcast_signals(self) -> dict[str, tuple[str, str]]:
        return self.vehicle.broadcast_signals

    # Data ----------------------------------------------------------------
    def describe(self, code: str) -> str:
        from q50diag.data.dtc_descriptions import describe_with

        return describe_with(code, self.vehicle.dtc_overlay)

    def _localize(self, dtcs: list[Dtc]) -> list[Dtc]:
        """Vehicle DTC names and module naming: generic decoders label OBD-II
        codes "ECM" and use the Nissan table."""
        engine = self.obd_module_name()
        return [replace(d, module=engine if d.module == "ECM" else d.module, description=self.describe(d.code))
                for d in dtcs]
