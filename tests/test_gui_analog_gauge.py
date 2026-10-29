"""Offscreen tests for the AnalogGauge dashboard widget."""

from __future__ import annotations

import math
import time

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QMouseEvent

from q50diag.gui.widgets.analog_gauge import SETTLE_FRACTION, AnalogGauge, dial_ticks
from tests.test_gui_fakes import app  # noqa: F401 (fixture)

FRAME_S = 0.033


@pytest.fixture
def gauge(app):
    widget = AnalogGauge()
    widget.resize(220, 200)
    return widget


@pytest.fixture
def rpm(gauge):
    gauge.set_parameter("Engine RPM", "rpm", 0, 7000)
    return gauge


def run_frames(widget: AnalogGauge, seconds: float) -> bool:
    moving = True
    for _ in range(round(seconds / FRAME_S)):
        moving = widget.step(FRAME_S)
    return moving


def test_default_is_empty(gauge):
    assert gauge.mode() == "empty"
    assert gauge.readout_text() == "--"
    assert gauge.range() is None
    assert gauge.minimumWidth() >= 150 and gauge.minimumHeight() >= 140


def test_dial_mode_with_valid_range(rpm):
    assert rpm.mode() == "dial"
    assert rpm.range() == (0.0, 7000.0)
    assert rpm.readout_text() == "--"
    assert rpm.needle_value == 0.0
    assert rpm.target_value == 0.0
    assert rpm.is_settled()


@pytest.mark.parametrize("low, high", [(None, None), (0, None), (None, 100), (100, 0), (5, 5),
                                       (0, math.inf), (math.nan, 10)])
def test_digital_mode_without_valid_range(gauge, low, high):
    gauge.set_parameter("Coolant temperature", "degC", low, high)
    assert gauge.mode() == "digital"
    assert gauge.range() is None
    gauge.set_reading(42.5)
    assert gauge.readout_text() == "42.50"
    assert gauge.step(FRAME_S) is False
    assert gauge.is_settled()


def test_out_of_range_clamps_needle_but_readout_is_true(gauge):
    gauge.set_parameter("Vehicle speed", "km/h", 0, 250)
    gauge.set_reading(300)
    assert gauge.target_value == 250.0
    assert gauge.readout_text() == "300"
    gauge.set_reading(-20)
    assert gauge.target_value == 0.0
    assert gauge.readout_text() == "-20"


def test_text_value_switches_to_text_mode(rpm):
    rpm.set_reading("OPEN")
    assert rpm.mode() == "text"
    assert rpm.readout_text() == "OPEN"
    assert rpm.step(FRAME_S) is False
    rpm.set_reading(1000)
    assert rpm.mode() == "dial"


def test_error_sets_flag_readout_and_tooltip(rpm):
    rpm.set_reading(2000)
    rpm.set_reading(None, error="No response from ECM")
    assert rpm.has_error
    assert rpm.readout_text() == "error"
    assert rpm.toolTip() == "No response from ECM"
    assert rpm.target_value == 2000.0
    rpm.set_reading(2100)
    assert not rpm.has_error
    assert rpm.toolTip() == ""


def test_bool_reading(gauge):
    gauge.set_parameter("Brake switch", "", 0, 1)
    gauge.set_reading(True)
    assert gauge.readout_text() == "ON"
    assert gauge.target_value == 1.0
    gauge.set_reading(False)
    assert gauge.readout_text() == "OFF"
    assert gauge.target_value == 0.0


def test_none_reading_shows_dashes(rpm):
    rpm.set_reading(3000)
    rpm.set_reading(None)
    assert rpm.readout_text() == "--"
    assert rpm.target_value == 0.0


def test_set_parameter_resets_reading(rpm):
    rpm.set_reading(3000, 800, 6000)
    run_frames(rpm, 1.0)
    rpm.set_parameter("Vehicle speed", "km/h", 0, 250)
    assert rpm.readout_text() == "--"
    assert rpm.needle_value == 0.0 and rpm.target_value == 0.0


def test_step_converges_then_settles(rpm):
    rpm.set_reading(3500)
    assert not rpm.is_settled()
    assert rpm.step(FRAME_S) is True
    assert 0.0 < rpm.needle_value < 3500.0
    moving = run_frames(rpm, 1.0)
    assert moving is False
    assert rpm.needle_value == 3500.0
    assert rpm.is_settled()
    assert rpm.step(FRAME_S) is False


