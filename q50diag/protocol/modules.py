"""Candidate endpoints from docs/research/q50-diagnostics.md.

Presence in this table does not imply that a particular Q50 has the module.
12 of the 15 answered the discovery sweep on a 2017 Q50 3.0t on 2026-10-17
(docs/q50-findings-2026-10-17.md); AWD, STEERING ANGLE and ADAS did not.
"""

from q50diag.core.types import ModuleAddress

KNOWN_MODULES: tuple[ModuleAddress, ...] = (
    ModuleAddress("ECM", 0x7E0, 0x7E8, "kwp", "Powertrain", "Confirmed Q50 address; also OBD-II"),
    ModuleAddress("TCM", 0x7E1, 0x7E9, "kwp", "Powertrain", "Confirmed Q50 address; also OBD-II"),
    ModuleAddress("ABS", 0x740, 0x760, "kwp", "Chassis", "ABS/VDC"),
    ModuleAddress("EPS", 0x742, 0x762, "kwp", "Chassis", "Rack EPS; confirmed Q50 address"),
    ModuleAddress("METER", 0x743, 0x763, "kwp", "Body"),
    ModuleAddress("HVAC", 0x744, 0x764, "kwp", "Body", "Nissan address; confirmed on Q50"),
    ModuleAddress("BCM", 0x745, 0x765, "kwp", "Body", "Leaf address; confirmed on Q50"),
    ModuleAddress("AV", 0x747, 0x767, "kwp", "Body", "Multi AV; confirmed on Q50"),
    ModuleAddress("AWD", 0x748, 0x768, "kwp", "Powertrain", "If equipped; unverified"),
    ModuleAddress("IPDM E/R", 0x74D, 0x76D, "kwp", "Body", "Leaf address; confirmed on Q50"),
    ModuleAddress("AIR BAG", 0x752, 0x772, "kwp", "Chassis", "Leaf address; confirmed on Q50"),
    ModuleAddress("TPMS", 0x758, 0x778, "kwp", "Chassis", "Nissan address; confirmed on Q50"),
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
