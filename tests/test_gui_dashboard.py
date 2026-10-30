"""Offscreen tests for the Dashboard page: layouts, sensor assignment, polling
subscriptions, live values, needle animation and responsiveness."""

from __future__ import annotations

import json
import time

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog, QStackedWidget, QWidget

from q50diag.core.types import Reading
from q50diag.gui.dashboard_layouts import (
    Layout,
    Preferences,
    SlotAssignment,
    active_layout,
    with_layout,
    with_preset,
)
from q50diag.gui.dialogs.dashboard_settings import DashboardSettingsDialog
from q50diag.gui.dialogs.sensor_picker import SensorPickerDialog
from q50diag.gui.favorites import default_preferences, load_favorites, save_favorites
from q50diag.gui.pages.dashboard import OWNER, DashboardPage
from q50diag.vehicles import vehicle_by_key
from tests.test_gui_fakes import app, ctx, wait_until  # noqa: F401 (fixtures)

FAVORITES = ("obd.01.0C", "obd.01.0D", "EPS.21.01.supply")
DEFAULT_SLOTS = (SlotAssignment("obd.01.0C", (0.0, 8000.0)), SlotAssignment("obd.01.0D"), None, None, None, None)
TRACK_SLOTS = (SlotAssignment("obd.01.0D"), SlotAssignment("EPS.21.01.supply"), SlotAssignment("not.in.catalog"))


def make_prefs() -> Preferences:
    return Preferences(
        favorites=FAVORITES,
        layouts=(Layout("default", "Default", "2x3", DEFAULT_SLOTS), Layout("track", "Track", "1+4", TRACK_SLOTS)),
        active="default",
    )


@pytest.fixture
def favorites_path(tmp_path):
    path = tmp_path / "favorites.json"
    save_favorites(make_prefs(), path)
    return path


@pytest.fixture
def page(ctx, favorites_path):
    widget = DashboardPage(ctx, favorites_path=favorites_path)
    widget.resize(1280, 800)
    yield widget
    widget.stop_polling()
    widget.hide()


def subscribed_keys(ctx) -> list[str]:
    return [param.key for _, param in ctx.session.polling_snapshot()[OWNER]]


def test_page_constructs_with_active_layout(page):
    assert not page.grab().isNull()
    assert page.title == "Dashboard"
    assert len(page.gauges) == 6
    assert [page.layout_combo.itemText(i) for i in range(page.layout_combo.count())] == ["Default", "Track"]
    assert page.gauges[0].mode() == "dial" and page.gauges[0].range() == (0.0, 8000.0)
    assert page.gauges[1].range() == (0.0, 250.0)
    assert page.gauges[2].mode() == "empty"


def test_layout_switch_rearranges_and_persists(page, favorites_path):
    page.select_layout("track")
    assert len(page.gauges) == 5
    assert page.grid.getItemPosition(page.grid.indexOf(page.gauges[0])) == (0, 0, 2, 2)
    assert page.grid.getItemPosition(page.grid.indexOf(page.gauges[4])) == (1, 3, 1, 1)
    assert page.gauges[2].mode() == "unavailable"
    assert json.loads(favorites_path.read_text())["active_layout"] == "track"
    page.select_layout("default")
    assert len(page.gauges) == 6 and page.gauges[0].range() == (0.0, 8000.0)


def test_shrinking_layout_preserves_assignments(page, favorites_path):
    prefs = page.preferences()
    page._commit(with_layout(prefs, with_preset(active_layout(prefs), "1x1")))
    assert len(page.gauges) == 1
    assert not any(gauge.isVisibleTo(page) for gauge in page._pool[1:])
    stored = load_favorites(favorites_path)
    assert active_layout(stored).slots[:2] == DEFAULT_SLOTS[:2]
    page._commit(with_layout(stored, with_preset(active_layout(stored), "2x3")))
    assert page.gauges[1].mode() == "dial" and page.gauges[1].range() == (0.0, 250.0)


