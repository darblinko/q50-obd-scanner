from types import SimpleNamespace

from q50diag.transport.adapters import guess_adapter
from q50diag.transport.ports import list_ports


def test_ports_identification_and_sorting(monkeypatch):
    rows = [
        SimpleNamespace(device="tty9", description="Generic serial", vid=None, pid=None),
        SimpleNamespace(device="tty2", description="USB Serial", vid=0x0403, pid=0x6001),
        SimpleNamespace(device="tty1", description="vLinker FS", vid=None, pid=None),
        SimpleNamespace(device="tty3", description="FT232R", vid=0x9999, pid=0x1234),
        SimpleNamespace(device="tty4", description="ELM327", vid=None, pid=None),
    ]
    monkeypatch.setattr("q50diag.transport.ports.serial_ports.comports", lambda: rows)
    result = list_ports()
    assert [port.device for port in result] == ["tty1", "tty2", "tty3", "tty4", "tty9"]
    assert all(port.is_likely_vlinker for port in result[:-1])
    assert not result[-1].is_likely_vlinker
    assert (result[1].vid, result[1].pid) == (0x0403, 0x6001)


def test_usb_product_string_identifies_adapter(monkeypatch):
    rows = [
        SimpleNamespace(device="tty5", description="FT231X USB UART", vid=0x0403, pid=0x6015, product="OBDLink EX"),
        SimpleNamespace(device="tty6", description="vLinker FS", vid=0x0403, pid=0x6015, product="vLinker FS"),
        SimpleNamespace(device="tty7", description="OBDLink EX", vid=None, pid=None, product=None),
        SimpleNamespace(device="tty8", description="USB Serial Port", vid=0x10C4, pid=0xEA60, product=""),
    ]
    monkeypatch.setattr("q50diag.transport.ports.serial_ports.comports", lambda: rows)
    result = {port.device: port for port in list_ports()}
    assert result["tty5"].description == "FT231X USB UART (OBDLink EX)"
    assert guess_adapter(result["tty5"].description).key == "obdlink_ex"
    assert result["tty6"].description == "vLinker FS"
    assert guess_adapter(result["tty6"].description).key == "vlinker_fs"
    assert result["tty7"].is_likely_vlinker
    assert guess_adapter(result["tty7"].description).key == "obdlink_ex"
    assert result["tty8"].description == "USB Serial Port" and not result["tty8"].is_likely_vlinker
