"""Modules, explorer and steering pages against the fake backend."""

from __future__ import annotations

import time

from q50diag.core.types import Reading
from q50diag.gui.pages.module_explorer import pinned_byte_entry, request_for, watch_param
from q50diag.gui.pages.modules import ModulesPage, grouped_modules, parse_hex_payload
from q50diag.gui.pages.steering import SteeringPage, dtc_color, eps_note
from q50diag.gui.widgets.hex_delegate import CHANGED_ROLE
from tests.test_gui_fakes import EPS, FAKE_MODULES, MYSTERY, app, ctx, wait_until  # noqa: F401


def _show(page):
    page.resize(1400, 900)
    page.show()
    return page


def test_helpers():
    assert parse_hex_payload("22 12 08") == b"\x22\x12\x08"
    assert parse_hex_payload("0x2101") == b"\x21\x01"
    assert parse_hex_payload("2") is None
    assert parse_hex_payload("zz") is None
    assert request_for(0x21, 0x01) == b"\x21\x01"
    assert request_for(0x22, 0x1208) == b"\x22\x12\x08"
    assert watch_param(EPS, 0x22, 0x1208).key == "explore.EPS.22.1208"
    entry = pinned_byte_entry(EPS, 0x21, 0x01, 3)
    assert entry.key == "pin.EPS.21.1.b3"
    assert entry.param.name == "EPS 21 01 byte 3"
    assert entry.param.decode(b"\x00\x01\x02\x07") == 7
    assert entry.param.decode(b"\x00") is None
    groups = dict(grouped_modules(list(FAKE_MODULES) + [MYSTERY]))
    assert [m.name for m in groups["Chassis"]] == ["ABS", "EPS"]
    assert "Unknown" in groups


def test_module_tree_and_discovery(ctx):
    page = _show(ModulesPage(ctx))
    names = page.tree.module_names()
    assert set(names) == {"ECM", "ABS", "EPS", "BCM"}
    assert names["EPS"] == "not checked"
    ctx.session.discover_modules()
    assert wait_until(lambda: page.tree.module_names().get("EPS") == "responding")
    names = page.tree.module_names()
    assert names["BCM"] == "no response"
    assert "0x71D" in names
    assert not page.grab().isNull()


def test_module_identification_dtcs_and_raw(ctx):
    page = _show(ModulesPage(ctx))
    assert page.select_module("EPS")
    page.read_selected()
    assert wait_until(lambda: page.ident_table.rowCount() == 2)
    assert wait_until(lambda: page.dtc_table.rowCount() == 2)
    assert page.dtc_table.item(0, 0).text() == "C1601"
    page.raw_edit.setText("21 01")
    page.send_raw()
    assert wait_until(lambda: "61 01" in page.raw_log.toPlainText())
    page.raw_edit.setText("2")
    page.send_raw()
    assert "invalid" not in page.raw_log.toPlainText().lower()


def test_module_parameters_live(ctx):
    page = _show(ModulesPage(ctx))
    page.select_module("ABS")
    page.tabs.setCurrentIndex(2)
    assert page.param_table.rowCount() == 1
    page.live_button.setChecked(True)
    assert "module" in ctx.session.polling_owners()
    assert wait_until(lambda: page.param_table.item(0, 1).text() not in ("--", ""))
    page.tabs.setCurrentIndex(0)
    assert "module" not in ctx.session.polling_owners()


def test_explorer_scan_watch_and_pin(ctx):
    page = _show(ModulesPage(ctx))
    page.select_module("EPS")
    page.tabs.setCurrentIndex(3)
    explorer = page.explorer
    explorer.end_edit.setText("10")
    explorer.start_scan()
    assert wait_until(lambda: explorer.table.rowCount() == 4)  # ids 01, 05, 09, 0D

    counter = {"n": 0}
    backend = ctx.backend

    def varying_read(t, m, p):
        counter["n"] += 1
        data = bytes([0x10, counter["n"] % 256, 0x20])
        return Reading(p.key, p.decode(data), p.unit, time.time(), raw=data)

    backend.read_parameter = varying_read
    explorer.watch_button.setChecked(True)
    assert explorer.watching
    assert wait_until(lambda: any(count >= 2 for count in explorer.change_counts.values()))
    ident = next(i for i, c in explorer.change_counts.items() if c >= 1)
    assert explorer.ever_changed[ident] == {1}
    bytes_item = explorer._items[ident][3]
    assert bytes_item.data(CHANGED_ROLE) == [1]

    explorer.table.setCurrentCell(explorer._items[ident][0].row(), 0)
    explorer.byte_spin.setValue(1)
    explorer.pin_selected()
    assert [p.key for p in ctx.pinned] == [f"pin.EPS.21.{ident:X}.b1"]
    page.tabs.setCurrentIndex(0)
    assert not explorer.watching
    assert "explorer" not in ctx.session.polling_owners()
    assert not page.grab().isNull()