def test_assign_slot_persists_and_clear(page, favorites_path):
    page.assign_slot(2, "obd.01.05")
    assert active_layout(load_favorites(favorites_path)).slots[2] == SlotAssignment("obd.01.05")
    assert page.gauges[2].mode() == "digital"  # coolant has no defined range in the fake catalog
    page.assign_slot(0, "")
    assert active_layout(load_favorites(favorites_path)).slots[0] is None
    assert page.gauges[0].mode() == "empty"


def test_removing_favorite_keeps_gauge(page, favorites_path):
    page.set_favorite("obd.01.0D", False)
    stored = load_favorites(favorites_path)
    assert "obd.01.0D" not in stored.favorites
    assert active_layout(stored).slots[1] == SlotAssignment("obd.01.0D")
    assert page.gauges[1].mode() == "dial"


def test_unavailable_keys_are_not_subscribed(page, ctx):
    page.select_layout("track")
    assert page.assigned_keys() == ["obd.01.0D", "EPS.21.01.supply"]
    page.start_polling()
    assert subscribed_keys(ctx) == ["obd.01.0D", "EPS.21.01.supply"]


def test_subscription_deduplicates_and_follows_assignments(page, ctx):
    page.assign_slot(2, "obd.01.0C")
    page.start_polling()
    assert OWNER in ctx.session.polling_owners()
    assert subscribed_keys(ctx) == ["obd.01.0C", "obd.01.0D"]
    page.assign_slot(3, "EPS.21.01.supply")
    assert subscribed_keys(ctx) == ["obd.01.0C", "obd.01.0D", "EPS.21.01.supply"]
    assert wait_until(lambda: ctx.store.has("obd.01.0C"), 5)
    page.stop_polling()
    assert OWNER not in ctx.session.polling_owners()


def test_polling_needs_an_assigned_sensor(page, ctx):
    for index in range(2):
        page.assign_slot(index, "")
    warnings = []
    ctx.toast.connect(lambda text, level: warnings.append(level))
    page.start_polling()
    assert not page.poll_button.isChecked() and warnings == ["warn"]


def show_in_stack(page) -> QStackedWidget:
    stack = QStackedWidget()
    stack.addWidget(page)
    stack.addWidget(QWidget())
    stack.show()
    return stack


def test_leaving_the_page_pauses_polling_and_returning_resumes(page, ctx):
    stack = show_in_stack(page)
    page.start_polling()
    assert OWNER in ctx.session.polling_owners()
    stack.setCurrentIndex(1)
    assert OWNER not in ctx.session.polling_owners()
    assert page.poll_button.isChecked()
    stack.setCurrentIndex(0)
    assert subscribed_keys(ctx) == ["obd.01.0C", "obd.01.0D"]
    page.setParent(None)


def test_recording_keeps_polling_after_leaving_the_page(page, ctx, tmp_path):
    stack = show_in_stack(page)
    page.start_polling()
    ctx.start_recording(tmp_path)
    stack.setCurrentIndex(1)
    assert OWNER in ctx.session.polling_owners()
    ctx.stop_recording()
    page.setParent(None)


def test_values_render_in_gauges(page, ctx):
    now = time.time()
    ctx.store.add_many(
        [
            Reading("obd.01.0D", 40, "km/h", now),
            Reading("obd.01.0D", 300, "km/h", now + 0.1),
            Reading("EPS.21.01.b10", "OPEN", "", now),
        ]
    )
    page.assign_slot(3, "EPS.21.01.b10")
    page.refresh_values()
    speed = page.gauges[1]
    assert speed.readout_text() == "300"
    assert speed.target_value == 250.0  # clamped to the dial
    assert page.gauges[3].mode() == "text" and page.gauges[3].readout_text() == "OPEN"


def test_error_reading_shows_error(page, ctx):
    ctx.store.add(Reading("obd.01.0C", None, "rpm", time.time(), error="NO DATA"))
    page.refresh_values()
    assert page.gauges[0].has_error and page.gauges[0].readout_text() == "error"
    assert page.gauges[1].readout_text() == "--"


