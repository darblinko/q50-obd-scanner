"""MechanIQ q50 tools and the main-thread bridge against the fake backend.

Tool handlers are called the way the Agent SDK calls them: as coroutines on a
worker thread with its own asyncio loop, while the test (main) thread keeps
processing Qt events."""

from __future__ import annotations

import asyncio
import json
import threading

import pytest

from q50diag.core.types import Reading
from q50diag.gui.pages.steering import SteeringPage
from q50diag.mechaniq.mainthread import call_in_main_thread
from q50diag.mechaniq.tools import TOOL_SPECS, build_mcp_server, build_tools, resample
from q50diag.recorder import Recorder
from tests.test_gui_fakes import app, ctx, wait_until  # noqa: F401

TOOL_NAMES = {
    "get_status", "list_parameters", "get_readings", "get_dtcs", "scan_dtcs", "read_module",
    "start_polling", "stop_polling", "start_steering_watch", "stop_steering_watch", "mark_event",
    "list_recordings", "read_recording", "raw_read", "read_app_log", "navigate", "apply_change",
    "undo_change",
}


class StubPage:
    def __init__(self, title: str) -> None:
        self.title = title


class FakeWindow:
    def __init__(self, context) -> None:
        self.pages = {"Steering": SteeringPage(context), "Recordings": StubPage("Recordings")}
        self.current = "Steering"

    def show_page(self, title: str) -> None:
        self.current = title

    def current_page(self):
        return self.pages[self.current]


class Applier:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def apply(self, summary: str, files: list[str]) -> str:
        self.calls.append(("apply", summary, files, threading.current_thread() is threading.main_thread()))
        return f"change 7 applied: {summary}"

    def undo(self, change_id: str) -> str:
        self.calls.append(("undo", change_id))
        return f"change {change_id} reverted"


@pytest.fixture
def harness(ctx):
    window = FakeWindow(ctx)
    applier = Applier()
    tools = {t.name: t for t in build_tools(ctx, lambda: window, applier.apply, applier.undo)}
    yield ctx, window, applier, tools
    window.pages["Steering"].stop_watch()
    if ctx.recorder.is_recording:
        ctx.stop_recording()


def run_tool(tools, name: str, args: dict | None = None, timeout_s: float = 15.0) -> dict:
    """Run one handler on a worker thread while this thread pumps Qt events."""
    box: dict = {}

    def target() -> None:
        try:
            box["result"] = asyncio.run(tools[name].handler(args or {}))
        except BaseException as exc:  # noqa: BLE001 (re-raised in the test thread)
            box["error"] = exc

    worker = threading.Thread(target=target)
    worker.start()
    assert wait_until(lambda: not worker.is_alive(), timeout_s), f"{name} did not finish"
    if "error" in box:
        raise box["error"]
    return box["result"]


def text_of(result: dict) -> str:
    return result["content"][0]["text"]


def data_of(result: dict):
    assert not result.get("is_error"), text_of(result)
    return json.loads(text_of(result))


# Main-thread bridge -----------------------------------------------------------
def test_call_in_main_thread_runs_on_main_and_reraises(app):
    box: dict = {}

    def worker() -> None:
        box["thread"] = call_in_main_thread(lambda: threading.current_thread() is threading.main_thread())
        try:
            call_in_main_thread(lambda: 1 / 0)
        except ZeroDivisionError as exc:
            box["error"] = exc

    thread = threading.Thread(target=worker)
    thread.start()
    assert wait_until(lambda: not thread.is_alive())
    assert box["thread"] is True
    assert isinstance(box["error"], ZeroDivisionError)
    assert call_in_main_thread(lambda x: x + 1, 1) == 2  # direct call on the main thread


def test_call_in_main_thread_times_out_and_skips_late_job(app):
    ran: list[int] = []
    box: dict = {}

    def worker() -> None:
        try:
            call_in_main_thread(lambda: ran.append(1), timeout_s=0.2)
        except TimeoutError as exc:
            box["error"] = exc

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(2.0)  # the main thread does not process events meanwhile
    assert isinstance(box.get("error"), TimeoutError)
    wait_until(lambda: False, 0.2)
    assert ran == []


# Server and schemas -----------------------------------------------------------
def test_tool_list_and_server(ctx):
    assert {name for name, _d, _s in TOOL_SPECS} == TOOL_NAMES
    tools = build_tools(ctx, lambda: None, lambda s, f: "")
    assert {t.name for t in tools} == TOOL_NAMES
    for item in tools:
        assert item.input_schema["type"] == "object"
    server = build_mcp_server(ctx, lambda: None, lambda s, f: "")
    assert server["type"] == "sdk" and server["name"] == "q50"


# App state --------------------------------------------------------------------
def test_get_status(harness):
    _ctx, _window, _applier, tools = harness
    status = data_of(run_tool(tools, "get_status"))
    assert status["connected"] is True
    assert status["adapter"]["identity"] == "ELM327 v2.3"
    assert status["protocol"].startswith("ISO 15765-4")
    assert status["current_page"] == "Steering"
    assert status["recording"]["active"] is False


