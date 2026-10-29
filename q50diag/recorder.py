"""CSV session recorder and loader.

Long format, one row per value: timestamp, type, key, name, value, unit,
module, error. `type` is DATA for readings and EVENT for user markers
(key "event", name holds the marker text)."""

from __future__ import annotations

import csv
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, TextIO

from q50diag.core.types import Reading

COLUMNS = ("timestamp", "type", "key", "name", "value", "unit", "module", "error")
DATA = "DATA"
EVENT = "EVENT"
FLUSH_INTERVAL_S = 1.0

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG_DIR = PROJECT_ROOT / "logs"


def recording_filename(started: float) -> str:
    return datetime.fromtimestamp(started).strftime("%Y%m%d-%H%M%S") + ".csv"


class Recorder:
    def __init__(self, folder: Path | None = None, clock: Callable[[], float] = time.time) -> None:
        self.folder = Path(folder) if folder else DEFAULT_LOG_DIR
        self._clock = clock
        self._file: TextIO | None = None
        self._writer = None
        self.path: Path | None = None
        self.started_at: float | None = None
        self.rows_written = 0
        self._last_flush = 0.0

    @property
    def is_recording(self) -> bool:
        return self._file is not None

    def elapsed(self) -> float:
        if self.started_at is None or not self.is_recording:
            return 0.0
        return max(0.0, self._clock() - self.started_at)

    def start(self, folder: Path | None = None) -> Path:
        if self.is_recording:
            self.stop()
        if folder:
            self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.started_at = self._clock()
        path = self._unique_path(self.folder / recording_filename(self.started_at))
        self._file = path.open("w", newline="", encoding="utf-8")
        self._writer = csv.writer(self._file)
        self._writer.writerow(COLUMNS)
        self.path = path
        self.rows_written = 0
        return path

    def stop(self) -> Path | None:
        if self._file is None:
            return None
        self._file.close()
        self._file = None
        self._writer = None
        return self.path

    def write_reading(self, reading: Reading, name: str = "", module: str = "") -> None:
        if self._writer is None:
            return
        self._writer.writerow(
            (
                f"{reading.timestamp:.3f}",
                DATA,
                reading.key,
                name,
                _format_value(reading.value),
                reading.unit,
                module,
                reading.error,
            )
        )
        self._after_write()

    def mark_event(self, text: str, timestamp: float | None = None) -> float:
        """Write an EVENT row. Returns the event timestamp. A no-op on disk
        when not recording, so the caller can still place the marker."""
        stamp = timestamp if timestamp is not None else self._clock()
        if self._writer is not None:
            self._writer.writerow((f"{stamp:.3f}", EVENT, "event", text, "", "", "", ""))
            self._after_write(force_flush=True)
        return stamp

    def _after_write(self, force_flush: bool = False) -> None:
        self.rows_written += 1
        now = self._clock()
        if self._file is not None and (force_flush or now - self._last_flush >= FLUSH_INTERVAL_S):
            self._file.flush()
            self._last_flush = now

    @staticmethod
    def _unique_path(path: Path) -> Path:
        candidate, index = path, 1
        while candidate.exists():
            candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
            index += 1
        return candidate


def _format_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


@dataclass
class Series:
    key: str
    name: str
    unit: str
    module: str
    timestamps: list[float] = field(default_factory=list)
    values: list[float] = field(default_factory=list)
    text_values: list[str] = field(default_factory=list)
    errors: int = 0


@dataclass(frozen=True)
class RecordedEvent:
    timestamp: float
    text: str


@dataclass
class Recording:
    path: Path
    series: dict[str, Series]
    events: list[RecordedEvent]
    start: float | None
    end: float | None
    bad_rows: int = 0


def load_recording(path: Path) -> Recording:
    """Parse a recorder CSV. Numeric values go to Series.values; text values
    are kept in text_values; rows that fail to parse are counted."""
    series: dict[str, Series] = {}
    events: list[RecordedEvent] = []
    stamps: list[float] = []
    bad = 0
    with Path(path).open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                stamp = float(row["timestamp"])
            except (KeyError, TypeError, ValueError):
                bad += 1
                continue
            stamps.append(stamp)
            if row.get("type") == EVENT:
                events.append(RecordedEvent(stamp, row.get("name") or ""))
                continue
            _add_row(series, row, stamp)
    start = min(stamps) if stamps else None
    end = max(stamps) if stamps else None
    return Recording(Path(path), series, events, start, end, bad)


def _add_row(series: dict[str, Series], row: dict, stamp: float) -> None:
    key = row.get("key") or ""
    entry = series.get(key)
    if entry is None:
        entry = Series(key, row.get("name") or key, row.get("unit") or "", row.get("module") or "")
        series[key] = entry
    if row.get("error"):
        entry.errors += 1
        return
    text = row.get("value") or ""
    try:
        number = float(text)
    except ValueError:
        if text:
            entry.text_values.append(text)
        return
    entry.timestamps.append(stamp)
    entry.values.append(number)


def list_recordings(folder: Path | None = None) -> list[Path]:
    root = Path(folder) if folder else DEFAULT_LOG_DIR
    if not root.is_dir():
        return []
    return sorted(root.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
