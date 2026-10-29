import dataclasses

import pytest

from q50diag.transport.adapters import (
    ADAPTERS, DEFAULT_ADAPTER_KEY, HS_CAN, MS_CAN, MS_PINS_500, AdapterProfile, BusConfig,
    adapter_by_key, bus_by_key, guess_adapter,
)


def test_bus_configs():
    assert (HS_CAN.key, HS_CAN.stn_protocol, HS_CAN.elm_protocol) == ("hs", "STP33", "ATSP6")
    assert (MS_CAN.key, MS_CAN.stn_protocol, MS_CAN.elm_protocol) == ("ms", "STP53", None)
    assert "500" in HS_CAN.label and "6/14" in HS_CAN.label
    assert "125" in MS_CAN.label and "3/11" in MS_CAN.label
    assert (MS_PINS_500.key, MS_PINS_500.stn_protocol, MS_PINS_500.elm_protocol) == ("ms500", "STP53", None)
    assert MS_PINS_500.label == "Pins 3/11 at 500 kbps (Ford HS-CAN2, experimental)"
    assert MS_PINS_500.stn_bitrate == 500000
    assert HS_CAN.stn_bitrate is None and MS_CAN.stn_bitrate is None


def test_adapter_registry_and_default():
    assert [adapter.key for adapter in ADAPTERS] == ["vlinker_fs", "obdlink_ex"]
    assert DEFAULT_ADAPTER_KEY == "vlinker_fs"
    for adapter in ADAPTERS:
        assert adapter.buses == (HS_CAN, MS_CAN, MS_PINS_500)
        assert adapter.baud_rates[0] == 115200
        assert adapter.identity_tokens and adapter.notes
    assert adapter_by_key("obdlink_ex").name == "OBDLink EX USB"
    assert adapter_by_key("vlinker_fs").name == "Vgate vLinker FS USB"


def test_lookup_errors():
    with pytest.raises(KeyError):
        adapter_by_key("elm_clone")
    hs_only = AdapterProfile("hs_only", "HS only", (HS_CAN,), (115200,), ("HS",), "Test profile.")
    assert bus_by_key(hs_only, "hs") is HS_CAN
    assert bus_by_key(adapter_by_key("obdlink_ex"), "ms") is MS_CAN
    with pytest.raises(KeyError):
        bus_by_key(hs_only, "ms")


def test_profiles_are_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        HS_CAN.key = "ms"
    with pytest.raises(dataclasses.FrozenInstanceError):
        ADAPTERS[0].name = "other"
    assert isinstance(MS_CAN, BusConfig)


@pytest.mark.parametrize("text, expected", [
    ("OBDLink EX r1.0.0", "obdlink_ex"),
    ("FT231X USB UART (OBDLink EX)", "obdlink_ex"),
    ("obdlink ex", "obdlink_ex"),
    ("vLinker FS r2", "vlinker_fs"),
    ("VLINKER FS USB", "vlinker_fs"),
])
def test_guess_adapter_matches_identity_tokens(text, expected):
    assert guess_adapter(text).key == expected


@pytest.mark.parametrize("text", ["", "ELM327 v1.5", "OBDLink SX r4.2.0", "USB Serial Port (COM5)"])
def test_guess_adapter_unknown(text):
    assert guess_adapter(text) is None