def test_list_parameters_search(harness):
    _ctx, _window, _applier, tools = harness
    found = data_of(run_tool(tools, "list_parameters", {"search": "eps supply"}))
    keys = [p["key"] for p in found["parameters"]]
    assert keys == ["EPS.21.01.supply"]
    assert found["parameters"][0]["unit"] == "V"
    everything = data_of(run_tool(tools, "list_parameters", {}))
    assert everything["count"] > 5


def test_polling_and_readings(harness):
    context, _window, _applier, tools = harness
    started = data_of(run_tool(tools, "start_polling", {"keys": ["EPS.21.01.supply", "nope.key"]}))
    assert started["polling"] == ["EPS.21.01.supply"]
    assert started["unknown"] == ["nope.key"]
    assert "mechaniq" in context.session.polling_owners()
    assert wait_until(lambda: (context.store.stats("EPS.21.01.supply") or None) is not None
                      and context.store.stats("EPS.21.01.supply").count >= 3)
    readings = data_of(run_tool(tools, "get_readings", {"keys": ["EPS.21.01.supply", "nope.key"], "samples": 2}))
    supply = readings["EPS.21.01.supply"]
    assert supply["unit"] == "V" and supply["value"] == pytest.approx(0x0DBD * 0.004)
    assert len(supply["samples"]) == 2
    assert readings["nope.key"]["status"] == "no data"
    data_of(run_tool(tools, "stop_polling"))
    assert "mechaniq" not in context.session.polling_owners()
    bad = run_tool(tools, "start_polling", {"keys": ["nope.key"]})
    assert bad["is_error"] and "list_parameters" in text_of(bad)


def test_dtcs_scan_and_cached(harness):
    _ctx, _window, _applier, tools = harness
    before = data_of(run_tool(tools, "get_dtcs"))
    assert before["scanned"] is False
    scan = data_of(run_tool(tools, "scan_dtcs"))
    codes = {d["code"] for d in scan["dtcs"]}
    assert {"P0300", "C1601"} <= codes
    assert wait_until(lambda: data_of(run_tool(tools, "get_dtcs"))["scanned"])


def test_read_module(harness):
    _ctx, _window, _applier, tools = harness
    report = data_of(run_tool(tools, "read_module", {"module": "eps"}))
    assert report["module"] == "EPS" and report["responding"] is True
    assert report["identification"]["Part number"] == "EPS-4GA0A"
    assert any(p["key"] == "EPS.21.01.supply" for p in report["parameters"])
    unknown = run_tool(tools, "read_module", {"module": "XYZ"})
    assert unknown["is_error"] and "Known modules" in text_of(unknown)


# Raw requests and the safety gate ---------------------------------------------
def test_raw_read_module_and_adapter(harness):
    context, _window, _applier, tools = harness
    reply = data_of(run_tool(tools, "raw_read", {"module": "EPS", "payload": "21 01"}))
    assert reply["request"] == "742 21 01"
    assert reply["reply"].startswith("61 01")
    adapter = data_of(run_tool(tools, "raw_read", {"payload": "ATI"}))
    assert adapter["reply"] == "ELM327 v2.3"
    missing_module = run_tool(tools, "raw_read", {"payload": "21 01"})
    assert missing_module["is_error"] and "module" in text_of(missing_module)


@pytest.mark.parametrize("payload", ["11 01", "27 01", "2E F1 90 00", "14 FF 00", "31 01 00"])
def test_raw_read_refuses_writes(harness, payload):
    context, _window, _applier, tools = harness
    transport = context.backend.transports[-1]
    sent_before = list(transport.requests)
    result = run_tool(tools, "raw_read", {"module": "EPS", "payload": payload})
    assert result["is_error"]
    assert "safety gate" in text_of(result)
    sent = [p for _tx, p, _clear in transport.requests[len(sent_before):]]
    assert bytes.fromhex(payload) not in sent


def test_raw_read_rejects_bad_hex(harness):
    _ctx, _window, _applier, tools = harness
    result = run_tool(tools, "raw_read", {"module": "EPS", "payload": "zz"})
    assert result["is_error"] and "hex" in text_of(result)


# Steering watch, events, navigation -------------------------------------------
def test_steering_watch_and_mark_event(harness):
    context, window, _applier, tools = harness
    started = data_of(run_tool(tools, "start_steering_watch"))
    assert started["watching"] is True and started["recording"] is True
    assert window.pages["Steering"].watch_button.isChecked()
    marked = data_of(run_tool(tools, "mark_event", {"label": "Heavy at 40 km/h"}))
    assert marked["recording"] is True and marked["label"] == "Heavy at 40 km/h"
    assert context.markers[-1].label == "Heavy at 40 km/h"
    stopped = data_of(run_tool(tools, "stop_steering_watch"))
    assert stopped["watching"] is False
    assert not window.pages["Steering"].watch_button.isChecked()
    assert run_tool(tools, "mark_event", {"label": " "})["is_error"]


