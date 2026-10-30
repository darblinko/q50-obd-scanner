import logging
import threading
import time

import pytest

from q50diag.core.transport import AdapterReplyError, NegativeResponse, NoResponse, RequestBlocked, TransportError
from q50diag.transport.adapters import adapter_by_key
from q50diag.transport.elm import ElmTransport
from tests.fake_serial import FakeSerial


@pytest.fixture
def elm(monkeypatch):
    fake = FakeSerial()
    monkeypatch.setattr("q50diag.transport.elm.serial.Serial", lambda *args, **kwargs: fake)
    transport = ElmTransport("fake", 115200)
    transport.open()
    yield transport, fake
    transport.close()


def test_initialization_and_voltage(elm):
    transport, fake = elm
    commands = [data.decode().strip() for data in fake.writes]
    for command in (
        "ATZ",
        "ATE0",
        "ATL0",
        "ATS1",
        "ATH1",
        "ATAT1",
        "STI",
        "STDI",
        "STP33",
        "ATCAF1",
        "ATI",
        "ATRV",
        "0100",
        "ATDP",
    ):
        assert command in commands
    assert not any(command.startswith("ATSP") for command in commands)
    assert commands.index("0100") < commands.index("ATDP")
    assert transport.read_voltage() == 12.4


def test_baudrate_detection(monkeypatch):
    attempts = []

    def factory(port, baudrate, **kwargs):
        fake = FakeSerial(port, baudrate, **kwargs)
        attempts.append(fake)
        if baudrate != 500000:
            fake.replies["ATI"] = "unrecognized"
        return fake

    monkeypatch.setattr("q50diag.transport.elm.serial.Serial", factory)
    transport = ElmTransport("fake")
    assert transport.open().identity == "ELM327 v2.2"
    assert [fake.baudrate for fake in attempts] == [115200, 38400, 500000]
    assert all(fake.closed for fake in attempts[:-1])
    transport.close()


def test_single_multi_cache_and_debug_logging(elm, caplog):
    transport, fake = elm
    caplog.set_level(logging.DEBUG, logger="q50diag.elm")
    fake.writes.clear()
    assert transport.request(0x7E0, 0x7E8, b"\x01\x0c") == bytes.fromhex("41 0C 1A F8")
    assert transport.request(0x7E0, 0x7E8, b"\x09\x02") == b"\x49\x02\x01JN1SAMPLEXXXXXXX0"
    assert fake.writes.count(b"ATSH7E0\r") == 1
    assert fake.writes.count(b"ATCRA7E8\r") == 1
    assert fake.writes.count(b"ATSTFA\r") == 1
    assert "TX 010C" in caplog.text and "RX 7E8" in caplog.text
    fake.replies["2183"] = "762 03 61 83 01"
    transport.request(0x742, 0x762, b"\x21\x83", timeout_s=0.08)
    assert b"ATST14\r" in fake.writes
    assert b"ATSH742\r" in fake.writes and b"ATCRA762\r" in fake.writes


def test_pending_positive_before_prompt(elm):
    transport, fake = elm
    fake.replies["221201"] = "7E8 03 7F 22 78\r7E8 05 62 12 01 01 00"
    assert transport.request(0x7E0, 0x7E8, b"\x22\x12\x01") == b"\x62\x12\x01\x01\x00"


def test_pending_after_prompt_retries_with_long_timeout(elm):
    transport, fake = elm
    fake.replies["221201"] = ["7E8 03 7F 22 78", "7E8 05 62 12 01 01 00"]
    assert transport.request(0x7E0, 0x7E8, b"\x22\x12\x01") == b"\x62\x12\x01\x01\x00"
    commands = [data.decode().strip() for data in fake.writes]
    assert commands.index("ATAT0") < commands.index("ATSTFF") < len(commands) - 1
    assert commands[-1] == "ATAT1"
    assert b" " not in fake.writes


def test_pending_that_never_completes_is_no_response_not_disconnect(elm):
    transport, fake = elm
    fake.replies["221201"] = "7E8 03 7F 22 78"
    with pytest.raises(NoResponse):
        transport.request(0x7E0, 0x7E8, b"\x22\x12\x01")
    assert not fake.closed
    assert transport.request(0x7E0, 0x7E8, b"\x01\x0c")[0] == 0x41


def test_negative_no_data_and_adapter_errors(elm):
    transport, fake = elm
    fake.replies["221201"] = "7E8 03 7F 22 31"
    with pytest.raises(NegativeResponse):
        transport.request(0x7E0, 0x7E8, b"\x22\x12\x01")
    with pytest.raises(NoResponse):
        transport.request(0x7E0, 0x7E8, b"\x01\xff")
    fake.replies["010C"] = "CAN ERROR"
    with pytest.raises(AdapterReplyError, match="CAN ERROR"):
        transport.request(0x7E0, 0x7E8, b"\x01\x0c")
    assert not fake.closed


