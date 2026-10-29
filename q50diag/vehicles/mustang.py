"""2015-2017 Ford Mustang (S550): 2016 Shelby GT350R and 2017 GT.

Module addresses (response = request + 8) are from the FORScan 2011-2018
address list (https://www.pickuptrucks.de/media/kunena/attachments/5776/FORScan2011-2018.pdf)
and an S550 owner sheet
(https://docs.google.com/spreadsheets/d/1k0ZBnLT5CnHuL6BbkKg0AxhCmNlE_0Zev3zVnyz-5_A).
Presence in this table does not imply that a particular car has the module.
The GT350R MagneRide damping module address is unknown and is omitted. The
6R80 automatic has no separate TCM: the PCM controls it."""

from __future__ import annotations

from q50diag.core.types import CanFrame, ModuleAddress, ParameterDef, Value
from q50diag.data.dtc_descriptions import FORD_DESCRIPTIONS
from q50diag.protocol import ford
from q50diag.vehicles import VehicleProfile

UNSURE_BUS = "Bus placement uncertain: HS-CAN (pins 6/14) or MS-CAN (pins 3/11)"

S550_MODULES: tuple[ModuleAddress, ...] = (
    ModuleAddress("PCM", 0x7E0, 0x7E8, "uds", "Powertrain", "Engine and 6R80 transmission; also OBD-II"),
    ModuleAddress("ABS", 0x760, 0x768, "uds", "Chassis", "Anti-lock brakes / stability. " + UNSURE_BUS),
    ModuleAddress("IPC", 0x720, 0x728, "uds", "Body", "Instrument cluster. " + UNSURE_BUS),
    ModuleAddress("PSCM", 0x730, 0x738, "uds", "Chassis", "Electric power steering"),
    ModuleAddress("RCM", 0x737, 0x73F, "uds", "Chassis", "Restraints (air bags)"),
    ModuleAddress("BCM", 0x726, 0x72E, "uds", "Body", "Body control"),
    ModuleAddress("ACM", 0x727, 0x72F, "uds", "Body", "Audio control"),
    ModuleAddress("APIM", 0x7D0, 0x7D8, "uds", "Body", "SYNC infotainment. " + UNSURE_BUS),
    ModuleAddress("HVAC", 0x733, 0x73B, "uds", "Body", "Climate control"),
)
SIM_MODULES = frozenset({"PCM", "ABS", "IPC", "BCM", "PSCM", "RCM", "APIM"})
DISCOVERY_PROBES = (b"\x3e\x00", b"\x22\xf1\x90")
EXPLORER_SERVICES = ((0x22, "F400", "F4FF"),)
FAVORITES = ("obd.01.0D", "obd.01.0C", "obd.01.05", "obd.01.04", "obd.01.11", "PCM.22.03EC", "obd.01.06", "obd.01.07")
GATEWAY_NOTE = (
    "S550 broadcast traffic at the OBD port has not been characterised in this app; "
    "frames are shown raw and none are decoded."
)
CONNECT_NOTE = (
    "Ford UDS. No public S550 DID table exists: PCM DIDs beyond the OBD-II PIDs are "
    "inferred or unverified (see each parameter's description). No Steering page."
)
LOW_VOLTAGE_NOTE = "Low: modules may drop off the bus or set low-voltage codes"
DATA_NOTES = """
- Modules (response = request + 8): PCM 7E0, ABS 760, IPC 720, PSCM 730, RCM 737, BCM 726,
  ACM 727, APIM 7D0, HVAC 733. Sources: FORScan 2011-2018 address list and an S550 owner sheet.
  IPC, ABS and APIM may sit on MS-CAN (pins 3/11) rather than HS-CAN (pins 6/14).
- Protocol: UDS. Identification via 22 F190 (VIN), F188 and F113 (part numbers); module DTCs
  via 19 02 (3-byte code plus ISO 14229 status byte). Standard Mode 01 PIDs work on the PCM.
- PCM DIDs in the app, with confidence: F405/F40F/F42F (inferred OBD-on-UDS mirrors of PIDs
  05/0F/2F), 03EC knock retard cylinder 1 (low; sign convention disputed), 0304 battery voltage
  and 054B oil life (unverified, single Focus RS source). No public S550 DID table exists.
""".strip()


def _no_signals(_frame: CanFrame) -> dict[str, Value]:
    return {}


def gt350r_parameters(module: ModuleAddress) -> list[ParameterDef]:
    return ford.pcm_parameters(module, automatic=False) if module.name == "PCM" else []


def gt_parameters(module: ModuleAddress) -> list[ParameterDef]:
    return ford.pcm_parameters(module, automatic=True) if module.name == "PCM" else []


def _profile(key: str, name: str, short_name: str, year: int, parameters, vin: str, car_note: str) -> VehicleProfile:
    return VehicleProfile(
        key=key,
        name=name,
        short_name=short_name,
        make="Ford",
        year=year,
        modules=S550_MODULES,
        obd_module="PCM",
        diag=ford,
        module_parameters=parameters,
        broadcast_signals={},
        decode_frame=_no_signals,
        discovery_probes=DISCOVERY_PROBES,
        sweep_protocol="uds",
        response_offsets=(8,),
        explorer_services=EXPLORER_SERVICES,
        has_steering_page=False,
        default_favorites=FAVORITES,
        dtc_overlay=FORD_DESCRIPTIONS,
        sim_modules=SIM_MODULES,
        sim_vin=vin,
        prompt_notes=f"- {car_note}\n{DATA_NOTES}",
        gateway_note=GATEWAY_NOTE,
        low_voltage_note=LOW_VOLTAGE_NOTE,
        connect_note=CONNECT_NOTE,
    )


MUSTANG_GT350R = _profile(
    "mustang_gt350r_2016",
    "2016 Ford Mustang Shelby GT350R",
    "Mustang GT350R",
    2016,
    gt350r_parameters,
    "1FASAMPLEXXXXXXX1",
    "2016 Ford Mustang Shelby GT350R (S550): 5.2L Voodoo flat-plane V8, Tremec TR-3160 6-speed manual, "
    "MagneRide dampers (damping module address unknown). Transmission DID 1E1C does not apply.",
)
MUSTANG_GT = _profile(
    "mustang_gt_2017",
    "2017 Ford Mustang GT",
    "Mustang GT",
    2017,
    gt_parameters,
    "1FASAMPLEXXXXXXX2",
    "2017 Ford Mustang GT (S550): 5.0L Coyote V8, either MT-82 6-speed manual or 6R80 6-speed "
    "automatic. PCM DID 1E1C transmission fluid temperature answers only on the automatic.",
)
