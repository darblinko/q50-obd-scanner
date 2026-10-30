"""Fakes shared by the GUI, session and recorder tests.

FakeBackend substitutes the protocol and transport packages so the GUI layer
is tested without the real backend. Import from other test modules:
`from tests.test_gui_fakes import FakeBackend, app, wait_until`."""

from __future__ import annotations

import math
import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEventLoop
from PySide6.QtWidgets import QApplication

from q50diag.core.transport import NoResponse, Transport, check_request_allowed
from q50diag.core.types import (
    AdapterInfo,
    CanFrame,
    DiscoveredModule,
    Dtc,
    ModuleAddress,
    MonitorStatus,
    ParameterDef,
    PortInfo,
    Reading,
)
from q50diag.gui.backend import SIMULATOR, Backend

ECM = ModuleAddress("ECM", 0x7E0, 0x7E8, "obd2", "Powertrain", "Engine control")
ABS = ModuleAddress("ABS", 0x740, 0x760, "kwp", "Chassis", "ABS / VDC")
EPS = ModuleAddress("EPS", 0x742, 0x762, "kwp", "Chassis", "Electric power steering")
BCM = ModuleAddress("BCM", 0x745, 0x765, "kwp", "Body", "Body control")
MYSTERY = ModuleAddress("0x71D", 0x71D, 0x72D, "uds", "Unknown")
FAKE_MODULES = (ECM, ABS, EPS, BCM)


def _u16(data: bytes) -> int:
    return int.from_bytes(data[:2], "big") if len(data) >= 2 else 0


RPM = ParameterDef("obd.01.0C", "Engine RPM", "rpm", "ECM", "Engine", b"\x01\x0c", lambda d: _u16(d) / 4, 0, 7000)
SPEED = ParameterDef(
    "obd.01.0D", "Vehicle speed", "km/h", "ECM", "Engine", b"\x01\x0d", lambda d: d[0] if d else None, 0, 250
)
COOLANT = ParameterDef(
    "obd.01.05", "Coolant temperature", "degC", "ECM", "Engine", b"\x01\x05", lambda d: d[0] - 40 if d else None
)
ABS_ANGLE = ParameterDef(
    "ABS.22.1208.steering_angle",
    "Steering angle (ABS)",
    "deg",
    "ABS",
    "Steering",
    b"\x22\x12\x08",
    lambda d: _u16(d) / 10,
)
EPS_SUPPLY = ParameterDef(
    "EPS.21.01.supply", "EPS supply voltage", "V", "EPS", "Steering", b"\x21\x01", lambda d: _u16(d) * 0.004
)
EPS_ANGLE = ParameterDef(
    "EPS.21.01.angle",
    "EPS steering angle",
    "deg",
    "EPS",
    "Steering",
    b"\x21\x01",
    lambda d: int.from_bytes(d[3:5], "big", signed=True) * -0.1,
)
EPS_ASSIST = ParameterDef("EPS.21.01.assist", "EPS assist load", "", "EPS", "Steering", b"\x21\x01", lambda d: d[7])
EPS_TORQUE = ParameterDef(
    "EPS.21.01.torque",
    "EPS driver torque",
    "",
    "EPS",
    "Steering",
    b"\x21\x01",
    lambda d: int.from_bytes(d[8:9], "big", signed=True),
)
EPS_TEMP = ParameterDef(
    "EPS.21.01.b10", "EPS temperature candidate", "", "EPS", "Steering", b"\x21\x01", lambda d: d[10]
)
EPS_LIMIT = ParameterDef(
    "EPS.21.01.b11", "EPS assist limit candidate", "", "EPS", "Steering", b"\x21\x01", lambda d: d[11]
)
EPS_FIELDS = [EPS_SUPPLY, EPS_ANGLE, EPS_TORQUE, EPS_ASSIST, EPS_TEMP, EPS_LIMIT]
BROADCAST = {
    "bcast.002.steering_angle": ("Steering angle (broadcast)", "deg"),
    "bcast.300.assist_raw": ("EPS assist (raw)", ""),
    "bcast.5E4.ps_warning_lamp": ("Power steering warning lamp", ""),
}


