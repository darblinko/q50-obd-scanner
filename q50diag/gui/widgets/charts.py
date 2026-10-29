"""Stacked, x-synchronized time charts built on pyqtgraph.

The chart pulls data from a source with `series(key) -> (x, y)` and an
integer `version` that changes when data changes (SeriesStore, or a replay
store). Live charts redraw on a timer at REDRAW_HZ; nothing redraws per
reading."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QComboBox, QGraphicsItem, QScrollArea, QVBoxLayout, QWidget

from q50diag.gui import theme
from q50diag.gui.widgets.common import button, hbox, label

REDRAW_HZ = 15
PLOT_MIN_HEIGHT = 120
WINDOWS = (("Last 60 s", 60.0), ("Last 5 min", 300.0), ("All", None))


class SeriesSource(Protocol):
    version: int

    def series(self, key: str) -> tuple[np.ndarray, np.ndarray]: ...


MetaFn = Callable[[str], tuple[str, str]]  # key -> (name, unit)


class StackedCharts(QWidget):
    def __init__(self, source: SeriesSource, meta: MetaFn, live: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._source = source
        self._meta = meta
        self._live = live
        self._keys: list[str] = []
        self._plots: list[pg.PlotItem] = []
        self._curves: dict[str, pg.PlotDataItem] = {}
        self._markers: list[tuple[float, str]] = []
        self._window: float | None = WINDOWS[0][1] if live else None
        self._paused = False
        self._drawn_version = -1
        self._build_ui()
        self._timer = QTimer(self)
        self._timer.setInterval(int(1000 / REDRAW_HZ))
        self._timer.timeout.connect(self._tick)
        if live:
            self._timer.start()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.window_box = QComboBox()
        for text, seconds in WINDOWS:
            self.window_box.addItem(text, seconds)
        self.window_box.setCurrentIndex(0 if self._live else len(WINDOWS) - 1)
        self.window_box.currentIndexChanged.connect(self._on_window_changed)
        self.pause_button = button("Pause", "ghost", "Freeze the charts to pan and zoom")
        self.pause_button.setCheckable(True)
        self.pause_button.toggled.connect(self.set_paused)
        self.pause_button.setVisible(self._live)
        autoscale = button("Autoscale", "ghost", "Reset the Y axis of every chart")
        autoscale.clicked.connect(self.autoscale)
        self.empty_label = label("Select parameters to chart", "faint")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addLayout(hbox(self.window_box, self.pause_button, autoscale, None))
        self.graphics = pg.GraphicsLayoutWidget()
        self.graphics.setBackground(theme.PANEL)
        self.graphics.ci.setSpacing(0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.graphics)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self.empty_label, 1)
        layout.addWidget(self.scroll, 1)
        self.scroll.setVisible(False)

    # Public API ---------------------------------------------------------
    def set_source(self, source: SeriesSource) -> None:
        self._source = source
        self._drawn_version = -1
        self.refresh(force=True)

    def keys(self) -> list[str]:
        return list(self._keys)

    def set_keys(self, keys: list[str]) -> None:
        if keys == self._keys:
            return
        self._keys = list(keys)
        self._rebuild_plots()
        self.refresh(force=True)

    def set_markers(self, markers: list[tuple[float, str]]) -> None:
        self._markers = list(markers)
        self._rebuild_plots()
        self.refresh(force=True)

    def add_marker(self, timestamp: float, text: str) -> None:
        self._markers.append((timestamp, text))
        number = len(self._markers)
        for index, plot in enumerate(self._plots):
            plot.addItem(self._marker_line(timestamp, text, number, index == 0))

    def set_window(self, seconds: float | None) -> None:
        for index in range(self.window_box.count()):
            if self.window_box.itemData(index) == seconds:
                self.window_box.setCurrentIndex(index)
                return

    def focus(self, start: float, end: float) -> None:
        """Show [start, end] on the shared x axis. Live charts pause so the
        range is not overwritten by the next redraw."""
        if not self._plots:
            return
        if self._live:
            self.set_paused(True)
        else:
            self._window = None
        self._plots[0].setXRange(start, end, padding=0)

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        self.pause_button.setText("Resume" if paused else "Pause")
        if self.pause_button.isChecked() != paused:
            self.pause_button.setChecked(paused)
        if not paused:
            self.refresh(force=True)

    def autoscale(self) -> None:
        for plot in self._plots:
            plot.enableAutoRange(axis="y", enable=True)
        if self._window is None:
            for plot in self._plots[:1]:
                plot.enableAutoRange(axis="x", enable=True)
        self.refresh(force=True)

    # Drawing ------------------------------------------------------------
    def _on_window_changed(self, _index: int) -> None:
        self._window = self.window_box.currentData()
        if self._window is None and self._plots:
            self._plots[0].enableAutoRange(axis="x", enable=True)
        self.refresh(force=True)

    def _rebuild_plots(self) -> None:
        self.graphics.clear()
        self._plots = []
        self._curves = {}
        for index, key in enumerate(self._keys):
            plot = self._make_plot(key, index)
            self.graphics.addItem(plot, row=index, col=0)
            self._plots.append(plot)
        for plot in self._plots[:-1]:
            # Shared x axis: only the bottom chart carries time labels.
            plot.getAxis("bottom").setStyle(showValues=False)
            plot.getAxis("bottom").setHeight(6)
        has_plots = bool(self._plots)
        self.scroll.setVisible(has_plots)
        self.empty_label.setVisible(not has_plots)
        self.graphics.setMinimumHeight(PLOT_MIN_HEIGHT * len(self._plots) + 30)

    def _make_plot(self, key: str, index: int) -> pg.PlotItem:
        axis = pg.DateAxisItem(orientation="bottom")
        plot = pg.PlotItem(axisItems={"bottom": axis})
        name, unit = self._meta(key)
        color = theme.SERIES_COLORS[index % len(theme.SERIES_COLORS)]
        plot.setTitle(self._title_html(name, unit, color, None), justify="left", size="9pt")
        plot.showGrid(x=True, y=True, alpha=0.12)
        plot.setMenuEnabled(False)
        plot.hideButtons()
        plot.setDownsampling(auto=True, mode="peak")
        plot.setClipToView(True)
        plot.setAutoVisible(y=True)
        plot.getAxis("left").setWidth(56)
        # Translucent grid lines take Qt's slow raster path; caching the y axis,
        # whose grid spans the plot, keeps them off every scrolled frame.
        plot.getAxis("left").setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)
        for axis_name in ("left", "bottom"):
            plot.getAxis(axis_name).setPen(pg.mkPen(theme.BORDER_STRONG))
            plot.getAxis(axis_name).setTextPen(pg.mkPen(theme.TEXT_DIM))
        if index > 0:
            plot.setXLink(self._plots[0])
        curve = plot.plot([], [], pen=pg.mkPen(color, width=1.6))
        self._curves[key] = curve
        for number, (timestamp, text) in enumerate(self._markers, start=1):
            plot.addItem(self._marker_line(timestamp, text, number, index == 0))
        return plot

    @staticmethod
    def _title_html(name: str, unit: str, color: str, value: float | None) -> str:
        reading = "" if value is None else f"&nbsp;&nbsp;<b>{value:.2f}</b> {unit}"
        unit_text = f" [{unit}]" if unit else ""
        return (
            f'<span style="color:{color}">&#9632;</span>&nbsp;'
            f'<span style="color:{theme.TEXT}">{name}</span>'
            f'<span style="color:{theme.TEXT_FAINT}">{unit_text}</span>'
            f'<span style="color:{color}">{reading}</span>'
        )

    @staticmethod
    def _marker_line(timestamp: float, text: str, number: int, show_label: bool) -> pg.InfiniteLine:
        """Markers are labelled "#n" so close markers do not overlap; the full
        text is the tooltip and appears in the pages' event tables."""
        pen = pg.mkPen(theme.WARN, width=1.2, style=Qt.PenStyle.DashLine)
        line = pg.InfiniteLine(pos=timestamp, angle=90, movable=False, pen=pen)
        line.setToolTip(f"#{number} {text}")
        if show_label:
            # Anchored to the label's right edge so it sits left of the line and
            # stays visible when the marker is at the live edge of the chart.
            line.label = pg.InfLineLabel(
                line, text=f"#{number}", position=0.92, anchors=[(1, 0), (1, 0)],
                color=theme.WARN, fill=pg.mkBrush(theme.BG_DEEP),
            )
        return line

    def _tick(self) -> None:
        # Pages stay alive in the stack while hidden; skip frames nobody sees.
        if self.isVisible():
            self.refresh()

    def refresh(self, force: bool = False) -> None:
        if not self._plots or (self._paused and not force):
            return
        version = getattr(self._source, "version", 0)
        if version != self._drawn_version or force:
            self._drawn_version = version
            self._update_curves()
        self._update_x_range()

    def _update_curves(self) -> None:
        for index, key in enumerate(self._keys):
            x, y = self._source.series(key)
            self._curves[key].setData(x, y)
            name, unit = self._meta(key)
            color = theme.SERIES_COLORS[index % len(theme.SERIES_COLORS)]
            last = float(y[-1]) if len(y) else None
            self._plots[index].setTitle(self._title_html(name, unit, color, last), justify="left", size="9pt")

    def _update_x_range(self) -> None:
        if self._window is None or not self._plots:
            return
        end = time.time() if self._live else self._source_end()
        if end is None:
            return
        self._plots[0].setXRange(end - self._window, end, padding=0)

    def _source_end(self) -> float | None:
        ends = [self._source.series(key)[0] for key in self._keys]
        ends = [float(x[-1]) for x in ends if len(x)]
        return max(ends) if ends else None