def test_step_snaps_inside_settle_fraction(rpm):
    rpm.set_reading(7000 * SETTLE_FRACTION * 0.5)
    assert rpm.step(FRAME_S) is False
    assert rpm.needle_value == rpm.target_value


def test_same_reading_does_not_repaint(rpm):
    rpm.set_reading(1500, 800, 2000)
    before = rpm._repaint_requests
    rpm.set_reading(1500, 800, 2000)
    assert rpm._repaint_requests == before
    rpm.set_reading(1600, 800, 2000)
    assert rpm._repaint_requests == before + 1


def test_settled_step_does_not_repaint(rpm):
    before = rpm._repaint_requests
    assert rpm.step(FRAME_S) is False
    assert rpm._repaint_requests == before


def test_face_cache_reused_and_rebuilt_on_resize(rpm):
    rpm.set_reading(2500, 900, 3000)
    rpm.grab()
    renders = rpm._face_renders
    rpm.step(FRAME_S)
    rpm.grab()
    assert rpm._face_renders == renders
    rpm.resize(400, 360)
    rpm.grab()
    assert rpm._face_renders == renders + 1


def test_empty_and_unavailable(gauge):
    gauge.set_unavailable("ABS.22.1208.steering_angle", "Steering angle (ABS)")
    assert gauge.mode() == "unavailable"
    gauge.set_reading(5.0)
    assert gauge.readout_text() == "--"
    gauge.set_empty()
    assert gauge.mode() == "empty"


def test_clicked_on_left_release(gauge):
    hits = []
    gauge.clicked.connect(lambda: hits.append(1))
    for button in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
        event = QMouseEvent(QMouseEvent.Type.MouseButtonRelease, QPointF(20, 20), QPointF(20, 20),
                            button, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
        gauge.mouseReleaseEvent(event)
    assert hits == [1]


def configure_state(widget: AnalogGauge, state: str) -> None:
    if state == "empty":
        widget.set_empty()
    elif state == "unavailable":
        widget.set_unavailable("x.key", "")
    elif state == "dial":
        widget.set_parameter("Engine RPM", "rpm", 0, 7000)
        widget.set_reading(3200.0, 750.0, 6100.0)
    elif state == "digital":
        widget.set_parameter("Coolant temperature", "degC", None, None)
        widget.set_reading(42.5, 20, 88)
    elif state == "text":
        widget.set_parameter("Brake switch", "", None, None)
        widget.set_reading("OPEN")
    elif state == "error":
        widget.set_parameter("Battery", "V", 10, 16)
        widget.set_reading(None, 11.0, 14.0, error="timeout")


@pytest.mark.parametrize("state", ["empty", "unavailable", "dial", "digital", "text", "error"])
@pytest.mark.parametrize("size", [(160, 150), (600, 500)])
def test_grab_renders_in_every_mode(gauge, state, size):
    gauge.resize(*size)
    configure_state(gauge, state)
    run_frames(gauge, 0.5)
    image = gauge.grab()
    assert not image.isNull()
    assert image.width() >= size[0]


@pytest.mark.parametrize("low, high", [(0, 7000), (0, 250), (10, 16), (-100, 250), (0.5, 1.5), (0, 1), (-40, 215)])
def test_dial_ticks_choose_four_to_eight_majors(low, high):
    majors, minors, label_radius, scale = dial_ticks(low, high, radius=120.0, char_width=10.0, label_px=18.0)
    assert 2 <= len(majors) <= 8
    assert label_radius > 0
    assert all(low - 1e-9 <= value <= high + 1e-9 for value, _ in majors)
    assert scale == (1000.0 if max(abs(low), abs(high)) >= 2000 else 1.0)
    if (low, high) == (0, 7000):
        assert [text for _, text in majors] == ["0", "1", "2", "3", "4", "5", "6", "7"]


def test_paint_budget_for_nine_gauges(app):
    gauges = []
    for index in range(9):
        widget = AnalogGauge()
        widget.resize(300, 260)
        widget.set_parameter(f"Sensor {index}", "rpm", 0, 7000)
        gauges.append(widget)
    for widget in gauges:
        widget.grab()
    started = time.perf_counter()
    for frame in range(30):
        for widget in gauges:
            widget.set_reading(100.0 * frame, 0.0, 100.0 * frame)
            widget.step(FRAME_S)
            widget.grab()
    elapsed = time.perf_counter() - started
    assert elapsed < 1.5, f"270 gauge paints took {elapsed:.2f} s"
