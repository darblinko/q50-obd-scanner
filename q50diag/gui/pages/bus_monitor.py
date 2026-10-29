"""Bus Monitor page: passive CAN capture aggregated by id, plus a raw view."""

from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QCheckBox, QLineEdit, QPlainTextEdit, QSplitter, QWidget

from q50diag.core.types import CanFrame, Value
from q50diag.gui import theme
from q50diag.gui.context import AppContext
from q50diag.gui.pages.base import Page
from q50diag.gui.widgets.common import Card, button, format_value, hbox, label, make_table, table_item
from q50diag.gui.widgets.hex_delegate import CHANGED_ROLE, HexBytesDelegate

REFRESH_MS = 200
RAW_MAX_LINES = 2000
RATE_WINDOW_S = 2.0
CHANGE_HIGHLIGHT_S = 1.0
MAX_CAN_ID = 0x7FF
HEADERS = ["ID", "Count", "Rate Hz", "Data", "Decoded"]
COL_ID, COL_COUNT, COL_RATE, COL_DATA, COL_DECODED = range(5)


def parse_can_ids(text: str) -> set[int] | None:
    """Parse "002, 300 5E4" into {0x002, 0x300, 0x5E4}. Empty text means no
    filter (None). Raises ValueError naming the first bad token."""
    tokens = [t for t in re.split(r"[\s,;]+", text.strip()) if t]
    if not tokens:
        return None
    ids: set[int] = set()
    for token in tokens:
        digits = token[2:] if token.lower().startswith("0x") else token
        if not re.fullmatch(r"[0-9A-Fa-f]{1,3}", digits):
            raise ValueError(f"'{token}' is not an 11-bit hex CAN id")
        value = int(digits, 16)
        if value > MAX_CAN_ID:
            raise ValueError(f"'{token}' is above 0x7FF")
        ids.add(value)
    return ids


@dataclass
class IdStats:
    can_id: int
    count: int = 0
    data: bytes = b""
    recent: deque = field(default_factory=lambda: deque(maxlen=1024))
    changed_at: list[float] = field(default_factory=list)
    decoded: dict[str, Value] = field(default_factory=dict)

    def add(self, frame: CanFrame) -> None:
        self.count += 1
        self.recent.append(frame.timestamp)
        if len(self.changed_at) < len(frame.data):
            self.changed_at += [0.0] * (len(frame.data) - len(self.changed_at))
        if self.data:
            for index, byte in enumerate(frame.data):
                if index >= len(self.data) or self.data[index] != byte:
                    self.changed_at[index] = frame.timestamp
        self.data = frame.data

    def rate(self, now: float) -> float:
        times = [t for t in self.recent if now - t <= RATE_WINDOW_S]
        if len(times) < 2:
            return 0.0
        span = times[-1] - times[0]
        return (len(times) - 1) / span if span > 0 else 0.0

    def changed_positions(self, now: float) -> list[int]:
        return [i for i, t in enumerate(self.changed_at[: len(self.data)]) if t and now - t <= CHANGE_HIGHLIGHT_S]


