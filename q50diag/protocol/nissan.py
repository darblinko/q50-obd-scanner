"""Conservative Nissan KWP probes and parameters documented in the research notes."""

from __future__ import annotations

import logging
import threading
import weakref

from q50diag.core.transport import NegativeResponse, NoResponse, RequestBlocked, Transport
from q50diag.core.types import Dtc, ModuleAddress, ParameterDef
from q50diag.protocol.dtc import decode_kwp_dtc_response, decode_uds_dtc_response

LOGGER = logging.getLogger(__name__)
_sessions: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_session_lock = threading.RLock()
PROBE_FAILURES = (NoResponse, NegativeResponse, RequestBlocked)
# 7E/7F: (sub)function not supported in active session (ISO 14229);
# 80: service not supported in active diagnostic mode (ISO 14230).
WRONG_SESSION_NRCS = (0x7E, 0x7F, 0x80)


def reset_sessions() -> None:
    with _session_lock:
        _sessions.clear()


def ensure_session(t: Transport, module: ModuleAddress) -> None:
    """Enter session C0, else 03. Only a positive reply is cached, so a failed
    entry is attempted again before the next read."""
    with _session_lock:
        generation = getattr(t, "connection_generation", 0)
        saved_generation, addresses = _sessions.get(t, (generation, set()))
        if saved_generation != generation:
            addresses = set()
        if module.tx_id in addresses:
            return
        for request in (b"\x10\xc0", b"\x10\x03"):
            try:
                reply = t.request(module.tx_id, module.rx_id, request, timeout_s=0.3)
                if reply[:2] == bytes((0x50, request[1])):
                    addresses.add(module.tx_id)
                    break
                LOGGER.debug("Unexpected session response from %s: %s", module.name, reply.hex())
            except PROBE_FAILURES as error:
                LOGGER.debug("Session %s on %s: %s", request.hex(), module.name, error)
        _sessions[t] = (generation, addresses)


def forget_session(t: Transport, module: ModuleAddress) -> None:
    with _session_lock:
        entry = _sessions.get(t)
        if entry is not None:
            entry[1].discard(module.tx_id)


def request(
    t: Transport, module: ModuleAddress, payload: bytes, timeout_s: float = 1.0, allow_clear: bool = False
) -> bytes:
    """Send `payload` in the module's diagnostic session. A wrong-session NRC
    (the session expired or a 10 81 returned the module to default) clears the
    cached entry, re-enters the session and retries once."""
    ensure_session(t, module)
    try:
        return t.request(module.tx_id, module.rx_id, payload, timeout_s=timeout_s, allow_clear=allow_clear)
    except NegativeResponse as error:
        if error.code not in WRONG_SESSION_NRCS:
            raise
    forget_session(t, module)
    ensure_session(t, module)
    return t.request(module.tx_id, module.rx_id, payload, timeout_s=timeout_s, allow_clear=allow_clear)


def read_identification(t: Transport, module: ModuleAddress) -> dict[str, str]:
    probes = (
        ("Local identification", b"\x21\x83"),
        ("ECU identification", b"\x1a\x81"),
        ("Part number", b"\x1a\x87"),
        ("Manufacturer part number", b"\x22\xf1\x87"),
        ("Serial number", b"\x22\xf1\x8c"),
        ("VIN", b"\x22\xf1\x90"),
    )
    result = {}
    for label, probe in probes:
        try:
            reply = request(t, module, probe, timeout_s=0.3)
            expected = bytes((probe[0] + 0x40,)) + probe[1:]
            if not reply.startswith(expected):
                LOGGER.warning("Unexpected identification echo from %s: %s", module.name, reply.hex())
                continue
            data = reply[len(expected) :]
            printable = "".join(chr(value) if 32 <= value < 127 else "." for value in data)
            result[label] = f"{printable} [{data.hex(' ').upper()}]"
        except PROBE_FAILURES as error:
            LOGGER.debug("Identification %s on %s: %s", label, module.name, error)
    return result


