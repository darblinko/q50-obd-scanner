import threading
import time
from dataclasses import replace

import pytest

from q50diag.core.transport import NegativeResponse, NoResponse, RequestBlocked, TransportError
from q50diag.core.types import ParameterDef
from q50diag.protocol import broadcast, discovery, explorer, nissan
from q50diag.protocol.modules import module_by_name, module_for_response_id
from q50diag.protocol.reader import read_parameter
from q50diag.transport.elm import ElmTransport
from q50diag.transport.sim import SimulatedTransport
from tests.fake_serial import FakeSerial


@pytest.fixture
def sim():
    transport = SimulatedTransport(seed=42)
    transport.open()
    yield transport
    transport.close()


def test_discovery_all_simulated_modules_and_progress(sim):
    updates = []
    modules = discovery.discover_modules(sim, lambda *args: updates.append(args))
    assert len(modules) == 11
    assert {found.address.name for found in modules} == {module.name for module in sim.modules}
    eps = next(found for found in modules if found.address.name == "EPS")
    assert (eps.address.tx_id, eps.address.rx_id) == (0x742, 0x762)
    assert updates[-1][0] == updates[-1][1]
    assert [found.address.tx_id for found in modules] == sorted(found.address.tx_id for found in modules)


def test_discovery_unknown_address_and_stop(sim):
    real_request = sim.request

    def request(tx, rx, payload, **kwargs):
        if tx == 0x710 and rx == 0x718:
            return b"\x7e\x00"
        return real_request(tx, rx, payload, **kwargs)

    sim.request = request
    modules = discovery.discover_modules(sim, id_range=range(0x710, 0x711))
    unknown = next(found for found in modules if found.address.tx_id == 0x710)
    assert unknown.address.name == "0x710" and unknown.address.group == "Unknown"
    stop = threading.Event()
    updates = []

    def progress(*args):
        updates.append(args)
        stop.set()

    assert len(discovery.discover_modules(sim, progress, stop)) == 1
    assert len(updates) == 1


def test_module_lookup_and_identification(sim):
    assert module_by_name("eps") == module_for_response_id(0x762)
    assert module_by_name("ABS/VDC").name == "ABS"
    assert module_by_name("missing") is None
    assert module_for_response_id(0x123) is None
    for module in sim.modules:
        identification = nissan.read_identification(sim, module)
        assert len(identification) == 6
        assert "[" in identification["Part number"]


def test_nissan_dtcs_and_clear(sim):
    eps = module_by_name("EPS")
    dtcs = nissan.read_module_dtcs(sim, eps)
    assert [(dtc.code, dtc.kind) for dtc in dtcs] == [("C1604", "history")]
    assert nissan.read_module_dtcs(sim, module_by_name("ABS"))[0].code == "U1000"
    nissan.clear_module_dtcs(sim, eps)
    assert nissan.read_module_dtcs(sim, eps) == []


def test_known_nissan_parameters_decode(sim):
    for module in sim.modules:
        for parameter in nissan.module_parameters(module):
            reading = read_parameter(sim, module, parameter)
            assert reading.error == "", (parameter.key, reading)
            assert reading.value is not None
    eps_keys = {parameter.key for parameter in nissan.module_parameters(module_by_name("EPS"))}
    assert {
        "EPS.21.01",
        "EPS.21.01.supply",
        "EPS.21.01.angle",
        "EPS.21.01.torque",
        "EPS.21.01.assist",
        "EPS.21.01.b11",
    } <= eps_keys


def test_simulated_eps_record_matches_q50_layout(sim, monkeypatch):
    eps = module_by_name("EPS")
    fields = {parameter.key: parameter for parameter in nissan.eps_fields(eps)}
    reply = sim.request(eps.tx_id, eps.rx_id, b"\x21\x01")
    assert reply[:2] == b"\x61\x01" and len(reply) == 23
    assert 13.5 <= read_parameter(sim, eps, fields["EPS.21.01.supply"]).value <= 14.6
    abs_angle = next(p for p in nissan.module_parameters(module_by_name("ABS")) if p.request == b"\x22\x12\x08")
    torques = set()
    for elapsed in (1, 7, 13, 19):
        monkeypatch.setattr(sim, "_elapsed", lambda elapsed=elapsed: elapsed)
        values = {key: read_parameter(sim, eps, parameter).value for key, parameter in fields.items()}
        torques.add(values["EPS.21.01.torque"])
        assert values["EPS.21.01.angle"] == pytest.approx(read_parameter(sim, module_by_name("ABS"), abs_angle).value)
        assert values["EPS.21.01.assist"] == round(abs(values["EPS.21.01.torque"]) / 2)
        assert values["EPS.21.01.b11"] == 100
    assert len(torques) == 4 and min(torques) < 0 < max(torques)
    monkeypatch.setattr(sim, "_elapsed", lambda: 61)
    assert read_parameter(sim, eps, fields["EPS.21.01.assist"]).value == 0
    assert read_parameter(sim, eps, fields["EPS.21.01.supply"]).value < 12


def test_explorer_and_diff(sim):
    eps = module_by_name("EPS")
    progress = []
    local = explorer.scan_identifiers(sim, eps, 0x21, 0, 0x10, lambda *args: progress.append(args))
    assert set(local) == {1, 2, 3, 4, 5}
    assert progress[-1][:2] == (17, 17)
    extended = explorer.scan_identifiers(sim, eps, 0x22, 0x1000, 0x1006)
    assert set(extended) == set(range(0x1001, 0x1006))
    assert explorer.diff_snapshots({1: b"abc", 2: b"x"}, {1: b"aBcd", 3: b"yz"}) == {1: [1, 3], 2: [0], 3: [0, 1]}
    stop = threading.Event()
    stop.set()
    assert explorer.scan_identifiers(sim, eps, 0x21, 0, 255, stop=stop) == {}
    with pytest.raises(ValueError):
        explorer.scan_identifiers(sim, eps, 0x2E, 0, 10)


