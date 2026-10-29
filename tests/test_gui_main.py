import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QLineEdit, QVBoxLayout, QWidget

from q50diag.core.types import Reading
from q50diag.gui.main_window import NAVIGATION, PAGE_MIN_WIDTH, MainWindow, PageFrame
from q50diag.gui.store import SeriesStore
from q50diag.gui.widgets.charts import StackedCharts
from q50diag.gui.widgets.top_bar import format_elapsed, voltage_state
from tests.test_gui_fakes import app, ctx, wait_until  # noqa: F401


@pytest.fixture
def window(ctx, tmp_path):
    ctx.recorder.folder = tmp_path
    win = MainWindow(ctx)
    win.show()
    yield win
    if ctx.recorder.is_recording:
        ctx.stop_recording()
    win.close()


def press_space(widget) -> bool:
    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier, " ")
    return QApplication.sendEvent(widget, event)


def test_every_page_renders(window, tmp_path):
    titles = [cls.title for _, classes in NAVIGATION for cls in classes]
    assert list(window.pages) == titles and len(titles) == 11
    for title in titles:
        window.show_page(title)
        assert window.current_page().title == title
        wait_until(lambda: False, 0.05)
        image = window.grab()
        assert not image.isNull() and image.width() > 800


def test_top_bar_shows_connection_vin_and_voltage(window, ctx):
    bar = window.top_bar
    assert wait_until(lambda: bar.vin.value.text() == "JN1SAMPLEXXXXXXX1")
    assert bar.connection.value.text() == "SIMULATOR"
    assert bar.protocol.value.text() == "CAN 11/500"
    assert wait_until(lambda: bar.voltage.value.text().endswith(" V"), 4)


def test_record_toggle_and_mark_event(window, ctx, tmp_path):
    window.top_bar.record_button.click()
    assert ctx.recorder.is_recording and ctx.recorder.path.parent == tmp_path
    assert wait_until(lambda: window.top_bar.recording.value.text().startswith("REC"))
    window.top_bar.mark_button.click()
    assert len(ctx.markers) == 1
    window.top_bar.record_button.click()
    assert not ctx.recorder.is_recording
    text = ctx.recorder.path.read_text()
    assert ",EVENT,event,Steering felt heavy," in text


def test_space_marks_event_except_in_text_inputs(window, ctx):
    window.activateWindow()
    if not wait_until(window.isActiveWindow, 1):
        pytest.skip("offscreen platform did not activate the window")
    window.show_page("Overview")
    window.current_page().setFocus()
    press_space(window.current_page())
    assert len(ctx.markers) == 1
    edit = window.findChild(QLineEdit)
    edit.setFocus()
    if QApplication.focusWidget() is edit:
        press_space(edit)
        assert len(ctx.markers) == 1


def test_errors_show_toast_and_status(window, ctx):
    ctx.session.error.emit("adapter fell over")
    assert wait_until(lambda: "adapter fell over" in " ".join(window.toasts.visible_texts()))
    assert "adapter fell over" in window.statusBar().currentMessage()


def test_helpers():
    assert voltage_state(None) == "idle"
    assert voltage_state(11.0) == "fault"
    assert voltage_state(12.0) == "warn"
    assert voltage_state(14.1) == "ok"
    assert format_elapsed(3725.4) == "01:02:05"


def test_store_and_chart(app):
    store = SeriesStore()
    for index in range(20):
        store.add(Reading("v", 12 + index / 10, "V", 1000.0 + index))
    store.add(Reading("v", None, "V", 1021.0, error="timeout"))
    store.add(Reading("vin", "JN1", "", 1000.0))
    stats = store.stats("v")
    assert stats.minimum == 12 and stats.maximum == pytest.approx(13.9) and stats.errors == 1
    assert store.rate("v", now=1019.5) == pytest.approx(1.0)
    assert store.window("v", 1005, 1007) == pytest.approx([12.5, 12.6, 12.7])
    assert store.latest("vin").value == "JN1" and not store.has("vin")
    charts = StackedCharts(store, lambda key: (key, "V"), live=False)
    charts.set_keys(["v"])
    charts.add_marker(1010.0, "heavy")
    charts.refresh(force=True)
    assert not charts.grab().isNull()


def test_ctrl_c_closes_the_window(app):
    import signal

    from PySide6.QtWidgets import QWidget

    from q50diag.__main__ import install_interrupt_handler

    window = QWidget()
    window.show()
    previous = signal.getsignal(signal.SIGINT)
    try:
        install_interrupt_handler(window)
        # os.kill with SIGINT terminates the process on Windows; raise_signal runs the handler.
        signal.raise_signal(signal.SIGINT)
        wait_until(lambda: not window.isVisible(), 2)
        assert not window.isVisible()
    finally:
        signal.signal(signal.SIGINT, previous)


def _framed_page(width: int, height: int) -> tuple[PageFrame, QWidget]:
    page = QWidget()
    content = QWidget()
    content.setMinimumSize(1200, 600)
    QVBoxLayout(page).addWidget(content)
    frame = PageFrame(page)
    frame.resize(width, height)
    frame.show()
    wait_until(lambda: False, 0.05)
    return frame, page


def test_page_frame_fills_a_roomy_frame_without_scrolling(app):
    frame, page = _framed_page(900, 800)
    assert page.size() == frame.viewport().size()
    assert not frame.horizontalScrollBar().isVisible() and not frame.verticalScrollBar().isVisible()
    frame.close()


def test_page_frame_scrolls_below_the_minimum_page_size(app):
    frame, page = _framed_page(500, 400)
    assert page.width() == PAGE_MIN_WIDTH and page.height() >= page.minimumSizeHint().height()
    assert frame.horizontalScrollBar().isVisible() and frame.verticalScrollBar().isVisible()
    frame.close()
