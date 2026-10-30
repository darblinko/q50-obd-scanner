"""Synchronous ELM327/STN transport for 11-bit CAN (HS-CAN 500 kbps or STN MS-CAN 125 kbps)."""

from __future__ import annotations

import logging
import math
import re
import threading
import time
from collections.abc import Iterator

import serial

from q50diag.core.transport import (
    AdapterReplyError,
    NegativeResponse,
    NoResponse,
    Transport,
    TransportError,
    check_request_allowed,
)
from q50diag.core.types import AdapterInfo, CanFrame
from q50diag.transport.adapters import (
    DEFAULT_ADAPTER_KEY,
    AdapterProfile,
    adapter_by_key,
    bus_by_key,
    guess_adapter,
)
from q50diag.transport.elm_parse import (
    check_status,
    console_command,
    final_response,
    monitor_filter,
    parse_can_line,
    parse_responses_by_ecu,
    response_lines,
)

LOGGER = logging.getLogger("q50diag.elm")
MONITOR_SETTLE_S = 0.05
# The protocol commands (STP and optional STPBR on STN devices, ATSP on plain ELM327) go between these.
SETUP_BEFORE_PROTOCOL = ("ATE0", "ATL0", "ATS1", "ATH1", "ATAT1")
SETUP_AFTER_PROTOCOL = ("ATCAF1", "ATCFC1", "ATD0", "ATAL", "ATFCSM0")
STN_FIRMWARE = re.compile(r"STN\d{4}")
READ_ONLY_COMMANDS = ("ATI", "AT@1", "ATRV", "ATDP", "ATDPN", "STI", "STDI")


