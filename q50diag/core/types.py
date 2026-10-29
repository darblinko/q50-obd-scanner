"""Shared data types. This module is the contract between the transport,
protocol and GUI layers. Change it only with all three in view."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

Value = float | int | str | bool | None


@dataclass(frozen=True)
class PortInfo:
    device: str  # "COM5" on Windows, "/dev/ttyUSB0" on Linux
    description: str
    vid: int | None
    pid: int | None
    is_likely_vlinker: bool


@dataclass(frozen=True)
class AdapterInfo:
    port: str
    identity: str  # ATI response, e.g. "ELM327 v2.3"
    description: str  # AT@1 response
    protocol: str  # ATDPN / ATDP response after connect
    voltage: float | None  # ATRV, volts at the OBD port


@dataclass(frozen=True)
class ModuleAddress:
    """A diagnostic endpoint on the CAN bus."""

    name: str  # "ECM", "ABS", "EPS"
    tx_id: int  # 11-bit request CAN id, e.g. 0x7E0
    rx_id: int  # 11-bit response CAN id, e.g. 0x7E8
    protocol: str  # "obd2", "kwp" (Nissan KWP2000 over CAN) or "uds"
    group: str  # "Powertrain", "Chassis", "Body", "Network", "Unknown"
    notes: str = ""
    bus: str = "hs"  # adapter bus key: "hs" (pins 6/14) or "ms"/"ms500" (pins 3/11)


@dataclass(frozen=True)
class DiscoveredModule:
    address: ModuleAddress
    responded_to: str  # hex of the request that got a positive answer, e.g. "3E00"
    response: bytes  # raw positive response payload


@dataclass(frozen=True)
class Dtc:
    code: str  # "P0300", "C1604", "U1000"
    description: str  # "" when unknown
    module: str  # module name
    kind: str  # "stored", "pending", "permanent", "current", "history"
    status: int | None = None  # raw status byte when the protocol provides one


@dataclass(frozen=True)
class ParameterDef:
    """A readable value. `request` is the full request payload sent to the
    module; `decode` turns the positive response payload (with the service
    echo and identifier bytes already stripped) into a value."""

    key: str  # globally unique, e.g. "obd.01.0C" or "EPS.21.01.motor_current"
    name: str  # "Engine RPM"
    unit: str  # "rpm", "V", "A", "degC", "%", "" for text
    module: str  # module name
    category: str  # "Engine", "Fuel", "Air", "Electrical", "Steering", ...
    request: bytes
    decode: Callable[[bytes], Value]
    min_value: float | None = None  # expected range, for charts and gauges
    max_value: float | None = None
    description: str = ""


@dataclass(frozen=True)
class Reading:
    key: str
    value: Value
    unit: str
    timestamp: float  # time.time()
    raw: bytes = b""
    error: str = ""  # non-empty when the read failed


@dataclass(frozen=True)
class MonitorStatus:
    """OBD-II mode 01 PID 01."""

    mil_on: bool
    dtc_count: int
    monitors: dict[str, str] = field(default_factory=dict)  # name -> "complete" / "incomplete" / "n/a"


@dataclass(frozen=True)
class CanFrame:
    timestamp: float
    can_id: int
    data: bytes
