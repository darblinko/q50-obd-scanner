"""Transport contract and the read-only safety gate.

Every diagnostic request from any layer (protocol code, GUI, raw console)
passes through `check_request_allowed` before it reaches the car. The gate
permits reads, session control limited to default/extended diagnostic
sessions, tester present, and DTC clearing. It refuses ECU reset, security
access, writes, routine control, I/O control, and programming."""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections.abc import Iterator

from q50diag.core.types import AdapterInfo, CanFrame


class TransportError(Exception):
    """Adapter or serial failure. The connection may be unusable."""


class NoResponse(TransportError):
    """The module did not answer within the timeout (ELM 'NO DATA')."""


class AdapterReplyError(NoResponse):
    """The adapter answered with a status line ("?", CAN ERROR, BUFFER FULL,
    STOPPED, ...) instead of data. The request failed; the link is intact."""


class NegativeResponse(Exception):
    """The module answered 0x7F <sid> <nrc>."""

    def __init__(self, service: int, code: int) -> None:
        super().__init__(f"negative response to 0x{service:02X}: NRC 0x{code:02X}")
        self.service = service
        self.code = code


class RequestBlocked(Exception):
    """The safety gate refused the request."""


# OBD-II read modes. 0x04 (clear) is in CLEAR_SERVICES; 0x08 (on-board test
# control) actuates components and is excluded.
OBD_SERVICES = {0x01, 0x02, 0x03, 0x05, 0x06, 0x07, 0x09, 0x0A}
# KWP2000 / UDS read services. No q50diag code builds a 0x17 request; 0x17 is allowed
# for raw reads only: a hex line typed on the Console page or passed to the agent's raw_read.
READ_SERVICES = {0x17, 0x18, 0x19, 0x1A, 0x21, 0x22}
CLEAR_SERVICES = {0x04, 0x14}
TESTER_PRESENT = 0x3E
SESSION_CONTROL = 0x10
# 0x01/0x81 default, 0x03 UDS extended, 0x89 KWP standby, 0xC0 Nissan CONSULT session.
ALLOWED_SESSIONS = {0x01, 0x03, 0x81, 0x89, 0xC0}


def check_request_allowed(payload: bytes, allow_clear: bool = False) -> None:
    if not payload:
        raise RequestBlocked("empty request")
    service = payload[0]
    if service in CLEAR_SERVICES:
        if not allow_clear:
            raise RequestBlocked("DTC clear requires explicit confirmation")
        return
    if service in OBD_SERVICES or service in READ_SERVICES or service == TESTER_PRESENT:
        return
    if service == SESSION_CONTROL and len(payload) >= 2 and payload[1] in ALLOWED_SESSIONS:
        return
    raise RequestBlocked(f"service 0x{service:02X} is not on the read-only allowlist")


class Transport(ABC):
    """One adapter connection. Implementations are not thread-safe; the GUI
    session layer serializes all calls onto a single worker thread."""

    @abstractmethod
    def open(self) -> AdapterInfo:
        """Connect, initialize the adapter for ISO 15765-4 CAN 11-bit 500 kbps,
        and return adapter details. Raises TransportError."""

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def read_voltage(self) -> float | None:
        """Voltage at the OBD port as measured by the adapter (ATRV)."""

    @abstractmethod
    def request(
        self,
        tx_id: int,
        rx_id: int,
        payload: bytes,
        timeout_s: float = 1.0,
        allow_clear: bool = False,
    ) -> bytes:
        """Send one diagnostic request to a physical address and return the
        reassembled ISO-TP response payload (first byte is SID + 0x40).
        Calls check_request_allowed first. Raises NoResponse,
        NegativeResponse (after handling 0x78 response-pending internally),
        RequestBlocked or TransportError."""

    @abstractmethod
    def request_functional(self, payload: bytes, timeout_s: float = 1.0) -> dict[int, bytes]:
        """Broadcast on 0x7DF and return {response CAN id: payload} for every
        OBD-II module that answered."""

    @abstractmethod
    def raw_command(self, line: str) -> str:
        """Raw console. AT/ST/VT lines pass to the adapter, except commands
        that reprogram the adapter (e.g. AT PP, firmware update). Hex lines are
        parsed and gated by check_request_allowed before sending, using the
        current header. Returns the adapter's text response."""

    @abstractmethod
    def monitor(
        self,
        stop: threading.Event,
        can_ids: set[int] | None = None,
        max_duration_s: float | None = None,
    ) -> Iterator[CanFrame]:
        """Passive bus monitor (ATMA). `can_ids` limits capture to those IDs
        (adapter-side filter where possible). Yields frames until `stop` is
        set or `max_duration_s` elapses, then restores the adapter to request
        mode (headers, filters and CAF as they were)."""