def test_navigate(harness):
    _ctx, window, _applier, tools = harness
    assert data_of(run_tool(tools, "navigate", {"page": "recordings"}))["current_page"] == "Recordings"
    assert window.current == "Recordings"
    missing = run_tool(tools, "navigate", {"page": "Nowhere"})
    assert missing["is_error"] and "Steering" in text_of(missing)


# Recordings and log -----------------------------------------------------------
def _write_recording(folder) -> None:
    clock = iter([1000.0 + i * 0.5 for i in range(100)])
    recorder = Recorder(folder, clock=lambda: next(clock))
    recorder.start()
    for step in range(10):
        recorder.write_reading(Reading("adapter.voltage", 12.0 + step * 0.1, "V", 1000.0 + step), "Battery", "ADAPTER")
    recorder.write_reading(Reading("EPS.21.01.supply", None, "V", 1001.0, error="NO DATA"), "EPS supply", "EPS")
    recorder.mark_event("Heavy", 1005.0)
    recorder.stop()


def test_list_and_read_recording(harness, tmp_path):
    context, _window, _applier, tools = harness
    _write_recording(tmp_path)
    listing = data_of(run_tool(tools, "list_recordings"))
    assert listing["count"] == 1 and listing["recordings"][0]["active"] is False
    summary = data_of(run_tool(tools, "read_recording", {"path": "latest", "keys": ["adapter.voltage", "x"], "points": 5}))
    assert summary["rows"] == 12
    assert summary["duration_s"] == pytest.approx(9.0)
    assert summary["events"] == [{"offset_s": 5.0, "text": "Heavy"}]
    voltage = summary["parameters"]["adapter.voltage"]
    assert voltage["min"] == pytest.approx(12.0) and voltage["max"] == pytest.approx(12.9)
    assert summary["parameters"]["EPS.21.01.supply"]["errors"] == 1
    assert len(summary["series"]["adapter.voltage"]) == 5
    assert summary["series_missing"] == ["x"]
    by_name = data_of(run_tool(tools, "read_recording", {"path": listing["recordings"][0]["name"]}))
    assert by_name["rows"] == 12 and "series" not in by_name
    missing = run_tool(tools, "read_recording", {"path": "nope.csv"})
    assert missing["is_error"] and "list_recordings" in text_of(missing)


def test_resample_bins():
    pairs = resample([0.0, 0.1, 5.0, 9.9], [1.0, 3.0, 5.0, 7.0], 0.0, 10.0, 2)
    assert pairs == [[2.5, 2.0], [7.5, 6.0]]
    assert resample([], [], 0.0, 1.0, 4) == []


def test_read_app_log(harness, tmp_path):
    _ctx, _window, _applier, tools = harness
    missing = run_tool(tools, "read_app_log")
    assert missing["is_error"]
    (tmp_path / "q50diag.log").write_text("one\ntwo\nthree\n", encoding="utf-8")
    text = text_of(run_tool(tools, "read_app_log", {"lines": 2}))
    assert text.splitlines()[1:] == ["two", "three"]


# Change application -----------------------------------------------------------
def test_apply_change_delegates_off_main_thread(harness):
    _ctx, _window, applier, tools = harness
    result = run_tool(tools, "apply_change", {"summary": "decode EPS torque", "files": ["q50diag/protocol/nissan.py"]})
    assert text_of(result) == "change 7 applied: decode EPS torque"
    assert applier.calls == [("apply", "decode EPS torque", ["q50diag/protocol/nissan.py"], False)]
    assert run_tool(tools, "apply_change", {"summary": "x", "files": []})["is_error"]
    assert text_of(run_tool(tools, "undo_change", {"change_id": "7"})) == "change 7 reverted"
    assert applier.calls[-1] == ("undo", "7")


def test_undo_without_undoer_and_applier_failure(ctx):
    def broken(summary, files):
        raise RuntimeError("git missing")

    tools = {t.name: t for t in build_tools(ctx, lambda: None, broken)}
    undo = run_tool(tools, "undo_change", {"change_id": "1"})
    assert undo["is_error"] and "Changes tab" in text_of(undo)
    failed = run_tool(tools, "apply_change", {"summary": "x", "files": ["a.py"]})
    assert failed["is_error"] and "git missing" in text_of(failed)
    status = data_of(run_tool(tools, "get_status"))
    assert status["current_page"] is None


def test_tools_report_not_connected(app, tmp_path):
    from q50diag.gui.context import AppContext
    from q50diag.session import Session
    from tests.test_gui_fakes import FakeBackend

    backend = FakeBackend()
    session = Session(backend)
    try:
        context = AppContext(session, backend)
        context.recorder.folder = tmp_path
        tools = {t.name: t for t in build_tools(context, lambda: None, lambda s, f: "")}
        for name, args in (("raw_read", {"module": "EPS", "payload": "21 01"}), ("scan_dtcs", {})):
            result = run_tool(tools, name, args)
            assert result["is_error"] and "Connect page" in text_of(result)
        gate = run_tool(tools, "raw_read", {"module": "EPS", "payload": "11 01"})
        assert "safety gate" in text_of(gate)
    finally:
        session.shutdown()
