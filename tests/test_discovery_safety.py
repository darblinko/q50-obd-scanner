from q50diag.core.transport import NoResponse
from q50diag.protocol.discovery import discover_modules
from q50diag.protocol.modules import KNOWN_MODULES


class RecordingTransport:
    def __init__(self):
        self.sent = []

    def request(self, tx_id, rx_id, payload, timeout_s=1.0, allow_clear=False):
        self.sent.append((tx_id, payload))
        raise NoResponse("silent bus")


def test_sweep_never_transmits_on_response_ids_and_only_sends_tester_present():
    transport = RecordingTransport()
    discover_modules(transport)
    known_tx = {module.tx_id for module in KNOWN_MODULES}
    response_ids = {module.rx_id for module in KNOWN_MODULES} | set(range(0x7E8, 0x7F0))
    sent_ids = {tx_id for tx_id, _ in transport.sent}
    assert not sent_ids & response_ids
    sweep_payloads = {payload for tx_id, payload in transport.sent if tx_id not in known_tx}
    assert sweep_payloads == {b"\x3e\x00"}