class BusMonitorPage(Page):
    title = "Bus Monitor"
    subtitle = "Passive capture of broadcast CAN traffic (ATMA). Frames captured while polling broadcast signals appear here too."

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(ctx, parent)
        self.stats: dict[int, IdStats] = {}
        self._rows: dict[int, int] = {}
        self._raw_pending: list[str] = []
        self._total_frames = 0
        self._names: dict[str, str] | None = None
        self._decode_failed = False
        self._build_controls()
        self._build_body()
        self._wire()
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()

    def _build_controls(self) -> None:
        self.filter_edit = QLineEdit()
        self.filter_edit.setProperty("role", "mono")
        self.filter_edit.setPlaceholderText("ID filter, hex: 002 300 5E4 (empty = all)")
        self.filter_edit.setMinimumWidth(280)
        self.clear_button = button("Clear", "ghost", "Clear the table and raw view")
        self.start_button = button("Start capture", "primary")
        self.start_button.setCheckable(True)
        self.header.add_action(self.filter_edit)
        self.header.add_action(self.clear_button)
        self.header.add_action(self.start_button)

    def _build_body(self) -> None:
        self.gateway_note = label(self.ctx.vehicle.gateway_note, "faint")
        self.gateway_note.setWordWrap(True)
        self.root.addWidget(self.gateway_note)
        self.summary = label("0 frames, 0 ids", "mono", "idle")
        self.root.addWidget(self.summary)
        splitter = QSplitter(Qt.Orientation.Vertical)
        table_card = Card("By CAN id")
        self.table = make_table(HEADERS, stretch_column=COL_DECODED)
        self.table.setItemDelegateForColumn(COL_DATA, HexBytesDelegate(self.table))
        for column, width in ((COL_ID, 70), (COL_COUNT, 90), (COL_RATE, 80), (COL_DATA, 260)):
            self.table.setColumnWidth(column, width)
        table_card.body.addWidget(self.table)
        raw_card = Card("Raw frames")
        self.raw_view = QPlainTextEdit()
        self.raw_view.setReadOnly(True)
        self.raw_view.setProperty("role", "mono")
        self.raw_view.setFont(theme.mono_font(9))
        self.raw_view.setMaximumBlockCount(RAW_MAX_LINES)
        self.raw_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.pause_raw = QCheckBox("Pause raw view")
        raw_card.body.addLayout(hbox(label(f"Last {RAW_MAX_LINES} frames", "faint"), None, self.pause_raw))
        raw_card.body.addWidget(self.raw_view)
        splitter.addWidget(table_card)
        splitter.addWidget(raw_card)
        splitter.setSizes([420, 280])
        self.root.addWidget(splitter, 1)

    def _wire(self) -> None:
        self.ctx.vehicle_changed.connect(lambda vehicle: self.gateway_note.setText(vehicle.gateway_note))
        self.start_button.toggled.connect(self._on_start_toggled)
        self.clear_button.clicked.connect(self.clear)
        self.filter_edit.returnPressed.connect(self._on_filter_entered)
        session = self.ctx.session
        session.frames.connect(self.add_frames)
        session.activity_changed.connect(self._sync_button)
        session.connection_changed.connect(lambda *_: self._sync_button())

    # Capture control ----------------------------------------------------
    def _parse_filter(self) -> tuple[bool, set[int] | None]:
        try:
            return True, parse_can_ids(self.filter_edit.text())
        except ValueError as exc:
            self.ctx.notify(f"Bus Monitor filter: {exc}", "warn")
            self._mark_filter(bad=True)
            return False, None

    def _mark_filter(self, bad: bool) -> None:
        theme.set_state(self.filter_edit, "fault" if bad else None)

    def _on_start_toggled(self, running: bool) -> None:
        self.start_button.setText("Stop capture" if running else "Start capture")
        if not running:
            self.ctx.session.stop_bus_monitor()
            return
        if not self.ctx.session.connected:
            self.ctx.notify("Bus Monitor: connect to an adapter or the simulator first", "warn")
            self._set_button(False)
            return
        ok, ids = self._parse_filter()
        if not ok:
            self._set_button(False)
            return
        self._mark_filter(bad=False)
        self.ctx.session.start_bus_monitor(ids)

    def _on_filter_entered(self) -> None:
        ok, ids = self._parse_filter()
        if ok:
            self._mark_filter(bad=False)
            if self.start_button.isChecked():
                self.ctx.session.start_bus_monitor(ids)

    def _set_button(self, running: bool) -> None:
        self.start_button.blockSignals(True)
        self.start_button.setChecked(running)
        self.start_button.setText("Stop capture" if running else "Start capture")
        self.start_button.blockSignals(False)

    def _sync_button(self) -> None:
        self._set_button(self.ctx.session.bus_monitor_active and self.ctx.session.connected)

    # Frames ---------------------------------------------------------------
    def add_frames(self, frames: list[CanFrame]) -> None:
        for frame in frames:
            entry = self.stats.get(frame.can_id)
            if entry is None:
                entry = self.stats[frame.can_id] = IdStats(frame.can_id)
            entry.add(frame)
            decoded = self._decode(frame)
            if decoded:
                entry.decoded.update(decoded)
            self._total_frames += 1
            if not self.pause_raw.isChecked():
                self._raw_pending.append(self._raw_line(frame))
        if len(self._raw_pending) > RAW_MAX_LINES:
            self._raw_pending = self._raw_pending[-RAW_MAX_LINES:]

    def _decode(self, frame: CanFrame) -> dict[str, Value]:
        if self._decode_failed:
            return {}
        try:
            return self.ctx.backend.decode_frame(frame)
        except ImportError as exc:
            self._decode_failed = True
            self.ctx.notify(f"Bus Monitor: broadcast decoder unavailable ({exc})", "warn")
        except Exception as exc:  # noqa: BLE001; one bad frame must not stop the capture
            self.ctx.notify(f"Bus Monitor: decode failed for 0x{frame.can_id:03X}: {exc}", "warn")
            self._decode_failed = True
        return {}

    @staticmethod
    def _raw_line(frame: CanFrame) -> str:
        stamp = datetime.fromtimestamp(frame.timestamp).strftime("%H:%M:%S.%f")[:-3]
        return f"{stamp}  {frame.can_id:03X}  [{len(frame.data)}]  {frame.data.hex(' ').upper()}"

    def _signal_names(self) -> dict[str, str]:
        if self._names is None:
            try:
                self._names = {k: name for k, (name, _unit) in self.ctx.backend.broadcast_signals().items()}
            except ImportError:
                self._names = {}
        return self._names

    def clear(self) -> None:
        self.stats.clear()
        self._rows.clear()
        self._raw_pending.clear()
        self._total_frames = 0
        self.table.setRowCount(0)
        self.raw_view.clear()
        self._update_summary(time.time())

    # Table refresh ------------------------------------------------------
    def refresh(self) -> None:
        now = time.time()
        if self._raw_pending:
            self.raw_view.appendPlainText("\n".join(self._raw_pending))
            self._raw_pending = []
        if set(self._rows) != set(self.stats):
            self._rebuild_rows()
        for can_id, row in self._rows.items():
            self._update_row(row, self.stats[can_id], now)
        self._update_summary(now)

    def _rebuild_rows(self) -> None:
        ids = sorted(self.stats)
        self.table.setRowCount(len(ids))
        self._rows = {can_id: row for row, can_id in enumerate(ids)}
        for can_id, row in self._rows.items():
            self.table.setItem(row, COL_ID, table_item(f"{can_id:03X}", mono=True, color=theme.ACCENT))
            self.table.setItem(row, COL_COUNT, table_item("", mono=True, align_right=True))
            self.table.setItem(row, COL_RATE, table_item("", mono=True, align_right=True))
            self.table.setItem(row, COL_DATA, table_item("", mono=True))
            self.table.setItem(row, COL_DECODED, table_item("", mono=True, color=theme.TEXT_DIM))

    def _update_row(self, row: int, entry: IdStats, now: float) -> None:
        self.table.item(row, COL_COUNT).setText(str(entry.count))
        self.table.item(row, COL_RATE).setText(f"{entry.rate(now):.1f}")
        data_item = self.table.item(row, COL_DATA)
        data_item.setText(entry.data.hex(" ").upper())
        data_item.setData(CHANGED_ROLE, entry.changed_positions(now))
        names = self._signal_names()
        decoded = "   ".join(f"{names.get(k, k)}: {format_value(v)}" for k, v in entry.decoded.items())
        self.table.item(row, COL_DECODED).setText(decoded)

    def _update_summary(self, now: float) -> None:
        total_rate = sum(entry.rate(now) for entry in self.stats.values())
        self.summary.setText(
            f"{self._total_frames} frames, {len(self.stats)} ids, {total_rate:.0f} frames/s"
        )
        theme.set_state(self.summary, "accent" if total_rate > 0 else "idle")
