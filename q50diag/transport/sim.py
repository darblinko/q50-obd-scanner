"""Deterministic, time-based simulator for the supported vehicles.

The default vehicle is the 2017 Q50. For the Ford S550 profiles the PCM
answers the same Mode 01 engine model plus the Ford DIDs in
q50diag.transport.sim_ford, and every simulated Ford module answers 22 F190,
F188, F113 and 19 02. Ford sends no broadcast frames.

EPS local ID 01 starts from the 21 byte record a 2017 Q50 returned at rest and
overlays bytes 0-1 supply voltage x 4 mV, bytes 3-4 steering angle x -0.1 deg,
byte 7 assist load (abs(torque byte) / 2, 0 during stiff events) and byte 8
signed driver torque. Bytes 5-6 and 15-16 stay 0. The remaining EPS
identifiers are synthetic: local IDs 02..05 encode motor current
/10 A, target current /10 A, voltage /100 V and motor temperature -40 degC, and
DIDs 1001..1005 encode torque, the same three values and temperature. They are
not real Nissan DIDs.
"""

from __future__ import annotations

import logging
import math
import random
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass

from q50diag.core.transport import NegativeResponse, NoResponse, Transport, TransportError, check_request_allowed
from q50diag.core.types import AdapterInfo, CanFrame, ModuleAddress
from q50diag.transport import sim_ford
from q50diag.transport.elm_parse import console_command
from q50diag.vehicles import VehicleProfile, default_vehicle

# Synthetic VIN. Position 9 is a letter, so it fails the check digit and matches no vehicle.
VIN = "JN1SAMPLEXXXXXXX0"
# EPS 21 01 reply data from a 2017 Q50 at rest, wheel still (2026-10-17).
EPS_RECORD_AT_REST = bytes.fromhex("0dbd02008f00000000003564000060000000000000")
SIMULATED_NAMES = default_vehicle().sim_modules
Q50_BROADCAST_RATES = {0x002: 100, 0x300: 50, 0x174: 10, 0x180: 50, 0x5E4: 5, 0x551: 2}
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class DriveState:
    elapsed: float
    rpm: float
    speed: float
    load: float
    throttle: float
    coolant: float
    intake: float
    maf: float
    manifold: float
    voltage: float
    angle: float
    torque: float
    motor_current: float
    target_current: float
    eps_voltage: float
    motor_temperature: float
    gear: int
    stiff: bool


def _byte(value: float) -> bytes:
    return bytes((max(0, min(255, round(value))),))


def _u16(value: float) -> bytes:
    return max(0, min(65535, round(value))).to_bytes(2, "big")


def _percent(value: float) -> bytes:
    return _byte(value * 255 / 100)


def _trim(value: float) -> bytes:
    return _byte(value * 128 / 100 + 128)


