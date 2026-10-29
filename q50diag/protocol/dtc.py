"""DTC wire formats. Nissan KWP status bit 0 is provisionally treated as active."""

from collections.abc import Callable

from q50diag.core.types import Dtc
from q50diag.data.dtc_descriptions import describe

DescribeFn = Callable[[str], str]


def decode_two_byte(hi: int, lo: int) -> str:
    if not (0 <= hi <= 255 and 0 <= lo <= 255):
        raise ValueError("DTC bytes must be between 0 and 255")
    return f"{'PCBU'[hi >> 6]}{(hi >> 4) & 3}{hi & 15:X}{lo:02X}"


def decode_kwp_dtc_response(payload: bytes, module_name: str, describe_fn: DescribeFn = describe) -> list[Dtc]:
    if len(payload) < 2 or payload[0] != 0x58:
        raise ValueError("Expected a 58 count KWP DTC response")
    count = payload[1]
    if len(payload) < 2 + count * 3:
        raise ValueError("Truncated KWP DTC response")
    result = []
    for offset in range(2, 2 + count * 3, 3):
        hi, lo, status = payload[offset : offset + 3]
        if hi == lo == 0:
            continue
        code = decode_two_byte(hi, lo)
        kind = "current" if status & 1 else "history"
        result.append(Dtc(code, describe_fn(code), module_name, kind, status))
    return result


def decode_uds_dtc_response(payload: bytes, module_name: str, describe_fn: DescribeFn = describe) -> list[Dtc]:
    if len(payload) < 3 or payload[:2] != b"\x59\x02":
        raise ValueError("Expected a 59 02 mask UDS DTC response")
    if (len(payload) - 3) % 4:
        raise ValueError("Truncated UDS DTC response")
    result = []
    for offset in range(3, len(payload), 4):
        hi, lo, failure_type, status = payload[offset : offset + 4]
        if hi == lo == failure_type == 0:
            continue
        base = decode_two_byte(hi, lo)
        code = f"{base}-{failure_type:02X}"
        kind = uds_status_kind(status)
        if kind is not None:
            result.append(Dtc(code, describe_fn(base), module_name, kind, status))
    return result


def uds_status_kind(status: int) -> str | None:
    """ISO 14229 status byte. Codes whose only bits are "test not completed"
    (0x10, 0x40) are supported-but-not-failed and are not faults."""
    if status & 0x01:
        return "current"
    if status & 0x08:
        return "stored"
    if status & 0x04:
        return "pending"
    if status & 0x20:
        return "history"
    return None
