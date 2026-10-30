"""Convert a parameter response into a timestamped reading."""

import time
from collections.abc import Callable

from q50diag.core.transport import NegativeResponse, NoResponse, RequestBlocked, Transport
from q50diag.core.types import ModuleAddress, ParameterDef, Reading
from q50diag.protocol import nissan

# send(t, module, payload) -> reply: a manufacturer request policy such as nissan.request or ford.request.
Sender = Callable[[Transport, ModuleAddress, bytes], bytes]


def read_parameter(t: Transport, module: ModuleAddress, param: ParameterDef, send: Sender = nissan.request) -> Reading:
    """21/22 reads on KWP and UDS modules go through `send`, so the vehicle's
    session handling and wrong-session retry apply (Nissan by default).
    Other services, such as OBD-II mode 01, go to the transport directly."""
    raw = b""
    try:
        if not param.request:
            raise ValueError("Parameter has no request")
        service = param.request[0]
        if module.protocol in ("kwp", "uds") and service in (0x21, 0x22):
            raw = send(t, module, param.request)
        else:
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
    expected = bytes((service + 0x40,)) + param.request[1 : 1 + width]
    if not raw.startswith(expected):
        raise ValueError(f"Unexpected response echo: {raw.hex(' ')}")
    return param.decode(raw[len(expected) :])
