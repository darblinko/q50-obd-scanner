"""Favorites group and dashboard layouts, stored as JSON (format version 2).

    {"version": 2, "favorites": ["obd.01.0C", ...], "active_layout": "default",
     "layouts": {"default": {"name": "Default", "preset": "2x3",
                             "slots": [{"key": "obd.01.0C", "range": [0, 8000]}, null, ...]}},
     "vehicle_layouts": {"q50_2017": "default"}}

`vehicle_layouts` is optional: the layout last activated for each vehicle.

A version 1 file (`keys` and six `panels`) is migrated in memory; the first save
over it copies it to favorites.v1.json. A file that fails to parse or carries a
newer version is never overwritten: loading logs a warning and returns defaults,
and saving logs a warning and returns None."""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path

from q50diag.gui.dashboard_layouts import (
    DEFAULT_PRESET,
    PRESETS,
    Layout,
    Preferences,
    SlotAssignment,
    active_layout,
    find_layout,
    valid_range,
    vehicle_layout_id,
    visible_slots,
    with_favorite,
    with_layout,
    with_vehicle_choice,
    without_favorite,
)

VERSION = 2
V1_PANEL_COUNT = 6
DEFAULT_PATH = Path.home() / ".config" / "q50diag" / "favorites.json"
DEFAULT_LAYOUT_ID = "default"
DEFAULT_KEYS = (
    "obd.01.0D",  # vehicle speed
    "obd.01.0C",  # engine RPM
    "EPS.21.01.supply",
    "EPS.21.01.angle",
    "EPS.21.01.torque",
    "EPS.21.01.assist",
    "obd.01.05",  # coolant temperature
    "obd.01.06",  # short term fuel trim bank 1
    "obd.01.07",  # long term fuel trim bank 1
)
# The OBD-II RPM scale tops out at 16383.75, too wide for a useful dial.
DEFAULT_RANGES = {"obd.01.0C": (0.0, 8000.0)}

log = logging.getLogger(__name__)

# The favorites-group operations the Live Data page uses.
with_key = with_favorite
without_key = without_favorite


def default_layout(slots: tuple[SlotAssignment | None, ...] | None = None) -> Layout:
    if slots is None:
        slots = tuple(SlotAssignment(key, DEFAULT_RANGES.get(key)) for key in DEFAULT_KEYS[:V1_PANEL_COUNT])
    return Layout(DEFAULT_LAYOUT_ID, "Default", DEFAULT_PRESET, slots)


def default_preferences() -> Preferences:
    return Preferences(favorites=DEFAULT_KEYS, layouts=(default_layout(),), active=DEFAULT_LAYOUT_ID)


def adopt_vehicle_layout(
    prefs: Preferences, vehicle_key: str, name: str, keys: tuple[str, ...], available: Callable[[str], bool]
) -> Preferences:
    """Choose the layout for a newly selected vehicle: the one remembered for
    it; else the active layout when every visible sensor exists on the vehicle;
    else a layout named after the vehicle, built from `keys` on first use, whose
    keys join the favorites group. The choice is remembered for the vehicle."""
    remembered = vehicle_layout_id(prefs, vehicle_key)
    if remembered is not None:
        return replace(prefs, active=remembered)
    current = active_layout(prefs)
    if all(available(slot.key) for slot in visible_slots(current) if slot):
        return with_vehicle_choice(prefs, vehicle_key, current.id)
    if find_layout(prefs, vehicle_key) is None:
        slots = tuple(SlotAssignment(key, DEFAULT_RANGES.get(key)) for key in keys[:V1_PANEL_COUNT])
        prefs = with_layout(prefs, Layout(vehicle_key, name, DEFAULT_PRESET, slots))
    prefs = replace(prefs, favorites=tuple(dict.fromkeys((*prefs.favorites, *keys))), active=vehicle_key)
    return with_vehicle_choice(prefs, vehicle_key, vehicle_key)


def _target(path: Path | None) -> Path:
    return Path(path) if path else DEFAULT_PATH


def _inspect(target: Path) -> tuple[str, dict | None]:
    """Classify the file as "missing", "corrupt", "newer", "v1" or "v2"."""
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "missing", None
    except (OSError, ValueError):
        return "corrupt", None
    if not isinstance(raw, dict):
        return "corrupt", None
    version = raw.get("version", 1)
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        return "corrupt", None
    if version > VERSION:
        return "newer", None
    return ("v1" if version == 1 else "v2"), raw


