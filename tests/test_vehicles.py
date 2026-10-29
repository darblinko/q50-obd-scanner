"""Vehicle profiles, the Ford UDS protocol module, the MechanIQ prompt builder
and the start-up selection of vehicle, adapter and bus."""

from __future__ import annotations

import pytest

from q50diag.__main__ import parse_args, resolve_selection
from q50diag.core.transport import NegativeResponse, NoResponse
from q50diag.core.types import ModuleAddress
from q50diag.gui.app_settings import AppSettings
from q50diag.mechaniq import prompt
from q50diag.protocol import ford, nissan
from q50diag.protocol.modules import KNOWN_MODULES
from q50diag.transport.adapters import adapter_by_key
from q50diag.vehicles import DEFAULT_VEHICLE_KEY, VEHICLES, default_vehicle, vehicle_by_key
from q50diag.vehicles.mustang import MUSTANG_GT, MUSTANG_GT350R, S550_MODULES
from q50diag.vehicles.q50 import Q50

PCM = ModuleAddress("PCM", 0x7E0, 0x7E8, "uds", "Powertrain")


# --- profiles -------------------------------------------------------------

def test_three_profiles_with_unique_keys_and_q50_default():
    assert [v.key for v in VEHICLES] == ["q50_2017", "mustang_gt350r_2016", "mustang_gt_2017"]
    assert default_vehicle() is Q50
    assert DEFAULT_VEHICLE_KEY == "q50_2017"
    with pytest.raises(KeyError):
        vehicle_by_key("no_such_car")


def test_q50_profile_wraps_existing_tables_unchanged():
    assert Q50.modules is KNOWN_MODULES
    assert Q50.diag is nissan
    assert Q50.obd_module == "ECM"
    assert Q50.has_steering_page
    assert [s for s, _, _ in Q50.explorer_services] == [0x21, 0x22]
    assert all(m.bus == "hs" for m in Q50.modules)
    assert Q50.module("ABS/VDC").name == "ABS"  # alias
    assert Q50.module_for_response_id(0x762).name == "EPS"


def test_mustang_modules_respond_at_request_plus_8():
    names = [m.name for m in S550_MODULES]
    assert names == ["PCM", "ABS", "IPC", "PSCM", "RCM", "BCM", "ACM", "APIM", "HVAC"]
    assert all(m.rx_id == m.tx_id + 8 and m.protocol == "uds" for m in S550_MODULES)
    assert MUSTANG_GT.module("PCM").tx_id == 0x7E0
    assert MUSTANG_GT.module_for_response_id(0x738).name == "PSCM"


@pytest.mark.parametrize("vehicle", [MUSTANG_GT350R, MUSTANG_GT])
def test_mustang_profiles_use_ford_uds_without_steering(vehicle):
    assert vehicle.diag is ford
    assert vehicle.obd_module == "PCM"
    assert not vehicle.has_steering_page
    assert vehicle.explorer_services == ((0x22, "F400", "F4FF"),)
    assert vehicle.sweep_protocol == "uds"
    assert vehicle.response_offsets == (8,)
    assert vehicle.broadcast_signals == {}
    assert vehicle.sim_vin.startswith("1FA")


def test_modules_on_bus_filters_by_bus():
    assert Q50.modules_on_bus("hs") == KNOWN_MODULES
    assert Q50.modules_on_bus("ms") == ()


def test_only_the_automatic_gt_gets_the_transmission_did():
    gt350r = [p.key for p in MUSTANG_GT350R.module_parameters(MUSTANG_GT350R.module("PCM"))]
    gt = [p.key for p in MUSTANG_GT.module_parameters(MUSTANG_GT.module("PCM"))]
    assert "PCM.22.1E1C" not in gt350r
    assert "PCM.22.1E1C" in gt
    assert set(gt) - set(gt350r) == {"PCM.22.1E1C"}
    assert {"PCM.22.03EC", "PCM.22.F405", "PCM.22.F40F", "PCM.22.F42F", "PCM.22.0304", "PCM.22.054B"} <= set(gt350r)
    assert MUSTANG_GT.module_parameters(MUSTANG_GT.module("ABS")) == []


def test_every_ford_parameter_states_its_confidence():
    for parameter in ford.pcm_parameters(PCM, automatic=True):
        assert "Confidence" in parameter.description, parameter.key
        assert parameter.request[0] == 0x22


# --- Ford decoders --------------------------------------------------------

def test_ford_decoders():
    assert ford.decode_transmission_temp(bytes([0x01, 0x90])) == 25.0
    assert ford.decode_transmission_temp(bytes([0xFF, 0xF0])) == -1.0
    assert ford.decode_knock_retard(bytes([0xFE, 0x00])) == -1.0
    assert ford.decode_knock_retard(bytes([0x02, 0x80])) == pytest.approx(1.25)
    assert ford.decode_temp_offset_40(bytes([0x5A])) == 50
    assert ford.decode_percent_255(bytes([0xFF])) == 100
    assert ford.decode_battery_voltage(bytes([0x6F, 0x00])) == pytest.approx(13.875)
    assert ford.decode_percent(bytes([73])) == 73
    with pytest.raises(ValueError):
        ford.decode_battery_voltage(b"\x01")


