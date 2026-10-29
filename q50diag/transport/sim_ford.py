"""Simulated Ford S550 module data for SimulatedTransport.

Every value is synthetic. Identification strings start with "SIM" so that a
simulated module is never mistaken for a real one. The PCM DIDs encode the
formulas in q50diag.protocol.ford so the decoders can be exercised end to end."""

from __future__ import annotations

import math

# (hi, lo, status) per module, sent as a 19 02 record with failure type 00.
# U0100 lost communication with ECM/PCM A, U0121 lost communication with ABS.
MODULE_CODES = {"ABS": [b"\xC1\x00\x28"], "IPC": [b"\xC1\x21\x2C"]}
# Status for the PCM's stored OBD-II codes: pending, confirmed, failed since clear, MIL requested.
PCM_DTC_STATUS = 0xAC
AT_REST_OIL_LIFE = 73
AT_REST_FUEL_PERCENT = 62


def _byte(value: float) -> bytes:
    return bytes((max(0, min(255, round(value))),))


def _u16(value: float) -> bytes:
    return max(0, min(65535, round(value))).to_bytes(2, "big")


def _knock(degrees: float) -> bytes:
    """Inverse of signed(A)/2 + B/512."""
    whole = math.floor(degrees * 2)
    fraction = round((degrees - whole / 2) * 512)
    return whole.to_bytes(1, "big", signed=True) + _byte(fraction)


def identification(module_name: str, tx_id: int, vin: str) -> dict[int, bytes]:
    return {
        0xF190: vin.encode("ascii"),
        0xF188: f"SIM-FR3A-{module_name}-AA".encode("ascii"),
        0xF113: f"SIM-{tx_id:03X}-PN".encode("ascii"),
    }


def pcm_dids(state, automatic: bool) -> dict[int, bytes]:
    """`state` is a sim DriveState."""
    retard = -1.5 * max(0.0, state.load - 60) / 25
    dids = {
        0xF405: _byte(state.coolant + 40),
        0xF40F: _byte(state.intake + 40),
        0xF42F: _byte(AT_REST_FUEL_PERCENT * 255 / 100),
        0x03EC: _knock(retard),
        0x0304: _u16(state.voltage * 2048),
        0x054B: _byte(AT_REST_OIL_LIFE),
    }
    if automatic:
        fluid = 25 + 60 * (1 - math.exp(-state.elapsed / 300))
        dids[0x1E1C] = round(fluid * 16).to_bytes(2, "big", signed=True)
    return dids
