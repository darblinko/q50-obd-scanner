"""Analog dashboard gauge: a 270 degree dial with a smoothed needle, or a large
digital readout when the parameter has no valid range.

The owner drives animation. `set_reading` sets the needle target and the
readout; `step(dt_s)` moves the needle toward the target. The static face
(card, name, track, ticks, labels, unit) is rendered once per size and
parameter into a cached pixmap. Each paint draws that pixmap, then the value
arc, needle, session markers and text."""

from __future__ import annotations

import math
import numbers
from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QMouseEvent, QPainter, QPaintEvent, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget

from q50diag.gui import theme
from q50diag.gui.widgets.common import format_value

TIME_CONSTANT_S = 0.1
SETTLE_FRACTION = 0.001

START_DEG = 225.0
SWEEP_DEG = 270.0
PAD = 8.0
CORNER_RADIUS = 6.0
MAX_MAJORS = 8
NICE_MANTISSAS = (1.0, 2.0, 2.5, 5.0)
MINOR_DIVISIONS = {1.0: 5, 2.0: 4, 2.5: 5, 5.0: 5}
KILO_THRESHOLD = 2000.0

# Radii as fractions of the dial radius.
TRACK_R = 0.95
TICK_OUTER_R = 0.90
MAJOR_INNER_R = 0.77
MINOR_INNER_R = 0.84
NEEDLE_TIP_R = 0.84
NEEDLE_TAIL_R = 0.14
HUB_R = 0.08
MARKER_TIP_R = 0.98
MARKER_BASE_R = 1.08


@dataclass
class _Geometry:
    """Rects and fonts for one widget size and layout (dial or digital)."""

    name_font: QFont
    name_rect: QRectF
    small_font: QFont
    footer_rect: QRectF
    err_font: QFont
    err_rect: QRectF
    unit_font: QFont = field(default_factory=QFont)
    unit_rect: QRectF = field(default_factory=QRectF)
    readout_fonts: tuple[QFont, ...] = ()
    readout_metrics: tuple[QFontMetricsF, ...] = ()
    readout_rect: QRectF = field(default_factory=QRectF)
    center: QPointF = field(default_factory=QPointF)
    radius: float = 0.0
    tick_font: QFont = field(default_factory=QFont)
    kilo_font: QFont = field(default_factory=QFont)


def _clamp(value: float, low: float, high: float) -> float:
    return min(max(value, low), high)


def _font(family: str, pixel_size: float, bold: bool = False) -> QFont:
    font = QFont(family)
    font.setPixelSize(max(1, round(pixel_size)))
    font.setBold(bold)
    return font


def _numeric(value) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, numbers.Real) and math.isfinite(float(value)):
        return float(value)
    return None


def _digits_for(*values: float | None) -> int:
    magnitude = max((abs(v) for v in values if v is not None), default=0.0)
    if magnitude >= 1000:
        return 0
    return 1 if magnitude >= 100 else 2


def _polar(center: QPointF, radius: float, angle_deg: float) -> QPointF:
    angle = math.radians(angle_deg)
    return QPointF(center.x() + radius * math.cos(angle), center.y() - radius * math.sin(angle))


def _label_decimals(step: float) -> int:
    for decimals in range(5):
        if abs(round(step, decimals) - step) < 1e-9 * max(1.0, abs(step)):
            return decimals
    return 4


def _label_text(value: float, decimals: int) -> str:
    text = f"{value:.{decimals}f}"
    return "0" if text.lstrip("-").strip("0.") == "" else text


def _nice_steps(span: float):
    """Yield (mantissa, step) for 1, 2, 2.5, 5 x 10^n in ascending order."""
    exponent = math.floor(math.log10(span / 10.0))
    while True:
        for mantissa in NICE_MANTISSAS:
            yield mantissa, mantissa * 10.0 ** exponent
        exponent += 1


def _multiples(low: float, high: float, step: float) -> list[float]:
    first = math.ceil(low / step - 1e-9)
    last = math.floor(high / step + 1e-9)
    return [index * step for index in range(first, last + 1)]


