import threading
import time

import pytest

from q50diag.core.transport import NegativeResponse, TransportError
from q50diag.core.types import ParameterDef
from q50diag.gui.backend import SIMULATOR
from q50diag.session import (
    ADAPTER_MODULE,
    BROADCAST_MODULE,
    VOLTAGE_PARAM,
    LoggingTransport,
    Session,
    broadcast_id,
    broadcast_param,
    request_groups,
)
from tests.test_gui_fakes import (  # noqa: F401
    ABS,
    ABS_ANGLE,
    BCM,
    ECM,
    EPS,
    EPS_FIELDS,
    RPM,
    SPEED,
    FakeBackend,
    app,
    wait_until,
)


class Collector:
    """Records every emission of the given session signals."""

    def __init__(self, session: Session, *names: str) -> None:
        self.events: dict[str, list] = {name: [] for name in names}
        for name in names:
            getattr(session, name).connect(lambda *args, n=name: self.events[n].append(args))

    def __getitem__(self, name: str) -> list:
        return self.events[name]


@pytest.fixture
def backend():
    return FakeBackend()


@pytest.fixture
def session(app, backend):
    instance = Session(backend)
    yield instance
    instance.shutdown()


def connect(session: Session) -> None:
    session.connect_port(SIMULATOR)
    assert wait_until(lambda: session.connected)


def test_connect_reads_overview_and_reports(session, backend):
    got = Collector(session, "connection_changed", "overview_ready", "voltage_changed")
    connect(session)
    assert wait_until(lambda: got["overview_ready"])
    connected, info = got["connection_changed"][0]
    assert connected and info.identity == "ELM327 v2.3"
    overview = got["overview_ready"][0][0]
    assert overview.vin == "JN1SAMPLEXXXXXXX1"
    assert overview.monitor.mil_on and overview.supported_pids == [0x05, 0x0C, 0x0D]
    assert session.supported_pids == [0x05, 0x0C, 0x0D]


def test_commands_without_connection_report_error(session):
    got = Collector(session, "error")
    session.read_vehicle_overview()
    assert wait_until(lambda: got["error"])
    assert "not connected" in got["error"][0][0]


def test_poll_loop_emits_readings_round_robin(session, backend):
    connect(session)
    got = Collector(session, "readings", "poll_rate")
    session.start_polling("live", [(ECM, RPM), (ECM, SPEED), (ABS, ABS_ANGLE), (ADAPTER_MODULE, VOLTAGE_PARAM)])
    assert wait_until(
        lambda: (
            {r.key for batch in got["readings"] for r in batch[0]}
            >= {RPM.key, SPEED.key, ABS_ANGLE.key, VOLTAGE_PARAM.key}
        )
    )
    assert wait_until(lambda: any(rate[0] > 0 for rate in got["poll_rate"]), 3)
    session.stop_polling("live")
    assert session.polling_owners() == []
    reads = backend.reads
    time.sleep(0.3)
    assert backend.reads - reads <= 1


def test_poll_errors_become_reading_errors(session):
    connect(session)
    got = Collector(session, "readings", "error", "connection_changed")
    bcm_param = ParameterDef("BCM.21.01", "BCM item", "", "BCM", "Body", b"\x21\x01", lambda d: d[0])
    session.start_polling("live", [(BCM, bcm_param)])
    assert wait_until(lambda: any(r.key == "BCM.21.01" and r.error for b in got["readings"] for r in b[0]))
    assert session.connected and not got["error"]


def test_request_groups_share_module_and_request():
    eps_subs = [(EPS, param) for param in EPS_FIELDS]
    voltage = (ADAPTER_MODULE, VOLTAGE_PARAM)
    groups = request_groups([(ECM, SPEED), *eps_subs, voltage, (ECM, SPEED), (ABS, ABS_ANGLE)])
    assert [[param.key for _module, param in group] for group in groups] == [
        [SPEED.key],
        [param.key for param in EPS_FIELDS],
        [VOLTAGE_PARAM.key],
        [ABS_ANGLE.key],
    ]


