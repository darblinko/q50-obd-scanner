"""Supported USB adapters and the CAN buses each one can select."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BusConfig:
    key: str  # "hs", "ms" or "ms500"
    label: str
    stn_protocol: str  # STN protocol command, FRPM Rev F p34
    elm_protocol: str | None  # plain ELM327 command; None when the bus needs an STN device
    stn_bitrate: int | None = None  # sent as STPBR after stn_protocol (FRPM Rev F p35)

    def stn_commands(self) -> tuple[str, ...]:
        if self.stn_bitrate is None:
            return (self.stn_protocol,)
        return (self.stn_protocol, f"STPBR{self.stn_bitrate}")


@dataclass(frozen=True)
class AdapterProfile:
    key: str
    name: str
    buses: tuple[BusConfig, ...]
    baud_rates: tuple[int, ...]  # probe order
    identity_tokens: tuple[str, ...]  # substrings expected in STDI, ATI or the USB description
    notes: str  # shown on the Connect page


HS_CAN = BusConfig("hs", "HS-CAN 500 kbps (pins 6/14)", "STP33", "ATSP6")
MS_CAN = BusConfig("ms", "MS-CAN 125 kbps (pins 3/11)", "STP53", None)
# Forum reports (FORScan t=970) say 2015-2017 Mustang pins 3/11 carry HS-CAN2 at 500 kbps.
MS_PINS_500 = BusConfig("ms500", "Pins 3/11 at 500 kbps (Ford HS-CAN2, experimental)", "STP53", None, 500000)

_BAUD_RATES = (115200, 38400, 500000, 230400, 9600)

ADAPTERS = (
    AdapterProfile(
        "vlinker_fs", "Vgate vLinker FS USB", (HS_CAN, MS_CAN, MS_PINS_500), _BAUD_RATES, ("vLinker",),
        "STN1170 adapter on FTDI USB at 115200 baud. HS-CAN is tested on a 2017 Q50; "
        "MS-CAN on pins 3/11 is untested on this adapter."),
    AdapterProfile(
        "obdlink_ex", "OBDLink EX USB", (HS_CAN, MS_CAN, MS_PINS_500), _BAUD_RATES, ("OBDLink EX",),
        "ScanTool.net STN223x adapter on FTDI USB at 115200 baud. "
        "Selects HS-CAN or Ford MS-CAN on pins 3/11 with STP."),
)
DEFAULT_ADAPTER_KEY = "vlinker_fs"


def adapter_by_key(key: str) -> AdapterProfile:
    for adapter in ADAPTERS:
        if adapter.key == key:
            return adapter
    raise KeyError(f"Unknown adapter: {key}")


def bus_by_key(adapter: AdapterProfile, key: str) -> BusConfig:
    for bus in adapter.buses:
        if bus.key == key:
            return bus
    raise KeyError(f"{adapter.name} has no bus {key!r}")


def guess_adapter(description: str) -> AdapterProfile | None:
    """Match a port description, USB product string or STDI reply to a profile."""
    folded = description.casefold()
    for adapter in ADAPTERS:
        if any(token.casefold() in folded for token in adapter.identity_tokens):
            return adapter
    return None
