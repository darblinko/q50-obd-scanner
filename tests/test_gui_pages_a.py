"""Offscreen tests for the Connect, Overview, Trouble Codes and Console pages."""

from __future__ import annotations

import sys

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent

from q50diag.gui import theme
from q50diag.gui.pages.connect import DEVICE_ROLE, ConnectPage
from q50diag.gui.pages.console import CommandHistory, ConsolePage
from q50diag.gui.pages.overview import OverviewPage, voltage_state
from q50diag.gui.pages.trouble_codes import TroubleCodesPage
from tests.test_gui_fakes import FakeBackend, app, ctx, wait_until  # noqa: F401 (fixtures)

PAGES = (ConnectPage, OverviewPage, TroubleCodesPage, ConsolePage)


def _grab_ok(widget) -> bool:
    widget.resize(1280, 800)
    image = widget.grab()
    return not image.isNull() and image.width() > 0


def test_pages_construct_and_render(ctx):
    for page_class in PAGES:
        page = page_class(ctx)
        page.on_shown()
        assert page.title
        assert _grab_ok(page)


def test_connect_lists_ports_with_vlinker_first(ctx):
    page = ConnectPage(ctx)
    items = [page.port_list.item(i) for i in range(page.port_list.count())]
    devices = [item.data(DEVICE_ROLE) for item in items]
    assert devices == ["/dev/ttyUSB0", "SIMULATOR", "/dev/ttyS4"]
    assert items[0].foreground().color().name() == theme.ACCENT
    assert "likely vLinker" in items[0].text()
    assert page.hint.isHidden()
    assert page.detail_labels["identity"].text() == "ELM327 v2.3"


def test_connect_shows_usbipd_hint_without_ports(ctx, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    page = ConnectPage(ctx)
    monkeypatch.setattr(ctx.backend, "list_ports", lambda: [])
    page.refresh_ports()
    assert not page.hint.isHidden()
    assert "usbipd attach --wsl --busid" in page.hint_label.text()
    assert page.port_list.count() == 1
    assert page.selected_device() == "SIMULATOR"


def test_connect_shows_driver_hint_without_ports_off_linux(ctx, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    page = ConnectPage(ctx)
    monkeypatch.setattr(ctx.backend, "list_ports", lambda: [])
    page.refresh_ports()
    assert "usbipd" not in page.hint_label.text()
    assert "driver" in page.hint_label.text()


def test_connect_port_error_is_reported(ctx, monkeypatch):
    toasts = []
    ctx.toast.connect(lambda text, level: toasts.append((text, level)))

    def broken():
        raise OSError("permission denied")

    monkeypatch.setattr(ctx.backend, "list_ports", broken)
    ConnectPage(ctx).refresh_ports()
    assert any(level == "error" and "permission denied" in text for text, level in toasts)


def test_overview_fills_cards_after_full_scan(ctx):
    page = OverviewPage(ctx)
    assert page.vin_card.value_label.text() == "JN1SAMPLEXXXXXXX1"
    assert page.mil_card.value_label.text() == "ON"
    assert page.monitor_table.rowCount() == 3
    ctx.session.full_scan()
    assert wait_until(lambda: page.dtc_card.value_label.text() not in ("--", ""), 5)
    assert wait_until(lambda: page.modules_card.value_label.text() == "4", 5)
    assert page.dtc_card.value_label.text() == "4"
    names = [page.module_table.item(r, 0).text() for r in range(page.module_table.rowCount())]
    assert "EPS" in names and "OBD-II" in names and "ECM" not in names


def test_voltage_bands():
    assert voltage_state(None) is None
    assert voltage_state(11.2) == "fault"
    assert voltage_state(12.0) == "warn"
    assert voltage_state(12.6) == "ok"
    assert voltage_state(15.4) == "warn"


def _scanned_page(ctx) -> TroubleCodesPage:
    page = TroubleCodesPage(ctx)
    ctx.session.scan_all_dtcs()
    assert wait_until(lambda: page.table.rowCount() > 0, 5)
    return page


def test_trouble_codes_table_and_filters(ctx):
    page = _scanned_page(ctx)
    codes = {page.table.item(r, 1).text() for r in range(page.table.rowCount())}
    assert codes == {"P0300", "P0171", "C1601", "C160A"}
    descriptions = {
        page.table.item(r, 1).text(): page.table.item(r, 2).text() for r in range(page.table.rowCount())
    }
    assert descriptions["C1601"] == "EPS supply voltage"
    page.module_filter.setCurrentText("EPS")
    assert page.table.rowCount() == 2
    page.kind_filter.setCurrentText("current")
    assert page.table.rowCount() == 1
    assert page.freeze_table.rowCount() == 2
    assert page.freeze_table.item(0, 0).text() == "Engine RPM"


def _click_when_enabled(button) -> None:
    # The page disables Clear while a command runs, such as the rescan after a clear.
    assert wait_until(button.isEnabled, 5)
    button.click()


def test_clear_requires_confirmation(ctx, monkeypatch):
    page = _scanned_page(ctx)
    backend: FakeBackend = ctx.backend
    index = page.clear_target.findData("EPS")
    assert index >= 0
    page.clear_target.setCurrentIndex(index)
    asked = []
    monkeypatch.setattr(page, "_confirm_clear", lambda name: asked.append(name) or False)
    _click_when_enabled(page.clear_button)
    wait_until(lambda: False, 0.3)
    assert asked == ["EPS"] and backend.cleared == []
    monkeypatch.setattr(page, "_confirm_clear", lambda name: True)
    _click_when_enabled(page.clear_button)
    assert wait_until(lambda: backend.cleared == ["EPS"], 3)
    page.clear_target.setCurrentIndex(page.clear_target.findData("OBD-II"))
    _click_when_enabled(page.clear_button)
    assert wait_until(lambda: backend.cleared == ["EPS", "OBD-II"], 3)


def test_command_history_navigation():
    history = CommandHistory()
    for line in ("ATI", "ATRV", "ATRV", "0100"):
        history.add(line)
    assert history.items() == ["ATI", "ATRV", "0100"]
    assert history.previous() == "0100"
    assert history.previous() == "ATRV"
    assert history.previous() == "ATI"
    assert history.previous() == "ATI"
    assert history.next() == "ATRV"
    assert history.next() == "0100"
    assert history.next() == ""


def test_console_sends_and_shows_rx(ctx):
    page = ConsolePage(ctx)
    page.input.setText("ATI")
    page.input.returnPressed.emit()
    assert wait_until(lambda: "ELM327 v2.3" in page.output.toPlainText(), 3)
    text = page.output.toPlainText()
    assert "TX" in text and "RX" in text
    up = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Up, Qt.KeyboardModifier.NoModifier)
    page.eventFilter(page.input, up)
    assert page.input.text() == "ATI"


def test_console_traffic_toggle(ctx):
    page = ConsolePage(ctx)
    page.traffic_box.setChecked(False)
    assert ctx.session.log_traffic is False
    page.traffic_box.setChecked(True)
    assert ctx.session.log_traffic is True
