"""In-process MCP tools ("q50" server) that give MechanIQ the running app.

Handlers run on the Agent SDK's asyncio thread. Every read or change of app
state runs on the Qt main thread through `call_in_main_thread`, wrapped in
`asyncio.to_thread` so neither the Qt thread nor the asyncio loop blocks.
Commands that answer through Session signals (raw_read, read_module,
scan_dtcs) connect a one-shot catcher on the main thread, submit the command,
then wait on a threading.Event with a timeout.

Vehicle requests go through the Session, so the transport's read-only safety
gate applies; raw_read also checks the gate up front to refuse clearly.
`change_applier(summary, files)` runs on a worker thread because it runs the
test suite; it must marshal its own main-thread work (the hot reload)."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from claude_agent_sdk import SdkMcpTool, create_sdk_mcp_server, tool

from q50diag.core.transport import RequestBlocked, check_request_allowed
from q50diag.core.types import Dtc, ModuleAddress, Reading
from q50diag.gui.context import AppContext
from q50diag.mechaniq.mainthread import call_in_main_thread
from q50diag.recorder import Recording, list_recordings, load_recording
from q50diag.session import VOLTAGE_KEY, DtcScanResult, ModuleReport

log = logging.getLogger(__name__)

POLL_OWNER = "mechaniq"
STEERING_PAGE = "Steering"
LOG_FILE = "q50diag.log"
RAW_TIMEOUT_S = 8.0
MODULE_TIMEOUT_S = 45.0
SCAN_TIMEOUT_S = 120.0
MAX_SAMPLES = 500
MAX_POINTS = 1000
MAX_LOG_LINES = 2000
MAX_PARAMETERS = 300
MAX_RECORDINGS = 50
ADAPTER_PREFIXES = ("AT", "ST", "VT")

ChangeApplier = Callable[[str, list[str]], object]
ChangeUndoer = Callable[[str], object]
WindowProvider = Callable[[], object]


class ToolError(Exception):
    """A failure the agent can act on; the message says what to do instead."""


def ok(data: Any) -> dict[str, Any]:
    text = data if isinstance(data, str) else json.dumps(data, indent=1, default=str)
    return {"content": [{"type": "text", "text": text}]}


def fail(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "is_error": True}


async def on_main(fn: Callable[..., Any], *args: Any) -> Any:
    return await asyncio.to_thread(call_in_main_thread, fn, *args)


def schema(properties: dict[str, Any] | None = None, required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"type": "object", "properties": properties or {}, "required": list(required)}


STRING = {"type": "string"}
KEY_LIST = {"type": "array", "items": {"type": "string"}}


# Serialization ---------------------------------------------------------------
def reading_dict(reading: Reading) -> dict[str, Any]:
    return {
        "key": reading.key,
        "value": reading.value,
        "unit": reading.unit,
        "timestamp": round(reading.timestamp, 3),
        "error": reading.error or None,
    }


def dtc_dict(dtc: Dtc) -> dict[str, Any]:
    return {
        "code": dtc.code,
        "description": dtc.description,
        "module": dtc.module,
        "kind": dtc.kind,
        "status": dtc.status,
    }


def dtc_result_dict(result: DtcScanResult) -> dict[str, Any]:
    return {
        "complete": result.complete,
        "dtcs": [dtc_dict(d) for d in result.dtcs],
        "module_status": dict(result.module_status),
        "freeze_frame": [reading_dict(r) for r in result.freeze_frame],
    }


def report_dict(report: ModuleReport) -> dict[str, Any]:
    return {
        "module": report.module.name,
        "address": f"{report.module.tx_id:03X}/{report.module.rx_id:03X}",
        "responding": report.responding,
        "identification": dict(report.identification),
        "dtcs": None if report.dtcs is None else [dtc_dict(d) for d in report.dtcs],
        "parameters": [reading_dict(r) for r in report.parameters],
        "errors": list(report.errors),
    }


# Waiting on Session signals --------------------------------------------------
class SignalCatcher:
    """Records the first emission a matcher accepts (returns non-None).
    Connect and close it on the main thread."""

    def __init__(self, hooks: list[tuple[Any, Callable[..., Any]]]) -> None:
        self.done = threading.Event()
        self.value: Any = None
        self._connections = []
        for signal, matcher in hooks:
            slot = self._slot(matcher)
            signal.connect(slot)
            self._connections.append((signal, slot))

    def _slot(self, matcher: Callable[..., Any]) -> Callable[..., None]:
        def slot(*args: Any) -> None:
            if self.done.is_set():
                return
            value = matcher(*args)
            if value is not None:
                self.value = value
                self.done.set()

        return slot

    def close(self) -> None:
        for signal, slot in self._connections:
            signal.disconnect(slot)
        self._connections = []


async def request_and_wait(
    hooks: Callable[[], list[tuple[Any, Callable[..., Any]]]],
    start: Callable[[], None],
    timeout_s: float,
) -> Any:
    """Connect the matchers, run start() on the main thread, wait for a match.
    Returns the matched value, or None on timeout."""
    catcher = await on_main(lambda: SignalCatcher(hooks()))
    try:
        await on_main(start)
        arrived = await asyncio.to_thread(catcher.done.wait, timeout_s)
    finally:
        await on_main(catcher.close)
    return catcher.value if arrived else None


def error_matcher(*prefixes: str) -> Callable[[str], Any]:
    """Matches Session.error texts from one command (by its label prefix) and
    connection loss."""

    def match(text: str) -> Any:
        if text.startswith(prefixes) or text.startswith("Connection lost"):
            return ("error", text)
        return None

    return match


# Pure helpers ----------------------------------------------------------------
def parse_hex(text: str) -> bytes:
    compact = "".join(text.split()).replace("0x", "").replace("0X", "")
    try:
        payload = bytes.fromhex(compact)
    except ValueError as exc:
        raise ToolError(f"'{text}' is not hex bytes. Pass the payload as hex such as '21 01'.") from exc
    if not payload:
        raise ToolError("Empty payload. Pass hex bytes such as '21 01', or an AT command such as 'ATRV'.")
    return payload


def is_adapter_command(text: str) -> bool:
    return "".join(text.split()).upper().startswith(ADAPTER_PREFIXES)


def find_module(modules: list[ModuleAddress], name: str) -> ModuleAddress:
    wanted = name.strip().upper()
    for module in modules:
        if module.name.upper() == wanted or f"0X{module.tx_id:03X}" == wanted:
            return module
    names = ", ".join(m.name for m in modules) or "none"
    raise ToolError(f"Unknown module '{name}'. Known modules: {names}.")


def tail_lines(path: Path, count: int) -> list[str]:
    with path.open(encoding="utf-8", errors="replace") as handle:
        return [line.rstrip("\n") for line in deque(handle, maxlen=count)]


def resample(timestamps: list[float], values: list[float], start: float, end: float, points: int) -> list[list[float]]:
    """Mean per time bin, as [offset_s, value] pairs; empty bins are skipped."""
    if not timestamps or points <= 0:
        return []
    times = np.asarray(timestamps, dtype=float)
    data = np.asarray(values, dtype=float)
    edges = np.linspace(start, end if end > start else start + 1.0, points + 1)
    bins = np.clip(np.digitize(times, edges) - 1, 0, points - 1)
    sums = np.bincount(bins, weights=data, minlength=points)
    counts = np.bincount(bins, minlength=points)
    centers = (edges[:-1] + edges[1:]) / 2 - start
    return [[round(float(c), 3), round(float(s / n), 6)] for c, s, n in zip(centers, sums, counts) if n]


def summarize_recording(recording: Recording, keys: list[str], points: int) -> dict[str, Any]:
    start = recording.start or 0.0
    end = recording.end or start
    params = {}
    rows = len(recording.events)
    for key, series in recording.series.items():
        rows += len(series.values) + len(series.text_values) + series.errors
        values = series.values
        params[key] = {
            "name": series.name,
            "unit": series.unit,
            "module": series.module,
            "samples": len(values),
            "errors": series.errors,
            "min": min(values) if values else None,
            "max": max(values) if values else None,
            "mean": round(sum(values) / len(values), 6) if values else None,
            "text_values": sorted(set(series.text_values))[:5],
        }
    summary: dict[str, Any] = {
        "path": str(recording.path),
        "rows": rows,
        "bad_rows": recording.bad_rows,
        "start": datetime.fromtimestamp(start).isoformat(timespec="seconds") if recording.start else None,
        "duration_s": round(end - start, 3),
        "events": [{"offset_s": round(e.timestamp - start, 3), "text": e.text} for e in recording.events],
        "parameters": params,
    }
    if points and keys:
        summary["series"] = {
            key: resample(recording.series[key].timestamps, recording.series[key].values, start, end, points)
            for key in keys
            if key in recording.series
        }
        summary["series_missing"] = [key for key in keys if key not in recording.series]
    return summary


def clamp(value: Any, default: int, upper: int) -> int:
    try:
        number = int(value) if value is not None else default
    except (TypeError, ValueError):
        number = default
    return max(0, min(number, upper))


class Q50Tools:
    """The tool handlers. Methods named `_..._main` run on the Qt main thread."""

    def __init__(
        self,
        ctx: AppContext,
        window_provider: WindowProvider,
        change_applier: ChangeApplier,
        change_undoer: ChangeUndoer | None = None,
    ) -> None:
        self.ctx = ctx
        self.window_provider = window_provider
        self.change_applier = change_applier
        self.change_undoer = change_undoer

    # Main-thread helpers -------------------------------------------------
    def _window(self) -> Any:
        try:
            return self.window_provider()
        except Exception:  # noqa: BLE001 (no window yet is reported as None)
            log.exception("window provider failed")
            return None

    def _current_page(self) -> str | None:
        window = self._window()
        page = window.current_page() if window is not None else None
        return getattr(page, "title", None)

    def _require_connected(self) -> None:
        if not self.ctx.session.connected:
            raise ToolError("Not connected to an adapter. Ask the user to connect on the Connect page.")

    def _status_main(self) -> dict[str, Any]:
        session, recorder = self.ctx.session, self.ctx.recorder
        adapter = session.adapter
        voltage = self.ctx.store.latest(VOLTAGE_KEY)
        return {
            "connected": session.connected,
            "port": session.port,
            "adapter": None
            if adapter is None
            else {
                "identity": adapter.identity,
                "description": adapter.description,
                "protocol": adapter.protocol,
            },
            "protocol": adapter.protocol if adapter else None,
            "voltage_v": voltage.value if voltage is not None and not voltage.error else None,
            "voltage_age_s": round(time.time() - voltage.timestamp, 1) if voltage is not None else None,
            "recording": {
                "active": recorder.is_recording,
                "path": str(recorder.path) if recorder.path else None,
                "elapsed_s": round(recorder.elapsed(), 1),
                "rows": recorder.rows_written,
                "folder": str(recorder.folder),
            },
            "polling_owners": session.polling_owners(),
            "bus_monitor": session.bus_monitor_active,
            "current_page": self._current_page(),
            "markers": len(self.ctx.markers),
            "dtc_scan_done": self.ctx.dtc_result is not None,
            "discovered_modules": [d.address.name for d in self.ctx.discovered],
        }

    def _parameters_main(self, search: str) -> dict[str, Any]:
        terms = search.lower().split()
        entries = list(self.ctx.catalog) + [p for p in self.ctx.pinned if p not in self.ctx.catalog]
        matches = []
        for entry in entries:
            param = entry.param
            haystack = " ".join((param.key, param.name, entry.module.name, param.category, param.unit)).lower()
            if all(term in haystack for term in terms):
                matches.append(
                    {
                        "key": param.key,
                        "name": param.name,
                        "module": entry.module.name,
                        "unit": param.unit,
                        "category": param.category,
                        "polled": entry.polled,
                    }
                )
        return {
            "count": len(matches),
            "shown": min(len(matches), MAX_PARAMETERS),
            "parameters": matches[:MAX_PARAMETERS],
        }

    def _readings_main(self, keys: list[str], samples: int) -> dict[str, Any]:
        store, now = self.ctx.store, time.time()
        result: dict[str, Any] = {}
        for key in keys or store.keys():
            latest, stats = store.latest(key), store.stats(key)
            if latest is None:
                result[key] = {"status": "no data", "in_catalog": self.ctx.entry(key) is not None}
                continue
            item = {
                "name": self.ctx.display_name(key),
                "value": latest.value,
                "unit": latest.unit,
                "age_s": round(now - latest.timestamp, 2),
                "error": latest.error or None,
                "min": stats.minimum if stats else None,
                "max": stats.maximum if stats else None,
                "count": stats.count if stats else 0,
                "errors": stats.errors if stats else 0,
                "rate_hz": round(store.rate(key, now), 2),
            }
            if samples:
                times, values = store.series(key)
                item["samples"] = [[round(t - now, 3), float(v)] for t, v in zip(times[-samples:], values[-samples:])]
            result[key] = item
        return result

    def _dtcs_main(self) -> dict[str, Any]:
        reads = {
            name: [dtc_dict(d) for d in report.dtcs]
            for name, report in self.ctx.module_reports.items()
            if report.dtcs is not None
        }
        if self.ctx.dtc_result is None:
            return {"scanned": False, "module_reads": reads, "hint": "Call scan_dtcs to read codes from every module."}
        return {"scanned": True, **dtc_result_dict(self.ctx.dtc_result), "module_reads": reads}

    def _start_polling_main(self, keys: list[str]) -> dict[str, Any]:
        entries = [self.ctx.entry(key) for key in keys]
        known = [e for e in entries if e is not None]
        unknown = [key for key, e in zip(keys, entries) if e is None]
        if not known:
            raise ToolError("None of those keys are in the catalog. Call list_parameters to find valid keys.")
        self.ctx.session.start_polling(POLL_OWNER, [e.subscription() for e in known])
        return {
            "owner": POLL_OWNER,
            "polling": [e.key for e in known],
            "unknown": unknown,
            "connected": self.ctx.session.connected,
            "owners": self.ctx.session.polling_owners(),
        }

    def _steering_page(self) -> Any:
        window = self._window()
        page = getattr(window, "pages", {}).get(STEERING_PAGE) if window is not None else None
        if page is None:
            raise ToolError("The Steering page is not available. The main window may not be built yet.")
        return page

    def _watch_main(self, start: bool) -> dict[str, Any]:
        page = self._steering_page()
        if start:
            self._require_connected()
            page.start_watch()
        else:
            page.stop_watch()
        recorder = self.ctx.recorder
        return {
            "watching": page.watching,
            "recording": recorder.is_recording,
            "path": str(recorder.path) if recorder.path else None,
        }

    def _mark_main(self, label: str) -> dict[str, Any]:
        marker = self.ctx.mark_event(label)
        recorder = self.ctx.recorder
        return {
            "timestamp": round(marker.timestamp, 3),
            "label": marker.label,
            "recording": recorder.is_recording,
            "path": str(recorder.path) if recorder.is_recording else None,
        }

    def _recorder_paths_main(self) -> tuple[Path, Path | None]:
        recorder = self.ctx.recorder
        return Path(recorder.folder), (recorder.path if recorder.is_recording else None)

    def _navigate_main(self, title: str) -> dict[str, Any]:
        window = self._window()
        if window is None:
            self.ctx.navigate.emit(title)
            return {"requested": title}
        titles = list(getattr(window, "pages", {}))
        match = next((t for t in titles if t.lower() == title.strip().lower()), None)
        if match is None:
            raise ToolError(f"No page named '{title}'. Pages: {', '.join(titles)}.")
        window.show_page(match)
        return {"current_page": self._current_page()}

    # Handlers --------------------------------------------------------------
    async def get_status(self, args: dict[str, Any]) -> dict[str, Any]:
        return ok(await on_main(self._status_main))

    async def list_parameters(self, args: dict[str, Any]) -> dict[str, Any]:
        return ok(await on_main(self._parameters_main, str(args.get("search") or "")))

    async def get_readings(self, args: dict[str, Any]) -> dict[str, Any]:
        keys = [str(k) for k in args.get("keys") or []]
        samples = clamp(args.get("samples"), 0, MAX_SAMPLES)
        return ok(await on_main(self._readings_main, keys, samples))

    async def get_dtcs(self, args: dict[str, Any]) -> dict[str, Any]:
        return ok(await on_main(self._dtcs_main))

    async def scan_dtcs(self, args: dict[str, Any]) -> dict[str, Any]:
        await on_main(self._require_connected)
        session = self.ctx.session
        value = await request_and_wait(
            lambda: [
                (session.dtcs_ready, lambda result: ("ok", result)),
                (session.error, error_matcher("DTC scan: ")),
            ],
            session.scan_all_dtcs,
            SCAN_TIMEOUT_S,
        )
        if value is None:
            return ok({"status": "running", "hint": f"The scan took over {SCAN_TIMEOUT_S:.0f} s; call get_dtcs later."})
        kind, payload = value
        return ok(dtc_result_dict(payload)) if kind == "ok" else fail(payload)

    async def read_module(self, args: dict[str, Any]) -> dict[str, Any]:
        await on_main(self._require_connected)
        module = await on_main(lambda: find_module(self.ctx.all_modules(), str(args.get("module") or "")))
        include = bool(args.get("include_parameters", True))
        session = self.ctx.session

        def matched(report: ModuleReport) -> Any:
            return ("ok", report) if report.module.name == module.name else None

        value = await request_and_wait(
            lambda: [(session.module_read, matched), (session.error, error_matcher(f"Read {module.name}: "))],
            lambda: session.read_module(module, include),
            MODULE_TIMEOUT_S,
        )
        if value is None:
            return fail(f"{module.name} did not report within {MODULE_TIMEOUT_S:.0f} s. Check get_status and retry.")
        kind, payload = value
        return ok(report_dict(payload)) if kind == "ok" else fail(payload)

    async def start_polling(self, args: dict[str, Any]) -> dict[str, Any]:
        keys = [str(k) for k in args.get("keys") or []]
        if not keys:
            return fail("Pass at least one parameter key. Call list_parameters to find keys.")
        return ok(await on_main(self._start_polling_main, keys))

    async def stop_polling(self, args: dict[str, Any]) -> dict[str, Any]:
        await on_main(self.ctx.session.stop_polling, POLL_OWNER)
        return ok({"stopped": POLL_OWNER, "owners": await on_main(self.ctx.session.polling_owners)})

    async def start_steering_watch(self, args: dict[str, Any]) -> dict[str, Any]:
        return ok(await on_main(self._watch_main, True))

    async def stop_steering_watch(self, args: dict[str, Any]) -> dict[str, Any]:
        return ok(await on_main(self._watch_main, False))

    async def mark_event(self, args: dict[str, Any]) -> dict[str, Any]:
        label = str(args.get("label") or "").strip()
        if not label:
            return fail("Pass a short label describing the moment, for example 'Steering felt heavy'.")
        return ok(await on_main(self._mark_main, label))

    async def list_recordings(self, args: dict[str, Any]) -> dict[str, Any]:
        folder, active = await on_main(self._recorder_paths_main)
        paths = await asyncio.to_thread(list_recordings, folder)
        items = []
        for path in paths[:MAX_RECORDINGS]:
            stat = path.stat()
            items.append(
                {
                    "name": path.name,
                    "path": str(path),
                    "size_bytes": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                    "active": path == active,
                }
            )
        return ok({"folder": str(folder), "count": len(paths), "recordings": items})

    async def read_recording(self, args: dict[str, Any]) -> dict[str, Any]:
        folder, _active = await on_main(self._recorder_paths_main)
        path = await asyncio.to_thread(self._resolve_recording, folder, str(args.get("path") or "latest"))
        keys = [str(k) for k in args.get("keys") or []]
        points = clamp(args.get("points"), 0, MAX_POINTS)
        recording = await asyncio.to_thread(load_recording, path)
        return ok(summarize_recording(recording, keys, points))

    @staticmethod
    def _resolve_recording(folder: Path, raw: str) -> Path:
        if raw.strip().lower() == "latest":
            paths = list_recordings(folder)
            if not paths:
                raise ToolError(f"No recordings in {folder}. Start the steering watch or a recording first.")
            return paths[0]
        path = Path(raw).expanduser()
        path = path if path.is_absolute() else folder / path
        if not path.is_file():
            raise ToolError(f"Recording not found: {path}. Call list_recordings for valid paths.")
        return path

    async def raw_read(self, args: dict[str, Any]) -> dict[str, Any]:
        text = str(args.get("payload") or "").strip()
        if not text:
            return fail("Pass a hex payload such as '21 01' with a module, or an AT command such as 'ATRV'.")
        if is_adapter_command(text):
            await on_main(self._require_connected)
            return await self._raw_adapter_command(text)
        payload = parse_hex(text)
        try:
            check_request_allowed(payload)
        except RequestBlocked as exc:
            return fail(
                f"Refused by the read-only safety gate: {exc}. Only read services are allowed "
                "(OBD modes 01-03, 05-07, 09, 0A; services 17, 18, 19, 1A, 21, 22; 3E; 10 with "
                "sessions 01, 03, 81, 89, C0). Clearing codes needs the user on the Trouble Codes page."
            )
        await on_main(self._require_connected)
        if not args.get("module"):
            return fail("A hex request needs a module name, for example module='EPS'. Call get_status or read_module.")
        module = await on_main(lambda: find_module(self.ctx.all_modules(), str(args["module"])))
        return await self._raw_module_request(module, payload)

    async def _raw_adapter_command(self, line: str) -> dict[str, Any]:
        session = self.ctx.session
        value = await request_and_wait(
            lambda: [
                (session.raw_response, lambda req, resp: ("ok", resp) if req == line else None),
                (session.error, error_matcher("Raw command: ")),
            ],
            lambda: session.raw_command(line),
            RAW_TIMEOUT_S,
        )
        return self._raw_result(line, value)

    async def _raw_module_request(self, module: ModuleAddress, payload: bytes) -> dict[str, Any]:
        session = self.ctx.session
        request_text = f"{module.tx_id:03X} {payload.hex(' ').upper()}"
        value = await request_and_wait(
            lambda: [
                (session.raw_response, lambda req, resp: ("ok", resp) if req == request_text else None),
                (session.error, error_matcher(f"Raw request {module.name}: ")),
            ],
            lambda: session.raw_request(module, payload),
            RAW_TIMEOUT_S,
        )
        return self._raw_result(request_text, value)

    @staticmethod
    def _raw_result(request_text: str, value: Any) -> dict[str, Any]:
        if value is None:
            return fail(f"No reply to {request_text} within {RAW_TIMEOUT_S:.0f} s. The session may be busy; retry.")
        kind, payload = value
        return ok({"request": request_text, "reply": payload}) if kind == "ok" else fail(payload)

    async def read_app_log(self, args: dict[str, Any]) -> dict[str, Any]:
        folder, _active = await on_main(self._recorder_paths_main)
        count = clamp(args.get("lines"), 100, MAX_LOG_LINES)
        path = folder / LOG_FILE
        if not path.is_file():
            return fail(f"No {LOG_FILE} in {folder}. The app may have been started with another --log-dir.")
        lines = await asyncio.to_thread(tail_lines, path, count)
        return ok("\n".join([f"# {path} (last {len(lines)} lines)", *lines]))

    async def navigate(self, args: dict[str, Any]) -> dict[str, Any]:
        title = str(args.get("page") or "").strip()
        if not title:
            return fail("Pass a page title such as 'Steering' or 'Live Data'.")
        return ok(await on_main(self._navigate_main, title))

    async def apply_change(self, args: dict[str, Any]) -> dict[str, Any]:
        summary = str(args.get("summary") or "").strip()
        files = [str(f) for f in args.get("files") or [] if str(f).strip()]
        if not summary or not files:
            return fail("Pass a one-line summary and the list of files you changed, relative to the project root.")
        report = await asyncio.to_thread(self.change_applier, summary, files)
        return ok(str(report))

    async def undo_change(self, args: dict[str, Any]) -> dict[str, Any]:
        change_id = str(args.get("change_id") or "").strip()
        if not change_id:
            return fail("Pass the change id shown in the apply_change report or the Changes tab.")
        if self.change_undoer is None:
            return fail("Undo is not available in this session. Ask the user to press Undo in the Changes tab.")
        report = await asyncio.to_thread(self.change_undoer, change_id)
        return ok(str(report))


def _safe(name: str, handler: Callable[[dict[str, Any]], Any]) -> Callable[[dict[str, Any]], Any]:
    async def run(args: dict[str, Any]) -> dict[str, Any]:
        try:
            return await handler(args or {})
        except ToolError as exc:
            return fail(str(exc))
        except TimeoutError as exc:
            return fail(f"{exc}. The app GUI is busy; wait a moment and retry.")
        except Exception as exc:  # noqa: BLE001 (reported to the agent, logged with traceback)
            log.exception("MechanIQ tool %s failed", name)
            return fail(f"{name} failed: {type(exc).__name__}: {exc}. Check read_app_log for details.")

    return run


# (name, description, input schema)
TOOL_SPECS: tuple[tuple[str, str, dict[str, Any]], ...] = (
    (
        "get_status",
        "Connection, adapter, protocol, battery voltage, recording state and path, "
        "polling owners and the current page.",
        schema(),
    ),
    (
        "list_parameters",
        "Search the parameter catalog. Every whitespace-separated term must match the key, "
        "name, module, category or unit. Empty search lists everything.",
        schema({"search": STRING}),
    ),
    (
        "get_readings",
        "Latest value, min, max, count, error count and rate for parameter keys (all keys with "
        "data when empty). samples > 0 adds the last N samples as [seconds before now, value].",
        schema({"keys": KEY_LIST, "samples": {"type": "integer", "minimum": 0, "maximum": MAX_SAMPLES}}),
    ),
    ("get_dtcs", "Trouble codes from the last DTC scan and from module reads, without contacting the car.", schema()),
    (
        "scan_dtcs",
        "Scan every module for trouble codes and return them. Takes up to two minutes. module_status is "
        '"ok" only when the module answered; any other value (no response, partial, malformed reply, '
        "negative response) means its codes are unknown or incomplete, not zero.",
        schema(),
    ),
    (
        "read_module",
        "Read identification, trouble codes and (optionally) parameters from one module, for example EPS, ABS or ECM.",
        schema({"module": STRING, "include_parameters": {"type": "boolean"}}, ("module",)),
    ),
    (
        "start_polling",
        "Poll these parameter keys continuously under the 'mechaniq' owner, replacing the "
        "previous MechanIQ set. Other owners (steering watch, live data) keep polling.",
        schema({"keys": KEY_LIST}, ("keys",)),
    ),
    ("stop_polling", "Stop the parameters polled by start_polling.", schema()),
    (
        "start_steering_watch",
        "Start the Steering page watch (polls the steering set and starts recording), so the UI shows it running.",
        schema(),
    ),
    ("stop_steering_watch", "Stop the Steering page watch. Recording keeps running.", schema()),
    (
        "mark_event",
        "Mark an event now: writes an EVENT row to the active recording and a marker on every chart.",
        schema({"label": STRING}, ("label",)),
    ),
    ("list_recordings", "Recording CSV files in the log folder, newest first.", schema()),
    (
        "read_recording",
        "Summarize a recording ('latest', a file name or a path): parameters with min, max and "
        "mean, row count, time span and events. keys plus points > 0 add series resampled to that many time bins.",
        schema({"path": STRING, "keys": KEY_LIST, "points": {"type": "integer", "minimum": 0, "maximum": MAX_POINTS}}),
    ),
    (
        "raw_read",
        "Send one raw request through the app's session and the read-only safety gate. Pass module "
        "plus a hex payload such as '21 01', or an adapter command such as 'ATRV' without a module.",
        schema({"module": STRING, "payload": STRING}, ("payload",)),
    ),
    (
        "read_app_log",
        "Last N lines (default 100) of q50diag.log.",
        schema({"lines": {"type": "integer", "minimum": 1, "maximum": MAX_LOG_LINES}}),
    ),
    ("navigate", "Show a page of the app by its title, for example 'Steering'.", schema({"page": STRING}, ("page",))),
    (
        "apply_change",
        "Apply a code change you made: runs the tests, hot reloads and commits it. Call it after "
        "every edit with a one-line summary and the changed files relative to the project root. Returns the report.",
        schema({"summary": STRING, "files": KEY_LIST}, ("summary", "files")),
    ),
    ("undo_change", "Revert a change applied earlier by its change id.", schema({"change_id": STRING}, ("change_id",))),
)


def build_tools(
    ctx: AppContext,
    window_provider: WindowProvider,
    change_applier: ChangeApplier,
    change_undoer: ChangeUndoer | None = None,
) -> list[SdkMcpTool]:
    handlers = Q50Tools(ctx, window_provider, change_applier, change_undoer)
    return [
        tool(name, description, input_schema)(_safe(name, getattr(handlers, name)))
        for name, description, input_schema in TOOL_SPECS
    ]


def build_mcp_server(
    ctx: AppContext,
    window_provider: WindowProvider,
    change_applier: ChangeApplier,
    change_undoer: ChangeUndoer | None = None,
):
    """The "q50" in-process MCP server for ClaudeAgentOptions.mcp_servers."""
    return create_sdk_mcp_server(
        name="q50",
        version="1.0.0",
        tools=build_tools(ctx, window_provider, change_applier, change_undoer),
    )
