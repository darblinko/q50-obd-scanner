"""Session: the single owner of the adapter connection.

Every transport call runs on one worker QThread. GUI code calls the public
methods here, which only enqueue commands or flip flags under a lock; results
come back as Qt signals (queued across threads automatically). Between queued
commands the worker runs background activities: round-robin polling of the
subscribed parameters, short broadcast capture windows, the bus monitor, and a
periodic adapter voltage read."""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QThread, Signal

from q50diag.core.transport import (
    NegativeResponse,
    NoResponse,
    RequestBlocked,
    Transport,
    TransportError,
)
from q50diag.core.types import (
    AdapterInfo,
    CanFrame,
    DiscoveredModule,
    Dtc,
    ModuleAddress,
    MonitorStatus,
    ParameterDef,
    Reading,
)
from q50diag.gui.backend import SIMULATOR, Backend

log = logging.getLogger(__name__)

ADAPTER_MODULE = ModuleAddress("ADAPTER", 0, 0, "adapter", "Network", "Measured by the adapter")
BROADCAST_MODULE = ModuleAddress("BROADCAST", 0, 0, "broadcast", "Network", "Passive CAN frames")
VOLTAGE_KEY = "adapter.voltage"
VOLTAGE_PARAM = ParameterDef(
    key=VOLTAGE_KEY,
    name="Battery voltage (OBD port)",
    unit="V",
    module="ADAPTER",
    category="Electrical",
    request=b"",
    decode=lambda payload: None,
    min_value=10.0,
    max_value=15.5,
    description="ATRV: voltage at OBD pin 16 as measured by the adapter.",
)

Subscription = tuple[ModuleAddress, ParameterDef]

VOLTAGE_INTERVAL_S = 2.0
FLUSH_INTERVAL_S = 0.1
BROADCAST_WINDOW_S = 0.25
MONITOR_WINDOW_S = 0.5


def broadcast_id(key: str) -> int:
    """CAN id of a broadcast signal key such as "bcast.300.assist_raw"."""
    return int(key.split(".")[1], 16)


def broadcast_param(key: str, name: str, unit: str) -> ParameterDef:
    return ParameterDef(
        key=key,
        name=name,
        unit=unit,
        module=BROADCAST_MODULE.name,
        category="Broadcast",
        request=b"",
        decode=lambda payload: None,
        description=f"Decoded from passive CAN frame 0x{broadcast_id(key):03X}",
    )


def frame_readings(backend: Backend, frames: list[CanFrame]) -> list[Reading]:
    """Decode broadcast frames into readings for the known signals."""
    units = {key: unit for key, (_, unit) in backend.broadcast_signals().items()}
    readings: list[Reading] = []
    for frame in frames:
        for key, value in backend.decode_frame(frame).items():
            readings.append(Reading(key, value, units.get(key, ""), frame.timestamp, raw=frame.data))
    return readings


def request_groups(subscriptions: list[Subscription]) -> list[list[Subscription]]:
    """Group subscriptions that send the same request to the same module, in
    first-seen order, dropping repeated keys. The adapter voltage is never grouped."""
    groups: dict[tuple, dict[str, Subscription]] = {}
    for module, param in subscriptions:
        shared = module.name != ADAPTER_MODULE.name and bool(param.request)
        group_key = (module.tx_id, module.rx_id, param.request) if shared else (param.key,)
        groups.setdefault(group_key, {}).setdefault(param.key, (module, param))
    return [list(group.values()) for group in groups.values()]


