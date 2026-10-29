"""Ford UDS (ISO 14229) reads for the S550 Mustang modules.

Reads use ReadDataByIdentifier (22), ReadDTCInformation by status mask (19 02)
and, only with allow_clear, ClearDiagnosticInformation (14 FF FF FF). Every
read starts in the default session. A module that refuses a read with NRC 0x7E
or 0x7F (not supported in the active session) gets one extended session
request (10 03) and one retry.

No S550-specific DID table is public. Each ParameterDef description states the
source and confidence of its identifier and formula."""

from __future__ import annotations

import logging
import threading
import weakref

from q50diag.core.transport import NegativeResponse, NoResponse, RequestBlocked, Transport
from q50diag.core.types import Dtc, ModuleAddress, ParameterDef
from q50diag.data.dtc_descriptions import FORD_DESCRIPTIONS, describe_with
from q50diag.protocol.dtc import decode_uds_dtc_response

LOGGER = logging.getLogger(__name__)
PROBE_FAILURES = (NoResponse, NegativeResponse, RequestBlocked)
WRONG_SESSION_NRCS = (0x7E, 0x7F)
DTC_STATUS_MASK = 0xFF
IDENTIFICATION_DIDS = (("VIN", 0xF190), ("Software part number", 0xF188), ("ECU part number", 0xF113))
_extended: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_lock = threading.RLock()


def describe(code: str) -> str:
    return describe_with(code, FORD_DESCRIPTIONS)


def reset_sessions() -> None:
    with _lock:
        _extended.clear()


def ensure_session(t: Transport, module: ModuleAddress) -> None:
    """Ford modules answer 22 and 19 in the default session; nothing to start."""


def _enter_extended(t: Transport, module: ModuleAddress) -> None:
    with _lock:
        generation = getattr(t, "connection_generation", 0)
        saved, addresses = _extended.get(t, (generation, set()))
        if saved != generation:
            addresses = set()
        if module.tx_id in addresses:
            return
        try:
            t.request(module.tx_id, module.rx_id, b"\x10\x03", timeout_s=0.3)
        except PROBE_FAILURES as error:
            LOGGER.debug("Extended session on %s: %s", module.name, error)
        addresses.add(module.tx_id)
        _extended[t] = (generation, addresses)


def request(t: Transport, module: ModuleAddress, payload: bytes, timeout_s: float = 1.0,
            allow_clear: bool = False) -> bytes:
    """Send `payload`; retry once in the extended session if the module asks for it."""
    try:
        return t.request(module.tx_id, module.rx_id, payload, timeout_s=timeout_s, allow_clear=allow_clear)
    except NegativeResponse as error:
        if error.code not in WRONG_SESSION_NRCS:
            raise
    _enter_extended(t, module)
    return t.request(module.tx_id, module.rx_id, payload, timeout_s=timeout_s, allow_clear=allow_clear)


def read_identification(t: Transport, module: ModuleAddress) -> dict[str, str]:
    result = {}
    for label, did in IDENTIFICATION_DIDS:
        payload = b"\x22" + did.to_bytes(2, "big")
        try:
            reply = request(t, module, payload, timeout_s=0.3)
        except PROBE_FAILURES as error:
            LOGGER.debug("Identification %s on %s: %s", label, module.name, error)
            continue
        echo = b"\x62" + payload[1:]
        if not reply.startswith(echo):
            LOGGER.warning("Unexpected identification echo from %s: %s", module.name, reply.hex())
            continue
        data = reply[len(echo):]
        printable = "".join(chr(value) if 32 <= value < 127 else "." for value in data)
        result[label] = f"{printable} [{data.hex(' ').upper()}]"
    return result


def read_module_dtcs(t: Transport, module: ModuleAddress) -> list[Dtc]:
    reply = request(t, module, bytes((0x19, 0x02, DTC_STATUS_MASK)))
    return decode_uds_dtc_response(reply, module.name, describe)


