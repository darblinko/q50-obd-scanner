"""Vehicle profiles: everything that differs between the supported cars.

The Backend reads the active profile for module tables, the diagnostic
protocol, parameters, broadcast decoding, discovery probes and DTC names.
Pages read it for vehicle-specific text and defaults."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import ModuleType

from q50diag.core.types import CanFrame, ModuleAddress, ParameterDef, Value


# eq=False: profiles are singletons compared by identity, and their dict fields are unhashable.
@dataclass(frozen=True, eq=False)
class VehicleProfile:
    key: str
    name: str  # "2017 Infiniti Q50 3.0t"
    short_name: str  # top bar label
    make: str
    year: int
    modules: tuple[ModuleAddress, ...]
    obd_module: str  # module whose request id answers Mode 01: "ECM" or "PCM"
    diag: ModuleType  # request, ensure_session, reset_sessions, read_identification, read/clear_module_dtcs
    module_parameters: Callable[[ModuleAddress], list[ParameterDef]]
    broadcast_signals: Mapping[str, tuple[str, str]]
    decode_frame: Callable[[CanFrame], dict[str, Value]]
    discovery_probes: tuple[bytes, ...]
    sweep_protocol: str  # protocol given to modules found by the id sweep
    response_offsets: tuple[int, ...]  # response id = request id + offset, tried in order
    explorer_services: tuple[tuple[int, str, str], ...]  # (service, default start, default end)
    has_steering_page: bool
    default_favorites: tuple[str, ...]
    dtc_overlay: Mapping[str, str]  # make-specific DTC meanings over the SAE table
    sim_modules: frozenset[str]
    sim_vin: str  # synthetic: position 9 is a letter, so it fails the 49 CFR 565 check digit
    prompt_notes: str  # MechanIQ paragraph about the car
    gateway_note: str  # Bus Monitor note about broadcast traffic at the OBD port
    low_voltage_note: str  # Overview battery card text below 12.2 V
    aliases: Mapping[str, str] = field(default_factory=dict)  # alternative module names, upper case
    connect_note: str = ""  # one line under the Connect page vehicle selector

    def module(self, name: str) -> ModuleAddress | None:
        cleaned = name.strip().upper()
        normalized = self.aliases.get(cleaned, cleaned)
        return next((m for m in self.modules if m.name == normalized), None)

    def module_for_response_id(self, rx_id: int) -> ModuleAddress | None:
        return next((m for m in self.modules if m.rx_id == rx_id), None)

    def modules_on_bus(self, bus: str) -> tuple[ModuleAddress, ...]:
        return tuple(m for m in self.modules if m.on_bus(bus))


DEFAULT_VEHICLE_KEY = "q50_2017"


def _profiles() -> tuple[VehicleProfile, ...]:
    from q50diag.vehicles.mustang import MUSTANG_GT, MUSTANG_GT350R
    from q50diag.vehicles.q50 import Q50

    return (Q50, MUSTANG_GT350R, MUSTANG_GT)


VEHICLES: tuple[VehicleProfile, ...] = _profiles()


def vehicle_by_key(key: str) -> VehicleProfile:
    for vehicle in VEHICLES:
        if vehicle.key == key:
            return vehicle
    raise KeyError(f"Unknown vehicle: {key}")


def default_vehicle() -> VehicleProfile:
    return vehicle_by_key(DEFAULT_VEHICLE_KEY)