def read_module_dtcs(t: Transport, module: ModuleAddress) -> list[Dtc]:
    """DTCs from KWP 18, falling back to UDS 19. Raises when neither service
    returns a decodable reply: NoResponse when both time out, otherwise the
    rejection or decode error, so a failed read is never an empty result."""
    probes = ((b"\x18\x00\xff\x00", decode_kwp_dtc_response), (b"\x19\x02\xff", decode_uds_dtc_response))
    failure: Exception | None = None
    for probe, decode in probes:
        try:
            return decode(request(t, module, probe), module.name)
        except NoResponse as error:
            LOGGER.debug("DTC probe %s on %s: %s", probe.hex(), module.name, error)
        except (NegativeResponse, RequestBlocked, ValueError) as error:
            LOGGER.debug("DTC probe %s on %s: %s", probe.hex(), module.name, error)
            failure = error
    if failure is None:
        raise NoResponse(f"{module.name} did not answer any DTC request")
    raise failure


def clear_module_dtcs(t: Transport, module: ModuleAddress) -> None:
    last_error: Exception | None = None
    for probe in (b"\x14\xff\x00", b"\x14\xff\xff\xff"):
        try:
            reply = request(t, module, probe, allow_clear=True)
            if not reply or reply[0] != 0x54:
                raise ValueError(f"Unexpected DTC clear response from {module.name}: {reply.hex()}")
            return
        except (NoResponse, NegativeResponse) as error:
            last_error = error
            LOGGER.debug("DTC clear %s on %s: %s", probe.hex(), module.name, error)
    if last_error is not None:
        raise last_error


def _scaled(size: int, scale: float = 1, offset: float = 0, signed: bool = False):
    def decode(data: bytes) -> float:
        if len(data) < size:
            raise ValueError(f"Expected at least {size} data bytes, got {len(data)}")
        return int.from_bytes(data[:size], "big", signed=signed) * scale + offset

    return decode


def _parameter(
    module: ModuleAddress,
    did: int,
    name: str,
    unit: str,
    category: str,
    decode,
    minimum=None,
    maximum=None,
    description="",
    service=0x22,
) -> ParameterDef:
    width = 2 if service == 0x22 else 1
    return ParameterDef(
        f"{module.name}.{service:02X}.{did:0{width * 2}X}",
        name,
        unit,
        module.name,
        category,
        bytes((service,)) + did.to_bytes(width, "big"),
        decode,
        minimum,
        maximum,
        description,
    )


def module_parameters(module: ModuleAddress) -> list[ParameterDef]:
    specifications = {
        "ECM": (
            (0x1101, "Coolant temperature", "degC", "Engine", _scaled(1, 1, -50), -50, 205),
            (0x1201, "Engine RPM", "rpm", "Engine", _scaled(2, 12.5), 0, 8000),
            (0x1209, "Mass air flow", "g/s", "Air", _scaled(2, 0.01), 0, 655.35),
            (0x120D, "Accelerator sensor 1", "V", "Electrical", _scaled(2, 0.005), 0, 5),
            (0x120E, "Accelerator sensor 2", "V", "Electrical", _scaled(2, 0.005), 0, 5),
            (0x121A, "Vehicle speed", "km/h", "Vehicle", _scaled(2, 0.1), 0, 300),
        ),
        "ABS": (
            (0x1208, "Steering angle", "deg", "Steering", _scaled(2, 0.1, signed=True), -780, 780),
            (0x1209, "Master cylinder pressure (raw)", "", "Vehicle", bytes.hex, None, None),
            (0x1115, "Accelerator pedal (raw)", "", "Vehicle", bytes.hex, None, None),
        ),
    }
    result = [
        _parameter(module, *spec, description="Nissan identifier; Q50 encoding unverified unless captured.")
        for spec in specifications.get(module.name, ())
    ]
    # Local IDs that answered a 21 00-FF scan on a 2017 Q50 3.0t (2026-10-17). Contents undecoded.
    local_ids = {
        "TCM": (0x01, 0x02, 0x10, 0x11, 0x12),
        "METER": (0x01, 0x02, 0x03),
        "EPS": (0x01,),
        "STEERING ANGLE": (0x01,),
    }
    for identifier in local_ids.get(module.name, ()):
        angle = module.name == "STEERING ANGLE"
        result.append(
            _parameter(
                module,
                identifier,
                "Steering angle" if angle else f"Local data {identifier:02X} (raw)",
                "deg" if angle else "",
                "Steering" if angle else "Vehicle",
                _scaled(2, 0.1, signed=True) if angle else bytes.hex,
                description="Answered on a 2017 Q50; layout undecoded.",
                service=0x21,
            )
        )
    if module.name == "EPS":
        result += eps_fields(module)
    return result