class FakeTransport(Transport):
    def __init__(self, port: str = "FAKE") -> None:
        self.port = port
        self.is_open = False
        self.requests: list[tuple[int, bytes, bool]] = []
        self.fail_next = False

    def open(self) -> AdapterInfo:
        self.is_open = True
        return AdapterInfo(self.port, "ELM327 v2.3", "vLinker FS", "ISO 15765-4 (CAN 11/500)", 12.6)

    def close(self) -> None:
        self.is_open = False

    def read_voltage(self) -> float | None:
        return 12.4 + 0.2 * math.sin(time.time())

    def request(self, tx_id, rx_id, payload, timeout_s=1.0, allow_clear=False) -> bytes:
        check_request_allowed(payload, allow_clear)
        self.requests.append((tx_id, payload, allow_clear))
        if tx_id == 0x745:
            raise NoResponse("NO DATA")
        if (tx_id, payload) == (0x742, b"\x21\x01"):
            torque = round(40 * math.sin(time.time()))
            data = bytearray.fromhex("0dbd02008f00000000003564000060000000000000")  # 2017 Q50 at rest
            data[7], data[8] = abs(torque) // 2, torque & 0xFF
            return b"\x61\x01" + bytes(data)
        return bytes([payload[0] + 0x40]) + payload[1:] + b"\x00\x2a"

    def request_functional(self, payload, timeout_s=1.0) -> dict[int, bytes]:
        return {0x7E8: bytes([payload[0] + 0x40]) + payload[1:]}

    def raw_command(self, line: str) -> str:
        return "ELM327 v2.3" if line.upper().replace(" ", "") == "ATI" else "OK"

    def monitor(self, stop, can_ids=None, max_duration_s=None):
        deadline = time.monotonic() + (max_duration_s or 0.2)
        tick = 0
        while not stop.is_set() and time.monotonic() < deadline:
            for can_id in (0x002, 0x300, 0x5E4, 0x174):
                if can_ids is None or can_id in can_ids:
                    data = bytes([tick % 256, (tick * 3) % 256, 0x04 if tick % 50 == 0 else 0, 0, 0, 0, 0, 0])
                    yield CanFrame(time.time(), can_id, data)
            tick += 1
            time.sleep(0.01)


class FakeBackend(Backend):
    def __init__(self) -> None:
        super().__init__()
        self.transports: list[FakeTransport] = []
        self.cleared: list[str] = []
        self.reads = 0
        self.dtc_mode_failures: dict[str, str] = {}

    def list_ports(self) -> list[PortInfo]:
        return [
            PortInfo("/dev/ttyUSB0", "vLinker FS (FTDI)", 0x0403, 0x6015, True),
            PortInfo("/dev/ttyS4", "Serial port", None, None, False),
        ]

    def make_transport(self, port: str) -> Transport:
        transport = FakeTransport("SIM" if port == SIMULATOR else port)
        self.transports.append(transport)
        return transport

    def mode01_pids(self):
        return {0x05: COOLANT, 0x0C: RPM, 0x0D: SPEED}

    def supported_pids(self, t):
        return [0x05, 0x0C, 0x0D]

    def read_monitor_status(self, t):
        return MonitorStatus(True, 2, {"Misfire": "complete", "Catalyst": "incomplete", "EVAP": "n/a"})

    def read_dtcs(self, t):
        return [
            Dtc("P0300", "Random/multiple cylinder misfire", "ECM", "stored"),
            Dtc("P0171", "System too lean bank 1", "ECM", "pending"),
        ]

    def read_dtcs_by_mode(self, t):
        return self.read_dtcs(t), dict(self.dtc_mode_failures)

    def clear_dtcs(self, t):
        t.request(0x7DF, 0x7E8, b"\x04", allow_clear=True)
        self.cleared.append("OBD-II")

    def read_vin(self, t):
        return "JN1SAMPLEXXXXXXX1"

    def read_freeze_frame(self, t):
        now = time.time()
        return [Reading("obd.02.0C", 812.0, "rpm", now), Reading("obd.02.0D", 0, "km/h", now)]

    def known_modules(self):
        return FAKE_MODULES

    def module_by_name(self, name):
        return next((m for m in FAKE_MODULES if m.name == name), None)

    def module_for_response_id(self, rx_id):
        return next((m for m in FAKE_MODULES if m.rx_id == rx_id), None)

    def ensure_session(self, t, m):
        pass  # the real ensure_session swallows NoResponse too

    def reset_sessions(self):
        pass

    def read_identification(self, t, m):
        if m.name == "BCM":
            raise NoResponse("NO DATA")
        return {"Part number": f"{m.name}-4GA0A", "Software": "1.02"}

    def read_module_dtcs(self, t, m):
        if m.name == "BCM":
            raise NoResponse("NO DATA")
        if m.name == "EPS":
            return [
                Dtc("C1601", "EPS supply voltage", "EPS", "history", 0x08),
                Dtc("C160A", "Overheat protection", "EPS", "current", 0x09),
            ]
        return []

    def clear_module_dtcs(self, t, m):
        t.request(m.tx_id, m.rx_id, b"\x14\xff\x00", allow_clear=True)
        self.cleared.append(m.name)

    def module_parameters(self, m):
        return {"ABS": [ABS_ANGLE], "EPS": EPS_FIELDS}.get(m.name, [])

    def read_parameter(self, t, m, p):
        self.reads += 1
        response = t.request(m.tx_id, m.rx_id, p.request)
        value = p.decode(response[len(p.request) :])
        if p.key == RPM.key:
            value = 800 + 400 * math.sin(time.time())
        return Reading(p.key, value, p.unit, time.time(), raw=response)

    def discover_modules(self, t, progress, stop):
        found = []
        for index, module in enumerate((ECM, ABS, EPS, MYSTERY), start=1):
            if stop is not None and stop.is_set():
                break
            if progress:
                progress(index, 4, f"0x{module.tx_id:03X}")
            found.append(DiscoveredModule(module, "3E00", b"\x7e\x00"))
        return found

    def scan_identifiers(self, t, m, service, start, end, progress=None, stop=None):
        results = {}
        for ident in range(start, end + 1):
            if stop is not None and stop.is_set():
                break
            if progress:
                progress(ident - start + 1, end - start + 1, f"0x{ident:02X}")
            if ident % 4 == 1:
                results[ident] = bytes([ident & 0x0F, 0x10, 0x00])  # echo stripped, as the real backend does
        return results

    def diff_snapshots(self, a, b):
        changed = {}
        for ident in set(a) & set(b):
            positions = [i for i, (x, y) in enumerate(zip(a[ident], b[ident])) if x != y]
            if positions:
                changed[ident] = positions
        return changed

    def decode_frame(self, frame):
        if frame.can_id == 0x002:
            return {"bcast.002.steering_angle": int.from_bytes(frame.data[:2], "little", signed=True) * -0.1}
        if frame.can_id == 0x300:
            return {"bcast.300.assist_raw": frame.data[0]}
        if frame.can_id == 0x5E4:
            return {"bcast.5E4.ps_warning_lamp": bool(frame.data[2] & 0x04)}
        return {}

    def broadcast_signals(self):
        return dict(BROADCAST)

    def describe(self, code):
        return {"C1601": "EPS supply voltage out of range"}.get(code, "")


@pytest.fixture(scope="session")
def app():
    instance = QApplication.instance() or QApplication([])
    from q50diag.gui.theme import apply_theme

    apply_theme(instance)
    return instance


def wait_until(predicate, timeout_s: float = 3.0) -> bool:
    """Process Qt events until predicate() is true or the timeout passes."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


@pytest.fixture
def ctx(app, tmp_path):
    """A connected AppContext over FakeBackend, recording into tmp_path. Shuts the session down after."""
    from q50diag.gui.context import AppContext
    from q50diag.session import Session

    backend = FakeBackend()
    session = Session(backend)
    context = AppContext(session, backend)
    context.recorder.folder = tmp_path
    session.connect_port(SIMULATOR)
    # The macOS Intel runner needed more than the 3 s default after a failed GUI test.
    assert wait_until(lambda: session.connected and context.overview is not None, 10)
    yield context
    # A test that fails mid-recording must not leave the recorder writing into the next test.
    if context.recorder.is_recording:
        context.stop_recording()
    session.shutdown()


def test_fake_transport_honours_safety_gate():
    from q50diag.core.transport import RequestBlocked

    transport = FakeTransport()
    with pytest.raises(RequestBlocked):
        transport.request(0x742, 0x762, b"\x27\x01")
    stop = threading.Event()
    frames = list(transport.monitor(stop, {0x300}, 0.05))
    assert frames and all(f.can_id == 0x300 for f in frames)
