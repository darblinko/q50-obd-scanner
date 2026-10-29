"""Offscreen tests for the sensor picker and instrument panel settings dialogs."""

from __future__ import annotations

from q50diag.gui.dashboard_layouts import (
    Layout,
    Preferences,
    SlotAssignment,
    active_layout,
    find_layout,
    visible_slots,
)
from q50diag.gui.dialogs.dashboard_settings import DashboardSettingsDialog
from q50diag.gui.dialogs.sensor_picker import STAR_COLUMN, STAR_OFF, STAR_ON, SensorPickerDialog
from tests.test_gui_fakes import app, ctx, wait_until  # noqa: F401; fixtures


def slot_keys(layout) -> list:
    return [slot.key if slot else None for slot in visible_slots(layout)]


def make_prefs(preset: str = "2x3", keys=("obd.01.0C", "obd.01.0D")) -> Preferences:
    slots = tuple(SlotAssignment(key) if key else None for key in keys)
    return Preferences(favorites=("obd.01.0C",), layouts=(Layout("default", "Default", preset, slots),), active="default")


# Sensor picker ------------------------------------------------------------
def test_picker_lists_whole_catalog_and_selects_current(ctx):  # noqa: F811
    picker = SensorPickerDialog(ctx.catalog, ("obd.01.0C",), "obd.01.0D")
    assert picker.visible_keys() == [entry.key for entry in ctx.catalog]
    assert picker._row_key(picker.table.currentRow()) == "obd.01.0D"
    assert picker.select_button.isEnabled()


def test_picker_search_matches_name_key_and_module(ctx):  # noqa: F811
    picker = SensorPickerDialog(ctx.catalog, ())
    picker.search_edit.setText("rpm")
    assert picker.visible_keys() == ["obd.01.0C"]
    picker.search_edit.setText("EPS.21.01.sup")
    assert picker.visible_keys() == ["EPS.21.01.supply"]
    picker.search_edit.setText("abs")
    assert picker.visible_keys() and all(key.startswith("ABS.") for key in picker.visible_keys())
    picker.search_edit.setText("eps torque")
    assert picker.visible_keys() == ["EPS.21.01.torque"]


def test_picker_favorites_filter_and_star_toggle(ctx):  # noqa: F811
    picker = SensorPickerDialog(ctx.catalog, ("obd.01.0C", "not.in.catalog"))
    picker.favorites_only.setChecked(True)
    assert picker.visible_keys() == ["obd.01.0C"]
    row = next(row for row in range(picker.table.rowCount()) if picker._row_key(row) == "obd.01.0D")
    assert picker.table.item(row, STAR_COLUMN).text() == STAR_OFF
    picker._on_cell_clicked(row, STAR_COLUMN)
    assert picker.table.item(row, STAR_COLUMN).text() == STAR_ON
    assert picker.visible_keys() == ["obd.01.0C", "obd.01.0D"]
    assert picker.favorites() == ("obd.01.0C", "not.in.catalog", "obd.01.0D")
    picker.toggle_favorite("obd.01.0C")
    assert picker.visible_keys() == ["obd.01.0D"]


def test_picker_select_and_clear(ctx):  # noqa: F811
    picker = SensorPickerDialog(ctx.catalog, (), "obd.01.0D")
    picker.select_key("obd.01.05")
    assert picker.result() == picker.DialogCode.Accepted
    assert picker.selected_key() == "obd.01.05"
    cleared = SensorPickerDialog(ctx.catalog, (), "obd.01.0D")
    cleared.clear_slot()
    assert cleared.selected_key() == "" and cleared.result() == cleared.DialogCode.Accepted


def test_picker_favorite_button_follows_selection(ctx):  # noqa: F811
    picker = SensorPickerDialog(ctx.catalog, ("obd.01.0C",), "obd.01.0C")
    assert picker.favorite_button.text() == "Remove from favorites"
    picker.favorite_button.click()
    assert picker.favorites() == ()
    assert picker.favorite_button.text() == "Add to favorites"


# Settings dialog ----------------------------------------------------------
def test_settings_preset_change_and_shrink_keeps_hidden(ctx):  # noqa: F811
    keys = ("obd.01.0C", "obd.01.0D", "obd.01.05", None, "EPS.21.01.supply")
    dialog = DashboardSettingsDialog(make_prefs("2x3", keys), ctx.catalog)
    assert dialog.preset_buttons["2x3"].isChecked()
    assert dialog.table.rowCount() == 6
    dialog.set_preset("1x1")
    assert dialog.table.rowCount() == 1
    dialog.preset_buttons["3x3"].click()
    assert dialog.table.rowCount() == 9
    result = active_layout(dialog.result_preferences())
    assert result.preset == "3x3"
    assert slot_keys(result)[:5] == list(keys)


def test_settings_assign_and_range_override(ctx):  # noqa: F811
    dialog = DashboardSettingsDialog(make_prefs(), ctx.catalog)
    assert dialog.table.item(0, 2).text() == "0 to 7000"
    dialog.table.item(0, 3).setText("0")
    dialog.table.item(0, 4).setText("8000")
    dialog.assign_slot(2, "obd.01.05")
    dialog.assign_slot(1, "")
    layout = active_layout(dialog.result_preferences())
    assert layout.slots[0] == SlotAssignment("obd.01.0C", (0.0, 8000.0))
    assert layout.slots[1] is None and layout.slots[2] == SlotAssignment("obd.01.05")
    assert dialog.apply_button.isEnabled()


def test_settings_invalid_range_blocks_apply(ctx):  # noqa: F811
    dialog = DashboardSettingsDialog(make_prefs(), ctx.catalog)
    dialog.set_range_text(0, "100", "50")
    assert not dialog.apply_button.isEnabled()
    assert "Slot 1" in dialog.error_label.text()
    dialog.set_range_text(0, "abc", "50")
    assert not dialog.apply_button.isEnabled()
    dialog.set_range_text(0, "", "")
    assert dialog.apply_button.isEnabled() and dialog.error_label.text() == ""
    assert active_layout(dialog.result_preferences()).slots[0].range is None


def test_settings_named_layouts(ctx):  # noqa: F811
    dialog = DashboardSettingsDialog(make_prefs(), ctx.catalog)
    assert not dialog.delete_button.isEnabled()
    dialog.save_as("Track day")
    dialog.set_preset("1+4")
    prefs = dialog.result_preferences()
    assert prefs.active == "track-day"
    assert find_layout(prefs, "track-day").preset == "1+4"
    assert find_layout(prefs, "default").preset == "2x3"
    assert slot_keys(find_layout(prefs, "track-day"))[:2] == ["obd.01.0C", "obd.01.0D"]
    dialog.rename("Autocross")
    assert [dialog.layout_combo.itemText(i) for i in range(dialog.layout_combo.count())] == ["Default", "Autocross"]
    dialog.select_layout("default")
    assert dialog.preset_buttons["2x3"].isChecked()
    assert dialog.result_preferences().active == "default"
    dialog.delete_layout()
    prefs = dialog.result_preferences()
    assert [layout.id for layout in prefs.layouts] == ["track-day"]
    assert prefs.active == "track-day"


def test_settings_marks_unavailable_and_renders(ctx):  # noqa: F811
    dialog = DashboardSettingsDialog(make_prefs(keys=("not.in.catalog",)), ctx.catalog)
    assert "unavailable" in dialog.table.item(0, 1).text()
    assert dialog.table.item(1, 1).text() == "(empty)"
    assert not dialog.grab().isNull()