def test_poll_step_sends_one_request_for_shared_fields(session, backend):
    got = Collector(session, "overview_ready")
    connect(session)
    assert wait_until(lambda: got["overview_ready"])  # worker idle from here on: no subscriptions
    transport = backend.transports[-1]
    sent = len(transport.requests)
    session._poll_step([(EPS, param) for param in EPS_FIELDS] + [(ECM, SPEED)])
    assert [payload for _tx, payload, _clear in transport.requests[sent:]] == [b"\x21\x01"]
    readings = {reading.key: reading for reading in session._pending}
    assert set(readings) == {param.key for param in EPS_FIELDS}
    assert abs(readings["EPS.21.01.supply"].value - 14.068) < 1e-9
    assert readings["EPS.21.01.b10"].value == 53 and readings["EPS.21.01.b11"].value == 100
    session._pending.clear()
    bcm_params = [
        ParameterDef(f"BCM.21.01.b{i}", "BCM item", "", "BCM", "Body", b"\x21\x01", lambda d: d[0]) for i in (0, 1)
    ]
    sent = len(transport.requests)
    errors = session._read_group([(BCM, param) for param in bcm_params])
    assert len(transport.requests) == sent + 1
    assert [reading.key for reading in errors] == ["BCM.21.01.b0", "BCM.21.01.b1"]
    assert all(reading.error and reading.value is None for reading in errors)


def test_steering_watch_captures_broadcast_between_rounds(session):
    connect(session)
    got = Collector(session, "readings", "frames")
    subs = [
        (ECM, SPEED),
        (BROADCAST_MODULE, broadcast_param("bcast.300.assist_raw", "Assist", "")),
        (BROADCAST_MODULE, broadcast_param("bcast.5E4.ps_warning_lamp", "Lamp", "")),
    ]
    session.start_steering_watch(subs)
    assert wait_until(
        lambda: (
            {r.key for b in got["readings"] for r in b[0]}
            >= {SPEED.key, "bcast.300.assist_raw", "bcast.5E4.ps_warning_lamp"}
        )
    )
    ids = {f.can_id for batch in got["frames"] for f in batch[0]}
    assert ids <= {0x300, 0x5E4}
    session.stop_steering_watch()


def test_bus_monitor_emits_filtered_frames(session):
    connect(session)
    got = Collector(session, "frames")
    session.start_bus_monitor({0x002})
    assert wait_until(lambda: got["frames"])
    session.stop_bus_monitor()
    assert {f.can_id for batch in got["frames"] for f in batch[0]} == {0x002}


def test_scan_all_dtcs_covers_obd_and_modules(session):
    connect(session)
    got = Collector(session, "dtcs_ready", "progress")
    session.scan_all_dtcs()
    assert wait_until(lambda: got["dtcs_ready"])
    result = got["dtcs_ready"][0][0]
    assert {d.code for d in result.dtcs} == {"P0300", "P0171", "C1601", "C160A"}
    assert result.module_status["OBD-II"] == "ok"
    assert result.module_status["BCM"] == "no response"
    assert result.freeze_frame and result.complete
    assert got["progress"][-1][1] == got["progress"][-1][2]


def test_scan_reports_partial_and_failed_dtc_reads(session, backend, monkeypatch):
    backend.dtc_mode_failures = {"permanent": "negative response to 0x0A: NRC 0x12"}
    original = backend.read_module_dtcs

    def module_dtcs(t, m):
        if m.name == "ABS":
            raise NegativeResponse(0x18, 0x11)
        if m.name == "EPS":
            raise ValueError("Truncated KWP DTC response")
        return original(t, m)

    monkeypatch.setattr(backend, "read_module_dtcs", module_dtcs)
    connect(session)
    got = Collector(session, "dtcs_ready")
    session.scan_all_dtcs()
    assert wait_until(lambda: got["dtcs_ready"])
    status = got["dtcs_ready"][0][0].module_status
    assert status["OBD-II"] == "partial: permanent: negative response to 0x0A: NRC 0x12"
    assert status["ABS"] == "negative response to 0x18: NRC 0x11"
    assert status["EPS"] == "malformed reply: Truncated KWP DTC response"


def test_clear_dtcs_uses_allow_clear_only_on_request(session, backend):
    connect(session)
    got = Collector(session, "dtcs_cleared")
    session.clear_dtcs(EPS)
    session.clear_dtcs(None)
    assert wait_until(lambda: len(got["dtcs_cleared"]) == 2)
    assert backend.cleared == ["EPS", "OBD-II"]
    transport = backend.transports[-1]
    assert [p for _, p, allow in transport.requests if allow] == [b"\x14\xff\x00", b"\x04"]


