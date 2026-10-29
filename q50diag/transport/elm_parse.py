"""Pure ELM line parsing and 11-bit classical CAN ISO-TP reassembly."""

from __future__ import annotations

import re
from collections.abc import Iterable

from q50diag.core.transport import AdapterReplyError, NegativeResponse, NoResponse, RequestBlocked, TransportError


def response_lines(text: str, command: str = "") -> list[str]:
    echo = "".join(command.upper().split())
    lines = re.split(r"[\r\n>]", text)
    return [line.strip() for line in lines if line.strip() and "".join(line.upper().split()) != echo]


def check_status(line: str, allow_stopped: bool = False) -> None:
    upper = line.strip().upper()
    if "NO DATA" in upper:
        raise NoResponse("ELM: NO DATA")
    if "LV RESET" in upper:
        raise TransportError(f"ELM: {line.strip()} (adapter reset itself)")
    errors = (
        "CAN ERROR",
        "BUFFER FULL",
        "UNABLE TO CONNECT",
        "BUS ERROR",
        "DATA ERROR",
        "RX ERROR",
        "FB ERROR",
        "OUT OF MEMORY",
    )
    if upper == "?" or any(error in upper for error in errors):
        raise AdapterReplyError(f"ELM: {line.strip()}")
    if upper == "STOPPED" and not allow_stopped:
        raise AdapterReplyError("ELM: STOPPED")
    if upper.startswith("BUS INIT") and "ERROR" in upper:
        raise AdapterReplyError(f"ELM: {line.strip()}")


def parse_can_line(line: str) -> tuple[int, bytes] | None:
    """Accept ATH1 with ATS0/1 and optional one-nibble DLC (ATD1)."""
    compact = "".join(line.split()).upper()
    if not re.fullmatch(r"[0-9A-F]+", compact) or len(compact) < 5:
        return None
    can_id = int(compact[:3], 16)
    if can_id > 0x7FF:
        return None
    encoded = compact[3:]
    if len(encoded) % 2:
        dlc, encoded = int(encoded[0], 16), encoded[1:]
        if dlc > 8 or len(encoded) != dlc * 2:
            raise TransportError(f"Invalid CAN DLC: {line}")
    if not encoded or len(encoded) > 16:
        return None
    return can_id, bytes.fromhex(encoded)


def reassemble_frames(frames: Iterable[tuple[int, bytes]]) -> dict[int, list[bytes]]:
    """Keep separate streams per ECU, including pending and final replies."""
    complete: dict[int, list[bytes]] = {}
    partial: dict[int, tuple[int, int, bytes]] = {}
    for can_id, data in frames:
        if not data:
            raise NoResponse("Empty ISO-TP frame")
        kind = data[0] >> 4
        if kind == 0:
            size = data[0] & 15
            if not 1 <= size <= 7 or len(data) <= size:
                raise NoResponse("Truncated or invalid ISO-TP single frame")
            complete.setdefault(can_id, []).append(data[1 : 1 + size])
        elif kind == 1:
            if len(data) < 8:
                raise NoResponse("Truncated ISO-TP first frame")
            size = (data[0] & 15) * 256 + data[1]
            if size <= 7 or can_id in partial:
                raise NoResponse("Invalid or overlapping ISO-TP first frame")
            partial[can_id] = (size, 1, data[2:])
        elif kind == 2:
            if can_id not in partial:
                # Tail of an earlier transfer that was interrupted at its prompt timeout.
                continue
            size, sequence, accumulated = partial[can_id]
            if data[0] & 15 != sequence or len(data) < 2:
                raise NoResponse("ISO-TP sequence mismatch or empty continuation")
            accumulated += data[1:]
            if len(accumulated) >= size:
                complete.setdefault(can_id, []).append(accumulated[:size])
                del partial[can_id]
            else:
                partial[can_id] = (size, (sequence + 1) & 15, accumulated)
        elif kind != 3:
            raise NoResponse("Invalid ISO-TP frame type")
    if partial:
        raise NoResponse("Incomplete ISO-TP response")
    return complete


def parse_responses(text: str, command: str = "", can_ids: set[int] | None = None) -> dict[int, list[bytes]]:
    frames = []
    for line in response_lines(text, command):
        check_status(line)
        frame = parse_can_line(line)
        if frame is not None and (can_ids is None or frame[0] in can_ids):
            frames.append(frame)
    return reassemble_frames(frames)


def final_response(messages: Iterable[bytes], service: int) -> bytes | None:
    final = None
    for payload in messages:
        if payload[0] == 0x7F:
            if len(payload) < 3:
                raise NoResponse("Truncated negative response")
            if payload[1] != service:
                continue
            if payload[2] != 0x78:
                raise NegativeResponse(payload[1], payload[2])
        elif payload[0] == service + 0x40:
            final = payload
    return final


def monitor_filter(can_ids: set[int]) -> tuple[int, int]:
    if not can_ids or any(not 0 <= can_id <= 0x7FF for can_id in can_ids):
        raise ValueError("Expected at least one 11-bit CAN ID")
    first = min(can_ids)
    differences = 0
    for can_id in can_ids:
        differences |= first ^ can_id
    mask = 0x7FF ^ differences
    return first & mask, mask


def console_command(line: str) -> tuple[str, bytes | None]:
    """Reject multiline injection and adapter programming before any I/O."""
    if any(character in line for character in "\r\n\x00"):
        raise RequestBlocked("Only one console command is allowed")
    compact = "".join(line.upper().split())
    if not compact:
        raise RequestBlocked("Empty console command would repeat the last request")
    if compact.startswith(("AT", "ST", "VT")):
        if compact.startswith(("ATPP", "ATBRD", "ATBRT", "STP", "STBR", "STWBR", "STSBR", "STRSTNVM", "VTUPDATE")):
            raise RequestBlocked("Adapter programming and baud changes are blocked")
        return compact, None
    try:
        return compact, bytes.fromhex(compact)
    except ValueError as error:
        raise RequestBlocked("Expected an AT/ST/VT command or complete hex bytes") from error
