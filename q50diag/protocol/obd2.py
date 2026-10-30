"""SAE J1979 reads on ISO 15765-4 CAN, using metric display units."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import replace
from functools import partial

from q50diag.core.transport import NegativeResponse, NoResponse, Transport, TransportError
from q50diag.core.types import Dtc, MonitorStatus, ParameterDef, Reading
from q50diag.data.dtc_descriptions import describe
from q50diag.protocol.dtc import decode_two_byte
from q50diag.protocol.obd_extended_data import EXTENDED_FIELDS
from q50diag.protocol.obd_fields import Field, decode_fields
from q50diag.protocol.obd_pid_data import PID_FIELDS

LOGGER = logging.getLogger(__name__)
ECM_TX, ECM_RX = 0x7E0, 0x7E8
BITMAP_PIDS = tuple(range(0, 0xE1, 0x20))


def _category(pid: int, name: str) -> str:
    name = name.lower()
    if pid in (1, 2, 3, 0x13, 0x1C, 0x1D, 0x1E, 0x41, 0x51) or "supported" in name:
        return "Status"
    if any(word in name for word in ("battery", "voltage", "generator", "charging", "electric motor")):
        return "Electrical"
    if any(word in name for word in ("fuel", "torque", "injection", "lambda", "equivalence")):
        return "Fuel" if "torque" not in name else "Engine"
    if any(
        word in name for word in ("catalyst", "oxygen", "o2", "nox", "evap", "egr", "particulate", "reagent", "def ")
    ):
        return "Emissions"
    if any(word in name for word in ("air", "boost", "turbo", "manifold", "wastegate")):
        return "Air"
    if any(word in name for word in ("speed", "distance", "odometer", "gear", "ambient")):
        return "Vehicle"
    return "Engine"


def _definition(pid: int, fields: tuple[Field, ...]) -> ParameterDef:
    name = fields[0].name if len(fields) == 1 else _COMPOUND_NAMES.get(pid, fields[0].name + " / related values")
    single = len(fields) == 1
    unit = fields[0].unit if single else ""
    low, high = fields[0].bounds() if single else (None, None)
    description = f"SAE J1979 PID {pid:02X}: {name}."
    if not single:
        description += " Multiple values are returned as labeled text."
    return ParameterDef(
        f"obd.01.{pid:02X}",
        name,
        unit,
        "ECM",
        _category(pid, name),
        bytes((1, pid)),
        partial(decode_fields, fields),
        low,
        high,
        description,
    )


_COMPOUND_NAMES = {
    1: "Monitor status since DTCs cleared",
    3: "Fuel system status",
    0x13: "Oxygen sensors present (two banks)",
    0x1D: "Oxygen sensors present (four banks)",
    0x41: "Monitor status this drive cycle",
    0x4F: "Maximum sensor values",
    0x64: "Engine torque at reference points",
    0x65: "Auxiliary inputs/outputs",
    0x66: "Mass air flow sensors",
    0x67: "Coolant temperature sensors",
    0x68: "Intake air temperature sensors",
    0x70: "Boost pressure control",
    0x7F: "Engine run time counters",
    0xA4: "Transmission gear and ratio",
}

# These fields are absent from the OBDb catalog. Names are this project's own
# wording; bit indices here are MSB-first.
PID_FIELDS[0x6F] = (
    Field("Turbo A inlet pressure", 8, 8, 1, 0, "kPa", support=7, wide_support=5, wide_scale=8),
    Field("Turbo B inlet pressure", 16, 8, 1, 0, "kPa", support=6, wide_support=4, wide_scale=8),
)
PID_FIELDS[0x7D] = tuple(
    Field(name, 7 - index, 1, 1, 0, "", choices=((0, "No"), (1, "Yes")))
    for index, name in enumerate(
        ("In NOx zone", "Out of NOx zone", "NOx exempt zone", "NOx NTE shortfall")
    )
)
EXTENDED_FIELDS[0x87] = (
    Field("Intake manifold pressure 1", 8, 16, 1 / 32, 0, "kPa", support=7),
    Field("Intake manifold pressure 2", 24, 16, 1 / 32, 0, "kPa", support=6),
)
for _pid, _bank in ((0x98, 1), (0x99, 2)):
    EXTENDED_FIELDS[_pid] = tuple(
        Field(f"EGT bank {_bank} sensor {index + 5}", 8 + 16 * index, 16, 0.1, -40, "degC", support=7 - index)
        for index in range(4)
    )
PID_FIELDS[0x6B] = tuple(
    replace(field, wide_support=3 - index, wide_scale=4) for index, field in enumerate(PID_FIELDS[0x6B])
)
MODE01_PIDS: dict[int, ParameterDef] = {
    pid: _definition(pid, fields) for pid, fields in (PID_FIELDS | EXTENDED_FIELDS).items() if fields
}


def _bitmap_text(data: bytes) -> str:
    if len(data) < 4:
        raise ValueError("Truncated supported-PID bitmap")
    return f"0x{int.from_bytes(data[:4], 'big'):08X}"


for _pid in BITMAP_PIDS:
    MODE01_PIDS[_pid] = ParameterDef(
        f"obd.01.{_pid:02X}",
        f"Supported PIDs {_pid + 1:02X}-{_pid + 32:02X}",
        "",
        "ECM",
        "Status",
        bytes((1, _pid)),
        _bitmap_text,
        description="Each set bit advertises one readable PID.",
    )


def _freeze_dtc(data: bytes) -> str:
    if len(data) < 2:
        raise ValueError("Truncated freeze-frame DTC")
    return decode_two_byte(data[0], data[1]) if any(data[:2]) else "None"


MODE01_PIDS[2] = replace(MODE01_PIDS[2], decode=_freeze_dtc, unit="", min_value=None, max_value=None)


def _physical_data(t: Transport, request: bytes, minimum: int = 0) -> bytes:
    reply = t.request(ECM_TX, ECM_RX, request)
    echo = bytes((request[0] + 0x40,)) + request[1:]
    if not reply.startswith(echo) or len(reply) < len(echo) + minimum:
        raise TransportError(f"Malformed response to {request.hex()}: {reply.hex()}")
    return reply[len(echo) :]


def _supported(t: Transport, mode: int) -> list[int]:
    supported = []
    for base in range(0, 0xC1, 0x20):
        request = bytes((mode, base)) + (b"\x00" if mode == 2 else b"")
        try:
            data = _physical_data(t, request, 4)
        except (NoResponse, NegativeResponse) as error:
            LOGGER.debug("Supported PID page %02X: %s", base, error)
            break
        mask = int.from_bytes(data[:4], "big")
        supported.extend(
            base + offset for offset in range(1, 33) if mask & (1 << (32 - offset)) and (base + offset) % 32
        )
        if not mask & 1:
            break
    return supported


def supported_pids(t: Transport) -> list[int]:
    return _supported(t, 1)


def _monitor_state(supported: bool, incomplete: bool) -> str:
    return ("incomplete" if incomplete else "complete") if supported else "n/a"


def read_monitor_status(t: Transport) -> MonitorStatus:
    data = _physical_data(t, b"\x01\x01", 4)
    a, b, c, d = data[:4]
    monitors = {
        name: _monitor_state(bool(b & (1 << bit)), bool(b & (1 << (bit + 4))))
        for bit, name in enumerate(("Misfire", "Fuel system", "Components"))
    }
    if b & 8:
        names = {
            0: "NMHC catalyst",
            1: "NOx/SCR",
            3: "Boost pressure",
            5: "Exhaust gas sensor",
            6: "PM filter",
            7: "EGR/VVT",
        }
    else:
        names = dict(
            enumerate(
                (
                    "Catalyst",
                    "Heated catalyst",
                    "Evaporative system",
                    "Secondary air",
                    "A/C refrigerant",
                    "Oxygen sensor",
                    "Oxygen sensor heater",
                    "EGR/VVT",
                )
            )
        )
    monitors.update({name: _monitor_state(bool(c & (1 << bit)), bool(d & (1 << bit))) for bit, name in names.items()})
    return MonitorStatus(bool(a & 0x80), a & 0x7F, monitors)


def _dtcs_from_can(payload: bytes, mode: int, module: str, kind: str) -> list[Dtc]:
    if len(payload) < 2 or payload[0] != mode + 0x40:
        raise TransportError("Malformed OBD DTC response")
    count = payload[1]
    if len(payload) < 2 + count * 2:
        raise TransportError("Truncated OBD DTC response")
    result = []
    for offset in range(2, 2 + count * 2, 2):
        hi, lo = payload[offset : offset + 2]
        if hi == lo == 0:
            continue
        code = decode_two_byte(hi, lo)
        result.append(Dtc(code, describe(code), module, kind))
    return result


DTC_MODES = ((3, "stored"), (7, "pending"), (10, "permanent"))


def _functional_with_failures(t: Transport, payload: bytes) -> tuple[dict[int, bytes], dict[int, Exception]]:
    method = getattr(t, "request_functional_with_failures", None)
    if method is None:
        return t.request_functional(payload), {}
    return method(payload)


def _dtc_module_name(can_id: int) -> str:
    return {0x7E8: "ECM", 0x7E9: "TCM"}.get(can_id, f"0x{can_id:03X}")


def read_dtcs_by_mode(t: Transport) -> tuple[list[Dtc], dict[str, str]]:
    """DTCs from modes 03, 07 and 0A, and {kind: reason} for every mode that
    failed, or that some ECU rejected or answered with a malformed reply while
    another ECU answered. Raises when no mode produced a usable reply, so a
    failed read is never an empty result."""
    result: list[Dtc] = []
    failures: dict[str, str] = {}
    errors: list[Exception] = []
    for mode, kind in DTC_MODES:
        try:
            responses, ecu_failures = _functional_with_failures(t, bytes((mode,)))
        except (NoResponse, NegativeResponse) as error:
            LOGGER.debug("DTC mode %02X unavailable: %s", mode, error)
            failures[kind] = str(error)
            errors.append(error)
            continue
        problems = [f"{_dtc_module_name(can_id)}: {error}" for can_id, error in sorted(ecu_failures.items())]
        usable = 0
        for can_id, payload in sorted(responses.items()):
            try:
                result.extend(_dtcs_from_can(payload, mode, _dtc_module_name(can_id), kind))
                usable += 1
            except TransportError as error:  # malformed reply from this ECU only
                problems.append(f"{_dtc_module_name(can_id)}: {error}")
        if problems:
            failures[kind] = "; ".join(problems)
        if not usable:
            errors.append(NoResponse(f"mode {mode:02X}: no usable reply ({failures[kind]})"))
    if len(errors) == len(DTC_MODES):
        rejection = next((error for error in errors if isinstance(error, NegativeResponse)), None)
        raise rejection or NoResponse("No OBD-II DTC mode answered: " + "; ".join(map(str, errors)))
    return result, failures


def read_dtcs(t: Transport) -> list[Dtc]:
    return read_dtcs_by_mode(t)[0]


def clear_dtcs(t: Transport) -> None:
    reply = t.request(ECM_TX, ECM_RX, b"\x04", allow_clear=True)
    if not reply or reply[0] != 0x44:
        raise TransportError("Unexpected OBD DTC clear response")


def read_vin(t: Transport) -> str | None:
    try:
        data = _physical_data(t, b"\x09\x02", 18)
    except (NoResponse, NegativeResponse) as error:
        LOGGER.debug("VIN unavailable: %s", error)
        return None
    vin = data[1:18].decode("ascii", errors="replace")
    if data[0] != 1 or not re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", vin):
        raise TransportError("Invalid VIN response")
    return vin


def read_freeze_frame(t: Transport) -> list[Reading]:
    try:
        trigger = _physical_data(t, b"\x02\x02\x00", 2)
    except (NoResponse, NegativeResponse) as error:
        LOGGER.debug("Freeze frame unavailable: %s", error)
        return []
    if not any(trigger[:2]):
        return []
    readings = []
    for pid in _supported(t, 2):
        if pid not in MODE01_PIDS or pid == 1:
            continue
        param = MODE01_PIDS[pid]
        request = bytes((2, pid, 0))
        data = b""
        try:
            data = _physical_data(t, request)
            readings.append(
                Reading(f"obd.02.{pid:02X}", param.decode(data), param.unit, time.time(), bytes((0x42, pid, 0)) + data)
            )
        except (NoResponse, NegativeResponse, ValueError, IndexError) as error:
            readings.append(Reading(f"obd.02.{pid:02X}", None, param.unit, time.time(), data, str(error)))
    return readings
