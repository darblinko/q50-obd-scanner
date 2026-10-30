"""Offscreen tests for choosing the vehicle, adapter and CAN bus: AppContext,
the Connect page card, the top bar and the pages that adapt to the vehicle."""

from __future__ import annotations

import json

import pytest

from q50diag.gui.app_settings import load_app_settings
from q50diag.gui.backend import SIMULATOR, Backend
from q50diag.gui.pages.connect import ConnectPage
from q50diag.gui.pages.module_explorer import ExplorerTab
from q50diag.gui.pages.steering import SteeringPage
from q50diag.gui.widgets.top_bar import TopBar
from q50diag.transport.adapters import ADAPTERS
from q50diag.vehicles.mustang import MUSTANG_GT, MUSTANG_GT350R
from q50diag.vehicles.q50 import Q50
from tests.test_gui_fakes import app, ctx, wait_until  # noqa: F401 (fixtures)


@pytest.fixture
def idle_ctx(app, tmp_path):
    """A disconnected AppContext over the real Backend, saving settings in tmp_path."""
    from q50diag.gui.context import AppContext
    from q50diag.session import Session

    session = Session(Backend())
    context = AppContext(session, settings_path=tmp_path / "app.json")
    context.recorder.folder = tmp_path
    yield context
    session.shutdown()


def test_set_vehicle_emits_resets_state_and_saves(idle_ctx, tmp_path):
    seen = []
    idle_ctx.vehicle_changed.connect(seen.append)
    idle_ctx.module_responding = {"EPS": True}
    idle_ctx.pinned = list(idle_ctx.catalog[:1])
    assert idle_ctx.set_vehicle(MUSTANG_GT)
    assert seen == [MUSTANG_GT]
    assert idle_ctx.vehicle is MUSTANG_GT and idle_ctx.backend.vehicle is MUSTANG_GT
    assert idle_ctx.module_responding == {} and idle_ctx.pinned == []
    assert load_app_settings(tmp_path / "app.json").vehicle == "mustang_gt_2017"
    assert idle_ctx.set_vehicle(MUSTANG_GT)  # same vehicle: no second signal
    assert seen == [MUSTANG_GT]


@pytest.mark.parametrize("change", ["vehicle", "bus"])
def test_vehicle_or_bus_change_drops_measurements_markers_and_recording(idle_ctx, change):
    from q50diag.core.types import Reading

    cleared = []
    idle_ctx.markers_cleared.connect(lambda: cleared.append(True))
    idle_ctx.store.add_many([Reading("obd.01.0D", 42.0, "km/h", 1.0)])
    idle_ctx.mark_event("before switch")
    assert idle_ctx.start_recording() is not None
    if change == "vehicle":
        assert idle_ctx.set_vehicle(MUSTANG_GT)
    else:
        assert idle_ctx.set_adapter("obdlink_ex", "ms")
    assert idle_ctx.store.latest("obd.01.0D") is None
    assert idle_ctx.store.stats("obd.01.0D") is None
    assert idle_ctx.markers == [] and cleared == [True]
    assert not idle_ctx.recorder.is_recording


def test_catalog_follows_the_vehicle(idle_ctx):
    q50_keys = {entry.key for entry in idle_ctx.catalog}
    assert any(key.startswith("EPS.21.01") for key in q50_keys)
    idle_ctx.set_vehicle(MUSTANG_GT350R)
    entries = {entry.key: entry for entry in idle_ctx.catalog}
    assert not any(key.startswith("EPS.") or key.startswith("bcast.") for key in entries)
    assert "PCM.22.03EC" in entries and "PCM.22.1E1C" not in entries
    assert entries["obd.01.0C"].module.name == "PCM"


def test_vehicle_and_adapter_changes_refused_while_connected(ctx):
    assert ctx.session.connected
    assert not ctx.set_vehicle(MUSTANG_GT)
    assert ctx.vehicle is Q50
    assert not ctx.set_adapter("obdlink_ex", "ms")
    assert (ctx.backend.adapter_key, ctx.backend.bus) == ("vlinker_fs", "hs")