class SimulatedTransport(Transport):
    def __init__(self, seed: int = 0, stiff_steering_events: bool = True, request_latency_s: float = 0.0,
                 vehicle: VehicleProfile | None = None, bus: str = "hs"):
        self.stiff_steering_events = stiff_steering_events
        self.request_latency_s = request_latency_s
        self.vehicle = vehicle or default_vehicle()
        self.bus = bus
        self.vin = self.vehicle.sim_vin
        self.modules = tuple(module for module in self.vehicle.modules
                             if module.name in self.vehicle.sim_modules and module.bus == bus)
        self._ford = self.vehicle.make == "Ford"
        self._engine = self.vehicle.obd_module
        self._obd_names = {name for name in (self._engine, "TCM") if name in self.vehicle.sim_modules}
        engine = self.vehicle.module(self._engine)
        self._automatic = engine is not None and any(
            p.request == b"\x22\x1e\x1c" for p in self.vehicle.module_parameters(engine))
        self.connection_generation = 0
        self._phase = random.Random(seed).uniform(0, 2 * math.pi)
        self._start = time.monotonic()
        self._opened = False
        self._lock = threading.RLock()
        self._tx_id = 0x7DF
        self._rx_id: int | None = None
        self._cleared_at: float | None = None
        self._stored = [b"\x01\x71"]
        self._pending = [b"\x03\x00"]
        self._permanent = [b"\x01\x71"]
        self._module_codes = ({name: list(codes) for name, codes in sim_ford.MODULE_CODES.items()} if self._ford
                              else {"EPS": [b"\x56\x04\x20"], "ABS": [b"\xD0\x00\x01"]})
        self._freeze = {pid: value for pid, value in self._pid_values(self._sample(35)).items() if pid != 1}

    def open(self) -> AdapterInfo:
        with self._lock:
            self._opened = True
            self._start = time.monotonic()
            self._tx_id, self._rx_id = 0x7DF, None
            self.connection_generation += 1
            return AdapterInfo("simulated", "ELM327 v2.2 (simulated)", self._description(),
                               self._protocol_text(), self.read_voltage())

    def _description(self) -> str:
        if self.vehicle is default_vehicle():
            return "Virtual vLinker FS / Q50"
        return f"Virtual STN adapter / {self.vehicle.short_name} (simulated)"

    def _protocol_text(self) -> str:
        if self.bus == "hs":
            return "ISO 15765-4 (CAN 11/500)"
        return f"ISO 15765 CAN 11-bit, {self.bus.upper()} bus (simulated)"

    def close(self) -> None:
        with self._lock:
            self._opened = False

    def _require_open(self) -> None:
        if not self._opened:
            raise TransportError("Simulated adapter is not open")

    def _elapsed(self) -> float:
        return max(0, time.monotonic() - self._start)

    def _sample(self, elapsed: float | None = None) -> DriveState:
        elapsed = self._elapsed() if elapsed is None else elapsed
        phase = elapsed % 90
        wave = math.sin(elapsed * 1.7 + self._phase)
        if phase < 15:
            speed, rpm, load, throttle = 0, 720 + 18 * wave, 19, 12
        elif phase < 40:
            progress = (phase - 15) / 25
            speed, rpm = 95 * progress, 1400 + 2200 * ((phase - 15) % 5) / 5
            load, throttle = 65 + 20 * progress, 52 + 25 * progress
        elif phase < 70:
            speed, rpm, load, throttle = 95 + 2 * wave, 1850 + 30 * wave, 32, 23
        else:
            progress = (90 - phase) / 20
            speed, rpm, load, throttle = 95 * progress, 720 + 1300 * progress, 12, 8
        voltage = 14.1 + 0.25 * math.sin(elapsed / 11 + self._phase)
        angle = (18 if speed > 30 else 90) * math.sin(elapsed / 4 + self._phase)
        torque = 2.8 * math.sin(elapsed / 4 + self._phase) + 0.15 * wave
        target = 3 + abs(torque) * (9 if speed < 30 else 4)
        stiff = self.stiff_steering_events and elapsed >= 60 and elapsed % 60 < 8
        manifold = 32 + throttle * 0.8 if phase < 15 or phase >= 70 else 55 + load * 1.8
        return DriveState(elapsed, rpm, speed, load, throttle,
                          22 + 70 * (1 - math.exp(-elapsed / 120)), 28 + load / 6,
                          3.5 + rpm * load / 3500, manifold, voltage, angle, torque,
                          0.15 if stiff else target * (0.97 + 0.02 * wave), target,
                          11.2 + 0.08 * wave if stiff else voltage - 0.15,
                          28 + 35 * (1 - math.exp(-elapsed / 180)),
                          0 if speed < 1 else min(7, 1 + int(speed / 15)), stiff)

    def _pid_values(self, state: DriveState) -> dict[int, bytes]:
        wave = math.sin(state.elapsed + self._phase)
        rich = state.load > 60
        ratio = 0.84 if rich else 1.01 + 0.015 * wave
        since_clear = 18000 + state.elapsed if self._cleared_at is None else state.elapsed - self._cleared_at
        readiness = b"\x07\xE5\x04" if self._cleared_at is None else b"\x77\xE5\xE5"
        values = {
            1: _byte((128 if self._stored else 0) + len(self._stored)) + readiness,
            2: self._stored[0] if self._stored else b"\x00\x00", 3: b"\x02\x02",
            4: _percent(state.load), 5: _byte(state.coolant + 40),
            6: _trim(7 + 3 * wave), 7: _trim(10), 8: _trim(1 + 2 * wave), 9: _trim(2),
            0x0A: _byte(400 / 3), 0x0B: _byte(state.manifold), 0x0C: _u16(state.rpm * 4),
            0x0D: _byte(state.speed), 0x0E: _byte((16 + 10 * wave + 64) * 2),
            0x0F: _byte(state.intake + 40), 0x10: _u16(state.maf * 100),
            0x11: _percent(state.throttle), 0x13: b"\x33", 0x14: _byte(100 + 70 * wave) + _trim(7),
            0x15: b"\x90\xFF", 0x18: _byte(100 + 60 * wave) + _trim(2), 0x19: b"\x8C\xFF",
            0x1C: b"\x01", 0x1F: _u16(state.elapsed), 0x21: _u16(24),
            0x23: _u16((4000 + state.load * 130) / 10),
            0x24: _u16(ratio * 32768) + _u16(3.2 * 8192),
            0x28: _u16(ratio * 32768) + _u16(3.15 * 8192),
            0x2C: b"\x00", 0x2D: b"\x80", 0x2E: _percent(20),
            0x2F: _percent(max(0, 68 - state.elapsed / 2000)), 0x30: _byte(0 if self._cleared_at is not None else 12),
            0x31: _u16(since_clear / 90), 0x33: b"\x65",
            0x3C: _u16((450 + state.load * 3 + 40) * 10),
            0x3D: _u16((445 + state.load * 3 + 40) * 10),
            0x42: _u16(state.voltage * 1000), 0x43: _u16(state.load * 255 / 100),
            0x44: _u16(ratio * 32768), 0x45: _percent(state.throttle), 0x46: _byte(62),
            0x49: _percent(max(0, state.throttle - 10)), 0x4A: _percent(max(0, state.throttle - 10)),
            0x4C: _percent(state.throttle), 0x4D: _u16(10 if self._stored else 0),
            0x4E: _u16(since_clear / 60), 0x51: b"\x01", 0x52: _percent(10),
            0x5C: _byte(22 + 80 * (1 - math.exp(-state.elapsed / 180)) + 40),
            0x5E: _u16((state.maf / (14.7 * ratio) / 0.745 * 3.6) * 20),
            0x61: _byte(125 + state.load), 0x62: _byte(125 + state.load - 3), 0x63: _u16(400),
        }
        return values

    def _bitmap(self, base: int, values: dict[int, bytes]) -> bytes:
        identifiers = set(values)
        for page in (0x20, 0x40, 0x60):
            if any(identifier > page for identifier in values):
                identifiers.add(page)
        mask = sum(1 << (32 - (pid - base)) for pid in identifiers if base < pid <= base + 32)
        return mask.to_bytes(4, "big")

    def _obd(self, module: ModuleAddress, payload: bytes, state: DriveState) -> bytes:
        mode = payload[0]
        if mode in (3, 7, 10) and len(payload) == 1:
            codes = {3: self._stored, 7: self._pending, 10: self._permanent}[mode] if module.name == self._engine else []
            return bytes((mode + 0x40, len(codes))) + b"".join(codes)
        if mode == 4 and len(payload) == 1:
            self._stored.clear()
            self._pending.clear()
            self._module_codes.pop(self._engine, None)
            self._cleared_at = state.elapsed
            self._freeze = {}
            return b"\x44"
        if module.name != self._engine and mode not in (1,):
            raise NegativeResponse(mode, 0x11)
        if mode == 9 and payload == b"\x09\x02":
            return b"\x49\x02\x01" + self.vin.encode("ascii")
        if mode not in (1, 2) or len(payload) != (3 if mode == 2 else 2):
            raise NegativeResponse(mode, 0x11)
        if mode == 2 and payload[2] != 0:
            raise NegativeResponse(mode, 0x31)
        values = self._pid_values(state) if mode == 1 else self._freeze
        if module.name == "TCM":
            values = {pid: values[pid] for pid in (0x0C, 0x0D, 0x42)}
        pid = payload[1]
        if mode == 2 and pid == 2 and not values:
            data = b"\x00\x00"
        elif pid in (0, 0x20, 0x40, 0x60):
            data = self._bitmap(pid, values)
        elif pid in values and not (mode == 2 and pid == 1):
            data = values[pid]
        else:
            raise NegativeResponse(mode, 0x31)
        return bytes((mode + 0x40,)) + payload[1:] + data

    def _identifiers(self, module: ModuleAddress, state: DriveState) -> tuple[dict, dict]:
        part = f"28500-4G{module.tx_id & 0xFF:02X}A".encode("ascii")
        local = {0x83: part}
        dids = {0xF187: part, 0xF18C: f"SIM{module.tx_id:03X}0001".encode(), 0xF190: VIN.encode()}
        if module.name == "ECM":
            dids.update({0x1101: _byte(state.coolant + 50), 0x1201: _u16(state.rpm / 12.5),
                         0x1209: _u16(state.maf * 100), 0x120D: _u16((0.5 + state.throttle * 0.04) / 0.005),
                         0x120E: _u16((0.25 + state.throttle * 0.02) / 0.005), 0x121A: _u16(state.speed * 10)})
        elif module.name == "ABS":
            dids.update({0x1115: _percent(state.throttle), 0x1208: round(state.angle * 10).to_bytes(2, "big", signed=True),
                         0x1209: _u16(100 if state.elapsed % 90 >= 70 else 0)})
        elif module.name == "METER":
            local.update({1: round(85432 + state.elapsed / 180).to_bytes(3, "big") + _byte(state.speed),
                          2: _byte(35 * 4) + _byte(state.coolant + 40), 3: _u16(state.voltage * 100)})
        elif module.name == "EPS":
            torque = round(state.torque * 100).to_bytes(2, "big", signed=True)
            values = (torque, _u16(state.motor_current * 10), _u16(state.target_current * 10),
                      _u16(state.eps_voltage * 100), _byte(state.motor_temperature + 40))
            local[1] = self._eps_record(state)
            local.update(dict(enumerate(values[1:], 2)))
            dids.update(dict(enumerate(values, 0x1001)))
        elif module.name == "TCM":
            local.update({1: _byte(state.gear) + _byte(state.coolant + 40), 2: _byte(state.gear),
                          0x10: _u16(state.speed * 10), 0x11: _u16(state.rpm),
                          0x12: _byte(45 + state.coolant / 2)})
        return local, dids

    @staticmethod
    def _eps_record(state: DriveState) -> bytes:
        record = bytearray(EPS_RECORD_AT_REST)
        supply = state.eps_voltage if state.stiff else state.voltage
        driver_torque = max(-128, min(127, round(state.torque * 15)))
        record[0:2] = _u16(supply / 0.004)
        record[3:5] = round(state.angle / -0.1).to_bytes(2, "big", signed=True)
        record[7] = 0 if state.stiff else round(abs(driver_torque) / 2)
        record[8] = driver_torque & 0xFF
        record[10] = _byte(state.motor_temperature)[0]
        return bytes(record)

    def _diagnostic(self, module: ModuleAddress, payload: bytes, state: DriveState) -> bytes:
        if self._ford:
            return self._ford_diagnostic(module, payload, state)
        service = payload[0]
        if payload in (b"\x10\xC0", b"\x10\x81", b"\x10\x01", b"\x10\x03", b"\x10\x89"):
            return b"\x50" + payload[1:]
        if payload == b"\x3E\x00":
            return b"\x7E\x00"
        codes = self._module_codes.get(module.name, [])
        if module.name == "ECM":
            codes = [code + b"\x01" for code in self._stored]
        if payload == b"\x18\x00\xFF\x00":
            return bytes((0x58, len(codes))) + b"".join(codes)
        if payload == b"\x19\x02\xFF":
            return b"\x59\x02\xFF" + b"".join(code[:2] + b"\x00" + code[2:] for code in codes)
        if payload in (b"\x14\xFF\x00", b"\x14\xFF\xFF\xFF"):
            self._module_codes[module.name] = []
            if module.name == "ECM":
                self._obd(module, b"\x04", state)
            return b"\x54"
        local, dids = self._identifiers(module, state)
        if service == 0x1A and payload[1:] in (b"\x81", b"\x87"):
            return b"\x5A" + payload[1:] + local[0x83]
        table = local if service == 0x21 else dids
        expected_size = 2 if service == 0x21 else 3
        if service in (0x21, 0x22) and len(payload) == expected_size:
            identifier = int.from_bytes(payload[1:], "big")
            if identifier in table:
                return bytes((service + 0x40,)) + payload[1:] + table[identifier]
        raise NegativeResponse(service, 0x31)

    def _ford_diagnostic(self, module: ModuleAddress, payload: bytes, state: DriveState) -> bytes:
        service = payload[0]
        if payload in (b"\x10\x01", b"\x10\x03"):
            return b"\x50" + payload[1:] + b"\x00\x32\x01\xF4"
        if payload == b"\x3E\x00":
            return b"\x7E\x00"
        if payload[:2] == b"\x19\x02" and len(payload) == 3:
            return b"\x59\x02\xFF" + b"".join(code[:2] + b"\x00" + bytes((code[2] & payload[2],))
                                              for code in self._ford_codes(module) if code[2] & payload[2])
        if payload == b"\x14\xFF\xFF\xFF":
            self._module_codes[module.name] = []
            if module.name == self._engine:
                self._obd(module, b"\x04", state)
            return b"\x54"
        if service == 0x22 and len(payload) == 3:
            dids = sim_ford.identification(module.name, module.tx_id, self.vin)
            if module.name == self._engine:
                dids.update(sim_ford.pcm_dids(state, self._automatic))
            identifier = int.from_bytes(payload[1:], "big")
            if identifier in dids:
                return b"\x62" + payload[1:] + dids[identifier]
            raise NegativeResponse(service, 0x31)
        raise NegativeResponse(service, 0x12 if service == 0x10 else 0x11)

    def _ford_codes(self, module: ModuleAddress) -> list[bytes]:
        if module.name == self._engine:
            return [code + bytes((sim_ford.PCM_DTC_STATUS,)) for code in self._stored]
        return self._module_codes.get(module.name, [])

    def request(self, tx_id: int, rx_id: int, payload: bytes, timeout_s: float = 1.0,
                allow_clear: bool = False) -> bytes:
        check_request_allowed(payload, allow_clear)
        if self.request_latency_s:
            time.sleep(self.request_latency_s)
        with self._lock:
            self._require_open()
            module = next((module for module in self.modules if (module.tx_id, module.rx_id) == (tx_id, rx_id)), None)
            if module is None:
                raise NoResponse(f"No simulated module at {tx_id:03X}/{rx_id:03X}")
            self._tx_id, self._rx_id = tx_id, rx_id
            state = self._sample()
            if payload[0] <= 0x0A and module.name in self._obd_names:
                return self._obd(module, payload, state)
            return self._diagnostic(module, payload, state)

    def request_functional(self, payload: bytes, timeout_s: float = 1.0) -> dict[int, bytes]:
        check_request_allowed(payload)
        with self._lock:
            self._require_open()
            result = {}
            for module in (module for module in self.modules if module.name in self._obd_names):
                try:
                    result[module.rx_id] = self.request(module.tx_id, module.rx_id, payload, timeout_s)
                except NegativeResponse as error:
                    if error.code not in (0x11, 0x12, 0x31):
                        raise
                    LOGGER.debug("Simulated functional request unsupported by %s: %s", module.name, error)
            self._tx_id, self._rx_id = 0x7DF, None
            if not result:
                raise NoResponse("No simulated OBD module supports this request")
            return result

    def read_voltage(self) -> float:
        with self._lock:
            self._require_open()
            return self._sample().voltage

    def raw_command(self, line: str) -> str:
        command, payload = console_command(line)
        if payload is not None:
            check_request_allowed(payload)
        with self._lock:
            self._require_open()
            if payload is not None:
                if self._tx_id == 0x7DF:
                    return "\n".join(f"{can_id:03X} {data.hex(' ').upper()}"
                                     for can_id, data in self.request_functional(payload).items())
                module = next((module for module in self.modules if module.tx_id == self._tx_id), None)
                if module is None:
                    raise NoResponse(f"No simulated module at {self._tx_id:03X}")
                return self.request(module.tx_id, self._rx_id or module.rx_id, payload).hex(" ").upper()
            if command in ("ATI", "ATZ", "ATWS"):
                if command != "ATI":
                    self._tx_id, self._rx_id = 0x7DF, None
                    self.connection_generation += 1
                return "ELM327 v2.2 (simulated)"
            if command == "ATRV":
                return f"{self.read_voltage():.1f}V"
            if command == "ATDP":
                return self._protocol_text()
            if command == "AT@1":
                return self._description()
            if command.startswith("ATSH"):
                self._tx_id = int(command[4:], 16)
            elif command.startswith("ATCRA"):
                self._rx_id = int(command[5:], 16) if command[5:] else None
            elif command == "ATAR":
                self._rx_id = None
            return "OK" if command.startswith("AT") else "?"

    def _broadcast_data(self, can_id: int, state: DriveState) -> bytes:
        if can_id == 0x002:
            return round(state.angle / -0.1).to_bytes(2, "little", signed=True) + bytes(6)
        if can_id == 0x300:
            return _byte(1 if state.stiff else state.motor_current * 6) + bytes(7)
        if can_id == 0x174:
            return _byte(state.gear) + bytes(7)
        if can_id == 0x5E4:
            return bytes((int(state.stiff),)) + bytes(7)
        if can_id == 0x180:
            return _u16(state.rpm * 4) + _u16(state.speed * 100) + bytes(4)
        return _u16(state.voltage * 100) + _byte(state.coolant + 40) + bytes(5)

    def monitor(self, stop: threading.Event, can_ids: set[int] | None = None,
                max_duration_s: float | None = None) -> Iterator[CanFrame]:
        self._require_open()
        if self._ford:
            # No simulated Ford broadcast: wait out the window as an idle bus would.
            stop.wait(max_duration_s)
            return
        rates = dict(Q50_BROADCAST_RATES)
        if can_ids is not None:
            rates = {can_id: rate for can_id, rate in rates.items() if can_id in can_ids}
        if not rates:
            return
        start = time.monotonic()
        deadline = start + max_duration_s if max_duration_s is not None else math.inf
        due = dict.fromkeys(rates, start)
        while not stop.is_set() and time.monotonic() < deadline:
            self._require_open()
            now = time.monotonic()
            state = self._sample()
            for can_id, rate in rates.items():
                if stop.is_set() or time.monotonic() >= deadline:
                    return
                if now >= due[can_id]:
                    due[can_id] = now + 1 / rate
                    yield CanFrame(time.time(), can_id, self._broadcast_data(can_id, state))
            stop.wait(max(0, min(min(due.values()), deadline) - time.monotonic()))