def test_reading_errors_are_data_but_connection_loss_propagates(sim):
    eps = module_by_name("EPS")
    parameter = ParameterDef("test", "test", "", "EPS", "Steering", b"\x21\xff", bytes.hex)
    assert "NRC 0x31" in read_parameter(sim, eps, parameter).error
    blocked = replace(parameter, request=b"\x27\x01")
    assert "allowlist" in read_parameter(sim, eps, blocked).error
    missing = replace(eps, tx_id=0x700, rx_id=0x720)
    assert "No simulated module" in read_parameter(sim, missing, parameter).error
    short = replace(parameter, request=b"\x21\x01", decode=lambda data: data[100])
    assert read_parameter(sim, eps, short).error
    sim.close()
    with pytest.raises(TransportError):
        read_parameter(sim, eps, parameter)


def test_monitor_broadcast_rates_filter_duration_and_stop(sim):
    frames = list(sim.monitor(threading.Event(), {0x002, 0x300}, max_duration_s=0.045))
    assert {frame.can_id for frame in frames} == {0x002, 0x300}
    assert all(broadcast.decode_frame(frame) for frame in frames)
    stop = threading.Event()
    iterator = sim.monitor(stop)
    assert next(iterator).can_id == 0x002
    stop.set()
    assert list(iterator) == []
    assert list(sim.monitor(threading.Event(), set(), 0.01)) == []
    assert sim.request(0x7E0, 0x7E8, b"\x01\x0c")[0] == 0x41


def test_stiff_steering_correlates_voltage_current_and_broadcast(sim, monkeypatch):
    eps = module_by_name("EPS")
    monkeypatch.setattr(sim, "_elapsed", lambda: 61)
    state = sim._sample()
    assert state.stiff and state.motor_current < 1 and 11 <= state.eps_voltage <= 11.4
    assert state.target_current > 3 and 13.8 <= state.voltage <= 14.4
    payload = sim.request(eps.tx_id, eps.rx_id, b"\x22\x10\x04")
    assert 1100 <= int.from_bytes(payload[3:], "big") <= 1140
    frame = next(sim.monitor(threading.Event(), {0x300}, max_duration_s=0.001))
    assert broadcast.decode_frame(frame)["bcast.300.assist_raw"] == 1
    sim.stiff_steering_events = False
    assert not sim._sample().stiff
    assert sim._sample().motor_current > 3


def test_drive_cycle_varies_and_warms(sim):
    samples = [sim._sample(second) for second in (0, 25, 55, 80, 300)]
    assert samples[0].speed == 0
    assert samples[1].load > samples[2].load
    assert samples[1].manifold > 100
    assert samples[3].speed < samples[2].speed
    assert samples[-1].coolant > samples[0].coolant


BLOCKED = [
    b"\x11\x01",
    b"\x27\x01",
    b"\x2e\x10\x01",
    b"\x31\x01",
    b"\x2f\x01",
    b"\x34\x00",
    b"\x10\x02",
    b"\x04",
    b"\x14\xff\x00",
    b"",
]


@pytest.mark.parametrize("payload", BLOCKED)
@pytest.mark.parametrize("transport_class", [SimulatedTransport, ElmTransport])
def test_safety_at_all_transport_entry_points(payload, transport_class, monkeypatch):
    fake = FakeSerial()
    monkeypatch.setattr("q50diag.transport.elm.serial.Serial", lambda *args, **kwargs: fake)
    transport = ElmTransport("fake", 115200) if transport_class is ElmTransport else SimulatedTransport()
    transport.open()
    before = len(fake.writes)
    try:
        with pytest.raises(RequestBlocked):
            transport.request(0x742, 0x762, payload)
        with pytest.raises(RequestBlocked):
            transport.request_functional(payload)
        with pytest.raises(RequestBlocked):
            transport.raw_command(payload.hex())
        assert len(fake.writes) == before
    finally:
        transport.close()


@pytest.mark.parametrize("line", ["ATPP00SV10", "STP", "ATBRD23", "ATBRT10", "ATI\r1101"])
def test_simulator_console_denylist(sim, line):
    with pytest.raises(RequestBlocked):
        sim.raw_command(line)


def test_simulator_console_and_unknown_addresses(sim):
    assert "simulated" in sim.raw_command("ATI")
    assert sim.raw_command("ATRV").endswith("V")
    sim.raw_command("ATSH742")
    assert sim.raw_command("2183").startswith("61 83")
    with pytest.raises(NoResponse):
        sim.request(0x700, 0x720, b"\x3e\x00")
    with pytest.raises(NegativeResponse) as caught:
        sim.request(0x742, 0x762, b"\x22\x00\x00")
    assert caught.value.code == 0x31


def test_simulator_request_latency_paces_requests():
    transport = SimulatedTransport(seed=42, request_latency_s=0.02)
    transport.open()
    # perf_counter: Windows time.monotonic ticks in ~15.6 ms steps and read 0.094 here.
    started = time.perf_counter()
    for _ in range(5):
        transport.request(0x7E0, 0x7E8, b"\x01\x0c")
    assert time.perf_counter() - started >= 0.1
    transport.close()


def test_gui_simulator_paces_requests_like_an_adapter():
    from q50diag.gui.backend import SIMULATOR, Backend

    transport = Backend().make_transport(SIMULATOR)
    assert transport.request_latency_s > 0
