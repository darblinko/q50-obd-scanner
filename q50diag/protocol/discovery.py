"""Read-only discovery with an explicit receive filter for each candidate."""

import logging
import threading
from collections.abc import Callable

from q50diag.core.transport import NegativeResponse, NoResponse, RequestBlocked, Transport
from q50diag.core.types import DiscoveredModule, ModuleAddress
from q50diag.protocol.modules import KNOWN_MODULES

LOGGER = logging.getLogger(__name__)
KNOWN_PROBES = (b"\x3e\x00", b"\x10\x81", b"\x21\x83")
# Unknown addresses get tester present only, so no session is started on them.
SWEEP_PROBES = (b"\x3e\x00",)
OBD_RESPONSE_IDS = set(range(0x7E8, 0x7F0))


def _probe(
    t: Transport, module: ModuleAddress, stop: threading.Event | None, probes: tuple[bytes, ...] = KNOWN_PROBES
) -> DiscoveredModule | None:
    for request in probes:
        if stop is not None and stop.is_set():
            return None
        try:
            response = t.request(module.tx_id, module.rx_id, request, timeout_s=0.08)
            if response and response[0] == request[0] + 0x40:
                return DiscoveredModule(module, request.hex().upper(), response)
        except (NoResponse, NegativeResponse, RequestBlocked) as error:
            LOGGER.debug("Discovery %s %s: %s", module.name, request.hex(), error)
    return None


def sweep_ids(
    id_range: range, modules: tuple[ModuleAddress, ...], reserved: tuple[ModuleAddress, ...] = ()
) -> list[int]:
    """Request ids the sweep may try. Never transmit on an ID that an ECU uses
    for its own responses, or on a known module's request id (probed already)."""
    everyone = (*modules, *reserved)
    response_ids = {module.rx_id for module in everyone} | OBD_RESPONSE_IDS
    known_ids = {module.tx_id for module in everyone}
    return [
        tx_id for tx_id in id_range if tx_id not in known_ids | response_ids and tx_id != 0x7DF and 0 <= tx_id <= 0x7FF
    ]


def discover_modules(
    t: Transport,
    progress: Callable[[int, int, str], None] | None = None,
    stop: threading.Event | None = None,
    id_range: range = range(0x700, 0x800),
    modules: tuple[ModuleAddress, ...] = KNOWN_MODULES,
    probes: tuple[bytes, ...] = KNOWN_PROBES,
    sweep_protocol: str = "kwp",
    response_offsets: tuple[int, ...] = (0x20, 8),
    reserved: tuple[ModuleAddress, ...] = (),
) -> list[DiscoveredModule]:
    """Probe `modules`, then sweep the remaining ids with tester present only.
    `reserved` are modules on another bus: not probed, but their ids are never swept."""
    sweep = sweep_ids(id_range, modules, reserved)
    total = len(modules) + len(sweep)
    result = []
    for done, module in enumerate(modules, 1):
        if stop is not None and stop.is_set():
            return sorted(result, key=lambda found: found.address.tx_id)
        found = _probe(t, module, stop, probes)
        if found is not None:
            result.append(found)
        if progress:
            progress(done, total, module.name)
    for done, tx_id in enumerate(sweep, len(modules) + 1):
        if stop is not None and stop.is_set():
            break
        found = _sweep_one(t, tx_id, stop, sweep_protocol, response_offsets)
        if found is not None:
            result.append(found)
        if progress:
            progress(done, total, f"0x{tx_id:03X}")
    return sorted(result, key=lambda found: found.address.tx_id)


def _sweep_one(
    t: Transport, tx_id: int, stop: threading.Event | None, protocol: str, response_offsets: tuple[int, ...]
) -> DiscoveredModule | None:
    for rx_id in (tx_id + offset for offset in response_offsets):
        if rx_id > 0x7FF:
            continue
        module = ModuleAddress(f"0x{tx_id:03X}", tx_id, rx_id, protocol, "Unknown")
        found = _probe(t, module, stop, SWEEP_PROBES)
        if found is not None:
            return found
    return None
