"""The simulator per vehicle profile, bus filtering in scans and discovery,
and the app settings file."""

from __future__ import annotations

import json
import threading
from dataclasses import replace

import pytest

from q50diag.core.transport import NegativeResponse
from q50diag.gui.app_settings import AppSettings, load_app_settings, save_app_settings
from q50diag.gui.backend import SIM_REQUEST_LATENCY_S, SIMULATOR, Backend
from q50diag.protocol import ford
from q50diag.protocol.discovery import discover_modules
from q50diag.transport.sim import SimulatedTransport
from q50diag.vehicles.mustang import MUSTANG_GT, MUSTANG_GT350R
from q50diag.vehicles.q50 import Q50


def open_backend(vehicle, bus="hs"):
    backend = Backend(vehicle=vehicle, bus=bus)
    transport = backend.make_transport(SIMULATOR)
    transport.open()
    return backend, transport


def test_simulator_keeps_request_latency_for_every_vehicle():
    for vehicle in (Q50, MUSTANG_GT):
        transport = Backend(vehicle=vehicle).make_transport(SIMULATOR)
        assert transport.request_latency_s == SIM_REQUEST_LATENCY_S
        assert transport.vehicle is vehicle


@pytest.mark.parametrize(
    "vehicle, vin",
    [
        (Q50, "JN1SAMPLEXXXXXXX0"),
        (MUSTANG_GT350R, "1FASAMPLEXXXXXXX1"),
        (MUSTANG_GT, "1FASAMPLEXXXXXXX2"),
    ],
)
def test_simulator_vin_per_vehicle(vehicle, vin):
    backend, transport = open_backend(vehicle)
    assert backend.read_vin(transport) == vin


def test_q50_simulator_behaviour_unchanged():
    backend, transport = open_backend(Q50)
    eps = backend.module_by_name("EPS")
    assert backend.read_identification(transport, eps)
    names = {found.address.name for found in backend.discover_modules(transport, None, None)}
    assert names == set(Q50.sim_modules)
    assert {d.module for d in backend.read_dtcs(transport)} <= {"ECM", "TCM"}


def test_ford_simulator_pcm_dids_and_obd_module_name():
    backend, transport = open_backend(MUSTANG_GT)
    pcm = backend.module_by_name("PCM")
    readings = {p.key: backend.read_parameter(transport, pcm, p) for p in backend.module_parameters(pcm)}
    assert not [r for r in readings.values() if r.error], readings
    assert -40 <= readings["PCM.22.F405"].value <= 130
    assert 10 <= readings["PCM.22.0304"].value <= 15
    assert "PCM.22.1E1C" in readings
    assert {d.module for d in backend.read_dtcs(transport)} <= {"PCM"}


def test_manual_gt350r_does_not_answer_the_transmission_did():
    backend, transport = open_backend(MUSTANG_GT350R)
    pcm = backend.module_by_name("PCM")
    assert "PCM.22.1E1C" not in [p.key for p in backend.module_parameters(pcm)]
    with pytest.raises(NegativeResponse):
        ford.request(transport, pcm, b"\x22\x1e\x1c", timeout_s=0.1)


def test_ford_simulator_identification_dtcs_and_discovery():
    backend, transport = open_backend(MUSTANG_GT350R)
    pcm = backend.module_by_name("PCM")
    ident = backend.read_identification(transport, pcm)
    assert ident["VIN"].startswith("1FASAMPLEXXXXXXX1")
    abs_codes = [d.code for d in backend.read_module_dtcs(transport, backend.module_by_name("ABS"))]
    assert abs_codes == ["U0100-00"]
    found = {f.address.name for f in backend.discover_modules(transport, None, None)}
    assert found == set(MUSTANG_GT350R.sim_modules)


def test_ford_simulator_monitor_yields_no_broadcast_frames():
    _, transport = open_backend(MUSTANG_GT)
    assert list(transport.monitor(threading.Event(), None, 0.05)) == []