class LoggingTransport(Transport):
    """Delegates to a real transport and reports each request and response."""

    def __init__(self, inner: Transport, log_fn: Callable[[str, str], None]) -> None:
        self.inner = inner
        self._log = log_fn

    def __getattr__(self, name: str):
        return getattr(self.inner, name)

    def open(self) -> AdapterInfo:
        return self.inner.open()

    def close(self) -> None:
        self.inner.close()

    def read_voltage(self) -> float | None:
        return self.inner.read_voltage()

    def request(self, tx_id, rx_id, payload, timeout_s=1.0, allow_clear=False) -> bytes:
        self._log("TX", f"{tx_id:03X}  {payload.hex(' ').upper()}")
        try:
            response = self.inner.request(tx_id, rx_id, payload, timeout_s, allow_clear)
        except NoResponse:
            self._log("RX", f"{rx_id:03X}  NO DATA")
            raise
        except NegativeResponse as exc:
            self._log("RX", f"{rx_id:03X}  7F {exc.service:02X} {exc.code:02X}")
            raise
        self._log("RX", f"{rx_id:03X}  {response.hex(' ').upper()}")
        return response

    def request_functional(self, payload, timeout_s=1.0) -> dict[int, bytes]:
        self._log("TX", f"7DF  {payload.hex(' ').upper()}")
        responses = self.inner.request_functional(payload, timeout_s)
        for can_id, data in responses.items():
            self._log("RX", f"{can_id:03X}  {data.hex(' ').upper()}")
        return responses

    def raw_command(self, line: str) -> str:
        return self.inner.raw_command(line)

    def monitor(self, stop, can_ids=None, max_duration_s=None):
        return self.inner.monitor(stop, can_ids=can_ids, max_duration_s=max_duration_s)


@dataclass(frozen=True)
class VehicleOverview:
    adapter: AdapterInfo | None
    vin: str | None
    monitor: MonitorStatus | None
    voltage: float | None
    supported_pids: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DtcScanResult:
    dtcs: list[Dtc]
    # module name -> "ok", "no response", "partial: <modes that failed>", "malformed reply: ..." or error text
    module_status: dict[str, str]
    freeze_frame: list[Reading] = field(default_factory=list)
    complete: bool = True  # False when cancelled part way


@dataclass(frozen=True)
class ModuleReport:
    module: ModuleAddress
    responding: bool
    identification: dict[str, str] = field(default_factory=dict)
    dtcs: list[Dtc] | None = None  # None when not requested
    parameters: list[Reading] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class IdentifierScan:
    module: ModuleAddress
    service: int
    start: int
    end: int
    results: dict[int, bytes]
    complete: bool = True


@dataclass(frozen=True)
class _Command:
    label: str
    fn: Callable[..., None]
    args: tuple
    cancellable: bool = False


class _NotConnected(Exception):
    pass


class _Worker(QThread):
    def __init__(self, session: Session) -> None:
        super().__init__()
        self._session = session

    def run(self) -> None:
        self._session._run_loop()