def test_discovery_full_scan_and_cancel(session, backend):
    connect(session)
    got = Collector(session, "modules_discovered", "module_read", "dtcs_ready")
    session.full_scan()
    assert wait_until(lambda: len(got["module_read"]) == 4, 5)
    assert [d.address.name for d in session.discovered] == ["ECM", "ABS", "EPS", "0x71D"]
    assert got["dtcs_ready"]

    cancelled = threading.Event()
    original = backend.discover_modules

    def slow_discovery(t, progress, stop):
        stop.wait(2)
        cancelled.set()
        return original(t, progress, stop)

    backend.discover_modules = slow_discovery
    session.discover_modules()
    time.sleep(0.1)
    session.cancel()
    assert wait_until(lambda: cancelled.is_set() and len(got["modules_discovered"]) == 2, 3)
    assert got["modules_discovered"][-1][0] == []


def test_read_module_reports_identification_and_responding(session):
    connect(session)
    got = Collector(session, "module_read")
    session.read_module(EPS)
    session.read_module(BCM)
    assert wait_until(lambda: len(got["module_read"]) == 2)
    eps, bcm = (args[0] for args in got["module_read"])
    assert eps.responding and eps.identification["Part number"] == "EPS-4GA0A"
    assert [d.code for d in eps.dtcs] == ["C1601", "C160A"]
    assert eps.parameters and eps.parameters[0].key == "EPS.21.01.supply"
    assert not bcm.responding and bcm.errors


def test_raw_session_control_forgets_cached_sessions(session, backend, monkeypatch):
    connect(session)
    resets = []
    monkeypatch.setattr(backend, "reset_sessions", lambda: resets.append(True))
    got = Collector(session, "raw_response")
    session.raw_request(EPS, bytes.fromhex("2101"))
    session.raw_request(EPS, bytes.fromhex("1081"))
    session.raw_command("10 C0")
    assert wait_until(lambda: len(got["raw_response"]) == 3)
    assert len(resets) == 2


def test_scan_identifiers_and_raw(session):
    connect(session)
    got = Collector(session, "identifiers_scanned", "raw_response", "raw_log")
    session.scan_identifiers(EPS, 0x21, 0x00, 0x0F)
    session.raw_command("ATI")
    session.raw_request(EPS, bytes.fromhex("2101"))
    session.raw_request(BCM, bytes.fromhex("2101"))
    session.raw_request(EPS, bytes.fromhex("2701"))
    assert wait_until(lambda: got["identifiers_scanned"] and len(got["raw_response"]) == 3)
    scan = got["identifiers_scanned"][0][0]
    assert sorted(scan.results) == [0x01, 0x05, 0x09, 0x0D]
    assert got["raw_response"][0] == ("ATI", "ELM327 v2.3")
    assert got["raw_response"][1][1].startswith("61 01")
    assert got["raw_response"][2][1] == "NO DATA"
    assert ("TX", "742  21 01") in got["raw_log"]


def test_blocked_request_reports_safety_gate(session):
    connect(session)
    got = Collector(session, "error")
    session.raw_request(EPS, bytes.fromhex("2701"))
    assert wait_until(lambda: got["error"])
    assert "safety gate" in got["error"][0][0]
    assert session.connected


def test_transport_error_marks_disconnected(session, backend):
    connect(session)
    got = Collector(session, "connection_changed", "error")
    transport = backend.transports[-1]

    def broken(*args, **kwargs):
        raise TransportError("serial port vanished")

    transport.request = broken
    session.start_polling("live", [(ECM, RPM)])
    # connection_changed is emitted before the error, so wait for both queued signals.
    assert wait_until(lambda: not session.connected and got["connection_changed"] and got["error"])
    assert got["connection_changed"][-1] == (False, None)
    assert any("Connection lost" in e[0] for e in got["error"])
    assert session.polling_owners() == []


def test_disconnect_closes_transport(session, backend):
    connect(session)
    session.disconnect_port()
    assert wait_until(lambda: not session.connected)
    assert not backend.transports[-1].is_open


def test_helpers():
    assert broadcast_id("bcast.5E4.ps_warning_lamp") == 0x5E4
    param = broadcast_param("bcast.002.steering_angle", "Angle", "deg")
    assert param.module == "BROADCAST" and param.category == "Broadcast"
    lines = []
    from tests.test_gui_fakes import FakeTransport

    wrapped = LoggingTransport(FakeTransport(), lambda d, t: lines.append((d, t)))
    wrapped.request(0x7E0, 0x7E8, b"\x01\x0c")
    assert lines[0] == ("TX", "7E0  01 0C") and lines[1][0] == "RX"
    assert wrapped.port == "FAKE"
