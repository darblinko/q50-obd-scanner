"""Favorites and dashboard layout persistence: defaults, v2 round trip, v1
migration with backup, and refusal to overwrite newer or corrupt files."""

from __future__ import annotations

import json
import logging
from dataclasses import replace

import pytest

from q50diag.gui.dashboard_layouts import (
    Layout,
    Preferences,
    SlotAssignment,
    active_layout,
    with_layout,
    with_slot,
)
from q50diag.gui.favorites import (
    DEFAULT_KEYS,
    VERSION,
    adopt_vehicle_layout,
    default_preferences,
    load_favorites,
    save_favorites,
    with_key,
    without_key,
)


def write_json(path, payload) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_missing_file_gives_defaults(tmp_path):
    prefs = load_favorites(tmp_path / "none.json")
    assert prefs == default_preferences()
    assert prefs.favorites == DEFAULT_KEYS
    layout = active_layout(prefs)
    assert (layout.id, layout.name, layout.preset) == ("default", "Default", "2x3")
    assert [slot.key for slot in layout.slots] == list(DEFAULT_KEYS[:6])


def test_round_trip_v2(tmp_path):
    path = tmp_path / "favorites.json"
    prefs = with_key(default_preferences(), "obd.01.10")
    layout = with_slot(active_layout(prefs), 2, SlotAssignment("obd.01.10", (0.0, 300.0)))
    track = Layout("track", "Track", "1+4", (SlotAssignment("obd.01.0C", (0.0, 8000.0)), None, SlotAssignment("x")))
    prefs = with_layout(with_layout(prefs, layout), track)
    assert save_favorites(prefs, path) == path
    assert load_favorites(path) == prefs
    stored = json.loads(path.read_text())
    assert stored["version"] == VERSION == 2
    assert stored["layouts"]["track"] == {
        "name": "Track",
        "preset": "1+4",
        "slots": [{"key": "obd.01.0C", "range": [0.0, 8000.0]}, None, {"key": "x"}],
    }


def test_v2_documented_example_loads(tmp_path):
    path = tmp_path / "favorites.json"
    write_json(
        path,
        {
            "version": 2,
            "favorites": ["obd.01.0C", "obd.01.0D"],
            "active_layout": "default",
            "layouts": {
                "default": {
                    "name": "Default",
                    "preset": "2x3",
                    "slots": [{"key": "obd.01.0C", "range": [0, 8000]}, {"key": "obd.01.0D"}, None],
                }
            },
        },
    )
    prefs = load_favorites(path)
    assert prefs.favorites == ("obd.01.0C", "obd.01.0D")
    assert active_layout(prefs).slots == (SlotAssignment("obd.01.0C", (0.0, 8000.0)), SlotAssignment("obd.01.0D"), None)


def test_v2_invalid_entries_are_sanitized(tmp_path):
    path = tmp_path / "favorites.json"
    write_json(
        path,
        {
            "version": 2,
            "favorites": ["a", "a", 3, "b"],
            "active_layout": "gone",
            "layouts": {
                "one": {
                    "name": "One",
                    "preset": "9x9",
                    "slots": [{"key": "a", "range": [10, 0]}, {"key": 5}, "junk", {"key": "b", "range": [0, 1e400]}],
                },
                "bad": "not a layout",
            },
        },
    )
    prefs = load_favorites(path)
    assert prefs.favorites == ("a", "b")
    assert [layout.id for layout in prefs.layouts] == ["one"]
    layout = active_layout(prefs)
    assert prefs.active == "one" and layout.preset == "2x3"
    assert layout.slots == (SlotAssignment("a"), None, None, SlotAssignment("b"))


def test_v2_without_layouts_gets_default_layout(tmp_path):
    path = tmp_path / "favorites.json"
    write_json(path, {"version": 2, "favorites": ["a"], "layouts": {}})
    prefs = load_favorites(path)
    assert prefs.favorites == ("a",)
    assert active_layout(prefs).id == "default"


def test_v1_migrates_in_memory(tmp_path):
    path = tmp_path / "favorites.json"
    write_json(path, {"keys": ["a", "b", "a"], "panels": ["b", "x", ""]})
    prefs = load_favorites(path)
    assert prefs.favorites == ("a", "b")
    layout = active_layout(prefs)
    assert (layout.name, layout.preset) == ("Default", "2x3")
    assert layout.slots == (SlotAssignment("b"), None, None)
    assert json.loads(path.read_text()) == {"keys": ["a", "b", "a"], "panels": ["b", "x", ""]}