class ElmTransport(Transport):
    def __init__(self, port: str, baudrate: int | None = None, adapter: AdapterProfile | None = None, bus: str = "hs"):
        self.port = port
        self.baudrate = baudrate
        self._adapter = adapter or adapter_by_key(DEFAULT_ADAPTER_KEY)
        self._bus_config = bus_by_key(self._adapter, bus)
        self._protocol_commands: tuple[str, ...] = ()
        self._device_identity = ""
        self._detected_adapter: AdapterProfile | None = None
        self.connection_generation = 0
        self._serial = None
        self._lock = threading.RLock()
        self._buffer = b""
        self._tx_id: int | None = None
        self._rx_id: int | None = None
        self._timeout_units: int | None = None
        self._settings_dirty = False
        self._flow_header: int | None = None
        self._monitor_ended = False

    @property
    def adapter(self) -> AdapterProfile:
        return self._adapter

    @property
    def bus(self) -> str:
        return self._bus_config.key

    @property
    def device_identity(self) -> str:
        """STDI reply on STN devices, ATI reply otherwise; empty while closed."""
        return self._device_identity

    @property
    def detected_adapter(self) -> AdapterProfile | None:
        """Profile whose identity tokens match the device, or None when unknown."""
        return self._detected_adapter

    def open(self) -> AdapterInfo:
        with self._lock:
            self.close()
            failures = []
            for baudrate in (self.baudrate,) if self.baudrate else self._adapter.baud_rates:
                try:
                    self._serial = serial.Serial(self.port, baudrate=baudrate, timeout=0.02, write_timeout=1.0)
                    self._discard_stale_input()
                    self._command("ATZ", 2.0)
                    identity = self._command("ATI", 1.0)
                    if "ELM" not in identity.upper():
                        raise TransportError(f"Unrecognized adapter: {identity}")
                    self._device_identity = identity
                    break
                except (OSError, serial.SerialException, TransportError) as error:
                    failures.append(f"{baudrate}: {error}")
                    LOGGER.debug("Baud probe failed: %s", failures[-1])
                    self.close()
            else:
                raise TransportError("Could not identify ELM adapter; " + "; ".join(failures))
            try:
                info = self._initialize()
            except TransportError:
                self.close()
                raise
            self.connection_generation += 1
            return info

    def _discard_stale_input(self) -> None:
        """Drop bytes left from an earlier session. ATI is harmless whether the
        adapter is idle or still in monitor mode (any byte stops ATMA)."""
        self._serial.reset_input_buffer()
        self._write(b"ATI\r")
        time.sleep(0.3)
        self._serial.reset_input_buffer()
        self._buffer = b""

    def _optional_command(self, command: str, timeout_s: float = 1.0) -> str | None:
        try:
            return self._command(command, timeout_s) or None
        except NoResponse:  # includes "?" from adapters without the command
            return None

    def _detect_device(self) -> str | None:
        """Choose the protocol command; return the STI firmware line on STN devices."""
        firmware = self._optional_command("STI", 0.5)
        if firmware is not None and STN_FIRMWARE.search(firmware.upper()):
            self._device_identity = self._optional_command("STDI") or firmware
            self._protocol_commands = self._bus_config.stn_commands()
        elif self._bus_config.elm_protocol is None:
            raise TransportError(
                "MS-CAN needs an STN-based adapter such as the OBDLink EX or vLinker FS "
                f"({self._bus_config.label} selected; adapter reports {self._device_identity!r})"
            )
        else:
            firmware = None
            self._protocol_commands = (self._bus_config.elm_protocol,)
        self._check_identity()
        return firmware

    def _check_identity(self) -> None:
        folded = self._device_identity.casefold()
        if any(token.casefold() in folded for token in self._adapter.identity_tokens):
            self._detected_adapter = self._adapter
            return
        self._detected_adapter = guess_adapter(self._device_identity)
        if self._detected_adapter is not None:
            LOGGER.warning(
                "Selected %s, but the adapter identifies as %r (%s); continuing",
                self._adapter.name,
                self._device_identity,
                self._detected_adapter.name,
            )

    def _setup_commands(self) -> tuple[str, ...]:
        return (*SETUP_BEFORE_PROTOCOL, *self._protocol_commands, *SETUP_AFTER_PROTOCOL)

    def _initialize(self) -> AdapterInfo:
        firmware = self._detect_device()
        for command in self._setup_commands():
            self._command(command)
        self._tx_id = self._rx_id = self._timeout_units = None
        self._settings_dirty = False
        self._flow_header = None
        identity = self._command("ATI")
        if firmware is None:
            description = self._command("AT@1")
        elif firmware == self._device_identity:
            description = firmware
        else:
            description = f"{self._device_identity} ({firmware})"
        voltage = self.read_voltage()
        self._set_address(0x7DF, None)
        try:
            self._command("0100", 3.0)
        except NoResponse as error:
            LOGGER.info("Adapter ready, but bus wakeup did not answer: %s", error)
        return AdapterInfo(self.port, identity, description, self._command("ATDP"), voltage)

    def close(self) -> None:
        with self._lock:
            connection, self._serial = self._serial, None
            self._buffer = b""
            self._tx_id = self._rx_id = self._timeout_units = None
            self._flow_header = None
            self._device_identity = ""
            self._detected_adapter = None
            if connection is not None:
                try:
                    connection.close()
                except (OSError, serial.SerialException) as error:
                    raise TransportError(f"Failed to close serial port: {error}") from error

    def _write(self, data: bytes) -> None:
        if self._serial is None:
            raise TransportError("Adapter is not open")
        LOGGER.debug("TX %s", data.decode("ascii", errors="replace").rstrip())
        try:
            count = self._serial.write(data)
            if count is not None and count != len(data):
                raise TransportError("Incomplete serial write")
        except (OSError, serial.SerialException) as error:
            raise TransportError(f"Serial write failed: {error}") from error

    def _read(self) -> bytes:
        if self._serial is None:
            raise TransportError("Adapter is not open")
        try:
            return self._serial.read(max(1, min(self._serial.in_waiting, 4096)))
        except (OSError, serial.SerialException) as error:
            raise TransportError(f"Serial read failed: {error}") from error

    def _read_prompt(self, timeout_s: float) -> str:
        deadline = time.monotonic() + timeout_s
        pending_deadline = deadline + 5.0
        # Long multi-frame replies keep the wait open while bytes still arrive.
        activity_limit = deadline + 5.0
        while True:
            if b">" in self._buffer:
                response, self._buffer = self._buffer.split(b">", 1)
                text = response.decode("ascii", errors="replace")
                for line in response_lines(text):
                    LOGGER.debug("RX %s", line)
                LOGGER.debug("RX >")
                return text
            if time.monotonic() >= deadline:
                for line in response_lines(self._buffer.decode("ascii", errors="replace")):
                    LOGGER.debug("RX %s", line)
                raise NoResponse("Timed out waiting for ELM prompt")
            chunk = self._read()
            self._buffer += chunk
            if len(self._buffer) > 1024 * 1024:
                raise TransportError("ELM response exceeded one megabyte")
            compact = re.sub(rb"\s+", b"", self._buffer).upper()
            if re.search(rb"037F[0-9A-F]{2}78", compact):
                deadline = pending_deadline
            if chunk:
                deadline = max(deadline, min(time.monotonic() + timeout_s, activity_limit))
            else:
                time.sleep(0.001)

    def _interrupt(self) -> None:
        # A character sent while the adapter is still parsing the command can be lost; retry once.
        for attempt in range(2):
            self._write(b" ")
            try:
                self._read_prompt(1.0)
                return
            except NoResponse as error:
                if attempt:
                    self.close()
                    raise TransportError("Adapter did not return to command mode") from error

    def _exchange(self, command: str, timeout_s: float = 1.0) -> str:
        self._write(command.encode("ascii") + b"\r")
        try:
            return self._read_prompt(timeout_s)
        except NoResponse:
            self._interrupt()
            raise

    def _command(self, command: str, timeout_s: float = 1.0) -> str:
        lines = response_lines(self._exchange(command, timeout_s), command)
        for line in lines:
            check_status(line)
        return "\n".join(lines)

    def _prepare(self) -> None:
        if self._settings_dirty:
            for command in self._setup_commands():
                self._command(command)
            if self._tx_id is not None:
                self._command(f"ATSH{self._tx_id:03X}")
            self._command("ATAR" if self._rx_id is None else f"ATCRA{self._rx_id:03X}")
            self._timeout_units = None
            self._flow_header = None
            self._settings_dirty = False

    def _set_address(self, tx_id: int, rx_id: int | None) -> None:
        if not 0 <= tx_id <= 0x7FF or (rx_id is not None and not 0 <= rx_id <= 0x7FF):
            raise ValueError("CAN addresses must be 11-bit")
        if self._tx_id != tx_id:
            self._command(f"ATSH{tx_id:03X}")
            self._tx_id = tx_id
        if rx_id is None:
            self._command("ATAR")
        elif self._rx_id != rx_id:
            self._command(f"ATCRA{rx_id:03X}")
        self._rx_id = rx_id
        self._set_flow_control(tx_id, rx_id)

    def _set_flow_control(self, tx_id: int, rx_id: int | None) -> None:
        # ELM's automatic OBD mapping is unsuitable for Nissan +0x20 addresses.
        custom_header = tx_id if rx_id is not None and not (0x7E0 <= tx_id <= 0x7E7 and rx_id == tx_id + 8) else None
        if custom_header == self._flow_header:
            return
        if custom_header is None:
            self._command("ATFCSM0")
        else:
            self._command(f"ATFCSH{custom_header:03X}")
            self._command("ATFCSD300000")
            self._command("ATFCSM1")
        self._flow_header = custom_header

    def _set_timeout(self, timeout_s: float) -> None:
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("Request timeout must be positive and finite")
        units = max(1, min(255, math.ceil(timeout_s / 0.004)))
        if units != self._timeout_units:
            self._command(f"ATST{units:02X}")
            self._timeout_units = units

    def _collect(
        self, payload: bytes, timeout_s: float, can_ids: set[int]
    ) -> tuple[dict[int, bytes], set[int], dict[int, Exception]]:
        """Positive answers, ECUs still pending (7F xx 78) and per-ECU
        failures. One ECU's negative or truncated reply does not discard
        the answers of the others."""
        command = payload.hex().upper()
        text = self._exchange(command, timeout_s + 0.15)
        answers: dict[int, bytes] = {}
        pending: set[int] = set()
        streams, reassembly_failures = parse_responses_by_ecu(text, command, can_ids)
        failures: dict[int, Exception] = dict(reassembly_failures)
        for can_id, messages in streams.items():
            try:
                answer = final_response(messages, payload[0])
            except (NegativeResponse, NoResponse) as error:
                failures[can_id] = error
                continue
            if answer is not None:
                answers[can_id] = answer
            elif any(message[:3] == bytes((0x7F, payload[0], 0x78)) for message in messages):
                pending.add(can_id)
        return answers, pending, failures

    def _responses(
        self, payload: bytes, timeout_s: float, can_ids: set[int]
    ) -> tuple[dict[int, bytes], dict[int, Exception]]:
        answers, pending, failures = self._collect(payload, timeout_s, can_ids)
        if not pending:
            return answers, failures
        # The ELM printed its prompt after 7F xx 78, so it is no longer
        # listening. Repeat the request once with the longest ELM timeout.
        LOGGER.info(
            "Response pending from %s; retrying with ATAT0 ATSTFF",
            ", ".join(f"0x{can_id:03X}" for can_id in sorted(pending)),
        )
        self._command("ATAT0")
        self._command("ATSTFF")
        self._timeout_units = 0xFF
        try:
            retry_answers, pending, retry_failures = self._collect(payload, 1.02, pending)
        finally:
            self._command("ATAT1")
        if pending:
            LOGGER.info("Response pending did not complete for %s", sorted(pending))
        return {**answers, **retry_answers}, {**failures, **retry_failures}

    def request(
        self, tx_id: int, rx_id: int, payload: bytes, timeout_s: float = 1.0, allow_clear: bool = False
    ) -> bytes:
        check_request_allowed(payload, allow_clear)
        with self._lock:
            self._prepare()
            self._set_address(tx_id, rx_id)
            self._set_timeout(timeout_s)
            answers, failures = self._responses(payload, timeout_s, {rx_id})
            if rx_id in failures:
                raise failures[rx_id]
            if rx_id not in answers:
                raise NoResponse(f"No response from 0x{rx_id:03X}")
            return answers[rx_id]

    def request_functional(self, payload: bytes, timeout_s: float = 1.0) -> dict[int, bytes]:
        return self.request_functional_with_failures(payload, timeout_s)[0]

    def request_functional_with_failures(
        self, payload: bytes, timeout_s: float = 1.0
    ) -> tuple[dict[int, bytes], dict[int, Exception]]:
        check_request_allowed(payload)
        with self._lock:
            self._prepare()
            self._set_address(0x7DF, None)
            self._set_timeout(timeout_s)
            answers, failures = self._responses(payload, timeout_s, set(range(0x7E8, 0x7F0)))
            result = {can_id: data for can_id, data in answers.items() if 0x7E8 <= can_id <= 0x7EF}
            failures = {can_id: failures[can_id] for can_id in sorted(failures) if can_id not in result}
            for can_id, error in failures.items():
                LOGGER.debug("Functional request %s: 0x%03X failed: %s", payload.hex(), can_id, error)
            if not result:
                rejection = next(iter(failures.values()), None)
                raise rejection or NoResponse("No OBD-II modules answered")
            return result, failures

    def raw_command(self, line: str) -> str:
        command, payload = console_command(line)
        if payload is not None:
            check_request_allowed(payload)
        with self._lock:
            if payload is not None:
                self._prepare()
                if self._tx_id is None:
                    self._set_address(0x7DF, None)
                self._set_flow_control(self._tx_id, self._rx_id)
                try:
                    return self._command(command)
                except AdapterReplyError as error:
                    return str(error)
            try:
                result = self._command(command, 2.0)
            except AdapterReplyError as error:
                return str(error)
            self._remember_raw_settings(command)
            self._settings_dirty = command not in READ_ONLY_COMMANDS or self._settings_dirty
            return result

    def _remember_raw_settings(self, command: str) -> None:
        if re.fullmatch(r"ATSH[0-7][0-9A-F]{2}", command):
            self._tx_id = int(command[4:], 16)
        elif re.fullmatch(r"ATCRA[0-7][0-9A-F]{2}", command):
            self._rx_id = int(command[5:], 16)
        elif command in ("ATZ", "ATWS", "ATD"):
            self._tx_id = self._rx_id = None
            self.connection_generation += 1
        elif command in ("ATAR", "ATCRA") or command.startswith(("ATCF", "ATCM")):
            self._rx_id = None

    def read_voltage(self) -> float | None:
        with self._lock:
            reply = self._command("ATRV")
            if "--" in reply:
                return None  # adapter reports "--.-V" when the OBD port has no power
            match = re.search(r"(\d+(?:\.\d+)?)\s*V\b", reply, re.IGNORECASE)
            if match is None:
                raise TransportError(f"Invalid adapter voltage: {reply}")
            return float(match[1])

    def _monitor_setup(self, can_ids: set[int] | None) -> None:
        self._command("ATH1")
        self._command("ATCAF0")
        if can_ids and len(can_ids) == 1:
            self._command(f"ATCRA{next(iter(can_ids)):03X}")
        else:
            code, mask = monitor_filter(can_ids) if can_ids else (0, 0)
            self._command(f"ATCF{code:03X}")
            self._command(f"ATCM{mask:03X}")

    def _monitor_lines(self, stop: threading.Event, deadline: float) -> Iterator[str]:
        while not stop.is_set() and time.monotonic() < deadline:
            match = re.search(rb"[\r\n>]", self._buffer)
            if match:
                line, self._buffer = self._buffer[: match.start()], self._buffer[match.end() :]
                if match[0] == b">":
                    # The adapter left monitor mode on its own (e.g. BUFFER FULL).
                    self._monitor_ended = True
                    return
                text = line.decode("ascii", errors="replace").strip()
                if text:
                    LOGGER.debug("RX %s", text)
                    yield text
            else:
                chunk = self._read()
                self._buffer += chunk
                if len(self._buffer) > 65536:
                    raise TransportError("Unterminated monitor line")
                if not chunk:
                    stop.wait(0.002)

    def monitor(
        self, stop: threading.Event, can_ids: set[int] | None = None, max_duration_s: float | None = None
    ) -> Iterator[CanFrame]:
        if stop.is_set() or can_ids == set() or (max_duration_s is not None and max_duration_s <= 0):
            return
        if can_ids:
            monitor_filter(can_ids)
        with self._lock:
            self._prepare()
            previous_rx = self._rx_id
            active = False
            self._monitor_ended = False
            try:
                self._monitor_setup(can_ids)
                self._write(b"ATMA\r")
                started = time.monotonic()
                active = True
                deadline = time.monotonic() + max_duration_s if max_duration_s is not None else math.inf
                for line in self._monitor_lines(stop, deadline):
                    try:
                        check_status(line)
                    except AdapterReplyError as error:
                        LOGGER.warning("Monitor: %s", error)
                        continue
                    parsed = parse_can_line(line)
                    if parsed is not None and (can_ids is None or parsed[0] in can_ids):
                        yield CanFrame(time.time(), *parsed)
            finally:
                if active and not self._monitor_ended:
                    # On 2026-10-17 a space sent 1 ms after ATMA was ignored by the STN1170.
                    time.sleep(max(0.0, started + MONITOR_SETTLE_S - time.monotonic()))
                    self._interrupt()
                self._command("ATCAF1")
                self._command("ATH1")
                self._command("ATAR" if previous_rx is None else f"ATCRA{previous_rx:03X}")
                self._rx_id = previous_rx
