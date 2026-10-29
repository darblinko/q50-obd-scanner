"""Small declarative decoder for SAE bit positions and linear scaling."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Field:
    name: str
    bit: int
    width: int
    scale: float
    offset: float
    unit: str
    signed: bool = False
    support: int | None = None
    choices: tuple[tuple[int, str], ...] = ()
    wide_support: int | None = None
    wide_scale: float = 1
    unused: int | None = None  # raw value meaning "not used", decoded as None

    def decode(self, data: bytes):
        wide = self.wide_support is not None and bool(extract(data, self.wide_support, 1))
        if self.support is not None and not extract(data, self.support, 1) and not wide:
            return None
        value = extract(data, self.bit, self.width)
        if value == self.unused:
            return None
        if self.choices:
            return dict(self.choices).get(value, f"Unknown ({value})")
        if self.signed and value & (1 << (self.width - 1)):
            value -= 1 << self.width
        return value * (self.wide_scale if wide else self.scale) + self.offset

    def bounds(self) -> tuple[float | None, float | None]:
        if self.choices:
            return None, None
        low = -(1 << (self.width - 1)) if self.signed else 0
        high = (1 << (self.width - int(self.signed))) - 1
        return low * self.scale + self.offset, high * self.scale + self.offset


def extract(data: bytes, bit: int, width: int) -> int:
    """Bit zero is the most significant bit of the first data byte."""
    shift = len(data) * 8 - bit - width
    if shift < 0:
        raise ValueError(f"Truncated PID data: need {(bit + width + 7) // 8} bytes")
    return (int.from_bytes(data, "big") >> shift) & ((1 << width) - 1)


def decode_fields(fields: tuple[Field, ...], data: bytes):
    if len(fields) == 1:
        return fields[0].decode(data)
    values = []
    for field in fields:
        value = field.decode(data)
        if value is None:
            rendered = "n/a"
        elif isinstance(value, (float, int)):
            rendered = f"{value:.6g} {field.unit}".strip()
        else:
            rendered = str(value)
        values.append(f"{field.name}: {rendered}")
    return "; ".join(values)