def test_first_save_over_v1_writes_backup_once(tmp_path):
    path = tmp_path / "favorites.json"
    original = {"keys": ["a"], "panels": ["a"]}
    write_json(path, original)
    prefs = load_favorites(path)
    assert save_favorites(with_key(prefs, "b"), path) == path
    backup = tmp_path / "favorites.v1.json"
    assert json.loads(backup.read_text()) == original
    assert json.loads(path.read_text())["version"] == 2
    save_favorites(with_key(prefs, "c"), path)
    assert json.loads(backup.read_text()) == original


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[1, 2]",
        json.dumps({"version": 3, "favorites": ["future"]}),
        json.dumps({"version": "two"}),
    ],
)
def test_newer_or_corrupt_file_is_never_overwritten(tmp_path, content, caplog):
    path = tmp_path / "favorites.json"
    path.write_text(content, encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        prefs = load_favorites(path)
    assert prefs == default_preferences()
    assert caplog.records
    with caplog.at_level(logging.WARNING):
        assert save_favorites(with_key(prefs, "new"), path) is None
    assert path.read_text(encoding="utf-8") == content
    assert not (tmp_path / "favorites.v1.json").exists()


def test_save_is_atomic_and_leaves_no_temp_files(tmp_path):
    path = tmp_path / "sub" / "favorites.json"
    save_favorites(default_preferences(), path)
    assert sorted(p.name for p in path.parent.iterdir()) == ["favorites.json"]


def test_with_key_and_without_key_change_favorites_only():
    base = default_preferences()
    added = with_key(base, "obd.01.10")
    assert added is not base and added.favorites[-1] == "obd.01.10"
    assert with_key(added, "obd.01.10") is added
    removed = without_key(base, DEFAULT_KEYS[0])
    assert DEFAULT_KEYS[0] not in removed.favorites
    assert active_layout(removed) == active_layout(base)


def test_preferences_type_is_shared():
    assert isinstance(default_preferences(), Preferences)


def test_vehicle_layouts_round_trip_and_bad_entries_are_ignored(tmp_path):
    path = tmp_path / "favorites.json"
    prefs = replace(default_preferences(), vehicle_layouts=(("q50_2017", "default"),))
    save_favorites(prefs, path)
    assert json.loads(path.read_text())["vehicle_layouts"] == {"q50_2017": "default"}
    assert load_favorites(path) == prefs
    stored = json.loads(path.read_text())
    stored["vehicle_layouts"] = {"q50_2017": "default", "mustang": "deleted", "gt350": 5, "": "default"}
    write_json(path, stored)
    assert load_favorites(path).vehicle_layouts == (("q50_2017", "default"),)
    stored["vehicle_layouts"] = ["not", "a", "mapping"]
    write_json(path, stored)
    assert load_favorites(path).vehicle_layouts == ()
    del stored["vehicle_layouts"]
    write_json(path, stored)
    assert load_favorites(path).vehicle_layouts == ()


def test_adopt_vehicle_layout_rules():
    base = default_preferences()
    everything = adopt_vehicle_layout(base, "q50", "Q50", ("x",), lambda key: True)
    assert everything.active == "default" and everything.vehicle_layouts == (("q50", "default"),)
    created = adopt_vehicle_layout(everything, "gt", "Mustang GT", ("a", "b"), lambda key: key in ("a", "b"))
    assert created.active == "gt" and active_layout(created).name == "Mustang GT"
    assert active_layout(created).slots == (SlotAssignment("a"), SlotAssignment("b"))
    assert created.favorites[-2:] == ("a", "b")
    assert dict(created.vehicle_layouts) == {"q50": "default", "gt": "gt"}
    back = adopt_vehicle_layout(created, "q50", "Q50", ("x",), lambda key: False)
    assert back.active == "default"
    reused = adopt_vehicle_layout(replace(back, vehicle_layouts=()), "gt", "Renamed", ("c",), lambda key: False)
    assert reused.active == "gt" and active_layout(reused).name == "Mustang GT"