def dial_ticks(low: float, high: float, radius: float, char_width: float, label_px: float):
    """Pick a nice major step whose labels fit along the arc. Returns
    (majors as (value, text), minors, label radius, scale)."""
    scale = 1000.0 if max(abs(low), abs(high)) >= KILO_THRESHOLD else 1.0
    span = high - low
    for mantissa, step in _nice_steps(span):
        majors = _multiples(low, high, step)
        if len(majors) > MAX_MAJORS:
            continue
        decimals = _label_decimals(step / scale)
        texts = [_label_text(value / scale, decimals) for value in majors]
        widest = max(len(text) for text in texts) * char_width
        label_radius = radius * (MAJOR_INNER_R - 0.04) - 0.55 * max(widest, label_px)
        spacing = math.radians(SWEEP_DEG) * label_radius * step / span
        if spacing >= widest + 0.8 * label_px or len(majors) <= 2:
            minor_step = step / MINOR_DIVISIONS[mantissa]
            minors = [] if spacing / MINOR_DIVISIONS[mantissa] < 3 else [
                value for value in _multiples(low, high, minor_step)
                if abs(value / step - round(value / step)) > 1e-6]
            return list(zip(majors, texts)), minors, label_radius, scale
    return [], [], 0.0, scale


def _frame_geometry(width: int, height: int) -> tuple[_Geometry, QRectF]:
    """Name row, footer row and ERR marker shared by every layout; also returns the body rect."""
    name_px = _clamp(min(width, height) * 0.075, 11, 15)
    small_px = _clamp(min(width, height) * 0.06, 9, 12)
    err_font = _font(theme.mono_family(), small_px, bold=True)
    err_width = QFontMetricsF(err_font).horizontalAdvance("ERR") + 8
    row_height = name_px * 1.4
    err_rect = QRectF(width - PAD - err_width, PAD + (row_height - small_px * 1.4) / 2, err_width, small_px * 1.4)
    name_rect = QRectF(PAD + 2, PAD, width - 2 * PAD - err_width - 8, row_height)
    footer_height = small_px * 1.5
    footer_rect = QRectF(PAD, height - PAD - footer_height, width - 2 * PAD, footer_height)
    body = QRectF(PAD, name_rect.bottom() + 2, width - 2 * PAD, footer_rect.top() - name_rect.bottom() - 4)
    geometry = _Geometry(_font(theme.sans_family(), name_px, bold=True), name_rect,
                         _font(theme.mono_family(), small_px), footer_rect, err_font, err_rect)
    return geometry, body


def _set_readout_fonts(geometry: _Geometry, pixel_size: float) -> None:
    fonts = tuple(_font(theme.mono_family(), pixel_size * factor, bold=True) for factor in (1.0, 0.75, 0.55))
    geometry.readout_fonts = fonts
    geometry.readout_metrics = tuple(QFontMetricsF(font) for font in fonts)


def build_geometry(width: int, height: int, dial: bool) -> _Geometry:
    geometry, body = _frame_geometry(width, height)
    if dial:
        radius = max(10.0, min(body.width() / (2 * MARKER_BASE_R), body.height() / (MARKER_BASE_R + 1.0)))
        center = QPointF(width / 2, body.top() + MARKER_BASE_R * radius
                         + max(0.0, body.height() - (MARKER_BASE_R + 1.0) * radius) / 2)
        readout_px = max(12.0, radius * 0.34)
        unit_px = max(9.0, radius * 0.14)
        kilo_px = max(8.0, radius * 0.11)
        geometry.center = center
        geometry.radius = radius
        geometry.tick_font = _font(theme.mono_family(), max(8.0, radius * 0.17))
        geometry.unit_font = _font(theme.sans_family(), unit_px)
        geometry.kilo_font = _font(theme.mono_family(), kilo_px)
        geometry.readout_rect = QRectF(center.x() - 0.64 * radius, center.y() + 0.80 * radius - readout_px * 0.65,
                                       1.28 * radius, readout_px * 1.3)
    else:
        readout_px = _clamp(min(body.height() * 0.42, body.width() * 0.2), 14, 64)
        unit_px = _clamp(readout_px * 0.32, 9, 16)
        top = body.center().y() - (readout_px * 1.3 + unit_px * 1.5) / 2
        geometry.unit_font = _font(theme.sans_family(), unit_px)
        geometry.readout_rect = QRectF(body.left(), top, body.width(), readout_px * 1.3)
        geometry.unit_rect = QRectF(body.left(), top + readout_px * 1.3, body.width(), unit_px * 1.5)
    _set_readout_fonts(geometry, readout_px)
    return geometry