def test_functional_responses(elm):
    transport, fake = elm
    fake.replies["03"] = "7E8 04 43 01 01 71\r7E9 04 43 01 07 00"
    result = transport.request_functional(b"\x03")
    assert result == {0x7E8: b"\x43\x01\x01\x71", 0x7E9: b"\x43\x01\x07\x00"}
    assert b"ATAR\r" in fake.writes


@pytest.mark.parametrize("can_ids", [None, {0x002}, {0x002, 0x300}])
def test_monitor_stops_and_restores_request_mode(elm, can_ids):
    transport, fake = elm
    transport.request(0x7E0, 0x7E8, b"\x01\x0c")
    stop = threading.Event()
    monitor = transport.monitor(stop, can_ids)
    frame = next(monitor)
    assert frame.can_id == 0x002
    stop.set()
    assert list(monitor) == []
    assert fake.writes.count(b" ") == 1
    assert not fake.monitoring
    assert fake.writes[-3:] == [b"ATCAF1\r", b"ATH1\r", b"ATCRA7E8\r"]
    assert transport.request(0x7E0, 0x7E8, b"\x01\x0c")[:2] == b"\x41\x0c"


def test_monitor_close_and_duration(elm):
    transport, fake = elm
    monitor = transport.monitor(threading.Event())
    next(monitor)
    monitor.close()
    assert not fake.monitoring
    frames = list(transport.monitor(threading.Event(), max_duration_s=0.015))
    assert {frame.can_id for frame in frames} == {0x002, 0x300, 0x174}
    assert not fake.monitoring
    assert list(transport.monitor(threading.Event(), set())) == []


def test_raw_console_restores_headers_before_next_request(elm):
    transport, fake = elm
    assert transport.raw_command("at ws") == "ELM327 v2.2"
    transport.raw_command("ATSH742")
    transport.raw_command("ATCAF0")
    fake.replies["2183"] = "762 03 61 83 01"
    assert "762" in transport.raw_command("2183")
    last_data = fake.writes.index(b"2183\r")
    assert b"ATCAF1\r" in fake.writes[:last_data]
    assert transport._tx_id == 0x742


def test_serial_disconnect_and_invalid_voltage(elm):
    transport, fake = elm
    fake.replies["ATRV"] = "nonsense"
    with pytest.raises(TransportError, match="voltage"):
        transport.read_voltage()
    fake.read_error = True
    with pytest.raises(TransportError, match="USB disconnected"):
        transport.request(0x7E0, 0x7E8, b"\x01\x0c")


def test_prompt_timeout_interrupts(elm):
    transport, fake = elm
    fake.replies["010C"] = None
    with pytest.raises(NoResponse, match="prompt"):
        transport.request(0x7E0, 0x7E8, b"\x01\x0c", timeout_s=0.005)
    assert fake.writes[-1] == b" "
    fake.replies["010C"] = "7E8 04 41 0C 1A F8"
    assert transport.request(0x7E0, 0x7E8, b"\x01\x0c") == bytes.fromhex("41 0C 1A F8")


def test_physical_response_ignores_other_ecus(elm):
    transport, fake = elm
    fake.replies["010C"] = "7E9 03 7F 01 31\r7E8 04 41 0C 1A F8"
    assert transport.request(0x7E0, 0x7E8, b"\x01\x0c") == bytes.fromhex("41 0C 1A F8")


def test_body_module_flow_control_and_functional_restore(elm):
    transport, fake = elm
    fake.replies["2183"] = "762 10 09 61 83 41 42 43 44\r762 21 45 46 47 00 00 00 00"
    assert transport.request(0x742, 0x762, b"\x21\x83") == b"\x61\x83ABCDEFG"
    assert all(command in fake.writes for command in (b"ATFCSH742\r", b"ATFCSD300000\r", b"ATFCSM1\r"))
    before = len(fake.writes)
    transport.request_functional(b"\x01\x0c")
    assert b"ATFCSM0\r" in fake.writes[before:]


def test_raw_filter_preserved_for_body_requests(elm):
    transport, fake = elm
    transport.raw_command("ATSH742")
    transport.raw_command("ATCRA762")
    fake.replies["2183"] = "762 03 61 83 01"
    transport.raw_command("2183")
    assert transport._rx_id == 0x762
    assert transport._flow_header == 0x742


