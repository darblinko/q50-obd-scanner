"""Read-only local identifier scans and byte-level snapshot differences."""

import logging

from q50diag.core.transport import NegativeResponse, NoResponse, RequestBlocked
from q50diag.protocol import nissan

LOGGER = logging.getLogger(__name__)


def scan_identifiers(
    t, module, service: int, start: int, end: int, progress=None, stop=None, send=nissan.request
) -> dict[int, bytes]:
    """Scan the inclusive interval [start, end]; progress is (done, total, label).
    Each read goes through `send(t, module, payload, timeout_s=...)`, the
    vehicle's request policy (Nissan session handling by default)."""
    if service not in (0x21, 0x22):
        raise ValueError("Explorer only supports read services 0x21 and 0x22")
    width = 1 if service == 0x21 else 2
    if not 0 <= start <= end < 1 << (8 * width):
        raise ValueError("Identifier range is invalid")
    if stop is not None and stop.is_set():
        return {}
    result = {}
    for identifier in range(start, end + 1):
        if stop is not None and stop.is_set():
            break
        request = bytes((service,)) + identifier.to_bytes(width, "big")
        try:
            reply = send(t, module, request, timeout_s=0.1)
            echo = bytes((service + 0x40,)) + request[1:]
            if reply.startswith(echo):
                result[identifier] = reply[len(echo) :]
            else:
                LOGGER.warning("Unexpected explorer echo: %s", reply.hex())
        except (NoResponse, NegativeResponse, RequestBlocked) as error:
            LOGGER.debug("Explorer %s %s: %s", module.name, request.hex(), error)
        if progress is not None:
            progress(identifier - start + 1, end - start + 1, f"{identifier:0{width * 2}X}")
    return result


def diff_snapshots(a: dict[int, bytes], b: dict[int, bytes]) -> dict[int, list[int]]:
    result = {}
    for identifier in sorted(a.keys() | b.keys()):
        before, after = a.get(identifier, b""), b.get(identifier, b"")
        changed = [
            index
            for index in range(max(len(before), len(after)))
            if before[index : index + 1] != after[index : index + 1]
        ]
        if changed:
            result[identifier] = changed
    return result
