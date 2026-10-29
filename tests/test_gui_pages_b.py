"""Offscreen tests for the Live Data, Bus Monitor and Recordings pages."""

from __future__ import annotations

import time

import pytest

from q50diag.core.types import CanFrame, Reading
from q50diag.gui.pages.bus_monitor import BusMonitorPage, parse_can_ids
from q50diag.gui.pages.live_data import KEY_ROLE, LiveDataPage
from q50diag.gui.pages.recordings import RecordingsPage, count_rows
from q50diag.gui.widgets.hex_delegate import CHANGED_ROLE
from q50diag.recorder import Recorder
from tests.test_gui_fakes import app, ctx, wait_until  # noqa: F401 (fixtures)


@pytest.mark.parametrize("page_class", [LiveDataPage, BusMonitorPage, RecordingsPage])
def test_page_constructs_and_renders(ctx, page_class):
    page = page_class(ctx)
    page.resize(1280, 800)
    image = page.grab()
    assert not image.isNull()
    assert page.title


def test_live_data_polls_checked_parameters(ctx):
    page = LiveDataPage(ctx)
    keys = ["obd.01.0C", "ABS.22.1208.steering_angle", "bcast.300.assist_raw"]
    page.set_checked(keys)
    assert page.checked_keys() == keys
    assert page.table.rowCount() == 3
    page.start_polling()
    assert "live" in ctx.session.polling_owners()
    assert wait_until(lambda: all(ctx.store.has(k) for k in keys), 5)
    page._refresh_table()
    assert page.table.item(0, 1).text() != "--"
    assert page.charts.keys() == keys
    page.stop_polling()
    assert "live" not in ctx.session.polling_owners()


def test_live_data_search_hides_non_matching(ctx):
    page = LiveDataPage(ctx)
    page.search.setText("rpm")
    visible = [item.text(0) for item in page._leaves() if not item.isHidden()]
    assert visible == ["Engine RPM"]


def test_live_data_keeps_checks_after_catalog_rebuild(ctx):
    page = LiveDataPage(ctx)
    page.set_checked(["obd.01.0D"])
    ctx.rebuild_catalog()
    assert page.checked_keys() == ["obd.01.0D"]


def test_live_data_marker_reaches_charts(ctx):
    page = LiveDataPage(ctx)
    page.set_checked(["obd.01.0C"])
    ctx.mark_event("heavy")
    assert page.charts._markers[-1][1] == "heavy"


def test_parse_can_ids():
    assert parse_can_ids("") is None
    assert parse_can_ids("002, 300 0x5E4") == {0x002, 0x300, 0x5E4}
    for bad in ("XYZ", "800", "12345"):
        with pytest.raises(ValueError):
            parse_can_ids(bad)


def test_bus_monitor_aggregates_frames(ctx):
    page = BusMonitorPage(ctx)
    now = time.time()
    frames = [
        CanFrame(now, 0x300, bytes([1, 2, 3])),
        CanFrame(now + 0.01, 0x300, bytes([1, 9, 3])),
        CanFrame(now + 0.02, 0x002, bytes([0x10, 0x00])),
    ]
    page.add_frames(frames)
    page.refresh()
    assert page.table.rowCount() == 2
    assert page.table.item(0, 0).text() == "002"
    assert page.table.item(1, 1).text() == "2"
    data_item = page.table.item(1, 3)
    assert data_item.text() == "01 09 03"
    assert data_item.data(CHANGED_ROLE) == [1]
    assert "EPS assist" in page.table.item(1, 4).text()
    assert page.raw_view.blockCount() >= 3
    page.clear()
    assert page.table.rowCount() == 0


def test_bus_monitor_bad_filter_is_reported(ctx):
    page = BusMonitorPage(ctx)
    messages = []
    ctx.toast.connect(lambda text, level: messages.append((text, level)))
    page.filter_edit.setText("300 zz")
    page.start_button.setChecked(True)
    assert not page.start_button.isChecked()
    assert not ctx.session.bus_monitor_active
    assert messages and "zz" in messages[-1][0]


