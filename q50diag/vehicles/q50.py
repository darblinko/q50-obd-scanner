"""2017 Infiniti Q50 3.0t: wraps the existing Nissan tables unchanged."""

from __future__ import annotations

from q50diag.core.types import ModuleAddress, ParameterDef
from q50diag.data.dtc_descriptions import NISSAN_DESCRIPTIONS
from q50diag.protocol import nissan
from q50diag.protocol.broadcast import BROADCAST_SIGNALS, decode_frame
from q50diag.protocol.discovery import KNOWN_PROBES
from q50diag.protocol.modules import KNOWN_MODULES
from q50diag.vehicles import VehicleProfile

PROMPT_NOTES = """
- 2017 Infiniti Q50 3.0t, no DAS (Standard/Sport steering only), rack electric power steering.
  Complaint under investigation: steering intermittently feels stiff and sluggish.
- The OBD port is gatewayed: an unfiltered bus monitor sees 0 broadcast frames. Data must be
  polled from modules; broadcast IDs such as 0x002, 0x300, 0x5E4 are not readable here.
- EPS module at request 0x742 / response 0x762. It answers Nissan local IDs (service 21 xx) only,
  no 22 xxxx DIDs. 21 01 is a 21-byte live block: bytes 0-1 supply voltage x 4 mV, bytes 3-4
  steering angle signed x -0.1 deg, bytes 5-6 steering velocity (inferred), byte 7 assist load
  (inferred), byte 8 driver torque signed (inferred), byte 10 temperature candidate, byte 11 assist
  limit candidate (100 in every sample so far), bytes 15-16 motor speed candidate (inferred).
- ABS at 0x740/0x760 gives steering angle via 22 1208. Stored codes seen on 2026-10-17: RADAR
  C1A01-00 and C1A16-00, AIR BAG B00A0-00; EPS, ECM and ABS clean.
- Findings so far: docs/q50-findings-2026-10-17.md. Background research: docs/research/.
""".strip()


def q50_parameters(module: ModuleAddress) -> list[ParameterDef]:
    # Resolved at call time so a hot reload of nissan takes effect without a restart.
    return nissan.module_parameters(module)


Q50 = VehicleProfile(
    key="q50_2017",
    name="2017 Infiniti Q50 3.0t",
    short_name="Q50 3.0t",
    make="Infiniti",
    year=2017,
    modules=KNOWN_MODULES,
    obd_module="ECM",
    diag=nissan,
    module_parameters=q50_parameters,
    broadcast_signals=BROADCAST_SIGNALS,
    decode_frame=decode_frame,
    discovery_probes=KNOWN_PROBES,
    sweep_protocol="kwp",
    response_offsets=(0x20, 8),
    explorer_services=((0x21, "00", "FF"), (0x22, "1100", "12FF")),
    has_steering_page=True,
    default_favorites=(
        "obd.01.0D",
        "obd.01.0C",
        "EPS.21.01.supply",
        "EPS.21.01.angle",
        "EPS.21.01.torque",
        "EPS.21.01.assist",
        "obd.01.05",
        "obd.01.06",
        "obd.01.07",
    ),
    dtc_overlay=NISSAN_DESCRIPTIONS,
    sim_modules=frozenset({"ECM", "TCM", "ABS", "EPS", "METER", "BCM", "IPDM E/R", "AIR BAG", "HVAC", "TPMS", "AV"}),
    sim_vin="JN1SAMPLEXXXXXXX0",
    prompt_notes=PROMPT_NOTES,
    gateway_note="On the 2017 Q50 the OBD port is gatewayed: capture showed no broadcast frames in testing.",
    low_voltage_note="Low: EPS assist may be limited below about 9 to 10 V",
    aliases={"ABS/VDC": "ABS", "IPDM": "IPDM E/R", "MULTI AV": "AV"},
    connect_note="Nissan KWP2000 over CAN. Steering page and EPS 21 01 fields available.",
)