def load_favorites(path: Path | None = None) -> Preferences:
    """Read preferences from `path`; a missing, corrupt or newer file gives defaults."""
    target = _target(path)
    status, raw = _inspect(target)
    if status in ("corrupt", "newer"):
        log.warning(
            "Favorites at %s are %s; using defaults and leaving the file untouched",
            target,
            "from a newer version" if status == "newer" else "unreadable",
        )
        return default_preferences()
    if raw is None:
        return default_preferences()
    return _from_v1(raw) if status == "v1" else _from_v2(raw)


def _string_tuple(value: object) -> tuple[str, ...] | None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return None
    return tuple(value)


def _from_v1(raw: Mapping[str, object]) -> Preferences:
    keys = _string_tuple(raw.get("keys"))
    keys = DEFAULT_KEYS if keys is None else tuple(dict.fromkeys(keys))
    panels = (_string_tuple(raw.get("panels")) or ())[:V1_PANEL_COUNT]
    slots = tuple(SlotAssignment(key) if key and key in keys else None for key in panels)
    return Preferences(favorites=keys, layouts=(default_layout(slots),), active=DEFAULT_LAYOUT_ID)


def _from_v2(raw: Mapping[str, object]) -> Preferences:
    favorites = raw.get("favorites")
    if isinstance(favorites, list):
        favorites = tuple(dict.fromkeys(key for key in favorites if isinstance(key, str) and key))
    else:
        favorites = DEFAULT_KEYS
    layouts = _layouts(raw.get("layouts")) or (default_layout(),)
    active = raw.get("active_layout")
    if not any(layout.id == active for layout in layouts):
        active = layouts[0].id
    vehicle_layouts = _vehicle_layouts(raw.get("vehicle_layouts"), {layout.id for layout in layouts})
    return Preferences(favorites=favorites, layouts=layouts, active=active, vehicle_layouts=vehicle_layouts)


def _vehicle_layouts(raw: object, layout_ids: set[str]) -> tuple[tuple[str, str], ...]:
    """Valid (vehicle key, layout id) pairs; entries naming a missing layout are dropped."""
    if not isinstance(raw, dict):
        return ()
    return tuple(
        (vehicle, layout_id)
        for vehicle, layout_id in raw.items()
        if isinstance(vehicle, str) and vehicle and isinstance(layout_id, str) and layout_id in layout_ids
    )


def _layouts(raw: object) -> tuple[Layout, ...]:
    if not isinstance(raw, dict):
        return ()
    layouts = []
    for layout_id, body in raw.items():
        if not isinstance(layout_id, str) or not layout_id or not isinstance(body, dict):
            continue
        name = body.get("name") if isinstance(body.get("name"), str) and body.get("name") else layout_id
        preset = body.get("preset") if body.get("preset") in PRESETS else DEFAULT_PRESET
        slots = body.get("slots") if isinstance(body.get("slots"), list) else []
        layouts.append(Layout(layout_id, name, preset, tuple(_slot(slot) for slot in slots)))
    return tuple(layouts)


def _slot(raw: object) -> SlotAssignment | None:
    if not isinstance(raw, dict):
        return None
    key = raw.get("key")
    if not isinstance(key, str) or not key:
        return None
    return SlotAssignment(key, valid_range(raw.get("range")))


def _to_dict(prefs: Preferences) -> dict:
    return {
        "version": VERSION,
        "favorites": list(prefs.favorites),
        "active_layout": prefs.active,
        "vehicle_layouts": dict(prefs.vehicle_layouts),
        "layouts": {
            layout.id: {
                "name": layout.name,
                "preset": layout.preset,
                "slots": [_slot_dict(slot) for slot in layout.slots],
            }
            for layout in prefs.layouts
        },
    }


def _slot_dict(slot: SlotAssignment | None) -> dict | None:
    if slot is None:
        return None
    if slot.range is None:
        return {"key": slot.key}
    return {"key": slot.key, "range": list(slot.range)}


def save_favorites(prefs: Preferences, path: Path | None = None) -> Path | None:
    """Write `prefs` atomically. Returns the path, or None when the existing
    file is corrupt or from a newer version and was left untouched."""
    target = _target(path)
    status, _ = _inspect(target)
    if status in ("corrupt", "newer"):
        log.warning("Not saving favorites over %s (%s file); changes kept in memory only", target, status)
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    if status == "v1":
        backup = target.with_name(f"{target.stem}.v1.json")
        if not backup.exists():
            shutil.copy2(target, backup)
    _write_atomic(target, json.dumps(_to_dict(prefs), indent=2))
    return target


def _write_atomic(target: Path, text: str) -> None:
    descriptor, temp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temp_name, target)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise
