"""Serial port discovery without opening devices."""

from serial.tools import list_ports as serial_ports

from q50diag.core.types import PortInfo

LIKELY_ADAPTER_TOKENS = ("vlinker", "obdlink", "ft232", "ftdi", "elm")


def _description(port) -> str:
    """Append the USB product string (e.g. "OBDLink EX") when the OS description omits it."""
    description = port.description or ""
    product = getattr(port, "product", None) or ""
    if product and product.casefold() not in description.casefold():
        return f"{description} ({product})" if description else product
    return description


def list_ports() -> list[PortInfo]:
    ports = []
    for port in serial_ports.comports():
        description = _description(port)
        likely = port.vid == 0x0403 or any(
            token in description.casefold() for token in LIKELY_ADAPTER_TOKENS
        )
        ports.append(PortInfo(port.device, description, port.vid, port.pid, likely))
    return sorted(ports, key=lambda port: (not port.is_likely_vlinker, port.device))