def test_set_adapter_saves_and_signals(idle_ctx, tmp_path):
    seen = []
    idle_ctx.adapter_changed.connect(lambda adapter, bus: seen.append((adapter, bus)))
    assert idle_ctx.set_adapter("obdlink_ex", "ms")
    assert seen == [("obdlink_ex", "ms")]
    saved = json.loads((tmp_path / "app.json").read_text())
    assert saved == {"vehicle": "q50_2017", "adapter": "obdlink_ex", "bus": "ms"}


def test_context_without_settings_path_writes_nothing(ctx, tmp_path):
    ctx.session.disconnect_port()
    assert wait_until(lambda: not ctx.session.connected)
    assert ctx.set_vehicle(MUSTANG_GT)
    assert not list(tmp_path.glob("*.json"))


def test_connect_card_combos_drive_the_context(idle_ctx):
    page = ConnectPage(idle_ctx)
    assert [page.vehicle_box.itemData(i) for i in range(page.vehicle_box.count())] == [
        "q50_2017",
        "mustang_gt350r_2016",
        "mustang_gt_2017",
    ]
    assert page.adapter_box.count() == len(ADAPTERS)
    assert page.bus_box.currentData() == "hs"
    page.vehicle_box.setCurrentIndex(2)
    assert idle_ctx.vehicle is MUSTANG_GT
    assert page.vehicle_note.text() == MUSTANG_GT.connect_note
    page.adapter_box.setCurrentIndex(1)
    assert idle_ctx.backend.adapter_key == ADAPTERS[1].key
    assert page.adapter_note.text() == ADAPTERS[1].notes
    bus_keys = [page.bus_box.itemData(i) for i in range(page.bus_box.count())]
    assert bus_keys == [bus.key for bus in ADAPTERS[1].buses]
    page.bus_box.setCurrentIndex(bus_keys.index("ms"))
    assert idle_ctx.backend.bus == "ms"


def test_connect_card_restores_saved_choice_and_locks_while_connected(idle_ctx):
    idle_ctx.set_vehicle(MUSTANG_GT350R)
    page = ConnectPage(idle_ctx)
    assert page.vehicle_box.currentData() == "mustang_gt350r_2016"
    assert page.vehicle_box.isEnabled()
    idle_ctx.session.connect_port(SIMULATOR)
    assert wait_until(lambda: idle_ctx.session.connected and idle_ctx.overview is not None, 10)
    assert idle_ctx.overview.vin == "1FASAMPLEXXXXXXX1"
    assert wait_until(lambda: not page.vehicle_box.isEnabled())
    assert not page.adapter_box.isEnabled() and not page.bus_box.isEnabled()
    assert "Mustang GT350R" in page._simulator_text()


def test_top_bar_shows_the_vehicle(idle_ctx):
    bar = TopBar(idle_ctx)
    assert bar.vehicle.value.text() == "Q50 3.0t"
    idle_ctx.set_vehicle(MUSTANG_GT)
    assert bar.vehicle.value.text() == "Mustang GT"


def test_steering_page_shows_not_available_for_mustang(idle_ctx):
    page = SteeringPage(idle_ctx)
    assert page.available and page.content.isVisibleTo(page)
    idle_ctx.set_vehicle(MUSTANG_GT)
    assert not page.available
    assert page.unavailable.isVisibleTo(page) and not page.content.isVisibleTo(page)
    assert not page.watch_button.isVisibleTo(page)
    page.start_watch()
    assert not page.watching
    idle_ctx.set_vehicle(Q50)
    assert page.available and page.content.isVisibleTo(page)


def test_explorer_services_follow_the_vehicle(idle_ctx):
    tab = ExplorerTab(idle_ctx)
    services = [tab.service_box.itemData(i) for i in range(tab.service_box.count())]
    assert services == [0x21, 0x22]
    assert tab.pin_button.isVisibleTo(tab)
    idle_ctx.set_vehicle(MUSTANG_GT)
    services = [tab.service_box.itemData(i) for i in range(tab.service_box.count())]
    assert services == [0x22]
    assert (tab.start_edit.text(), tab.end_edit.text()) == ("F400", "F4FF")
    assert not tab.pin_button.isVisibleTo(tab)
