"""Minimal byte-stream ELM, including monitor interruption.

`device` selects the emulated adapter: "vlinker" (STN1170), "obdlink_ex"
(STN2230) or "elm327" (plain ELM327 that answers "?" to ST commands)."""

DEVICE_REPLIES = {
    "vlinker": {"ATZ": "ELM327 v2.2", "ATWS": "ELM327 v2.2", "ATI": "ELM327 v2.2",
                "AT@1": "vLinker FS", "STI": "STN1170 v4.3.1", "STDI": "vLinker FS r2"},
    "obdlink_ex": {"ATZ": "ELM327 v1.4b", "ATWS": "ELM327 v1.4b", "ATI": "ELM327 v1.4b",
                   "AT@1": "OBDLink EX", "STI": "STN2230 v5.6.19", "STDI": "OBDLink EX r1.0.0"},
    "elm327": {"ATZ": "ELM327 v2.2", "ATWS": "ELM327 v2.2", "ATI": "ELM327 v2.2",
               "AT@1": "OBDII to RS232 Interpreter"},
}


class FakeSerial:
    def __init__(self, port="fake", baudrate=115200, timeout=0.02, write_timeout=1.0, device="vlinker"):
        self.port, self.baudrate = port, baudrate
        self.timeout = timeout
        self.device = device
        self.writes = []
        self.buffer = bytearray()
        self.closed = False
        self.monitoring = False
        self.read_error = False
        self.replies = {
            **DEVICE_REPLIES[device],
            "ATRV": "12.4V", "ATDP": "ISO 15765-4 (CAN 11/500)",
            "0100": "SEARCHING...\r7E8 06 41 00 BE 3F A8 13",
            "010C": "7E8 04 41 0C 1A F8 00 00 00",
            "0902": "7E8 10 14 49 02 01 4A 4E 31\r7E8 21 53 41 4D 50 4C 45 58\r7E8 22 58 58 58 58 58 58 30",
        }
        self.monitor_lines = "002 00 01 00 00 00 00 00 00\r300 6F 00 00 00 00 00 00 00\r174 03 00 00 00 00 00 00 00\r"

    @property
    def in_waiting(self):
        return len(self.buffer)

    @property
    def commands(self):
        return [data.decode("ascii").strip() for data in self.writes if data != b" "]

    def _default_reply(self, command):
        if command.startswith("AT"):
            return "OK"
        if command.startswith("ST"):
            return "?" if self.device == "elm327" else "OK"
        return "NO DATA"

    def write(self, data):
        self.writes.append(data)
        if data == b" ":
            self.monitoring = False
            self.buffer.extend(b"STOPPED\r>")
            return len(data)
        command = data.decode("ascii").strip().upper()
        if command == "ATMA":
            self.monitoring = True
            self.buffer.extend(self.monitor_lines.encode())
        else:
            reply = self.replies.get(command, self._default_reply(command))
            if isinstance(reply, list):
                reply = reply.pop(0) if len(reply) > 1 else reply[0]
            if reply is not None:
                self.buffer.extend((reply + "\r>").encode())
        return len(data)

    def read(self, size=1):
        if self.read_error:
            raise OSError("USB disconnected")
        data = bytes(self.buffer[:size])
        del self.buffer[:size]
        return data

    def reset_input_buffer(self):
        self.buffer.clear()

    def close(self):
        self.closed = True
