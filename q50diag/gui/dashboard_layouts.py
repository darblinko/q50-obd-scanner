"""Dashboard layout model: grid presets, named layouts and the preferences
that hold them with the favorites group. Frozen dataclasses and pure functions;
persistence lives in q50diag.gui.favorites."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace

# (row, column, row_span, column_span)
Cell = tuple[int, int, int, int]


@dataclass(frozen=True)
class Preset:
    name: str
    label: str
    rows: int
    columns: int
    cells: tuple[Cell, ...]

    @property
    def slot_count(self) -> int:
        return len(self.cells)


def _uniform(name: str, label: str, rows: int, columns: int) -> Preset:
    cells = tuple((row, column, 1, 1) for row in range(rows) for column in range(columns))
    return Preset(name, label, rows, columns, cells)


PRESETS: dict[str, Preset] = {
    "1x1": _uniform("1x1", "1 gauge", 1, 1),
    "2x2": _uniform("2x2", "2 x 2", 2, 2),
    "2x3": _uniform("2x3", "2 x 3", 2, 3),
    "3x3": _uniform("3x3", "3 x 3", 3, 3),
    "1+4": Preset(
        "1+4", "1 large + 4 small", 2, 4, ((0, 0, 2, 2), (0, 2, 1, 1), (0, 3, 1, 1), (1, 2, 1, 1), (1, 3, 1, 1))
    ),
}
DEFAULT_PRESET = "2x3"
MAX_SLOTS = max(preset.slot_count for preset in PRESETS.values())


@dataclass(frozen=True)
class SlotAssignment:
    """A parameter key shown in one gauge slot, with an optional display range
    that overrides the parameter's own min/max."""

    key: str
    range: tuple[float, float] | None = None


@dataclass(frozen=True)
class Layout:
    """A named layout. `slots` may hold more entries than the preset shows;
    the extra assignments are kept so growing the preset brings them back."""

    id: str
    name: str
    preset: str = DEFAULT_PRESET
    slots: tuple[SlotAssignment | None, ...] = ()


@dataclass(frozen=True)
class Preferences:
    """`vehicle_layouts` holds (vehicle key, layout id) pairs: the layout last
    activated while that vehicle was selected."""

    favorites: tuple[str, ...]
    layouts: tuple[Layout, ...]
    active: str
    vehicle_layouts: tuple[tuple[str, str], ...] = ()


def valid_range(value: object) -> tuple[float, float] | None:
    """(low, high) as floats when `value` is a pair of finite numbers with
    high > low, otherwise None."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    if not all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value):
        return None
    low, high = float(value[0]), float(value[1])
    if not (math.isfinite(low) and math.isfinite(high)) or high <= low:
        return None
    return low, high


def visible_slots(layout: Layout) -> tuple[SlotAssignment | None, ...]:
    """The slots the layout's preset shows, padded with None."""
    count = PRESETS.get(layout.preset, PRESETS[DEFAULT_PRESET]).slot_count
    shown = layout.slots[:count]
    return shown + (None,) * (count - len(shown))


def with_preset(layout: Layout, preset: str) -> Layout:
    if preset not in PRESETS:
        raise KeyError(f"unknown layout preset {preset!r}")
    return replace(layout, preset=preset)


def with_slot(layout: Layout, index: int, assignment: SlotAssignment | None) -> Layout:
    if not 0 <= index < MAX_SLOTS:
        raise IndexError(f"slot index {index} outside 0..{MAX_SLOTS - 1}")
    slots = list(layout.slots) + [None] * max(0, index + 1 - len(layout.slots))
    slots[index] = assignment
    return replace(layout, slots=tuple(slots))


def active_layout(prefs: Preferences) -> Layout:
    for layout in prefs.layouts:
        if layout.id == prefs.active:
            return layout
    return prefs.layouts[0]


def find_layout(prefs: Preferences, layout_id: str) -> Layout | None:
    return next((layout for layout in prefs.layouts if layout.id == layout_id), None)


def with_layout(prefs: Preferences, layout: Layout) -> Preferences:
    """Replace the layout with the same id, or append it."""
    if find_layout(prefs, layout.id) is None:
        return replace(prefs, layouts=(*prefs.layouts, layout))
    layouts = tuple(layout if existing.id == layout.id else existing for existing in prefs.layouts)
    return replace(prefs, layouts=layouts)


def without_layout(prefs: Preferences, layout_id: str) -> Preferences:
    """Drop a layout; the last remaining layout is never removed."""
    layouts = tuple(layout for layout in prefs.layouts if layout.id != layout_id)
    if not layouts or len(layouts) == len(prefs.layouts):
        return prefs
    active = prefs.active if prefs.active != layout_id else layouts[0].id
    remembered = tuple(pair for pair in prefs.vehicle_layouts if pair[1] != layout_id)
    return replace(prefs, layouts=layouts, active=active, vehicle_layouts=remembered)


def with_active(prefs: Preferences, layout_id: str) -> Preferences:
    if find_layout(prefs, layout_id) is None:
        raise KeyError(f"unknown layout {layout_id!r}")
    return replace(prefs, active=layout_id)


def unique_layout_id(prefs: Preferences, name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "layout"
    taken = {layout.id for layout in prefs.layouts}
    candidate, number = base, 2
    while candidate in taken:
        candidate, number = f"{base}-{number}", number + 1
    return candidate


def with_favorite(prefs: Preferences, key: str) -> Preferences:
    if key in prefs.favorites:
        return prefs
    return replace(prefs, favorites=(*prefs.favorites, key))


def without_favorite(prefs: Preferences, key: str) -> Preferences:
    """Drop `key` from the favorites group. Gauge slots showing it are kept."""
    return replace(prefs, favorites=tuple(existing for existing in prefs.favorites if existing != key))


def vehicle_layout_id(prefs: Preferences, vehicle_key: str) -> str | None:
    """The layout remembered for `vehicle_key`, or None when there is none or
    it no longer exists."""
    layout_id = dict(prefs.vehicle_layouts).get(vehicle_key)
    return layout_id if layout_id is not None and find_layout(prefs, layout_id) is not None else None


def with_vehicle_choice(prefs: Preferences, vehicle_key: str, layout_id: str) -> Preferences:
    """Remember `layout_id` as the layout for `vehicle_key`."""
    if dict(prefs.vehicle_layouts).get(vehicle_key) == layout_id:
        return prefs
    others = tuple(pair for pair in prefs.vehicle_layouts if pair[0] != vehicle_key)
    return replace(prefs, vehicle_layouts=(*others, (vehicle_key, layout_id)))
