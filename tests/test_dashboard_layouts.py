"""Dashboard layout presets, named layouts and preferences: pure model tests."""

from __future__ import annotations

import math

import pytest

from q50diag.gui.dashboard_layouts import (
    DEFAULT_PRESET,
    PRESETS,
    Layout,
    Preferences,
    SlotAssignment,
    active_layout,
    unique_layout_id,
    valid_range,
    vehicle_layout_id,
    visible_slots,
    with_active,
    with_favorite,
    with_layout,
    with_preset,
    with_slot,
    with_vehicle_choice,
    without_favorite,
    without_layout,
)


def make_layout(preset: str = "2x3", keys: tuple = ("a", "b")) -> Layout:
    slots = tuple(SlotAssignment(key) if key else None for key in keys)
    return Layout("default", "Default", preset, slots)


def test_presets_cover_the_required_set():
    assert set(PRESETS) == {"1x1", "2x2", "2x3", "3x3", "1+4"}
    assert DEFAULT_PRESET == "2x3"
    assert [PRESETS[name].slot_count for name in ("1x1", "2x2", "2x3", "3x3", "1+4")] == [1, 4, 6, 9, 5]


@pytest.mark.parametrize("name", list(PRESETS))
def test_preset_cells_tile_the_grid_without_overlap(name):
    preset = PRESETS[name]
    covered = set()
    for row, column, row_span, column_span in preset.cells:
        for r in range(row, row + row_span):
            for c in range(column, column + column_span):
                assert 0 <= r < preset.rows and 0 <= c < preset.columns
                assert (r, c) not in covered
                covered.add((r, c))
    assert len(covered) == preset.rows * preset.columns


def test_large_plus_four_has_one_two_by_two_cell():
    preset = PRESETS["1+4"]
    assert preset.cells[0][2:] == (2, 2)
    assert all(cell[2:] == (1, 1) for cell in preset.cells[1:])


def test_visible_slots_pads_and_truncates():
    layout = make_layout("2x2", ("a", None, "b", "c", "d", "e"))
    assert [slot.key if slot else None for slot in visible_slots(layout)] == ["a", None, "b", "c"]
    small = make_layout("2x3", ("a",))
    assert len(visible_slots(small)) == 6 and visible_slots(small)[1:] == (None,) * 5


def test_shrinking_preset_keeps_hidden_assignments():
    layout = make_layout("3x3", tuple("abcdefghi"))
    shrunk = with_preset(layout, "1x1")
    assert [slot.key for slot in visible_slots(shrunk)] == ["a"]
    restored = with_preset(shrunk, "3x3")
    assert [slot.key for slot in visible_slots(restored)] == list("abcdefghi")


def test_with_preset_rejects_unknown():
    with pytest.raises(KeyError):
        with_preset(make_layout(), "4x4")


def test_with_slot_extends_and_returns_new_layout():
    layout = make_layout("2x3", ("a",))
    changed = with_slot(layout, 3, SlotAssignment("x", (0.0, 10.0)))
    assert changed is not layout
    assert changed.slots == (SlotAssignment("a"), None, None, SlotAssignment("x", (0.0, 10.0)))
    assert layout.slots == (SlotAssignment("a"),)
    cleared = with_slot(changed, 0, None)
    assert cleared.slots[0] is None


@pytest.mark.parametrize("value, expected", [
    ((0, 10), (0.0, 10.0)),
    ([1.5, 2], (1.5, 2.0)),
    ((10, 0), None),
    ((5, 5), None),
    ((0, math.inf), None),
    ((math.nan, 1), None),
    (("a", 1), None),
    ((1,), None),
    (None, None),
    ((True, 3), None),
])
def test_valid_range(value, expected):
    assert valid_range(value) == expected


def test_preferences_layout_operations():
    prefs = Preferences(favorites=("a",), layouts=(make_layout(),), active="default")
    second = Layout(unique_layout_id(prefs, "Track day"), "Track day", "1+4", ())
    assert second.id == "track-day"
    prefs = with_active(with_layout(prefs, second), second.id)
    assert active_layout(prefs).name == "Track day"
    renamed = with_layout(prefs, Layout(second.id, "Autocross", second.preset, second.slots))
    assert [layout.name for layout in renamed.layouts] == ["Default", "Autocross"]
    removed = without_layout(renamed, second.id)
    assert [layout.id for layout in removed.layouts] == ["default"]
    assert removed.active == "default"


def test_cannot_remove_last_layout():
    prefs = Preferences(favorites=(), layouts=(make_layout(),), active="default")
    assert without_layout(prefs, "default") is prefs


def test_unique_layout_id_avoids_collisions():
    prefs = Preferences(favorites=(), layouts=(make_layout(),), active="default")
    assert unique_layout_id(prefs, "Default") == "default-2"
    assert unique_layout_id(prefs, "!!!") == "layout"


def test_active_layout_falls_back_to_first():
    prefs = Preferences(favorites=(), layouts=(make_layout(),), active="missing")
    assert active_layout(prefs).id == "default"


def test_removing_favorite_keeps_gauge_slots():
    prefs = Preferences(favorites=("a", "b"), layouts=(make_layout(keys=("a", "b")),), active="default")
    removed = without_favorite(prefs, "a")
    assert removed.favorites == ("b",)
    assert active_layout(removed).slots[0] == SlotAssignment("a")
    assert with_favorite(removed, "b") is removed
    assert with_favorite(removed, "c").favorites == ("b", "c")


def test_vehicle_choice_is_remembered_and_dropped_with_its_layout():
    track = Layout("track", "Track", "1+4", ())
    prefs = Preferences(favorites=(), layouts=(make_layout(), track), active="default")
    prefs = with_vehicle_choice(with_vehicle_choice(prefs, "q50", "default"), "gt", "track")
    assert vehicle_layout_id(prefs, "gt") == "track"
    assert with_vehicle_choice(prefs, "gt", "track") is prefs
    prefs = with_vehicle_choice(prefs, "q50", "track")
    assert prefs.vehicle_layouts == (("gt", "track"), ("q50", "track"))
    removed = without_layout(prefs, "track")
    assert removed.vehicle_layouts == () and vehicle_layout_id(removed, "gt") is None
    stale = Preferences(favorites=(), layouts=(make_layout(),), active="default", vehicle_layouts=(("gt", "gone"),))
    assert vehicle_layout_id(stale, "gt") is None