def test_eps_notes():
    assert "8.5 V" in eps_note("C1601")
    assert "not a fault" in eps_note("C160A")
    assert dtc_color("C1604") is not None
    assert dtc_color("P0300") is None


def test_steering_watch_readouts_and_recording(ctx, tmp_path):
    ctx.recorder.folder = tmp_path
    page = _show(SteeringPage(ctx))
    assert page.chart_keys()
    subscribed = {param.key for _module, param in page.subscriptions()}
    assert {
        "EPS.21.01.supply",
        "EPS.21.01.angle",
        "EPS.21.01.torque",
        "EPS.21.01.assist",
        "EPS.21.01.b11",
    } <= subscribed
    assert not any(key.startswith("bcast.") for key in subscribed)
    frames = []
    ctx.session.frames.connect(frames.append)
    page.watch_button.setChecked(True)
    assert page.watching
    assert ctx.recorder.is_recording
    assert ctx.recorder.path.parent == tmp_path

    def filled():
        page.refresh_readouts()
        ids = ("voltage", "speed", "angle_abs", "eps_supply", "eps_angle", "eps_torque", "eps_assist", "eps_limit")
        return all(page.readouts[k].value_label.text() not in ("--", "n/a") for k in ids)

    # 5 s ran out on the macOS Intel runner, which runs this suite at about half speed.
    assert wait_until(filled, 15)
    assert page.readouts["eps_supply"].value_label.text() == "14.07"
    assert page.readouts["eps_angle"].value_label.text() == "-14.3"
    assert page.readouts["eps_limit"].value_label.text() == "100"
    assert frames == []  # the default watch opens no broadcast capture windows
    page.watch_button.setChecked(False)
    assert not page.watching
    assert ctx.recorder.is_recording  # recording keeps running until the top bar stops it
    assert not page.grab().isNull()
    ctx.stop_recording()


def test_steering_mark_event_summary(ctx):
    page = _show(SteeringPage(ctx))
    page.watch_button.setChecked(True)
    # The summary reads battery voltage and EPS torque; a slow machine polls voltage later.
    assert wait_until(lambda: all(ctx.store.latest(page.keys[k]) is not None for k in ("voltage", "eps_torque")), 10)
    marker = ctx.mark_event()
    assert page.events_table.rowCount() == 1
    assert page.events_table.item(0, 2).text() == "pending"
    wait_until(lambda: False, 0.5)
    page.refresh_events(now=marker.timestamp + 10)
    assert page.events_table.item(0, 2).text() not in ("pending", "no data")
    assert page.events_table.item(0, 3).text() not in ("pending", "no data")
    page.stop_watch()
    ctx.stop_recording()


def test_steering_eps_card_highlights_codes(ctx):
    page = _show(SteeringPage(ctx))
    assert page.eps_state.text() == "NOT CHECKED"
    page.read_eps()
    assert wait_until(lambda: page.eps_table.rowCount() == 2)
    assert page.eps_state.text() == "RESPONDING"
    codes = {page.eps_table.item(r, 0).text(): page.eps_table.item(r, 2).text() for r in range(2)}
    assert "8.5 V" in codes["C1601"]
    assert "not a fault" in codes["C160A"]
    color = page.eps_table.item(0, 0).foreground().color().name()
    assert color in ("#f85149", "#d29922")


def test_steering_without_backend_keys(ctx):
    ctx.catalog = []
    page = _show(SteeringPage(ctx))
    page._resolve_keys()
    page.refresh_readouts()
    assert page.readouts["eps_supply"].value_label.text() == "n/a"
    assert page.readouts["voltage"].value_label.text() == "n/a"
