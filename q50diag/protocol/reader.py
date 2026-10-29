"""Convert a parameter response into a timestamped reading."""

import time

from q50diag.core.transport import NegativeResponse, NoResponse, RequestBlocked, Transport
from q50diag.core.types import ModuleAddress, ParameterDef, Reading
from q50diag.protocol import nissan


def read_parameter(t: Transport, module: ModuleAddress, param: ParameterDef,
                   session=nissan.ensure_session) -> Reading:
    """`session(t, module)` runs before 21/22 reads on KWP modules (Nissan by default)."""
    raw = b""
    try:
        if not param.request:
            raise ValueError("Parameter has no request")
        service = param.request[0]
        if module.protocol == "kwp" and service in (0x21, 0x22):
            session(t, module)
        raw = t.request(module.tx_id, module.rx_id, param.request)
        return Reading(param.key, _decode(param, raw), param.unit, time.time(), raw)
    except (NoResponse, NegativeResponse, RequestBlocked, ValueError, IndexError) as error:
        return Reading(param.key, None, param.unit, time.time(), raw, str(error))


def decode_reply(param: ParameterDef, raw: bytes) -> Reading:
    """Decode a reply already received for another parameter with the same request."""
    try:
        return Reading(param.key, _decode(param, raw), param.unit, time.time(), raw)
    except (ValueError, IndexError) as error:
        return Reading(param.key, None, param.unit, time.time(), raw, str(error))


def _decode(param: ParameterDef, raw: bytes):
    service = param.request[0]
    width = {0x01: 1, 0x02: 2, 0x21: 1, 0x22: 2}.get(service, 0)
    expected = bytes((service + 0x40,)) + param.request[1:1 + width]
    if not raw.startswith(expected):
        raise ValueError(f"Unexpected response echo: {raw.hex(' ')}")
    return param.decode(raw[len(expected):])