def clear_module_dtcs(t: Transport, module: ModuleAddress) -> None:
    reply = request(t, module, b"\x14\xFF\xFF\xFF", allow_clear=True)
    if not reply or reply[0] != 0x54:
        raise ValueError(f"Unexpected DTC clear response from {module.name}: {reply.hex()}")


def format_dtc(dtc: Dtc) -> str:
    """"P0300-00 (status AC)": 3-byte UDS code plus the raw ISO 14229 status byte."""
    return dtc.code if dtc.status is None else f"{dtc.code} (status {dtc.status:02X})"


# Decoders take the data bytes after the 62 DID echo -------------------------------


def _need(data: bytes, size: int) -> None:
    if len(data) < size:
        raise ValueError(f"Expected at least {size} data bytes, got {len(data)}")


def decode_transmission_temp(data: bytes) -> float:
    _need(data, 2)
    return int.from_bytes(data[:2], "big", signed=True) / 16


def decode_knock_retard(data: bytes) -> float:
    _need(data, 2)
    return int.from_bytes(data[:1], "big", signed=True) / 2 + data[1] / 512


def decode_temp_offset_40(data: bytes) -> float:
    _need(data, 1)
    return data[0] - 40


def decode_percent_255(data: bytes) -> float:
    _need(data, 1)
    return data[0] * 100 / 255


def decode_battery_voltage(data: bytes) -> float:
    _need(data, 2)
    return int.from_bytes(data[:2], "big") / 2048


def decode_percent(data: bytes) -> float:
    _need(data, 1)
    return data[0]


MIRROR_NOTE = ("Confidence: inferred. OBD-on-UDS mirror of a Mode 01 PID (DID F4xx = PID xx, "
               "SAE J1979-2); not confirmed on an S550.")
UNVERIFIED_NOTE = "Confidence: unverified. Single source (Ford Focus RS); not confirmed on an S550."
# (did, name, unit, category, decode, min, max, description)
PCM_DIDS = (
    (0xF405, "Coolant temperature (DID F405)", "degC", "Engine", decode_temp_offset_40, -40, 215, MIRROR_NOTE),
    (0xF40F, "Intake air temperature (DID F40F)", "degC", "Air", decode_temp_offset_40, -40, 215, MIRROR_NOTE),
    (0xF42F, "Fuel level (DID F42F)", "%", "Fuel", decode_percent_255, 0, 100, MIRROR_NOTE),
    (0x03EC, "Ignition correction cylinder 1 (knock retard)", "deg", "Engine", decode_knock_retard, -10, 10,
     "Formula signed(A)/2 + B/512 deg. Confidence: low. Sources disagree on the sign: "
     "some show retard as negative, others as positive."),
    (0x0304, "Battery voltage (PCM)", "V", "Electrical", decode_battery_voltage, 0, 20,
     "Formula (A*256+B)/2048 V. " + UNVERIFIED_NOTE),
    (0x054B, "Oil life remaining", "%", "Engine", decode_percent, 0, 100, "Formula A %. " + UNVERIFIED_NOTE),
)
TRANSMISSION_DID = (
    0x1E1C, "Transmission fluid temperature (6R80)", "degC", "Transmission", decode_transmission_temp, -30, 150,
    "Formula signed(A*256+B)/16 degC. Confidence: medium; several sources including an S550 owner "
    "report. 6R80 automatic only: a manual car does not answer.",
)


def _parameter(module: ModuleAddress, did: int, name: str, unit: str, category: str, decode,
               minimum, maximum, description: str) -> ParameterDef:
    return ParameterDef(f"{module.name}.22.{did:04X}", name, unit, module.name, category,
                        b"\x22" + did.to_bytes(2, "big"), decode, minimum, maximum, description)


def pcm_parameters(module: ModuleAddress, automatic: bool) -> list[ParameterDef]:
    specs = PCM_DIDS + ((TRANSMISSION_DID,) if automatic else ())
    return [_parameter(module, *spec) for spec in specs]
