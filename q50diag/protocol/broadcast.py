"""Q50 broadcast signals. Assist/gear byte positions are provisional.

The research notes specify the steering encoding but not assist, lamp or gear
bit positions. Those values remain explicitly raw pending a verified DBC/capture.
"""

from q50diag.core.types import CanFrame, Value

BROADCAST_SIGNALS: dict[str, tuple[str, str]] = {
    "bcast.002.steering_angle": ("Steering angle", "deg"),
    "bcast.300.assist_raw": ("Steering assist (uncalibrated, provisional byte 0)", ""),
    "bcast.174.gear": ("Gear (raw, provisional byte 0)", ""),
    "bcast.5E4.ps_warning_lamp": ("Power steering warning lamp (byte 0 bit 2, 2019 Q50 DBC)", ""),
}


def decode_frame(frame: CanFrame) -> dict[str, Value]:
    if frame.can_id == 0x002 and len(frame.data) >= 2:
        return {"bcast.002.steering_angle": int.from_bytes(frame.data[:2], "little", signed=True) * -0.1}
    if frame.can_id == 0x5E4 and frame.data:
        # nullperez/infiniti-q50-canbus q50_main.dbc: lamp is bit 2 of byte 0.
        return {"bcast.5E4.ps_warning_lamp": bool(frame.data[0] & 0x04)}
    signals = {0x300: "bcast.300.assist_raw", 0x174: "bcast.174.gear"}
    if frame.can_id in signals and frame.data:
        return {signals[frame.can_id]: frame.data[0]}
    return {}