def _pen(color: str, width: float = 1.0, cap: Qt.PenCapStyle = Qt.PenCapStyle.RoundCap) -> QPen:
    pen = QPen(QColor(color), width)
    pen.setCapStyle(cap)
    return pen


def _draw_text(painter: QPainter, rect: QRectF, text: str, font: QFont, color: str,
               align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignCenter) -> None:
    painter.setFont(font)
    painter.setPen(QColor(color))
    elided = QFontMetricsF(font).elidedText(text, Qt.TextElideMode.ElideRight, rect.width())
    painter.drawText(rect, int(align | Qt.AlignmentFlag.AlignVCenter), elided)


def _arc_rect(center: QPointF, radius: float) -> QRectF:
    return QRectF(center.x() - radius, center.y() - radius, 2 * radius, 2 * radius)


def _paint_card(painter: QPainter, width: int, height: int, dashed: bool = False) -> None:
    pen = _pen(theme.BORDER_STRONG if dashed else theme.BORDER, 1.2 if dashed else 1.0)
    if dashed:
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setBrush(Qt.BrushStyle.NoBrush)
    else:
        painter.setBrush(QColor(theme.PANEL))
    painter.setPen(pen)
    painter.drawRoundedRect(QRectF(0.5, 0.5, width - 1, height - 1), CORNER_RADIUS, CORNER_RADIUS)


def _paint_dial_ticks(painter: QPainter, geometry: _Geometry, low: float, high: float) -> tuple[list[QRectF], float]:
    """Draw minor and major ticks with labels. Returns the label rects and the label scale."""
    center, radius = geometry.center, geometry.radius
    metrics = QFontMetricsF(geometry.tick_font)
    majors, minors, label_radius, scale = dial_ticks(low, high, radius, metrics.horizontalAdvance("0"),
                                                     geometry.tick_font.pixelSize())
    span = high - low

    def angle(value: float) -> float:
        return START_DEG - SWEEP_DEG * (value - low) / span

    painter.setPen(_pen(theme.TEXT_FAINT, max(1.0, radius * 0.012)))
    for value in minors:
        painter.drawLine(_polar(center, radius * MINOR_INNER_R, angle(value)),
                         _polar(center, radius * TICK_OUTER_R, angle(value)))
    painter.setPen(_pen(theme.TEXT_DIM, max(1.5, radius * 0.025)))
    for value, _ in majors:
        painter.drawLine(_polar(center, radius * MAJOR_INNER_R, angle(value)),
                         _polar(center, radius * TICK_OUTER_R, angle(value)))
    painter.setFont(geometry.tick_font)
    label_rects = []
    for value, text in majors:
        point = _polar(center, label_radius, angle(value))
        width, height = metrics.horizontalAdvance(text) + 2, metrics.height()
        rect = QRectF(point.x() - width / 2, point.y() - height / 2, width, height)
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), text)
        label_rects.append(rect)
    return label_rects, scale