def test_failure_after_success_shows_error_and_recovers(page, ctx):
    ctx.store.add(Reading("obd.01.0C", 4000, "rpm", time.time()))
    page.refresh_values()
    assert page.gauges[0].readout_text() == "4000" and not page.gauges[0].has_error
    ctx.store.add(Reading("obd.01.0C", None, "rpm", time.time(), error="No response"))
    page.refresh_values()
    assert page.gauges[0].has_error and page.gauges[0].readout_text() == "error"
    assert page.gauges[0].toolTip() == "No response"
    ctx.store.add(Reading("obd.01.0C", 1200, "rpm", time.time()))
    page.refresh_values()
    assert not page.gauges[0].has_error and page.gauges[0].readout_text() == "1200"


def test_needles_animate_then_timer_stops_and_stops_when_hidden(page, ctx):
    page.show()
    ctx.store.add(Reading("obd.01.0C", 4000, "rpm", time.time()))
    page.refresh_values()
    assert page.animation_timer.isActive()
    assert wait_until(lambda: not page.animation_timer.isActive(), 3)
    assert page.gauges[0].needle_value == pytest.approx(4000)
    ctx.store.add(Reading("obd.01.0C", 1000, "rpm", time.time()))
    page.refresh_values()
    assert page.animation_timer.isActive()
    page.hide()
    assert not page.animation_timer.isActive()


def test_show_event_reloads_external_changes(page, favorites_path):
    prefs = load_favorites(favorites_path)
    save_favorites(Preferences(prefs.favorites + ("obd.01.05",), prefs.layouts, "track"), favorites_path)
    page.show()
    assert len(page.gauges) == 5
    assert "obd.01.05" in page.preferences().favorites


def test_newer_file_is_left_untouched(ctx, tmp_path):
    path = tmp_path / "favorites.json"
    path.write_text(json.dumps({"version": 9}), encoding="utf-8")
    levels = []
    ctx.toast.connect(lambda text, level: levels.append(level))
    page = DashboardPage(ctx, favorites_path=path)
    page.assign_slot(0, "obd.01.05")
    page.assign_slot(1, "obd.01.05")
    assert json.loads(path.read_text()) == {"version": 9}
    assert levels == ["warn"]
    page.show()
    assert active_layout(page.preferences()).slots[0] == SlotAssignment("obd.01.05")
    page.hide()


def test_gauge_click_opens_picker_and_assigns(page, monkeypatch, favorites_path):
    def fake_exec(dialog):
        dialog.toggle_favorite("obd.01.05")
        dialog.select_key("obd.01.05")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(SensorPickerDialog, "exec", fake_exec)
    page.gauges[4].clicked.emit()
    stored = load_favorites(favorites_path)
    assert active_layout(stored).slots[4] == SlotAssignment("obd.01.05")
    assert "obd.01.05" in stored.favorites


def test_settings_dialog_result_is_applied(page, monkeypatch, favorites_path):
    def fake_exec(dialog):
        dialog.save_as("Big")
        dialog.set_preset("3x3")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(DashboardSettingsDialog, "exec", fake_exec)
    page.settings_button.click()
    assert len(page.gauges) == 9
    assert page.layout_combo.currentText() == "Big"
    assert load_favorites(favorites_path).active == "big"


def test_nine_gauges_refreshing_do_not_block_event_loop(page, ctx):
    keys = [entry.key for entry in ctx.catalog if entry.param.max_value is not None][:9]
    prefs = page.preferences()
    nine = Layout("nine", "Nine", "3x3", tuple(SlotAssignment(key, (0.0, 100.0)) for key in keys))
    page._commit(Preferences(prefs.favorites, prefs.layouts + (nine,), "nine"))
    page.show()
    assert len(page.gauges) == 9
    gaps: list[float] = []
    last = [time.monotonic()]

    def probe() -> None:
        now = time.monotonic()
        gaps.append(now - last[0])
        last[0] = now

    probe_timer = QTimer()
    probe_timer.timeout.connect(probe)
    probe_timer.start(10)
    start = time.monotonic()
    step = 0
    while time.monotonic() - start < 1.5:
        step += 1
        ctx.store.add_many([Reading(key, (step * 7 + i * 11) % 100, "", time.time()) for i, key in enumerate(keys)])
        page.refresh_values()
        wait_until(lambda: False, 0.1)
    probe_timer.stop()
    # A loaded CI machine fires the 10 ms probe less often; the stall bound is the check.
    assert len(gaps) > 20
    assert max(gaps[1:]) < 0.25, f"event loop stalled for {max(gaps[1:]):.3f}s"