def test_monitor_buffer_full_ends_window_without_disconnect(elm):
    transport, fake = elm
    fake.monitor_lines = "300 10 00\rBUFFER FULL\r>"
    frames = list(transport.monitor(threading.Event(), {0x300}, max_duration_s=1.0))
    assert [frame.can_id for frame in frames] == [0x300]
    assert b" " not in fake.writes
    assert not fake.closed
    assert transport.request(0x7E0, 0x7E8, b"\x01\x0c")[0] == 0x41


def test_monitor_mask_software_filters_superset(elm):
    transport, fake = elm
    fake.monitor_lines = "002 00 01\r102 FF FF\r300 10 00\r"
    frames = list(transport.monitor(threading.Event(), {0x002, 0x300}, 0.01))
    assert [frame.can_id for frame in frames] == [0x002, 0x300]
    assert any(command.startswith(b"ATCF") for command in fake.writes)
    assert any(command.startswith(b"ATCM") for command in fake.writes)


def test_explicit_clear_and_rejected_initialization(monkeypatch, elm):
    transport, fake = elm
    fake.replies["04"] = "7E8 01 44"
    assert transport.request(0x7E0, 0x7E8, b"\x04", allow_clear=True) == b"\x44"
    bad = FakeSerial()
    bad.replies["ATH1"] = "?"
    monkeypatch.setattr("q50diag.transport.elm.serial.Serial", lambda *args, **kwargs: bad)
    with pytest.raises(TransportError, match="ELM"):
        ElmTransport("bad", 115200).open()
    assert bad.closed


def test_console_adapter_errors_are_returned_as_text(elm):
    transport, fake = elm
    fake.replies["ATXYZ"] = "?"
    assert transport.raw_command("ATXYZ") == "ELM: ?"
    assert not fake.closed


def test_open_discards_stale_input(monkeypatch):
    fake = FakeSerial()
    fake.buffer.extend(b"OLD BANNER\r>")
    monkeypatch.setattr("q50diag.transport.elm.serial.Serial", lambda *args, **kwargs: fake)
    transport = ElmTransport("fake", 115200)
    assert "ELM327" in transport.open().identity
    assert transport.read_voltage() == 12.4


def test_wakeup_can_error_does_not_abort_open(monkeypatch):
    fake = FakeSerial()
    fake.replies["0100"] = "UNABLE TO CONNECT"
    monkeypatch.setattr("q50diag.transport.elm.serial.Serial", lambda *args, **kwargs: fake)
    assert ElmTransport("fake", 115200).open().voltage == 12.4


@pytest.mark.parametrize("payload", [b"\x08\x01", b"\x08"])
def test_obd_mode_08_is_blocked(elm, payload):
    transport, _ = elm
    with pytest.raises(RequestBlocked):
        transport.request(0x7E0, 0x7E8, payload)
    with pytest.raises(RequestBlocked):
        transport.request_functional(payload)


def test_unpowered_port_voltage_is_none(elm):
    transport, fake = elm
    fake.replies["ATRV"] = "--.-V"
    assert transport.read_voltage() is None


def test_slow_multi_frame_reply_extends_prompt_wait(elm, monkeypatch):
    transport, fake = elm
    frames = ["763 10 3A 61 01 00 00 00 00"] + [f"763 2{index % 16:X} 00 00 00 00 00 00 00" for index in range(1, 9)]
    fake.replies["2101"] = "\r".join(frames)
    original_read = fake.read

    def trickle(size=1):
        time.sleep(0.002)
        return original_read(1)

    monkeypatch.setattr(fake, "read", trickle)
    # About 250 bytes at 2 ms or more each outlast the timeout at least twice over;
    # the margin per byte absorbs scheduler stalls on a loaded machine.
    reply = transport.request(0x743, 0x763, b"\x21\x01", timeout_s=0.25)
    assert reply[:2] == b"\x61\x01" and len(reply) == 0x3A
    assert b" " not in fake.writes


def test_monitor_interrupt_retries_when_first_space_is_ignored(elm, monkeypatch):
    transport, fake = elm
    fake.monitor_lines = ""
    original_write = fake.write
    ignored = []

    def write(data):
        if data == b" " and not ignored:
            ignored.append(data)
            fake.writes.append(data)
            return 1
        return original_write(data)

    monkeypatch.setattr(fake, "write", write)
    monitor = transport.monitor(threading.Event(), {0x002}, max_duration_s=0.01)
    assert list(monitor) == []
    assert fake.writes.count(b" ") == 2
    assert not fake.closed
    assert transport.request(0x7E0, 0x7E8, b"\x01\x0c")[0] == 0x41