class Session(QObject):
    connection_changed = Signal(bool, object)  # connected, AdapterInfo | None
    status = Signal(str)
    error = Signal(str)
    progress = Signal(str, int, int)  # label, done, total
    busy_changed = Signal(bool, str)  # busy, label of the running command
    raw_log = Signal(str, str)  # direction ("TX", "RX", "INFO"), text
    voltage_changed = Signal(object)  # float | None
    overview_ready = Signal(object)  # VehicleOverview
    dtcs_ready = Signal(object)  # DtcScanResult
    dtcs_cleared = Signal(str)  # module name, "OBD-II" for mode 04
    modules_discovered = Signal(object)  # list[DiscoveredModule]
    module_read = Signal(object)  # ModuleReport
    identifiers_scanned = Signal(object)  # IdentifierScan
    raw_response = Signal(str, str)  # request text, response text
    readings = Signal(object)  # list[Reading]
    frames = Signal(object)  # list[CanFrame]
    poll_rate = Signal(float)  # module reads per second; one read can serve several parameters
    activity_changed = Signal()  # polling or monitor state changed

    def __init__(self, backend: Backend | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.backend = backend or Backend()
        self.log_traffic = True
        self.adapter: AdapterInfo | None = None
        self.port: str | None = None  # port requested at connect, SIMULATOR for the simulator
        self.discovered: list[DiscoveredModule] = []
        self.supported_pids: list[int] = []
        self._transport: Transport | None = None
        self._queue: queue.Queue[_Command | None] = queue.Queue()
        self._cancel = threading.Event()
        self._interrupt = threading.Event()
        self._shutdown = threading.Event()
        self._lock = threading.Lock()
        self._subscriptions: dict[str, list[Subscription]] = {}
        self._monitor_ids: set[int] | None = None
        self._monitor_on = False
        self._cursor = 0
        self._pending: list[Reading] = []
        self._last_flush = 0.0
        self._last_voltage = 0.0
        self._rate_count = 0
        self._rate_started = time.monotonic()
        self._worker = _Worker(self)
        self._worker.start()

    # ------------------------------------------------------------------
    # Public API (GUI thread)
    # ------------------------------------------------------------------
    @property
    def connected(self) -> bool:
        return self._transport is not None

    def connect_port(self, port: str) -> None:
        self._submit("Connect", self._do_connect, port)

    def disconnect_port(self) -> None:
        with self._lock:
            self._subscriptions.clear()
            self._monitor_on = False
        self._cancel.set()
        self._submit("Disconnect", self._do_disconnect)

    def read_vehicle_overview(self) -> None:
        self._submit("Vehicle overview", self._do_overview)

    def scan_all_dtcs(self, modules: list[ModuleAddress] | None = None) -> None:
        self._submit("DTC scan", self._do_scan_dtcs, modules, cancellable=True)

    def clear_dtcs(self, module: ModuleAddress | None) -> None:
        """Call only after the user confirmed. None clears OBD-II (mode 04)."""
        self._submit("Clear DTCs", self._do_clear_dtcs, module)

    def discover_modules(self) -> None:
        self._submit("Module discovery", self._do_discover, cancellable=True)

    def full_scan(self) -> None:
        self._submit("Full scan", self._do_full_scan, cancellable=True)

    def read_module(self, module: ModuleAddress, include_parameters: bool = True) -> None:
        self._submit(f"Read {module.name}", self._do_read_module, module, include_parameters)

    def scan_identifiers(self, module: ModuleAddress, service: int, start: int, end: int) -> None:
        self._submit(
            f"Scan {module.name} 0x{service:02X}",
            self._do_scan_identifiers,
            module,
            service,
            start,
            end,
            cancellable=True,
        )

    def raw_command(self, line: str) -> None:
        self._submit("Raw command", self._do_raw_command, line)

    def raw_request(self, module: ModuleAddress, payload: bytes) -> None:
        self._submit(f"Raw request {module.name}", self._do_raw_request, module, payload)

    def cancel(self) -> None:
        self._cancel.set()
        self._interrupt.set()

    def start_polling(self, owner: str, subscriptions: list[Subscription]) -> None:
        with self._lock:
            self._subscriptions[owner] = list(subscriptions)
        self._interrupt.set()
        self.activity_changed.emit()

    def stop_polling(self, owner: str) -> None:
        with self._lock:
            self._subscriptions.pop(owner, None)
        self.activity_changed.emit()

    def polling_owners(self) -> list[str]:
        with self._lock:
            return [owner for owner, subs in self._subscriptions.items() if subs]

    def polling_snapshot(self) -> dict[str, list[Subscription]]:
        """Copy of the active subscriptions by owner, so a reconnect for a
        code reload can restore them with start_polling."""
        with self._lock:
            return {owner: list(subs) for owner, subs in self._subscriptions.items() if subs}

    @property
    def bus_monitor_ids(self) -> set[int] | None:
        with self._lock:
            return set(self._monitor_ids) if self._monitor_ids else None

    def start_bus_monitor(self, can_ids: set[int] | None = None) -> None:
        with self._lock:
            self._monitor_ids = set(can_ids) if can_ids else None
            self._monitor_on = True
        self._interrupt.set()
        self.activity_changed.emit()

    def stop_bus_monitor(self) -> None:
        with self._lock:
            self._monitor_on = False
        self._interrupt.set()
        self.activity_changed.emit()

    @property
    def bus_monitor_active(self) -> bool:
        return self._monitor_on

    def start_steering_watch(self, subscriptions: list[Subscription]) -> None:
        """Steering watch is polling under the "steering" owner. Broadcast
        subscriptions in the set make each polling round end with a short
        filtered capture window for their CAN ids."""
        self.start_polling("steering", subscriptions)

    def stop_steering_watch(self) -> None:
        self.stop_polling("steering")

    def shutdown(self, timeout_s: float = 3.0) -> None:
        self._shutdown.set()
        self._cancel.set()
        self._interrupt.set()
        self._queue.put(None)
        self._worker.wait(int(timeout_s * 1000))

    # ------------------------------------------------------------------
    # Worker loop
    # ------------------------------------------------------------------
    def _submit(self, label: str, fn: Callable[..., None], *args, cancellable: bool = False) -> None:
        self._interrupt.set()
        self._queue.put(_Command(label, fn, args, cancellable))

    def _run_loop(self) -> None:
        while not self._shutdown.is_set():
            command = self._next_command()
            if command is not None:
                self._execute(command)
            elif not self._shutdown.is_set():
                self._guarded(self._background_tick)
        self._close_transport()

    def _next_command(self) -> _Command | None:
        try:
            if self._has_background_work():
                return self._queue.get_nowait()
            return self._queue.get(timeout=0.25)
        except queue.Empty:
            return None

    def _has_background_work(self) -> bool:
        if self._transport is None:
            return False
        with self._lock:
            return self._monitor_on or any(self._subscriptions.values())

    def _execute(self, command: _Command) -> None:
        if command.cancellable:
            self._cancel.clear()
        self.busy_changed.emit(True, command.label)
        try:
            self._guarded(command.fn, *command.args, label=command.label)
        finally:
            self.busy_changed.emit(False, "")

    def _guarded(self, fn: Callable[..., None], *args, label: str = "") -> None:
        prefix = f"{label}: " if label else ""
        try:
            fn(*args)
        except _NotConnected:
            self.error.emit(f"{prefix}not connected to an adapter")
        except NoResponse as exc:
            self.error.emit(f"{prefix}no response ({exc})")
        except RequestBlocked as exc:
            self.error.emit(f"{prefix}blocked by the read-only safety gate: {exc}")
        except NegativeResponse as exc:
            self.error.emit(f"{prefix}{exc}")
        except TransportError as exc:
            self._connection_lost(exc)
        except ImportError as exc:
            log.exception("backend module missing")
            self.error.emit(f"{prefix}backend module missing: {exc}")
        except Exception as exc:  # noqa: BLE001 (reported to the user, logged with traceback)
            log.exception("%s failed", label or "background task")
            self.error.emit(f"{prefix}failed: {exc}")

    def _connection_lost(self, exc: Exception) -> None:
        log.warning("connection lost: %s", exc)
        with self._lock:
            self._subscriptions.clear()
            self._monitor_on = False
        self._close_transport()
        self.activity_changed.emit()
        self.error.emit(f"Connection lost: {exc}. Check the adapter and reconnect.")
        self.status.emit("Disconnected (connection lost)")

    def _close_transport(self) -> None:
        transport, self._transport = self._transport, None
        if transport is None:
            return
        try:
            transport.close()
        except Exception:  # noqa: BLE001 (closing a broken port may fail; nothing to recover)
            log.exception("error closing transport")
        self.adapter = None
        self.port = None
        self.connection_changed.emit(False, None)
        self.voltage_changed.emit(None)

    def _require(self) -> Transport:
        if self._transport is None:
            raise _NotConnected()
        return self._transport

    def _report_progress(self, label: str) -> Callable[[int, int, str], None]:
        def report(done: int, total: int, detail: str) -> None:
            text = f"{label}: {detail}" if detail else label
            self.progress.emit(text, done, total)

        return report

    def _log_raw(self, direction: str, text: str) -> None:
        if self.log_traffic:
            self.raw_log.emit(direction, text)

    # ------------------------------------------------------------------
    # Background activities (worker thread)
    # ------------------------------------------------------------------
    def _background_tick(self) -> None:
        if self._transport is None:
            return
        self._interrupt.clear()
        with self._lock:
            monitor_on = self._monitor_on
            monitor_ids = self._monitor_ids
            subscriptions = [s for subs in self._subscriptions.values() for s in subs]
        self._maybe_read_voltage(subscriptions)
        if monitor_on:
            self._capture(monitor_ids, MONITOR_WINDOW_S)
        elif subscriptions:
            self._poll_step(subscriptions)
        self._flush_readings()
        self._update_rate()

    def _maybe_read_voltage(self, subscriptions: list[Subscription]) -> None:
        if any(param.key == VOLTAGE_KEY for _, param in subscriptions):
            return
        if time.monotonic() - self._last_voltage < VOLTAGE_INTERVAL_S:
            return
        self._pending.append(self._read_voltage())

    def _read_voltage(self) -> Reading:
        self._last_voltage = time.monotonic()
        value = self._require().read_voltage()
        self.voltage_changed.emit(value)
        error = "" if value is not None else "adapter did not report voltage"
        return Reading(VOLTAGE_KEY, value, "V", time.time(), error=error)

    def _poll_step(self, subscriptions: list[Subscription]) -> None:
        polled = request_groups([s for s in subscriptions if s[0].name != BROADCAST_MODULE.name])
        ids = {broadcast_id(p.key) for m, p in subscriptions if m.name == BROADCAST_MODULE.name}
        if polled:
            group = polled[self._cursor % len(polled)]
            self._cursor += 1
            self._pending.extend(self._read_group(group))
            self._rate_count += 1
        round_done = not polled or self._cursor >= len(polled)
        if round_done:
            self._cursor = 0
            if ids:
                self._capture(ids, BROADCAST_WINDOW_S)

    def _read_group(self, group: list[Subscription]) -> list[Reading]:
        """One request per group; the other parameters decode the same reply."""
        module, param = group[0]
        first = self._read_one(module, param)
        readings = [first]
        for _module, other in group[1:]:
            if first.error:
                readings.append(Reading(other.key, None, other.unit, first.timestamp, first.raw, first.error))
            else:
                readings.append(self.backend.decode_reply(other, first.raw))
        return readings

    def _read_one(self, module: ModuleAddress, param: ParameterDef) -> Reading:
        if module.name == ADAPTER_MODULE.name:
            return self._read_voltage()
        transport = self._require()
        try:
            return self.backend.read_parameter(transport, module, param)
        except (NoResponse, NegativeResponse, RequestBlocked) as exc:
            return Reading(param.key, None, param.unit, time.time(), error=str(exc))

    def _capture(self, can_ids: set[int] | None, window_s: float) -> None:
        transport = self._require()
        frames: list[CanFrame] = []
        for frame in transport.monitor(self._interrupt, can_ids=can_ids, max_duration_s=window_s):
            frames.append(frame)
            if self._shutdown.is_set():
                break
        if not frames:
            return
        self.frames.emit(frames)
        self._pending.extend(frame_readings(self.backend, frames))

    def _flush_readings(self, force: bool = False) -> None:
        now = time.monotonic()
        if not self._pending or (not force and now - self._last_flush < FLUSH_INTERVAL_S):
            return
        batch, self._pending = self._pending, []
        self._last_flush = now
        self.readings.emit(batch)

    def _update_rate(self) -> None:
        elapsed = time.monotonic() - self._rate_started
        if elapsed < 1.0:
            return
        self.poll_rate.emit(self._rate_count / elapsed)
        self._rate_count = 0
        self._rate_started = time.monotonic()

    # ------------------------------------------------------------------
    # Commands (worker thread)
    # ------------------------------------------------------------------
    def _do_connect(self, port: str) -> None:
        self._close_transport()
        label = "simulator" if port == SIMULATOR else port
        self.status.emit(f"Connecting to {label}")
        raw = self.backend.make_transport(port)
        transport = LoggingTransport(raw, self._log_raw)
        info = transport.open()
        self._transport = transport
        self.adapter = info
        self.port = port
        self.discovered = []
        self.supported_pids = []
        self.backend.reset_sessions()
        self.connection_changed.emit(True, info)
        self.voltage_changed.emit(info.voltage)
        self.status.emit(f"Connected to {label}: {info.identity}, {info.protocol}")
        self._do_overview()

    def _do_disconnect(self) -> None:
        if self._transport is None:
            self.status.emit("Not connected")
            return
        self._close_transport()
        self.activity_changed.emit()
        self.status.emit("Disconnected")

    def _do_overview(self) -> None:
        transport = self._require()
        errors: list[str] = []
        voltage = self._try(errors, "voltage", transport.read_voltage)
        self.voltage_changed.emit(voltage)
        vin = self._try(errors, "VIN", self.backend.read_vin, transport)
        monitor = self._try(errors, "monitor status", self.backend.read_monitor_status, transport)
        pids = self._try(errors, "supported PIDs", self.backend.supported_pids, transport) or []
        self.supported_pids = list(pids)
        overview = VehicleOverview(self.adapter, vin, monitor, voltage, list(pids), errors)
        self.overview_ready.emit(overview)
        self.status.emit("Vehicle overview read" + (f" ({len(errors)} errors)" if errors else ""))

    def _try(self, errors: list[str], what: str, fn: Callable, *args):
        try:
            return fn(*args)
        except (NoResponse, NegativeResponse, RequestBlocked) as exc:
            errors.append(f"{what}: {exc}")
            return None
        except ValueError as exc:
            errors.append(f"{what}: malformed reply: {exc}")
            return None

    def _modules_to_scan(self) -> list[ModuleAddress]:
        """Discovered modules once discovery has run, else every known module on the selected bus."""
        if self.discovered:
            return [found.address for found in self.discovered]
        return list(self.backend.scan_modules())

    def _is_discovered(self, module: ModuleAddress) -> bool:
        return any(found.address.tx_id == module.tx_id for found in self.discovered)

    def _do_scan_dtcs(self, modules: list[ModuleAddress] | None) -> None:
        transport = self._require()
        targets = [m for m in (modules or self._modules_to_scan()) if m.protocol != "obd2"]
        total = len(targets) + 1
        dtcs: list[Dtc] = []
        status: dict[str, str] = {}
        self.progress.emit("DTC scan: OBD-II", 0, total)
        freeze = self._scan_obd_dtcs(transport, dtcs, status)
        complete = True
        for index, module in enumerate(targets, start=1):
            if self._cancel.is_set():
                complete = False
                break
            self.progress.emit(f"DTC scan: {module.name}", index, total)
            status[module.name] = self._scan_module_dtcs(transport, module, dtcs)
        self.progress.emit("DTC scan done", total, total)
        self.dtcs_ready.emit(DtcScanResult(dtcs, status, freeze, complete))
        self.status.emit(f"DTC scan: {len(dtcs)} codes across {len(status)} modules")

    def _scan_obd_dtcs(self, transport: Transport, dtcs: list[Dtc], status: dict[str, str]) -> list[Reading]:
        try:
            found, failures = self.backend.read_dtcs_by_mode(transport)
            dtcs.extend(found)
            partial = "; ".join(f"{kind}: {reason}" for kind, reason in failures.items())
            status["OBD-II"] = f"partial: {partial}" if partial else "ok"
        except (NoResponse, NegativeResponse, RequestBlocked) as exc:
            status["OBD-II"] = str(exc)
            return []
        try:
            return self.backend.read_freeze_frame(transport) if found else []
        except (NoResponse, NegativeResponse, RequestBlocked):
            return []

    def _scan_module_dtcs(self, transport: Transport, module: ModuleAddress, dtcs: list[Dtc]) -> str:
        try:
            found = self.backend.read_module_dtcs(transport, module)
            dtcs.extend(found)
            return "ok"
        except NoResponse:
            return "no response"
        except (NegativeResponse, RequestBlocked) as exc:
            return str(exc)
        except ValueError as exc:
            return f"malformed reply: {exc}"

    def _do_clear_dtcs(self, module: ModuleAddress | None) -> None:
        transport = self._require()
        if module is None:
            self.backend.clear_dtcs(transport)
            name = "OBD-II"
        else:
            self.backend.clear_module_dtcs(transport, module)
            name = module.name
        self.dtcs_cleared.emit(name)
        self.status.emit(f"Cleared DTCs on {name}")

    def _do_discover(self) -> None:
        transport = self._require()
        found = self.backend.discover_modules(transport, self._report_progress("Discovery"), self._cancel)
        self.discovered = list(found)
        self.modules_discovered.emit(list(found))
        state = "cancelled, partial result" if self._cancel.is_set() else "done"
        self.status.emit(f"Discovery {state}: {len(found)} modules responded")

    def _do_full_scan(self) -> None:
        self._do_overview()
        self._do_discover()
        if self._cancel.is_set():
            return
        self._do_scan_dtcs(None)
        responding = [d.address for d in self.discovered]
        for index, module in enumerate(responding, start=1):
            if self._cancel.is_set():
                break
            self.progress.emit(f"Identification: {module.name}", index, len(responding))
            self._do_read_module(module, False, False)
        self.status.emit("Full scan complete" if not self._cancel.is_set() else "Full scan cancelled")

    def _do_read_module(self, module: ModuleAddress, include_parameters: bool, include_dtcs: bool = True) -> None:
        transport = self._require()
        errors: list[str] = []
        ident = self._try(errors, "identification", self.backend.read_identification, transport, module) or {}
        dtcs = None
        if include_dtcs:
            dtcs = self._try(errors, "DTCs", self.backend.read_module_dtcs, transport, module)
        readings: list[Reading] = []
        if include_parameters:
            for param in self.backend.module_parameters(module):
                readings.append(self._read_one(module, param))
        responding = bool(ident) or bool(dtcs) or any(not r.error for r in readings) or self._is_discovered(module)
        report = ModuleReport(module, responding, ident, dtcs, readings, errors)
        self.module_read.emit(report)
        self.status.emit(f"{module.name}: {'responding' if responding else 'no response'}")

    def _do_scan_identifiers(self, module: ModuleAddress, service: int, start: int, end: int) -> None:
        transport = self._require()
        label = f"Scan {module.name} 0x{service:02X}"
        results = self.backend.scan_identifiers(
            transport,
            module,
            service,
            start,
            end,
            progress=self._report_progress(label),
            stop=self._cancel,
        )
        complete = not self._cancel.is_set()
        self.identifiers_scanned.emit(IdentifierScan(module, service, start, end, dict(results), complete))
        self.status.emit(f"{label}: {len(results)} identifiers answered")

    def _do_raw_command(self, line: str) -> None:
        transport = self._require()
        self.raw_log.emit("TX", line)
        try:
            response = transport.raw_command(line)
        finally:
            compact = "".join(line.upper().split())
            if compact.startswith("10") and all(c in "0123456789ABCDEF" for c in compact):
                self.backend.reset_sessions()  # the module's session may have changed
        self.raw_log.emit("RX", response)
        self.raw_response.emit(line, response)

    def _do_raw_request(self, module: ModuleAddress, payload: bytes) -> None:
        transport = self._require()
        request_text = f"{module.tx_id:03X} {payload.hex(' ').upper()}"
        try:
            response = transport.request(module.tx_id, module.rx_id, payload)
            text = response.hex(" ").upper()
        except NoResponse:
            text = "NO DATA"
        except NegativeResponse as exc:
            text = f"7F {exc.service:02X} {exc.code:02X} (negative response)"
        finally:
            if payload[:1] == b"\x10":
                self.backend.reset_sessions()  # the module's session may have changed
        self.raw_response.emit(request_text, text)
