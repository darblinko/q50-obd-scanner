from q50diag.core.types import CanFrame
from q50diag.protocol.broadcast import decode_frame


def test_ps_warning_lamp_reads_bit_2_of_byte_0():
    lamp_on = decode_frame(CanFrame(0.0, 0x5E4, bytes([0x04, 0, 0, 0])))
    other_bits = decode_frame(CanFrame(0.0, 0x5E4, bytes([0xFB, 0xFF, 0, 0])))
    assert lamp_on == {"bcast.5E4.ps_warning_lamp": True}
    assert other_bits == {"bcast.5E4.ps_warning_lamp": False}