def _paint_dial_unit(painter: QPainter, geometry: _Geometry, unit: str, kilo: bool, label_rects: list[QRectF]) -> None:
    """Draw the unit (and "x1000") below the hub, or above it when that collides with a label."""
    center, radius = geometry.center, geometry.radius
    unit_metrics, kilo_metrics = QFontMetricsF(geometry.unit_font), QFontMetricsF(geometry.kilo_font)
    unit_height = unit_metrics.height() if unit else 0.0
    kilo_height = kilo_metrics.height() if kilo else 0.0
    width = min(0.9 * radius, 4 + max(unit_metrics.horizontalAdvance(unit),
                                      kilo_metrics.horizontalAdvance("x1000") if kilo else 0.0))
    gap = radius * (HUB_R + 0.04)
    candidates = (center.y() + gap, center.y() - gap - unit_height - kilo_height)
    top = candidates[0]
    for candidate in candidates:
        block = QRectF(center.x() - width / 2, candidate, width, unit_height + kilo_height)
        if not any(block.intersects(rect) for rect in label_rects):
            top = candidate
            break
    left = center.x() - 0.45 * radius
    if unit:
        _draw_text(painter, QRectF(left, top, 0.9 * radius, unit_height), unit, geometry.unit_font, theme.TEXT_DIM)
    if kilo:
        _draw_text(painter, QRectF(left, top + unit_height, 0.9 * radius, kilo_height), "x1000",
                   geometry.kilo_font, theme.TEXT_FAINT)


def _paint_dial_face(painter: QPainter, geometry: _Geometry, low: float, high: float, unit: str) -> None:
    radius = geometry.radius
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(_pen(theme.BORDER_STRONG, max(2.0, radius * 0.05), Qt.PenCapStyle.FlatCap))
    painter.drawArc(_arc_rect(geometry.center, radius * TRACK_R), round(START_DEG * 16), round(-SWEEP_DEG * 16))
    label_rects, scale = _paint_dial_ticks(painter, geometry, low, high)
    _paint_dial_unit(painter, geometry, unit, scale != 1.0, label_rects)


