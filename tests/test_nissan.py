import pytest

from q50diag.core.transport import NegativeResponse, NoResponse, RequestBlocked, TransportError
from q50diag.protocol import nissan
from q50diag.protocol.modules import module_by_name
from q50diag.transport.sim import SimulatedTransport

EPS = module_by_name("EPS")


class ProbeTransport:
    def __init__(self):
        self.connection_generation = 1
        self.requests = []
        self.replies = {}

    def request(self, tx, rx, payload, **kwargs):
        self.requests.append((payload, kwargs))
        response = self.replies.get(payload, NegativeResponse(payload[0], 0x31))
        if isinstance(response, Exception):
            raise response
        return response


def test_session_once_per_module_connection_and_reset():
    nissan.reset_sessions()
    first, second = ProbeTransport(), ProbeTransport()
    first.replies[b"\x10\xc0"] = second.replies[b"\x10\xc0"] = b"\x50\xc0"
    nissan.ensure_session(first, EPS)
    nissan.ensure_session(first, EPS)
    nissan.ensure_session(second, EPS)
    assert len(first.requests) == len(second.requests) == 1
    first.connection_generation += 1
    nissan.ensure_session(first, EPS)
    assert len(first.requests) == 2
    nissan.reset_sessions()
    nissan.ensure_session(first, EPS)
    assert len(first.requests) == 3


def test_session_fallback_and_no_session_supported():
    transport = ProbeTransport()
    transport.replies[b"\x10\x03"] = b"\x50\x03"
    nissan.ensure_session(transport, EPS)
    assert [request for request, _ in transport.requests] == [b"\x10\xc0", b"\x10\x03"]
    unsupported = ProbeTransport()
    nissan.ensure_session(unsupported, EPS)
    nissan.ensure_session(unsupported, EPS)
    assert len(unsupported.requests) == 4  # a failed entry is not cached


def test_identification_continues_after_failed_probes():
    transport = ProbeTransport()
    transport.replies.update(
        {
            b"\x21\x83": NoResponse("timeout"),
            b"\x1a\x81": RequestBlocked("read unavailable"),
            b"\x22\xf1\x87": b"\x62\xf1\x87PART\x00",
        }
    )
    result = nissan.read_identification(transport, EPS)
    assert result == {"Manufacturer part number": "PART. [50 41 52 54 00]"}


def test_dtc_uds_fallback():
    transport = ProbeTransport()
    transport.replies[b"\x19\x02\xff"] = bytes.fromhex("59 02 FF 56 04 00 01")
    result = nissan.read_module_dtcs(transport, EPS)
    assert result[0].code == "C1604-00" and result[0].kind == "current"


def test_clear_fallback_carries_confirmation():
    transport = ProbeTransport()
    transport.replies[b"\x14\xff\xff\xff"] = b"\x54"
    nissan.clear_module_dtcs(transport, EPS)
    clears = [(payload, options) for payload, options in transport.requests if payload[0] == 0x14]
    assert len(clears) == 2 and all(options["allow_clear"] for _, options in clears)
    with pytest.raises(NegativeResponse):
        nissan.clear_module_dtcs(ProbeTransport(), EPS)


@pytest.mark.parametrize(
    "operation", [nissan.ensure_session, nissan.read_identification, nissan.read_module_dtcs, nissan.clear_module_dtcs]
)
def test_connection_failure_propagates(operation):
    transport = ProbeTransport()
    transport.replies[b"\x10\xc0"] = TransportError("USB unplugged")
    with pytest.raises(TransportError, match="USB unplugged"):
        operation(transport, EPS)


def test_simulator_reopen_starts_new_session():
    transport = SimulatedTransport()
    transport.open()
    nissan.ensure_session(transport, EPS)
    old_generation = transport.connection_generation
    transport.close()
    transport.open()
    assert transport.connection_generation == old_generation + 1
    nissan.ensure_session(transport, EPS)
    assert nissan._sessions[transport][0] == transport.connection_generation
    transport.close()


def test_dtc_read_raises_when_module_is_silent():
    transport = ProbeTransport()
    transport.replies[b"\x18\x00\xff\x00"] = NoResponse("timeout")
    transport.replies[b"\x19\x02\xff"] = NoResponse("timeout")
    with pytest.raises(NoResponse):
        nissan.read_module_dtcs(transport, EPS)


def test_unexpected_clear_reply_is_not_a_connection_error():
    transport = ProbeTransport()
    transport.replies[b"\x14\xff\x00"] = b"\x99"
    with pytest.raises(ValueError, match="Unexpected DTC clear response"):
        nissan.clear_module_dtcs(transport, EPS)


def test_eps_fields_decode_q50_samples():
    fields = {parameter.key: parameter for parameter in nissan.eps_fields(EPS)}
    assert all(parameter.request == b"\x21\x01" for parameter in fields.values())
    at_rest = bytes.fromhex("0dbd02008f00000000003564000060000000000000")  # 2017 Q50, after 61 01
    values = {key: parameter.decode(at_rest) for key, parameter in fields.items()}
    assert abs(values["EPS.21.01.supply"] - 14.068) < 1e-9
    assert abs(values["EPS.21.01.angle"] + 14.3) < 1e-9
    assert values["EPS.21.01.assist"] == 0 and values["EPS.21.01.torque"] == 0
    assert values["EPS.21.01.b10"] == 53 and values["EPS.21.01.b11"] == 100
    assert values["EPS.21.01.velocity"] == 0 and values["EPS.21.01.motor"] == 0
    loaded = bytearray(at_rest)
    loaded[7], loaded[8] = 25, 0xCF
    assert fields["EPS.21.01.assist"].decode(bytes(loaded)) == 25
    assert fields["EPS.21.01.torque"].decode(bytes(loaded)) == -49
    with pytest.raises(ValueError):
        fields["EPS.21.01.motor"].decode(at_rest[:16])


def test_dtc_read_raises_when_module_rejects_both_services():
    with pytest.raises(NegativeResponse):
        nissan.read_module_dtcs(ProbeTransport(), EPS)


def test_dtc_read_raises_on_malformed_reply():
    transport = ProbeTransport()
    transport.replies[b"\x18\x00\xff\x00"] = b"\x58\x05"
    transport.replies[b"\x19\x02\xff"] = NoResponse("timeout")
    with pytest.raises(ValueError):
        nissan.read_module_dtcs(transport, EPS)
