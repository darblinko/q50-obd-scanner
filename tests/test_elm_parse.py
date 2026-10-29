import pytest

from q50diag.core.transport import NegativeResponse, NoResponse, RequestBlocked, TransportError
from q50diag.transport.elm_parse import (
    check_status, console_command, final_response, monitor_filter, parse_can_line,
    parse_responses, reassemble_frames, response_lines,
)


@pytest.mark.parametrize("line", ["7E8 04 41 0C 1A F8 00 00 00", "7E804410C1AF8000000",
                                 "7E8 8 04 41 0C 1A F8 00 00 00", "7E8804410C1AF8000000"])
def test_single_frame_and_dlc(line):
    assert parse_can_line(line) == (0x7E8, bytes.fromhex("04 41 0C 1A F8 00 00 00"))
    assert parse_responses(line) == {0x7E8: [bytes.fromhex("41 0C 1A F8")]}


def test_multiframe_vin_and_interleaved_ecus():
    response = """0902
SEARCHING...
7E8 10 14 49 02 01 4A 4E 31
7E9 03 49 02 00 00 00 00 00
7E8 21 53 41 4D 50 4C 45 58
7E8 22 58 58 58 58 58 58 30
>"""
    assert parse_responses(response, "0902") == {
        0x7E8: [b"\x49\x02\x01JN1SAMPLEXXXXXXX0"], 0x7E9: [b"\x49\x02\x00"]}


def test_two_interleaved_multiframe_responses():
    frames = [(0x762, b"\x10\x09\x61\x83ABCD"), (0x763, b"\x10\x09\x61\x83abcd"),
              (0x762, b"\x21EFG\x00\x00\x00\x00"), (0x763, b"\x21efg\x00\x00\x00\x00")]
    assert reassemble_frames(frames) == {0x762: [b"\x61\x83ABCDEFG"], 0x763: [b"\x61\x83abcdefg"]}


def test_sequence_wrap_and_padding():
    payload = bytes(range(128))
    frames = [(0x762, b"\x10\x80" + payload[:6])]
    for number, offset in enumerate(range(6, len(payload), 7), 1):
        frames.append((0x762, bytes((0x20 | (number & 15),)) + payload[offset:offset + 7].ljust(7, b"\xAA")))
    assert reassemble_frames(frames)[0x762] == [payload]


def test_pending_then_positive():
    response = "7E8 03 7F 22 78\r7E8 04 62 12 01 40\r>"
    messages = parse_responses(response)[0x7E8]
    assert len(messages) == 2
    assert final_response(messages, 0x22) == b"\x62\x12\x01\x40"
    assert final_response(messages[:1], 0x22) is None


def test_negative_response_has_service_and_code():
    with pytest.raises(NegativeResponse) as caught:
        final_response([b"\x7F\x22\x31"], 0x22)
    assert (caught.value.service, caught.value.code) == (0x22, 0x31)


@pytest.mark.parametrize("text", ["NO DATA", "SEARCHING...\rNO DATA\r>"])
def test_no_data(text):
    with pytest.raises(NoResponse):
        parse_responses(text)


@pytest.mark.parametrize("status", ["CAN ERROR", "BUFFER FULL", "?", "STOPPED", "UNABLE TO CONNECT",
                                   "BUS INIT: ERROR", "BUS INIT...ERROR", "<DATA ERROR", "LV RESET",
                                   "OUT OF MEMORY"])
def test_adapter_errors(status):
    with pytest.raises(TransportError):
        check_status(status)


@pytest.mark.parametrize("status", ["SEARCHING...", "BUS INIT: OK", "BUS INIT...", "OK"])
def test_informational_status(status):
    assert check_status(status) is None


@pytest.mark.parametrize("frames", [
    [(0x7E8, b"\x04\x41\x0C")], [(0x7E8, b"\x00")], [(0x7E8, b"")],
    [(0x7E8, b"\x10\x14ab")],
    [(0x7E8, b"\x10\x09abcdef"), (0x7E8, b"\x22ghi")],
    [(0x7E8, b"\x40abc")],
])
def test_bad_frames(frames):
    with pytest.raises(TransportError):
        reassemble_frames(frames)


def test_incomplete_multiframe():
    with pytest.raises(NoResponse, match="Incomplete"):
        reassemble_frames([(0x7E8, b"\x10\x14abcdef")])


def test_command_echo_and_flow_control():
    assert response_lines("ATI\rELM327 v2.2\r\n>", "ATI") == ["ELM327 v2.2"]
    assert reassemble_frames([(0x7E0, b"\x30\x00\x00")]) == {}
    assert parse_can_line("SEARCHING...") is None
    assert parse_can_line("ATMA") is None


def test_filter_matches_all_requested_ids():
    for identifiers in ({0x002}, {0x002, 0x300, 0x174}, {0x760, 0x762}):
        code, mask = monitor_filter(identifiers)
        assert all(identifier & mask == code for identifier in identifiers)
    assert monitor_filter({0x762}) == (0x762, 0x7FF)


@pytest.mark.parametrize("line", ["AT PP 00 SV 10", "STP", "AT BRD 23", "ATBRT10", "STPBR",
                                  "ATZ\r2701", "ATI\n1101", "", "0", "nonsense",
                                  "STP 53", "STP33", "STRSTNVM", "st rst nvm", "STSBR 9600"])
def test_console_validation(line):
    with pytest.raises(RequestBlocked):
        console_command(line)


def test_warm_start_is_allowed():
    assert console_command("at ws") == ("ATWS", None)
    assert console_command("01 0c") == ("010C", b"\x01\x0C")


def test_stn_identity_reads_are_allowed():
    assert console_command("st di") == ("STDI", None)
    assert console_command("STI") == ("STI", None)


def test_orphan_consecutive_frames_from_interrupted_transfer_are_ignored():
    text = "763 2E 00 C2 00 66 81 62 03\r763 2F 01 02 5D 00 00 03 0D\r763 03 61 1B 01"
    assert parse_responses(text) == {0x763: [bytes.fromhex("61 1B 01")]}


def test_malformed_iso_tp_is_no_response_not_link_loss():
    with pytest.raises(NoResponse):
        parse_responses("763 10 20 61 01 00 00 00 00\r763 23 00 00 00 00 00 00 00")