class AnalogGauge(QWidget):
    """`clicked()` fires on a left mouse release inside the widget."""

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(160, 150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._kind = "empty"
        self._name = ""
        self._unit = ""
        self._range: tuple[float, float] | None = None
        self._geometry_key: tuple | None = None
        self._geometry_cache: _Geometry | None = None
        self._face: QPixmap | None = None
        self._face_key: tuple | None = None
        self._face_renders = 0
        self._repaint_requests = 0
        self._fit_key: tuple | None = None
        self._fit: tuple[QFont, str] | None = None
        self._reset_reading()

    # Public interface

    def sizeHint(self) -> QSize:
        return QSize(220, 200)

    def set_empty(self) -> None:
        self._configure("empty", "", "", None)

    def set_unavailable(self, key: str, name: str = "") -> None:
        self._configure("unavailable", name or key, "", None)

    def set_parameter(self, name: str, unit: str, low: float | None, high: float | None) -> None:
        valid = (low is not None and high is not None and math.isfinite(low) and math.isfinite(high)
                 and high > low)
        self._configure("parameter", name, unit, (float(low), float(high)) if valid else None)

    def set_reading(self, value, minimum: float | None = None, maximum: float | None = None, error: str = "") -> None:
        if self._kind != "parameter":
            return
        numeric = _numeric(value)
        self._is_text = isinstance(value, str)
        self._error = error
        self._readout = "error" if error else format_value(value, _digits_for(numeric))
        session_min, session_max = _numeric(minimum), _numeric(maximum)
        if minimum is None and maximum is None:
            self._minmax_text = ""
        else:
            digits = _digits_for(session_min, session_max)
            self._minmax_text = f"min {format_value(minimum, digits)}  max {format_value(maximum, digits)}"
        if self._range is not None:
            low, high = self._range
            if not error:
                self._target = low if numeric is None else _clamp(numeric, low, high)
            self._markers = tuple(None if v is None else _clamp(v, low, high) for v in (session_min, session_max))
            if self._is_text:
                self._needle = self._target
        if self.toolTip() != error:
            self.setToolTip(error)
        state = self._visible_state()
        if state != self._last_state:
            self._last_state = state
            self._request_update()

    def step(self, dt_s: float) -> bool:
        if self.mode() != "dial" or self._needle == self._target:
            return False
        low, high = self._range
        threshold = SETTLE_FRACTION * (high - low)
        before = self._needle
        if dt_s > 0:
            self._needle += (self._target - self._needle) * (1.0 - math.exp(-dt_s / TIME_CONSTANT_S))
        if abs(self._target - self._needle) <= threshold:
            self._needle = self._target
        if self._needle != before:
            self._request_update()
        return self._needle != self._target

    def is_settled(self) -> bool:
        return self.mode() != "dial" or self._needle == self._target

    def mode(self) -> str:
        if self._kind != "parameter":
            return self._kind
        if self._is_text:
            return "text"
        return "dial" if self._range is not None else "digital"

    def readout_text(self) -> str:
        return self._readout

    def range(self) -> tuple[float, float] | None:
        return self._range

    @property
    def needle_value(self) -> float:
        return self._needle

    @property
    def target_value(self) -> float:
        return self._target

    @property
    def has_error(self) -> bool:
        return bool(self._error)

    # State helpers

    def _configure(self, kind: str, name: str, unit: str, value_range: tuple[float, float] | None) -> None:
        self._kind, self._name, self._unit, self._range = kind, name, unit, value_range
        self._reset_reading()
        self.setToolTip("")
        self._request_update()

    def _reset_reading(self) -> None:
        self._is_text = False
        self._error = ""
        self._readout = "--"
        self._minmax_text = ""
        self._markers: tuple[float | None, float | None] = (None, None)
        self._target = self._needle = self._range[0] if self._range else 0.0
        self._last_state = self._visible_state()

    def _visible_state(self) -> tuple:
        return (self._readout, self._minmax_text, self._is_text, self._error, self._markers)

    def _request_update(self) -> None:
        self._repaint_requests += 1
        self.update()

    def _geometry(self) -> _Geometry:
        key = (self.width(), self.height(), self.mode() == "dial")
        if key != self._geometry_key:
            self._geometry_cache = build_geometry(*key)
            self._geometry_key = key
        return self._geometry_cache

    # Painting

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        geometry = self._geometry()
        mode = self.mode()
        low, high = self._range or (None, None)
        key = (self.width(), self.height(), self.devicePixelRatioF(), low, high, self._name, self._unit, mode)
        if key != self._face_key or self._face is None:
            self._face = self._render_face(geometry, mode)
            self._face_key = key
            self._face_renders += 1
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._face)
        if mode not in ("empty", "unavailable"):
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            if mode == "dial":
                self._paint_value_arc(painter, geometry)
                self._paint_markers(painter, geometry)
                self._paint_needle(painter, geometry)
            self._paint_readout(painter, geometry)
            _draw_text(painter, geometry.footer_rect, self._minmax_text, geometry.small_font, theme.TEXT_FAINT)
            if self._error:
                self._paint_error_marker(painter, geometry)
        painter.end()

    def _render_face(self, geometry: _Geometry, mode: str) -> QPixmap:
        ratio = self.devicePixelRatioF()
        pixmap = QPixmap(max(1, round(self.width() * ratio)), max(1, round(self.height() * ratio)))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        _paint_card(painter, self.width(), self.height(), dashed=mode == "empty")
        if mode in ("empty", "unavailable"):
            self._paint_placeholder(painter, geometry, mode)
        else:
            _draw_text(painter, geometry.name_rect, self._name, geometry.name_font, theme.TEXT_DIM,
                       Qt.AlignmentFlag.AlignLeft)
            if mode == "dial":
                _paint_dial_face(painter, geometry, *self._range, self._unit)
            else:
                _draw_text(painter, geometry.unit_rect, self._unit, geometry.unit_font, theme.TEXT_DIM)
        painter.end()
        return pixmap

    def _paint_placeholder(self, painter: QPainter, geometry: _Geometry, mode: str) -> None:
        title = "Empty slot" if mode == "empty" else self._name
        detail = "Click to choose a sensor" if mode == "empty" else "Unavailable on this vehicle"
        line = geometry.name_font.pixelSize() * 1.6
        middle = self.height() / 2
        width = self.width() - 2 * PAD
        _draw_text(painter, QRectF(PAD, middle - line, width, line), title, geometry.name_font,
                   theme.TEXT_FAINT if mode == "empty" else theme.TEXT_DIM)
        painter.setFont(geometry.small_font)
        painter.setPen(QColor(theme.TEXT_FAINT))
        flags = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap
        painter.drawText(QRectF(PAD, middle, width, line * 2), int(flags), detail)

    def _angle(self, value: float) -> float:
        low, high = self._range
        return START_DEG - SWEEP_DEG * (value - low) / (high - low)

    def _paint_value_arc(self, painter: QPainter, geometry: _Geometry) -> None:
        low, high = self._range
        fraction = (self._needle - low) / (high - low)
        if fraction <= 0:
            return
        color = theme.TEXT_FAINT if self._error else theme.ACCENT
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(_pen(color, max(2.0, geometry.radius * 0.05), Qt.PenCapStyle.FlatCap))
        painter.drawArc(_arc_rect(geometry.center, geometry.radius * TRACK_R), round(START_DEG * 16),
                        round(-SWEEP_DEG * fraction * 16))

    def _paint_markers(self, painter: QPainter, geometry: _Geometry) -> None:
        center, radius = geometry.center, geometry.radius
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.TEXT_DIM))
        half_width_deg = math.degrees(0.045 * radius / (radius * MARKER_BASE_R))
        for value in self._markers:
            if value is None:
                continue
            angle = self._angle(value)
            painter.drawPolygon(QPolygonF([
                _polar(center, radius * MARKER_TIP_R, angle),
                _polar(center, radius * MARKER_BASE_R, angle - half_width_deg),
                _polar(center, radius * MARKER_BASE_R, angle + half_width_deg),
            ]))

    def _paint_needle(self, painter: QPainter, geometry: _Geometry) -> None:
        center, radius = geometry.center, geometry.radius
        color = QColor(theme.TEXT_FAINT if self._error else theme.ACCENT)
        half_width = max(1.5, radius * 0.035)
        tip, tail = radius * NEEDLE_TIP_R, radius * NEEDLE_TAIL_R
        painter.save()
        painter.translate(center)
        painter.rotate(-self._angle(self._needle))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawPolygon(QPolygonF([QPointF(-tail, -half_width), QPointF(tip, -half_width * 0.25),
                                       QPointF(tip, half_width * 0.25), QPointF(-tail, half_width)]))
        painter.restore()
        hub = radius * HUB_R
        painter.setPen(_pen(theme.BORDER_STRONG, 1.0))
        painter.setBrush(QColor(theme.PANEL_ALT))
        painter.drawEllipse(center, hub, hub)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(center, hub * 0.4, hub * 0.4)

    def _paint_readout(self, painter: QPainter, geometry: _Geometry) -> None:
        key = (self._readout, id(geometry))
        if key != self._fit_key:
            self._fit = self._fit_readout(geometry)
            self._fit_key = key
        font, text = self._fit
        if self._error:
            color = theme.FAULT
        else:
            color = theme.TEXT_DIM if self._readout == "--" else theme.TEXT
        painter.setFont(font)
        painter.setPen(QColor(color))
        painter.drawText(geometry.readout_rect, int(Qt.AlignmentFlag.AlignCenter), text)

    def _fit_readout(self, geometry: _Geometry) -> tuple[QFont, str]:
        available = geometry.readout_rect.width()
        for font, metrics in zip(geometry.readout_fonts, geometry.readout_metrics):
            if metrics.horizontalAdvance(self._readout) <= available:
                return font, self._readout
        metrics = geometry.readout_metrics[-1]
        return geometry.readout_fonts[-1], metrics.elidedText(self._readout, Qt.TextElideMode.ElideRight, available)

    def _paint_error_marker(self, painter: QPainter, geometry: _Geometry) -> None:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(_pen(theme.FAULT, 1.0))
        painter.drawRoundedRect(geometry.err_rect, 3, 3)
        painter.setFont(geometry.err_font)
        painter.drawText(geometry.err_rect, int(Qt.AlignmentFlag.AlignCenter), "ERR")
