"""Parameter catalog: every value the Live Data and Steering pages can show.

Built from pure backend data (no transport access): supported OBD-II mode 01
PIDs on the vehicle's engine module, the vehicle's module parameters for known
and discovered modules, the adapter voltage and the decoded broadcast signals."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from q50diag.core.types import ModuleAddress, ParameterDef
from q50diag.gui.backend import Backend
from q50diag.session import (
    ADAPTER_MODULE,
    BROADCAST_MODULE,
    VOLTAGE_PARAM,
    Subscription,
    broadcast_param,
)

log = logging.getLogger(__name__)

# Steering watch defaults, all polled. The 2017 Q50 OBD port is gatewayed and
# carried no broadcast frames in testing, so no broadcast keys are included.
STEERING_REQUESTS = (
    ("ECM", b"\x01\x0d"),  # vehicle speed
    ("ECM", b"\x01\x0c"),  # engine RPM
    ("ABS", b"\x22\x12\x08"),  # steering angle
)
# EPS 21 01 fields (q50diag.protocol.nissan.eps_fields); one request serves all of them.
STEERING_KEYS = (
    "EPS.21.01.supply",
    "EPS.21.01.angle",
    "EPS.21.01.torque",
    "EPS.21.01.assist",
    "EPS.21.01.b11",
    "EPS.21.01.b10",
)


@dataclass(frozen=True)
class CatalogEntry:
    module: ModuleAddress
    param: ParameterDef

    @property
    def key(self) -> str:
        return self.param.key

    @property
    def polled(self) -> bool:
        return self.module.name != BROADCAST_MODULE.name

    def subscription(self) -> Subscription:
        return (self.module, self.param)


def build_catalog(
    backend: Backend,
    supported_pids: list[int] | None,
    extra_modules: list[ModuleAddress] | None = None,
) -> list[CatalogEntry]:
    entries: list[CatalogEntry] = [CatalogEntry(ADAPTER_MODULE, VOLTAGE_PARAM)]
    entries += _obd_entries(backend, supported_pids)
    entries += _module_entries(backend, extra_modules or [])
    entries += _broadcast_entries(backend)
    unique: dict[str, CatalogEntry] = {}
    for entry in entries:
        unique.setdefault(entry.key, entry)
    return list(unique.values())


def _obd_entries(backend: Backend, supported_pids: list[int] | None) -> list[CatalogEntry]:
    try:
        ecm = backend.module_by_name(backend.obd_module_name())
        pids = backend.mode01_pids()
    except ImportError as exc:
        log.warning("OBD-II catalog unavailable: %s", exc)
        return []
    if ecm is None:
        return []
    allowed = set(supported_pids) if supported_pids else None
    return [CatalogEntry(ecm, param) for pid, param in sorted(pids.items()) if allowed is None or pid in allowed]


def _module_entries(backend: Backend, extra_modules: list[ModuleAddress]) -> list[CatalogEntry]:
    try:
        modules = list(backend.known_modules())
    except ImportError as exc:
        log.warning("module catalog unavailable: %s", exc)
        return []
    known = {(m.tx_id, m.rx_id) for m in modules}
    modules += [m for m in extra_modules if (m.tx_id, m.rx_id) not in known]
    entries: list[CatalogEntry] = []
    for module in modules:
        try:
            params = backend.module_parameters(module)
        except (ImportError, KeyError, LookupError):
            continue
        entries += [CatalogEntry(module, param) for param in params]
    return entries


def _broadcast_entries(backend: Backend) -> list[CatalogEntry]:
    try:
        signals = backend.broadcast_signals()
    except ImportError as exc:
        log.warning("broadcast catalog unavailable: %s", exc)
        return []
    return [
        CatalogEntry(BROADCAST_MODULE, broadcast_param(key, name, unit))
        for key, (name, unit) in sorted(signals.items())
    ]


def find_by_request(catalog: list[CatalogEntry], module: str, request: bytes) -> CatalogEntry | None:
    for entry in catalog:
        if entry.module.name == module and entry.param.request == request:
            return entry
    return None


def find_by_key(catalog: list[CatalogEntry], key: str) -> CatalogEntry | None:
    for entry in catalog:
        if entry.key == key:
            return entry
    return None


def steering_entries(catalog: list[CatalogEntry]) -> list[CatalogEntry]:
    """Default steering watch set, all polled: voltage, speed, RPM, ABS
    steering angle and the EPS 21 01 supply voltage, angle, driver torque,
    assist load, assist limit and temperature candidates. Entries the backend
    does not provide are skipped."""
    found: list[CatalogEntry | None] = [find_by_key(catalog, VOLTAGE_PARAM.key)]
    found += [find_by_request(catalog, module, request) for module, request in STEERING_REQUESTS]
    found += [find_by_key(catalog, key) for key in STEERING_KEYS]
    return [entry for entry in found if entry is not None]


def group_by_category(entries: list[CatalogEntry]) -> dict[str, list[CatalogEntry]]:
    groups: dict[str, list[CatalogEntry]] = {}
    for entry in entries:
        groups.setdefault(entry.param.category or "Other", []).append(entry)
    return dict(sorted(groups.items()))
