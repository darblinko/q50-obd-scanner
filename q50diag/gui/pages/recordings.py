"""Recordings page: list recorder CSV files and replay one in the charts."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QFileDialog,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from q50diag.gui import theme
from q50diag.gui.context import AppContext
from q50diag.gui.pages.base import Page
from q50diag.gui.widgets.charts import StackedCharts
from q50diag.gui.widgets.common import Card, button, hbox, label, make_table, table_item
from q50diag.recorder import DEFAULT_LOG_DIR, Recording, list_recordings, load_recording

PATH_ROLE = Qt.ItemDataRole.UserRole + 1
KEY_ROLE = Qt.ItemDataRole.UserRole + 2
DEFAULT_CHARTED = 4
EVENT_SPAN_S = 10.0  # seconds shown either side of a clicked event
PREFERRED_KEYS = (
    "adapter.voltage",
    "EPS.21.01.supply",
    "EPS.21.01.torque",
    "EPS.21.01.assist",
    "bcast.300.assist_raw",
    "bcast.002.steering_angle",
    "obd.01.0D",
    # Vehicle-neutral keys, so recordings without EPS data (Ford) still chart something useful.
    "obd.01.0C",
    "obd.01.05",
    "PCM.22.03EC",
)


class ReplaySource:
    """Series source for StackedCharts built from a loaded recording."""

    def __init__(self, recording: Recording | None = None) -> None:
        self.version = 0
        self._series: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        self._meta: dict[str, tuple[str, str]] = {}
        if recording is not None:
            self.load(recording)

    def load(self, recording: Recording) -> None:
        self._series = {
            key: (np.asarray(s.timestamps, dtype=float), np.asarray(s.values, dtype=float))
            for key, s in recording.series.items()
        }
        self._meta = {key: (s.name or key, s.unit) for key, s in recording.series.items()}
        self.version += 1

    def series(self, key: str) -> tuple[np.ndarray, np.ndarray]:
        return self._series.get(key, (np.empty(0), np.empty(0)))

    def meta(self, key: str) -> tuple[str, str]:
        return self._meta.get(key, (key, ""))


def count_rows(path: Path) -> int:
    """Data rows in a CSV (lines minus the header)."""
    lines = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            lines += block.count(b"\n")
    return max(0, lines - 1)


def format_size(size: int) -> str:
    for unit in ("B", "KB", "MB"):
        if size < 1024:
            return f"{size:.0f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


class RecordingsPage(Page):
    title = "Recordings"
    subtitle = "Session CSV files. Open one to replay its values and event markers."

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(ctx, parent)
        self.folder = ctx.recorder.folder if ctx.recorder.folder else DEFAULT_LOG_DIR
        self.recording: Recording | None = None
        self.source = ReplaySource()
        self._build_header()
        self._build_body()
        self._wire()
        self.refresh()

    def _build_header(self) -> None:
        self.folder_label = label("", "mono", "idle")
        self.folder_button = button("Choose folder", "ghost")
        self.refresh_button = button("Refresh", "ghost")
        self.open_button = button("Open", "primary")
        self.folder_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.root.addWidget(self.folder_label)
        for widget in (self.folder_button, self.refresh_button, self.open_button):
            self.header.add_action(widget)

    def _build_body(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_left())
        splitter.addWidget(self._build_right())
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([470, 900])
        self.root.addWidget(splitter, 1)

    def _build_left(self) -> QWidget:
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        files_card = Card("Files")
        self.files = make_table(["File", "Started", "Size", "Rows"], stretch_column=0)
        self.files.setColumnWidth(1, 112)
        self.files.setColumnWidth(2, 66)
        self.files.setColumnWidth(3, 60)
        self.empty_label = label("No recordings in this folder. Use Record in the top bar.", "faint")
        self.empty_label.setWordWrap(True)
        files_card.body.addWidget(self.files, 1)
        files_card.body.addWidget(self.empty_label)
        params_card = Card("Parameters")
        self.params = QListWidget()
        self.info_label = label("Open a recording to replay it.", "dim")
        self.info_label.setWordWrap(True)
        params_card.body.addWidget(self.info_label)
        params_card.body.addWidget(self.params, 1)
        layout.addWidget(files_card, 2)
        layout.addWidget(params_card, 3)
        return holder

    def _build_right(self) -> QWidget:
        splitter = QSplitter(Qt.Orientation.Vertical)
        chart_card = Card("Replay")
        self.charts = StackedCharts(self.source, self.source.meta, live=False)
        chart_card.body.addWidget(self.charts, 1)
        events_card = Card("Events")
        self.events = make_table(["Time", "Offset", "Event"], stretch_column=2)
        self.events.setColumnWidth(0, 130)
        self.events.setColumnWidth(1, 90)
        events_card.body.addWidget(self.events)
        splitter.addWidget(chart_card)
        splitter.addWidget(events_card)
        splitter.setSizes([560, 180])
        return splitter

    def _wire(self) -> None:
        self.folder_button.clicked.connect(self._choose_folder)
        self.refresh_button.clicked.connect(self.refresh)
        self.open_button.clicked.connect(self._open_selected)
        self.files.cellDoubleClicked.connect(self._on_file_double_clicked)
        self.params.itemChanged.connect(self._on_param_changed)
        self.events.cellClicked.connect(self._on_event_clicked)
        self.ctx.recording_changed.connect(lambda _on: self.refresh())

    def on_shown(self) -> None:
        self.refresh()

    # File list ----------------------------------------------------------
    def set_folder(self, folder: Path) -> None:
        self.folder = Path(folder)
        self.refresh()

    def _choose_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Recordings folder", str(self.folder))
        if chosen:
            self.set_folder(Path(chosen))

    def refresh(self) -> None:
        self.folder_label.setText(str(self.folder))
        try:
            paths = list_recordings(self.folder)
        except OSError as exc:
            self.ctx.notify(f"Recordings: cannot list {self.folder}: {exc}", "error")
            paths = []
        self.files.setRowCount(len(paths))
        for row, path in enumerate(paths):
            self._fill_file_row(row, path)
        self.empty_label.setVisible(not paths)

    def _fill_file_row(self, row: int, path: Path) -> None:
        try:
            stat = path.stat()
            size, started = format_size(stat.st_size), datetime.fromtimestamp(stat.st_mtime)
            rows = str(count_rows(path))
        except OSError:
            size, started, rows = "?", None, "?"
        name_item = table_item(path.name, mono=True)
        name_item.setData(PATH_ROLE, str(path))
        if self.ctx.recorder.is_recording and self.ctx.recorder.path == path:
            name_item.setForeground(_brush(theme.FAULT))
            name_item.setToolTip("Recording in progress")
        stamp = started.strftime("%m-%d %H:%M") if started else "?"
        self.files.setItem(row, 0, name_item)
        self.files.setItem(row, 1, table_item(stamp, mono=True, color=theme.TEXT_DIM))
        self.files.setItem(row, 2, table_item(size, mono=True, align_right=True))
        self.files.setItem(row, 3, table_item(rows, mono=True, align_right=True))

    def _open_selected(self) -> None:
        rows = self.files.selectionModel().selectedRows()
        if not rows:
            self.ctx.notify("Recordings: select a file to open", "warn")
            return
        self._open_row(rows[0].row())

    def _open_row(self, row: int) -> None:
        item = self.files.item(row, 0)
        if item is not None:
            self.open_recording(Path(item.data(PATH_ROLE)))

    # Replay ---------------------------------------------------------------
    def open_recording(self, path: Path) -> Recording | None:
        try:
            recording = load_recording(path)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            self.ctx.notify(f"Recordings: cannot open {path.name}: {exc}", "error")
            return None
        self.recording = recording
        self.source.load(recording)
        self._fill_params(recording)
        self._fill_events(recording)
        self._fill_info(recording)
        self.charts.set_markers([(e.timestamp, e.text) for e in recording.events])
        self._update_chart_keys()
        self.charts.set_source(self.source)
        if recording.bad_rows:
            self.ctx.notify(f"{path.name}: {recording.bad_rows} rows could not be parsed", "warn")
        return recording

    def _fill_params(self, recording: Recording) -> None:
        numeric = [s for s in recording.series.values() if s.values]
        numeric.sort(key=lambda s: (s.module, s.name))
        preferred = [k for k in PREFERRED_KEYS if k in recording.series and recording.series[k].values]
        default = set((preferred + [s.key for s in numeric])[:DEFAULT_CHARTED])
        self.params.blockSignals(True)
        self.params.clear()
        for series in numeric:
            unit = f" [{series.unit}]" if series.unit else ""
            text = f"{series.name}{unit}   {series.module}   {len(series.values)} pts"
            item = QListWidgetItem(text)
            item.setData(KEY_ROLE, series.key)
            item.setToolTip(series.key)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if series.key in default else Qt.CheckState.Unchecked)
            self.params.addItem(item)
        self.params.blockSignals(False)

    def _fill_events(self, recording: Recording) -> None:
        self.events.setRowCount(len(recording.events))
        start = recording.start or 0.0
        for row, event in enumerate(recording.events):
            clock = datetime.fromtimestamp(event.timestamp).strftime("%H:%M:%S.%f")[:-3]
            self.events.setItem(row, 0, table_item(clock, mono=True))
            self.events.setItem(row, 1, table_item(f"+{event.timestamp - start:.1f} s", mono=True, color=theme.TEXT_DIM))
            self.events.setItem(row, 2, table_item(f"#{row + 1}  {event.text}", color=theme.WARN))

    def _fill_info(self, recording: Recording) -> None:
        duration = (recording.end - recording.start) if recording.start and recording.end else 0.0
        self.info_label.setText(
            f"{recording.path.name}: {len(recording.series)} parameters, "
            f"{len(recording.events)} events, {duration:.1f} s"
        )

    def checked_keys(self) -> list[str]:
        keys = []
        for index in range(self.params.count()):
            item = self.params.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                keys.append(item.data(KEY_ROLE))
        return keys

    def _update_chart_keys(self) -> None:
        self.charts.set_keys(self.checked_keys())

    def _on_file_double_clicked(self, row: int, _column: int) -> None:
        self._open_row(row)

    def _on_param_changed(self, _item) -> None:
        self._update_chart_keys()

    def _on_event_clicked(self, row: int, _column: int) -> None:
        self.focus_event(row)

    def focus_event(self, row: int) -> None:
        """Center the charts on an event."""
        if self.recording is None or not 0 <= row < len(self.recording.events):
            return
        stamp = self.recording.events[row].timestamp
        self.charts.set_window(None)
        self.charts.focus(stamp - EVENT_SPAN_S, stamp + EVENT_SPAN_S)


def _brush(color: str) -> QBrush:
    return QBrush(QColor(color))
