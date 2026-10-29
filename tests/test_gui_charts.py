"""StackedCharts redraw cost: hidden pages and the y axis grid."""

import time

import pyqtgraph as pg
from PySide6.QtWidgets import QStackedWidget, QWidget

from q50diag.core.types import Reading
from q50diag.gui.store import SeriesStore
from q50diag.gui.widgets.charts import StackedCharts
from tests.test_gui_fakes import app, wait_until  # noqa: F401


def live_charts(keys: list[str]) -> tuple[SeriesStore, StackedCharts]:
    store = SeriesStore()
    start = time.time() - 5
    for key in keys:
        for index in range(50):
            store.add(Reading(key, float(index % 2) * 10, "V", start + index * 0.1))
    charts = StackedCharts(store, lambda key: (key, "V"), live=True)
    charts.set_keys(keys)
    return store, charts


def pump(seconds: float) -> None:
    wait_until(lambda: False, seconds)


def test_hidden_live_charts_skip_timer_redraws(app):
    store, charts = live_charts(["a", "b"])
    stack = QStackedWidget()
    other_page = QWidget()
    stack.addWidget(other_page)
    stack.addWidget(charts)
    stack.resize(1000, 600)
    stack.show()
    pump(0.2)
    plot = charts._plots[0]
    hidden_range = plot.viewRange()[0]
    store.add(Reading("a", 5.0, "V", time.time()))

    pump(0.4)
    assert plot.viewRange()[0] == hidden_range
    assert len(charts._curves["a"].getData()[0]) == 50

    stack.setCurrentWidget(charts)
    assert wait_until(lambda: len(charts._curves["a"].getData()[0]) == 51, 1.0)
    assert plot.viewRange()[0] != hidden_range


def test_scrolling_charts_do_not_repaint_the_y_axis_grid(app, monkeypatch):
    # Translucent grid lines take Qt's slow raster path, so the y axis, whose
    # grid spans the plot, must not be redrawn on every scrolled frame.
    painted_axes = []
    original_paint = pg.AxisItem.paint

    def counting_paint(self, *args):
        painted_axes.append(self)
        return original_paint(self, *args)

    monkeypatch.setattr(pg.AxisItem, "paint", counting_paint)
    _store, charts = live_charts(["a"])
    charts.resize(1200, 500)
    charts.show()
    pump(0.3)
    plot = charts._plots[0]
    scroll_start = plot.viewRange()[0]
    painted_axes.clear()

    pump(0.5)
    assert plot.viewRange()[0] != scroll_start
    assert plot.getAxis("bottom") in painted_axes
    assert painted_axes.count(plot.getAxis("left")) <= 1