def open_device(monkeypatch, device, bus="hs", adapter_key="vlinker_fs"):
    fake = FakeSerial(device=device)
    monkeypatch.setattr("q50diag.transport.elm.serial.Serial", lambda *args, **kwargs: fake)
    transport = ElmTransport("fake", 115200, adapter=adapter_by_key(adapter_key), bus=bus)
    return transport, fake, transport.open()


def test_default_profile_and_bus():
    transport = ElmTransport("fake")
    assert (transport.adapter.key, transport.bus, transport.device_identity) == ("vlinker_fs", "hs", "")
    with pytest.raises(KeyError):
        ElmTransport("fake", bus="lin")


def protocol_commands(commands):
    return [command for command in commands if command.startswith(("ATSP", "STP"))]


@pytest.mark.parametrize(
    "device, adapter_key, bus, protocol",
    [
        ("vlinker", "vlinker_fs", "hs", ["STP33"]),
        ("vlinker", "vlinker_fs", "ms", ["STP53"]),
        ("vlinker", "vlinker_fs", "ms500", ["STP53", "STPBR500000"]),
        ("obdlink_ex", "obdlink_ex", "hs", ["STP33"]),
        ("obdlink_ex", "obdlink_ex", "ms", ["STP53"]),
        ("obdlink_ex", "obdlink_ex", "ms500", ["STP53", "STPBR500000"]),
        ("elm327", "vlinker_fs", "hs", ["ATSP6"]),
    ],
)
def test_initialization_per_device_and_bus(monkeypatch, device, adapter_key, bus, protocol):
    transport, fake, _ = open_device(monkeypatch, device, bus, adapter_key)
    commands = fake.commands
    first, last = commands.index(protocol[0]), commands.index(protocol[-1])
    assert commands.index("ATI") < commands.index("STI") < first <= last < commands.index("ATCAF1")
    assert commands.index("ATAT1") < first
    assert protocol_commands(commands) == protocol
    assert transport.bus == bus
    transport.close()


@pytest.mark.parametrize(
    "device, identity",
    [
        ("vlinker", "vLinker FS r2"),
        ("obdlink_ex", "OBDLink EX r1.0.0"),
        ("elm327", "ELM327 v2.2"),
    ],
)
def test_device_identity_and_adapter_info(monkeypatch, device, identity):
    transport, fake, info = open_device(
        monkeypatch, device, adapter_key="obdlink_ex" if device == "obdlink_ex" else "vlinker_fs"
    )
    assert transport.device_identity == identity
    if device == "elm327":
        assert "STDI" not in fake.commands
        assert info.description == "OBDII to RS232 Interpreter"
    else:
        assert identity in info.description and fake.replies["STI"] in info.description
    assert info.identity == fake.replies["ATI"]
    transport.close()
    assert transport.device_identity == ""


@pytest.mark.parametrize("bus", ["ms", "ms500"])
def test_plain_elm_cannot_select_ms_can(monkeypatch, bus):
    fake = FakeSerial(device="elm327")
    monkeypatch.setattr("q50diag.transport.elm.serial.Serial", lambda *args, **kwargs: fake)
    with pytest.raises(TransportError, match="MS-CAN needs an STN-based adapter"):
        ElmTransport("fake", 115200, bus=bus).open()
    assert fake.closed
    assert not any(command.startswith(("ATSP", "STP")) for command in fake.commands)


@pytest.mark.parametrize(
    "device, bus, protocol",
    [
        ("obdlink_ex", "ms", ["STP53"]),
        ("obdlink_ex", "ms500", ["STP53", "STPBR500000"]),
        ("vlinker", "hs", ["STP33"]),
        ("elm327", "hs", ["ATSP6"]),
    ],
)
def test_prepare_reapplies_selected_protocol(monkeypatch, device, bus, protocol):
    transport, fake, _ = open_device(monkeypatch, device, bus)
    transport.raw_command("ATCAF0")
    fake.writes.clear()
    transport.request(0x7E0, 0x7E8, b"\x01\x0c")
    commands = fake.commands
    assert protocol_commands(commands) == protocol
    assert commands.index(protocol[-1]) < commands.index("010C")


def test_identity_reads_do_not_dirty_settings(monkeypatch):
    transport, fake, _ = open_device(monkeypatch, "obdlink_ex", "ms", "obdlink_ex")
    assert transport.raw_command("STDI") == "OBDLink EX r1.0.0"
    assert transport.raw_command("STI") == "STN2230 v5.6.19"
    fake.writes.clear()
    transport.request(0x7E0, 0x7E8, b"\x01\x0c")
    assert "STP53" not in fake.commands