def test_simulator_only_answers_modules_on_the_selected_bus():
    moved = tuple(replace(m, bus="ms") if m.name == "IPC" else m for m in MUSTANG_GT.modules)
    vehicle = replace(MUSTANG_GT, modules=moved)
    assert "IPC" not in {m.name for m in SimulatedTransport(vehicle=vehicle, bus="hs").modules}
    assert {m.name for m in SimulatedTransport(vehicle=vehicle, bus="ms").modules} == {"IPC"}


# --- bus filtering in scans and discovery ---------------------------------


class RecordingTransport:
    def __init__(self):
        self.sent = []

    def request(self, tx_id, rx_id, payload, timeout_s=1.0, allow_clear=False):
        self.sent.append(tx_id)
        from q50diag.core.transport import NoResponse

        raise NoResponse("silent bus")


def test_scan_and_discovery_skip_modules_on_another_bus():
    moved = tuple(replace(m, bus="ms") if m.name in ("IPC", "APIM") else m for m in MUSTANG_GT.modules)
    backend = Backend(vehicle=replace(MUSTANG_GT, modules=moved), bus="hs")
    assert {m.name for m in backend.scan_modules()} == {m.name for m in moved} - {"IPC", "APIM"}
    transport = RecordingTransport()
    backend.discover_modules(transport, None, None)
    off_bus = {0x720, 0x728, 0x7D0, 0x7D8}
    assert not off_bus & set(transport.sent)
    assert 0x7E0 in transport.sent


def test_uds_sweep_listens_at_request_plus_8_and_never_sends_on_response_ids():
    transport = RecordingTransport()
    discover_modules(
        transport,
        modules=MUSTANG_GT.modules,
        probes=MUSTANG_GT.discovery_probes,
        sweep_protocol="uds",
        response_offsets=(8,),
    )
    response_ids = {m.rx_id for m in MUSTANG_GT.modules}
    assert not response_ids & set(transport.sent)


# --- app settings ---------------------------------------------------------


def test_app_settings_roundtrip_and_defaults(tmp_path):
    path = tmp_path / "cfg" / "app.json"
    assert load_app_settings(path) == AppSettings()
    save_app_settings(AppSettings("mustang_gt_2017", "obdlink_ex", "ms"), path)
    assert json.loads(path.read_text()) == {"vehicle": "mustang_gt_2017", "adapter": "obdlink_ex", "bus": "ms"}
    assert load_app_settings(path) == AppSettings("mustang_gt_2017", "obdlink_ex", "ms")
    assert [p.name for p in path.parent.iterdir()] == ["app.json"]  # no temporary file left


def test_app_settings_tolerate_bad_files(tmp_path):
    path = tmp_path / "app.json"
    path.write_text("{not json")
    assert load_app_settings(path) == AppSettings()
    path.write_text("[1, 2]")
    assert load_app_settings(path) == AppSettings()
    path.write_text(json.dumps({"vehicle": "mustang_gt_2017", "bus": 5}))
    assert load_app_settings(path) == AppSettings(vehicle="mustang_gt_2017")


@pytest.mark.parametrize("bus", ["hs", "ms"])
def test_modules_with_uncertain_bus_are_probed_on_the_selected_bus(bus):
    uncertain = {"ABS", "IPC", "APIM"}
    assert {m.name for m in MUSTANG_GT.modules if m.bus == "any"} == uncertain
    backend = Backend(vehicle=MUSTANG_GT, bus=bus)
    assert uncertain <= {m.name for m in backend.scan_modules()}
    transport = RecordingTransport()
    backend.discover_modules(transport, None, None)
    assert {0x720, 0x760, 0x7D0} <= set(transport.sent)


def test_simulator_places_uncertain_modules_on_hs():
    assert {"ABS", "IPC", "APIM"} <= {m.name for m in SimulatedTransport(vehicle=MUSTANG_GT, bus="hs").modules}
    assert SimulatedTransport(vehicle=MUSTANG_GT, bus="ms").modules == ()
