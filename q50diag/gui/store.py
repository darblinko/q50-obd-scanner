"""In-memory time series of every reading received this session.

All pages and charts read from one store, filled from Session.readings on the
GUI thread. Text values are kept as `latest` only; numeric values (bools as
0/1) also go into the series."""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from q50diag.core.types import Reading, Value

MAX_POINTS = 50_000  # per key; about 80 minutes at 10 Hz
RATE_WINDOW_S = 5.0


@dataclass
class KeyStats:
    minimum: float | None = None
    maximum: float | None = None
    count: int = 0
    errors: int = 0
    last_error: str = ""
    recent: deque = field(default_factory=lambda: deque(maxlen=512))

    def rate(self, now: float) -> float:
        times = [t for t in self.recent if now - t <= RATE_WINDOW_S]
        if len(times) < 2:
            return 0.0
        span = times[-1] - times[0]
        return (len(times) - 1) / span if span > 0 else 0.0


def numeric(value: Value) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    return None


class SeriesStore:
    """Implements the series source interface used by the charts:
    `series(key) -> (x, y)` arrays and `has(key)`."""

    def __init__(self, max_points: int = MAX_POINTS) -> None:
        self._max_points = max_points
        self._times: dict[str, deque[float]] = {}
        self._values: dict[str, deque[float]] = {}
        self._latest: dict[str, Reading] = {}
        self._attempts: dict[str, Reading] = {}  # newest reading per key, failed or not
        self._stats: dict[str, KeyStats] = {}
        self.version = 0

    def add(self, reading: Reading) -> None:
        stats = self._stats.setdefault(reading.key, KeyStats())
        self.version += 1
        self._attempts[reading.key] = reading
        if reading.error:
            stats.errors += 1
            stats.last_error = reading.error
            if reading.key not in self._latest:
                self._latest[reading.key] = reading
            return
        self._latest[reading.key] = reading
        stats.count += 1
        stats.recent.append(reading.timestamp)
        number = numeric(reading.value)
        if number is None:
            return
        stats.minimum = number if stats.minimum is None else min(stats.minimum, number)
        stats.maximum = number if stats.maximum is None else max(stats.maximum, number)
        if reading.key not in self._times:
            self._times[reading.key] = deque(maxlen=self._max_points)
            self._values[reading.key] = deque(maxlen=self._max_points)
        self._times[reading.key].append(reading.timestamp)
        self._values[reading.key].append(number)

    def add_many(self, readings: list[Reading]) -> None:
        for reading in readings:
            self.add(reading)

    def keys(self) -> list[str]:
        return list(self._latest)

    def has(self, key: str) -> bool:
        return key in self._times

    def latest(self, key: str) -> Reading | None:
        """The newest successful reading, or the first error when none succeeded."""
        return self._latest.get(key)

    def latest_attempt(self, key: str) -> Reading | None:
        """The newest reading, including a failed one."""
        return self._attempts.get(key)

    def stats(self, key: str) -> KeyStats | None:
        return self._stats.get(key)

    def rate(self, key: str, now: float | None = None) -> float:
        stats = self._stats.get(key)
        return stats.rate(now if now is not None else time.time()) if stats else 0.0

    def series(self, key: str) -> tuple[np.ndarray, np.ndarray]:
        if key not in self._times:
            return np.empty(0), np.empty(0)
        return np.fromiter(self._times[key], float), np.fromiter(self._values[key], float)

    def window(self, key: str, start: float, end: float) -> list[float]:
        """Numeric values with start <= timestamp <= end."""
        if key not in self._times:
            return []
        return [v for t, v in zip(self._times[key], self._values[key]) if start <= t <= end]

    def time_range(self) -> tuple[float, float] | None:
        starts = [times[0] for times in self._times.values() if times]
        ends = [times[-1] for times in self._times.values() if times]
        if not starts:
            return None
        return min(starts), max(ends)

    def reset_stats(self, key: str | None = None) -> None:
        targets = [key] if key else list(self._stats)
        for name in targets:
            if name in self._stats:
                self._stats[name] = KeyStats()
        self.version += 1

    def clear(self) -> None:
        self._times.clear()
        self._values.clear()
        self._latest.clear()
        self._attempts.clear()
        self._stats.clear()
        self.version += 1