def test_ford_format_dtc_shows_raw_status_byte():
    from q50diag.protocol.dtc import decode_uds_dtc_response

    dtcs = decode_uds_dtc_response(bytes([0x59, 0x02, 0xFF, 0x01, 0x71, 0x00, 0xAC]), "PCM", ford.describe)
    assert ford.format_dtc(dtcs[0]) == "P0171-00 (status AC)"


# --- Ford requests over a scripted transport ------------------------------

class ScriptedTransport:
    """Answers from a dict keyed by payload; a list value is consumed in order."""

    def __init__(self, replies):
        self.replies = replies
        self.sent = []

    def request(self, tx_id, rx_id, payload, timeout_s=1.0, allow_clear=False):
        self.sent.append(payload)
        reply = self.replies.get(payload)
        if isinstance(reply, list):
            reply = reply.pop(0)
        if reply is None:
            raise NoResponse("silent")
        if isinstance(reply, Exception):
            raise reply
        return reply


def test_ford_request_retries_once_in_extended_session():
    ford.reset_sessions()
    transport = ScriptedTransport({
        b"\x22\x03\xEC": [NegativeResponse(0x22, 0x7F), b"\x62\x03\xEC\x00\x00"],
        b"\x10\x03": b"\x50\x03",
    })
    assert ford.request(transport, PCM, b"\x22\x03\xEC") == b"\x62\x03\xEC\x00\x00"
    assert transport.sent == [b"\x22\x03\xEC", b"\x10\x03", b"\x22\x03\xEC"]


def test_ford_request_does_not_retry_other_negative_responses():
    ford.reset_sessions()
    transport = ScriptedTransport({b"\x22\x03\xEC": NegativeResponse(0x22, 0x31)})
    with pytest.raises(NegativeResponse):
        ford.request(transport, PCM, b"\x22\x03\xEC")
    assert transport.sent == [b"\x22\x03\xEC"]


def test_ford_identification_and_dtcs():
    ford.reset_sessions()
    transport = ScriptedTransport({
        b"\x22\xF1\x90": b"\x62\xF1\x90" + b"1FASAMPLEXXXXXXX2",
        b"\x22\xF1\x88": b"\x62\xF1\x88" + b"FR3A-AB",
        b"\x19\x02\xFF": bytes([0x59, 0x02, 0xFF, 0xC1, 0x00, 0x28, 0x09]),
    })
    ident = ford.read_identification(transport, PCM)
    assert ident["VIN"].startswith("1FASAMPLEXXXXXXX2 [")
    assert ident["Software part number"].startswith("FR3A-AB")
    assert "ECU part number" not in ident  # no reply
    dtcs = ford.read_module_dtcs(transport, PCM)
    assert [(d.code, d.status, d.module) for d in dtcs] == [("U0100-28", 0x09, "PCM")]


def test_ford_clear_uses_uds_14_with_allow_clear():
    ford.reset_sessions()
    transport = ScriptedTransport({b"\x14\xFF\xFF\xFF": b"\x54"})
    ford.clear_module_dtcs(transport, PCM)
    assert transport.sent == [b"\x14\xFF\xFF\xFF"]


# --- MechanIQ prompt ------------------------------------------------------

def test_default_system_append_describes_q50_and_vlinker():
    assert prompt.SYSTEM_APPEND == prompt.build_system_append(Q50, adapter_by_key("vlinker_fs"), "hs")
    assert "2017 Infiniti Q50 3.0t" in prompt.SYSTEM_APPEND
    assert "Vgate vLinker FS USB on HS-CAN" in prompt.SYSTEM_APPEND
    assert "EPS module at request 0x742" in prompt.SYSTEM_APPEND


def test_system_append_follows_vehicle_adapter_and_bus():
    text = prompt.build_system_append(MUSTANG_GT350R, adapter_by_key("obdlink_ex"), "ms")
    assert "Shelby GT350R" in text
    assert "OBDLink EX USB on MS-CAN 125 kbps" in text
    assert "0x742" not in text
    assert "## Repository layout" in text and "apply_change" in text


# --- start-up selection ---------------------------------------------------

def test_cli_vehicle_option_and_selection():
    assert parse_args(["--vehicle", "mustang_gt_2017", "--sim"]).vehicle == "mustang_gt_2017"
    assert parse_args([]).vehicle is None
    with pytest.raises(SystemExit):
        parse_args(["--vehicle", "corolla"])
    vehicle, adapter, bus = resolve_selection(AppSettings("mustang_gt350r_2016", "obdlink_ex", "ms"))
    assert (vehicle, adapter, bus) == (MUSTANG_GT350R, "obdlink_ex", "ms")
    vehicle, _, _ = resolve_selection(AppSettings("mustang_gt350r_2016"), "q50_2017")
    assert vehicle is Q50


def test_selection_falls_back_on_unknown_saved_keys():
    vehicle, adapter, bus = resolve_selection(AppSettings("corolla", "elm_clone", "lin"))
    assert (vehicle, adapter, bus) == (Q50, "vlinker_fs", "hs")


def test_build_options_takes_a_vehicle_specific_append(tmp_path):
    from q50diag.mechaniq.runner import build_options
    from q50diag.mechaniq.settings import MechaniqSettings

    text = prompt.build_system_append(MUSTANG_GT)
    options = build_options(MechaniqSettings(), tmp_path, None, None, base_env={}, system_append=text)
    assert options.system_prompt["append"] == text
    default = build_options(MechaniqSettings(), tmp_path, None, None, base_env={})
    assert default.system_prompt["append"] == prompt.SYSTEM_APPEND
