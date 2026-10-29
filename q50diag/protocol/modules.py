"""Candidate endpoints from docs/research/q50-diagnostics.md.

Presence in this table does not imply that a particular Q50 has the module.
"""

from q50diag.core.types import ModuleAddress

KNOWN_MODULES: tuple[ModuleAddress, ...] = (
    ModuleAddress("ECM", 0x7E0, 0x7E8, "kwp", "Powertrain", "Confirmed Q50 address; also OBD-II"),
    ModuleAddress("TCM", 0x7E1, 0x7E9, "kwp", "Powertrain", "Q50 address unverified; also OBD-II"),
    ModuleAddress("ABS", 0x740, 0x760, "kwp", "Chassis", "ABS/VDC"),
    ModuleAddress("EPS", 0x742, 0x762, "kwp", "Chassis", "Rack EPS; address unverified on Q50"),
    ModuleAddress("METER", 0x743, 0x763, "kwp", "Body"),
    ModuleAddress("HVAC", 0x744, 0x764, "kwp", "Body", "Nissan address; Q50 unverified"),
    ModuleAddress("BCM", 0x745, 0x765, "kwp", "Body", "Leaf address; Q50 unverified"),
    ModuleAddress("AV", 0x747, 0x767, "kwp", "Body", "Multi AV; Q50 unverified"),
    ModuleAddress("AWD", 0x748, 0x768, "kwp", "Powertrain", "If equipped; unverified"),
    ModuleAddress("IPDM E/R", 0x74D, 0x76D, "kwp", "Body", "Leaf address; Q50 unverified"),
    ModuleAddress("AIR BAG", 0x752, 0x772, "kwp", "Chassis", "Leaf address; Q50 unverified"),
    ModuleAddress("TPMS", 0x758, 0x778, "kwp", "Chassis", "Nissan address; Q50 unverified"),
    ModuleAddress("STEERING ANGLE", 0x75C, 0x77C, "kwp", "Chassis", "Model unspecified"),
    ModuleAddress("ADAS", 0x707, 0x727, "kwp", "Chassis", "Front camera; if equipped"),
    ModuleAddress("RADAR", 0x723, 0x735, "kwp", "Chassis", "If equipped; nonstandard response offset"),
)


def module_by_name(name: str) -> ModuleAddress | None:
    aliases = {"ABS/VDC": "ABS", "IPDM": "IPDM E/R", "MULTI AV": "AV"}
    normalized = aliases.get(name.strip().upper(), name.strip().upper())
    return next((module for module in KNOWN_MODULES if module.name == normalized), None)


def module_for_response_id(rx_id: int) -> ModuleAddress | None:
    return next((module for module in KNOWN_MODULES if module.rx_id == rx_id), None)