def _eps_field(
    module: ModuleAddress, suffix: str, name: str, unit: str, decode, minimum=None, maximum=None, description: str = ""
) -> ParameterDef:
    return ParameterDef(
        f"{module.name}.21.01.{suffix}",
        name,
        unit,
        module.name,
        "Steering",
        b"\x21\x01",
        decode,
        minimum,
        maximum,
        description,
    )


def _byte(index: int):
    def decode(data: bytes) -> int:
        if len(data) <= index:
            raise ValueError(f"Expected at least {index + 1} data bytes, got {len(data)}")
        return data[index]

    return decode


def _word(index: int, scale: float = 1, signed: bool = False):
    def decode(data: bytes) -> float:
        if len(data) < index + 2:
            raise ValueError(f"Expected at least {index + 2} data bytes, got {len(data)}")
        return int.from_bytes(data[index : index + 2], "big", signed=signed) * scale

    return decode


def _signed_byte(index: int):
    def decode(data: bytes) -> int:
        value = _byte(index)(data)
        return value - 256 if value > 127 else value

    return decode


def eps_fields(module: ModuleAddress) -> list[ParameterDef]:
    """EPS 21 01 fields from 2017 Q50 captures (2026-10-17): 97 samples at rest and 215 samples
    during lock-to-lock turns, compared against ABS 22 1208 steering angle and its rate."""
    note = "Inferred from a wheel-turn capture; scale unconfirmed."
    return [
        _eps_field(
            module,
            "supply",
            "EPS supply voltage (bytes 0-1 x 4 mV)",
            "V",
            _word(0, 0.004),
            0,
            20,
            "14.0-14.25 V at rest (ATRV 14.0 V); dipped to 13.26 V under full-lock load.",
        ),
        _eps_field(
            module,
            "angle",
            "EPS steering angle (bytes 3-4)",
            "deg",
            _word(3, -0.1, signed=True),
            -780,
            780,
            "Equals ABS 22 1208 at rest; correlation -1.00 with it during turns.",
        ),
        _eps_field(
            module,
            "velocity",
            "EPS steering velocity (bytes 5-6, raw)",
            "",
            _word(5, signed=True),
            -200,
            200,
            "About -3.7 deg/s per count; 0 when the wheel is held. " + note,
        ),
        _eps_field(
            module,
            "assist",
            "EPS assist load (byte 7, raw)",
            "",
            _byte(7),
            0,
            255,
            "0 at rest, 25 pushing into full lock, tracks supply voltage dip. " + note,
        ),
        _eps_field(
            module,
            "torque",
            "EPS driver torque (byte 8, signed raw)",
            "",
            _signed_byte(8),
            -128,
            127,
            "Positive pushing left, negative pushing right; held at 49 against left lock. " + note,
        ),
        _eps_field(
            module,
            "b10",
            "EPS byte 10 (raw, temperature candidate)",
            "",
            _byte(10),
            0,
            255,
            "Rose 45 to 53 as the car warmed up. " + note,
        ),
        _eps_field(
            module,
            "b11",
            "EPS byte 11 (raw, assist limit candidate)",
            "",
            _byte(11),
            0,
            255,
            "100 in every sample, including full-lock load. " + note,
        ),
        _eps_field(
            module,
            "motor",
            "EPS bytes 15-16 (signed raw, motor speed candidate)",
            "",
            _word(15, signed=True),
            -32768,
            32767,
            "About -3 counts per deg/s of steering rate. " + note,
        ),
    ]
