import pytest

from q50diag.core.transport import NegativeResponse, TransportError
from q50diag.protocol import obd2
from q50diag.protocol.modules import module_by_name
from q50diag.protocol.reader import read_parameter
from q50diag.transport.sim import VIN, SimulatedTransport


@pytest.mark.parametrize(
    "pid, data, expected",
    [
        (0x0C, "1AF8", 1726),
        (0x0D, "64", 100),
        (0x05, "7B", 83),
        (0x10, "01F4", 5),
        (0x06, "80", 0),
        (0x06, "A0", 25),
        (0x07, "00", -100),
        (0x42, "36B0", 14),
        (0x0E, "80", 0),
        (0x22, "0100", 20.224),
        (0x23, "0100", 2560),
        (0x44, "8000", 1),
        (0x32, "FFF0", -0.004),
        (0x3C, "1388", 460),
        (0x4D, "0002", 120),
        (0x5C, "82", 90),
        (0x61, "7D", 0),
        (0xA6, "000186A0", 10000),
    ],
)
def test_pid_formulas(pid, data, expected):
    assert obd2.MODE01_PIDS[pid].decode(bytes.fromhex(data)) == pytest.approx(expected)


@pytest.mark.parametrize("pid", [0x05, 0x0C, 0x10, 0x42, 0xA6])
def test_truncated_numeric_data(pid):
    with pytest.raises(ValueError):
        obd2.MODE01_PIDS[pid].decode(b"")


def test_catalog_metadata_and_compounds():
    assert len(obd2.MODE01_PIDS) >= 200
    categories = {"Engine", "Fuel", "Air", "Emissions", "Electrical", "Vehicle", "Status"}
    for pid, param in obd2.MODE01_PIDS.items():
        assert param.key == f"obd.01.{pid:02X}"
        assert param.request == bytes((1, pid))
        assert param.module == "ECM" and param.category in categories
        assert param.name and param.description
    assert "Voltage" in obd2.MODE01_PIDS[0x24].decode(bytes.fromhex("80004000"))
    assert obd2.MODE01_PIDS[0x42].max_value == 65.535


@pytest.fixture
def sim():
    transport = SimulatedTransport()
    transport.open()
    yield transport
    transport.close()


def test_simulator_support_bitmaps_match_readable_pids(sim):
    supported = obd2.supported_pids(sim)
    assert {0x0C, 0x0D, 0x42, 0x63} <= set(supported)
    assert not set(supported) & {0, 0x20, 0x40, 0x60}
    assert supported == sorted(set(supported))
    for pid in supported:
        reading = read_parameter(sim, module_by_name("ECM"), obd2.MODE01_PIDS[pid])
        assert not reading.error, (pid, reading.error)


def test_supported_walk_reaches_c0():
    class Pages:
        def __init__(self):
            self.requests = []

        def request(self, tx, rx, payload):
            self.requests.append(payload)
            mask = 0x80000001 if payload[1] < 0xC0 else 0x80000000
            return b"\x41" + payload[1:] + mask.to_bytes(4, "big")

    transport = Pages()
    assert obd2.supported_pids(transport) == [1, 0x21, 0x41, 0x61, 0x81, 0xA1, 0xC1]
    assert transport.requests[-1] == b"\x01\xc0"


def test_monitor_status_spark_and_clear(sim):
    status = obd2.read_monitor_status(sim)
    assert status.mil_on and status.dtc_count == 1
    assert status.monitors["Misfire"] == "complete"
    assert status.monitors["Evaporative system"] == "incomplete"
    assert status.monitors["Heated catalyst"] == "n/a"
    obd2.clear_dtcs(sim)
    assert not obd2.read_monitor_status(sim).mil_on
    assert obd2.read_monitor_status(sim).monitors["Misfire"] == "incomplete"


def test_monitor_status_compression():
    class Diesel:
        def request(self, *args):
            return bytes.fromhex("41 01 00 0F EB 08")

    status = obd2.read_monitor_status(Diesel())
    assert status.monitors["Boost pressure"] == "incomplete"
    assert status.monitors["NOx/SCR"] == "complete"
    assert "Catalyst" not in status.monitors


def test_vin_freeze_frame_and_dtcs(sim):
    assert obd2.read_vin(sim) == VIN
    result = obd2.read_dtcs(sim)
    assert {(dtc.code, dtc.kind) for dtc in result} == {
        ("P0171", "stored"),
        ("P0300", "pending"),
        ("P0171", "permanent"),
    }
    frames = obd2.read_freeze_frame(sim)
    assert frames and not any(reading.error for reading in frames)
    assert next(reading.value for reading in frames if reading.key == "obd.02.02") == "P0171"
    before = {reading.key: reading.value for reading in frames}
    sim._start -= 40
    assert {reading.key: reading.value for reading in obd2.read_freeze_frame(sim)} == before
    obd2.clear_dtcs(sim)
    assert obd2.read_freeze_frame(sim) == []
    assert [dtc.kind for dtc in obd2.read_dtcs(sim)] == ["permanent"]


def test_functional_dtc_module_names():
    class Replies:
        def request_functional(self, request):
            if request != b"\x03":
                raise NegativeResponse(request[0], 0x11)
            return {0x7E8: b"\x43\x01\x01\x71", 0x7E9: b"\x43\x01\x07\x00", 0x7EA: b"\x43\x01\xd0\x00"}

    assert [dtc.module for dtc in obd2.read_dtcs(Replies())] == ["ECM", "TCM", "0x7EA"]


def test_malformed_dtc_response():
    with pytest.raises(TransportError, match="Truncated"):
        obd2._dtcs_from_can(bytes.fromhex("43 02 01 71"), 3, "ECM", "stored")


def test_legacy_multi_sensor_and_wide_scaling():
    normal = obd2.MODE01_PIDS[0x6F].decode(bytes((1, 100, 0)))
    wide = obd2.MODE01_PIDS[0x6F].decode(bytes((4, 100, 0)))
    assert "100 kPa" in normal and "800 kPa" in wide
    assert "n/a" in wide
    assert "360 degC" in obd2.MODE01_PIDS[0x6B].decode(bytes((16, 100, 0, 0, 0)))
    assert "100 kPa" in obd2.MODE01_PIDS[0x87].decode(bytes.fromhex("01 0C80 0000"))
    assert "sensor 5: 60 degC" in obd2.MODE01_PIDS[0x98].decode(bytes.fromhex("01 03E8 0000 0000 0000"))


def test_freeze_frame_bitmap_does_not_advertise_mode01_status(sim):
    reply = sim.request(0x7E0, 0x7E8, b"\x02\x00\x00")
    assert not int.from_bytes(reply[3:], "big") & (1 << 31)


def test_shrtft_ff_means_sensor_not_used_for_trim():
    # Q50 capture: 41 15 0D FF (bank 1 sensor 2, downstream, not used for trim).
    text = obd2.MODE01_PIDS[0x15].decode(bytes.fromhex("0D FF"))
    assert "SHRTFT associated with O2S12: n/a" in text
    assert "0.065 V" in text