def test_vehicle_layout_adopted_when_a_visible_sensor_is_missing(ctx, tmp_path):
    path = tmp_path / "favorites.json"
    foreign = Layout("other", "Other car", "2x2", (SlotAssignment("not.in.catalog"),))
    save_favorites(Preferences(favorites=(), layouts=(foreign,), active="other"), path)
    page = DashboardPage(ctx, favorites_path=path)
    vehicle = ctx.vehicle
    layout = active_layout(page.preferences())
    assert layout.id == vehicle.key
    assert [slot.key for slot in layout.slots if slot] == list(vehicle.default_favorites[:6])
    assert set(vehicle.default_favorites) <= set(page.preferences().favorites)
    stored = load_favorites(path)
    assert active_layout(stored).id == vehicle.key
    assert dict(stored.vehicle_layouts) == {vehicle.key: vehicle.key}


def test_catalog_rebuild_for_same_vehicle_keeps_user_choice(page, ctx, favorites_path):
    assert dict(load_favorites(favorites_path).vehicle_layouts) == {ctx.vehicle.key: "default"}
    page.select_layout("track")  # shows a key this vehicle lacks
    ctx.catalog_changed.emit()
    assert active_layout(page.preferences()).id == "track"
    assert dict(load_favorites(favorites_path).vehicle_layouts) == {ctx.vehicle.key: "track"}


@pytest.fixture
def idle_ctx(app, tmp_path):
    """A disconnected AppContext over the real Backend, so the vehicle can change."""
    from q50diag.gui.backend import Backend
    from q50diag.gui.context import AppContext
    from q50diag.session import Session

    session = Session(Backend())
    context = AppContext(session)
    context.recorder.folder = tmp_path
    yield context
    session.shutdown()


@pytest.fixture
def vehicle_page(idle_ctx, tmp_path):
    path = tmp_path / "vehicle-favorites.json"
    generic = Layout("generic", "Generic", "2x2", (SlotAssignment("obd.01.0C"), SlotAssignment("obd.01.0D")))
    save_favorites(with_layout(default_preferences(), generic), path)
    return DashboardPage(idle_ctx, favorites_path=path), path


def test_switch_to_mustang_creates_its_layout_and_back_restores_default(idle_ctx, vehicle_page):
    page, path = vehicle_page
    assert active_layout(page.preferences()).id == "default"
    assert idle_ctx.set_vehicle(vehicle_by_key("mustang_gt_2017"))
    mustang = idle_ctx.vehicle
    layout = active_layout(page.preferences())
    assert (layout.id, layout.name, layout.preset) == (mustang.key, mustang.name, "2x3")
    assert [slot.key for slot in layout.slots] == list(mustang.default_favorites[:6])
    assert all(page.gauges[i].mode() != "unavailable" for i in range(len(layout.slots)))
    assert set(mustang.default_favorites) <= set(page.preferences().favorites)
    assert idle_ctx.set_vehicle(vehicle_by_key("q50_2017"))
    assert active_layout(page.preferences()).id == "default"
    stored = load_favorites(path)
    assert dict(stored.vehicle_layouts) == {"q50_2017": "default", mustang.key: mustang.key}


def test_layout_picked_on_mustang_is_restored_for_mustang(idle_ctx, vehicle_page):
    page, _ = vehicle_page
    idle_ctx.set_vehicle(vehicle_by_key("mustang_gt_2017"))
    page.select_layout("generic")
    idle_ctx.set_vehicle(vehicle_by_key("q50_2017"))
    assert active_layout(page.preferences()).id == "default"
    idle_ctx.set_vehicle(vehicle_by_key("mustang_gt_2017"))
    assert active_layout(page.preferences()).id == "generic"


def test_settings_apply_is_remembered_for_vehicle(idle_ctx, vehicle_page, monkeypatch):
    page, path = vehicle_page

    def fake_exec(dialog):
        dialog.save_as("Q50 track")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(DashboardSettingsDialog, "exec", fake_exec)
    page.settings_button.click()
    assert dict(load_favorites(path).vehicle_layouts)["q50_2017"] == "q50-track"
