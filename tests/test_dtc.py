import pytest

from q50diag.data import dtc_descriptions
from q50diag.data.dtc_descriptions import describe, describe_generic, load_descriptions, parse_descriptions
from q50diag.protocol.dtc import decode_kwp_dtc_response, decode_two_byte, decode_uds_dtc_response


@pytest.mark.parametrize(
    "hi, lo, expected",
    [(1, 0x71, "P0171"), (0x56, 4, "C1604"), (0xD0, 0, "U1000"), (0x80, 1, "B0001"), (0x3F, 0xAB, "P3FAB")],
)
def test_two_byte(hi, lo, expected):
    assert decode_two_byte(hi, lo) == expected


def test_kwp_codes_and_raw_status():
    result = decode_kwp_dtc_response(bytes.fromhex("58 02 56 04 20 D0 00 01"), "EPS")
    assert [(dtc.code, dtc.kind, dtc.status) for dtc in result] == [("C1604", "history", 0x20), ("U1000", "current", 1)]
    assert result[0].module == "EPS"
    assert decode_kwp_dtc_response(b"\x58\x00", "EPS") == []


def test_uds_failure_type_is_preserved():
    result = decode_uds_dtc_response(bytes.fromhex("59 02 FF 56 04 16 09 D0 00 00 08"), "EPS")
    assert [(dtc.code, dtc.kind, dtc.status) for dtc in result] == [
        ("C1604-16", "current", 9),
        ("U1000-00", "stored", 8),
    ]
    assert result[0].description == describe("C1604")
    assert decode_uds_dtc_response(b"\x59\x02\xff", "EPS") == []


@pytest.mark.parametrize("payload", [b"", b"\x58", b"\x58\x02\x56\x04\x20", b"\x59\x00"])
def test_truncated_or_wrong_kwp(payload):
    with pytest.raises(ValueError):
        decode_kwp_dtc_response(payload, "EPS")


@pytest.mark.parametrize("payload", [b"", b"\x59\x02", b"\x59\x02\xff\x01", b"\x59\x01\x00"])
def test_truncated_or_wrong_uds(payload):
    with pytest.raises(ValueError):
        decode_uds_dtc_response(payload, "EPS")


_SAMPLE = """\
P0171|Lean test description
U1000|Network test description
[nissan]
C1604|Nissan test torque sensor
C160A|Nissan test overheat note
"""


@pytest.fixture
def sample_descriptions(monkeypatch):
    tables = parse_descriptions(_SAMPLE)
    monkeypatch.setattr(dtc_descriptions, "_DESCRIPTIONS", tables["generic"])
    monkeypatch.setattr(dtc_descriptions, "NISSAN_DESCRIPTIONS", tables["nissan"])


def test_no_descriptions_ship_with_the_package():
    assert dtc_descriptions.load_descriptions("/nonexistent/dtc.txt") == {"generic": {}, "nissan": {}, "ford": {}}


def test_parse_sections_comments_and_bad_lines():
    tables = parse_descriptions("# note\nP0001|one\nbad line\n[Nissan]\nc1604 | two \n[unknown]\nX|y\n")
    assert tables["generic"] == {"P0001": "one"}
    assert tables["nissan"] == {"C1604": "two"}
    assert tables["ford"] == {}


def test_load_descriptions_from_file(tmp_path):
    path = tmp_path / "dtc.txt"
    path.write_text(_SAMPLE, encoding="utf-8")
    assert load_descriptions(path)["generic"]["P0171"] == "Lean test description"


def test_description_lookup_and_no_invention(sample_descriptions):
    assert "Lean" in describe(" p0171 ")
    assert "Network" in describe("U1000")
    assert describe("C1604-16") == describe("C1604")
    assert describe("C1602") == ""
    assert describe("P3999") == ""
    assert describe_generic("C1604") == ""


def test_make_specific_section_overrides_generic(sample_descriptions):
    assert describe("C160A") == "Nissan test overheat note"
    assert describe("C1613-00") == ""
    assert describe_generic("C160A") == ""


def test_uds_untested_status_is_not_a_fault():
    # Captured from a 2017 Q50 ECM: 19 02 FF lists supported codes with status 0x40.
    payload = bytes.fromhex("5902CF0500004012170040D0000140")
    assert decode_uds_dtc_response(payload, "ECM") == []


def test_uds_status_kinds_from_real_q50_modules():
    airbag = decode_uds_dtc_response(bytes.fromhex("59020980A00008"), "AIR BAG")
    radar = decode_uds_dtc_response(bytes.fromhex("59020B5A0100085A160008"), "RADAR")
    assert [(d.code, d.kind) for d in airbag] == [("B00A0-00", "stored")]
    assert [d.code for d in radar] == ["C1A01-00", "C1A16-00"]