def test_identity_mismatch_warns_and_keeps_connection(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="q50diag.elm")
    transport, fake, info = open_device(monkeypatch, "obdlink_ex", "ms", adapter_key="vlinker_fs")
    assert transport.adapter.key == "vlinker_fs"
    assert transport.detected_adapter.key == "obdlink_ex"
    assert "OBDLink EX USB" in caplog.text and "Vgate vLinker FS USB" in caplog.text
    assert "STP53" in fake.commands
    assert transport.request(0x7E0, 0x7E8, b"\x01\x0c")[0] == 0x41


def test_identity_match_has_no_warning(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="q50diag.elm")
    transport, _, _ = open_device(monkeypatch, "obdlink_ex", adapter_key="obdlink_ex")
    assert transport.detected_adapter.key == "obdlink_ex"
    assert caplog.text == ""


def test_unknown_identity_has_no_detected_adapter(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="q50diag.elm")
    transport, _, _ = open_device(monkeypatch, "elm327")
    assert transport.detected_adapter is None
    assert caplog.text == ""


def test_stdi_failure_falls_back_to_sti(monkeypatch):
    fake = FakeSerial(device="vlinker")
    fake.replies["STDI"] = "?"
    monkeypatch.setattr("q50diag.transport.elm.serial.Serial", lambda *args, **kwargs: fake)
    transport = ElmTransport("fake", 115200)
    transport.open()
    assert transport.device_identity == "STN1170 v4.3.1"
    assert "STP33" in fake.commands


def test_silent_sti_is_treated_as_plain_elm(monkeypatch):
    fake = FakeSerial(device="elm327")
    fake.replies["STI"] = None
    monkeypatch.setattr("q50diag.transport.elm.serial.Serial", lambda *args, **kwargs: fake)
    transport = ElmTransport("fake", 115200)
    transport.open()
    assert b" " in fake.writes
    assert "ATSP6" in fake.commands and transport.device_identity == "ELM327 v2.2"
    assert transport.request(0x7E0, 0x7E8, b"\x01\x0c")[0] == 0x41


def test_out_of_memory_is_an_adapter_error(elm):
    transport, fake = elm
    fake.replies["2101"] = "OUT OF MEMORY"
    with pytest.raises(AdapterReplyError, match="OUT OF MEMORY"):
        transport.request(0x743, 0x763, b"\x21\x01")
    assert not fake.closed
    assert transport.request(0x7E0, 0x7E8, b"\x01\x0c")[0] == 0x41


def test_console_blocks_nvm_reset(elm):
    transport, fake = elm
    for line in ("STRSTNVM", "STP53", "stsbr 9600"):
        with pytest.raises(RequestBlocked):
            transport.raw_command(line)


def test_functional_negative_response_keeps_other_ecu_answers(elm):
    transport, fake = elm
    fake.replies["03"] = "7E8 04 43 01 01 71\r7E9 03 7F 03 11"
    assert transport.request_functional(b"\x03") == {0x7E8: bytes.fromhex("43 01 01 71")}


def test_functional_raises_negative_response_when_every_ecu_rejects(elm):
    transport, fake = elm
    fake.replies["0A"] = "7E8 03 7F 0A 12\r7E9 03 7F 0A 11"
    with pytest.raises(NegativeResponse):
        transport.request_functional(b"\x0a")


def test_functional_incomplete_transfer_keeps_complete_ecu_answer(elm):
    transport, fake = elm
    fake.replies["03"] = "7E8 04 43 01 01 71\r7E9 10 0E 43 06 01 01 02 02"
    answers, failures = transport.request_functional_with_failures(b"\x03")
    assert answers == {0x7E8: bytes.fromhex("43 01 01 71")}
    assert list(failures) == [0x7E9] and isinstance(failures[0x7E9], NoResponse)


def test_functional_reports_rejecting_ecu_alongside_answers(elm):
    transport, fake = elm
    fake.replies["03"] = "7E8 02 43 00\r7E9 03 7F 03 22"
    answers, failures = transport.request_functional_with_failures(b"\x03")
    assert answers == {0x7E8: bytes.fromhex("43 00")}
    assert isinstance(failures[0x7E9], NegativeResponse) and failures[0x7E9].code == 0x22


def test_physical_request_to_complete_ecu_ignores_other_incomplete_stream(elm):
    transport, fake = elm
    fake.replies["2183"] = "762 03 61 83 01\r763 10 0E 61 83 01 02 03 04"
    assert transport.request(0x742, 0x762, b"\x21\x83") == bytes.fromhex("61 83 01")