def test_bus_monitor_live_capture(ctx):
    page = BusMonitorPage(ctx)
    page.filter_edit.setText("300")
    page.start_button.setChecked(True)
    assert ctx.session.bus_monitor_active
    assert wait_until(lambda: 0x300 in page.stats, 5)
    page.start_button.setChecked(False)
    assert not ctx.session.bus_monitor_active
    assert set(page.stats) == {0x300}


def _write_recording(folder):
    recorder = Recorder(folder)
    path = recorder.start()
    base = time.time()
    for index in range(20):
        recorder.write_reading(Reading("adapter.voltage", 12.0 + index * 0.01, "V", base + index * 0.1), "Battery voltage", "ADAPTER")
        recorder.write_reading(Reading("obd.01.0C", 800 + index, "rpm", base + index * 0.1), "Engine RPM", "ECM")
    recorder.write_reading(Reading("obd.vin", "JN1TEXT", "", base), "VIN", "ECM")
    recorder.mark_event("Steering felt heavy", base + 1.0)
    recorder.stop()
    return path


def test_recordings_lists_and_replays(ctx, tmp_path):
    path = _write_recording(tmp_path)
    assert count_rows(path) == 42
    page = RecordingsPage(ctx)
    page.set_folder(tmp_path)
    assert page.files.rowCount() == 1
    assert page.files.item(0, 3).text() == "42"
    page._open_row(0)
    assert page.recording is not None
    assert page.params.count() == 2  # text-only series are not chartable
    assert set(page.checked_keys()) == {"adapter.voltage", "obd.01.0C"}
    assert set(page.charts.keys()) == {"adapter.voltage", "obd.01.0C"}
    assert page.events.rowCount() == 1
    assert page.events.item(0, 2).text() == "#1  Steering felt heavy"
    page.focus_event(0)
    page.grab()


def test_recordings_reports_unreadable_file(ctx, tmp_path):
    messages = []
    ctx.toast.connect(lambda text, level: messages.append(level))
    page = RecordingsPage(ctx)
    assert page.open_recording(tmp_path / "missing.csv") is None
    assert messages == ["error"]


def test_live_data_add_and_remove_favorite(ctx, tmp_path):
    from q50diag.gui.favorites import load_favorites

    path = tmp_path / "favorites.json"
    page = LiveDataPage(ctx, favorites_path=path)
    key = "ABS.22.1208.steering_angle"
    leaf = next(item for item in page._leaves() if item.data(0, KEY_ROLE) == key)
    assert not leaf.font(0).bold()
    toasts = []
    ctx.toast.connect(lambda text, level: toasts.append(text))
    page.add_favorite(key)
    assert key in load_favorites(path).favorites
    assert leaf.font(0).bold()
    page.remove_favorite(key)
    assert key not in load_favorites(path).favorites
    assert not leaf.font(0).bold()
    assert len(toasts) == 2


def test_live_data_favorite_checked_adds_all(ctx, tmp_path):
    from q50diag.gui.favorites import load_favorites

    path = tmp_path / "favorites.json"
    page = LiveDataPage(ctx, favorites_path=path)
    keys = ["ABS.22.1208.steering_angle", "bcast.300.assist_raw"]
    page.set_checked(keys)
    page.favorite_button.click()
    saved = load_favorites(path).favorites
    assert all(key in saved for key in keys)
    assert page.favorite_keys() == saved


def test_live_data_reloads_favorites_on_show(ctx, tmp_path):
    from dataclasses import replace

    from q50diag.gui.favorites import default_preferences, save_favorites

    path = tmp_path / "favorites.json"
    page = LiveDataPage(ctx, favorites_path=path)
    save_favorites(replace(default_preferences(), favorites=("obd.01.0C",)), path)
    page.show()
    assert page.favorite_keys() == ("obd.01.0C",)
    page.hide()
