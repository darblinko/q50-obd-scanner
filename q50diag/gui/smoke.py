"""Hidden smoke run for `--smoke-seconds N [--screenshot-dir DIR]`.

Drives the main window through a scripted session (full scan, DTC scan, bus
monitor, live polling, steering watch with markers, explorer scan, console),
then optionally saves a screenshot of every page and quits. Used to prove the
GUI works end to end against the simulator; not part of normal operation. For
a vehicle without a Steering page the steering watch is skipped and the
explorer scans the engine module with the vehicle's first explorer service.

The run fails on any session error and on a missing milestone: an overview,
a successful vehicle reading (the adapter voltage does not count), a complete
DTC scan in which a module answered, a complete explorer scan with results,
a console reply and, with a Steering page, a recording made during the run."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication

from q50diag.recorder import list_recordings
from q50diag.session import VOLTAGE_KEY

log = logging.getLogger(__name__)

SESSION_MILESTONES = frozenset({"overview", "live reading", "trouble codes", "explorer scan", "console reply"})
LIVE_KEYS = ("adapter.voltage", "obd.01.0C", "obd.01.0D", "bcast.300.assist_raw", "PCM.22.F405")


class SmokeRun(QObject):
    def __init__(self, window, seconds: float, screenshot_dir: Path | None) -> None:
        super().__init__(window)
        self.window = window
        self.ctx = window.ctx
        self.seconds = max(seconds, 4.0)
        self.screenshot_dir = screenshot_dir
        self.started = time.monotonic()
        self.failures: list[str] = []
        self.seen: set[str] = set()
        self._recordings_before = set(list_recordings(self.page("Recordings").folder))
        self._wire_milestones()
        steps: list[tuple[float, Callable[[], None]]] = [
            (0.10, self._full_scan),
            (0.30, self._bus_monitor),
            (0.40, self._live_and_steering),
            (0.55, self._mark),
            (0.65, self._explorer_and_console),
            (0.75, self._mark),
            (1.00, self._finish),
        ]
        for fraction, step in steps:
            QTimer.singleShot(int(self.seconds * fraction * 1000), self._guard(step))

    def _wire_milestones(self) -> None:
        session = self.ctx.session
        session.error.connect(self._on_error)
        session.overview_ready.connect(lambda _overview: self.seen.add("overview"))
        session.readings.connect(self._on_readings)
        session.dtcs_ready.connect(self._on_dtcs)
        session.identifiers_scanned.connect(self._on_identifiers)
        session.raw_response.connect(lambda _request, _response: self.seen.add("console reply"))

    def _on_error(self, text: str) -> None:
        log.warning("smoke: session error: %s", text)
        failure = f"session error: {text}"
        if failure not in self.failures:  # a polling error repeats every round
            self.failures.append(failure)

    def _on_readings(self, readings) -> None:
        if any(not reading.error and reading.key != VOLTAGE_KEY for reading in readings):
            self.seen.add("live reading")

    def _on_dtcs(self, result) -> None:
        """The full scan is cancelled by the bus monitor step; the separate DTC scan must complete."""
        if result.complete and any(status == "ok" for status in result.module_status.values()):
            self.seen.add("trouble codes")

    def _on_identifiers(self, scan) -> None:
        if scan.complete and scan.results:
            self.seen.add("explorer scan")

    def _check_milestones(self) -> None:
        if not self.ctx.session.connected:
            self.failures.append("not connected at the end of the run")
        for milestone in sorted(SESSION_MILESTONES - self.seen):
            self.failures.append(f"missing milestone: {milestone}")

    def _guard(self, step: Callable[[], None]) -> Callable[[], None]:
        def run() -> None:
            log.info("smoke step %s at %.1fs", step.__name__, time.monotonic() - self.started)
            try:
                step()
            except Exception as exc:  # noqa: BLE001 (record and keep going to the end)
                log.exception("smoke step %s failed", step.__name__)
                self.failures.append(f"{step.__name__}: {exc}")

        return run

    def page(self, title: str):
        return self.window.pages[title]

    def _full_scan(self) -> None:
        self.window.show_page("Overview")
        self.ctx.session.full_scan()

    def _bus_monitor(self) -> None:
        self.ctx.session.cancel()  # cut a long discovery short so the rest can run
        self.ctx.session.scan_all_dtcs()
        self.window.show_page("Bus Monitor")
        self.ctx.session.start_bus_monitor(None)

    def _live_and_steering(self) -> None:
        self.ctx.session.stop_bus_monitor()
        live = self.page("Live Data")
        live.set_checked([key for key in LIVE_KEYS if self.ctx.entry(key)])
        live.start_polling()
        if not self.ctx.vehicle.has_steering_page:
            self.window.show_page("Live Data")
            return
        steering = self.page("Steering")
        steering.start_watch()
        self.window.show_page("Steering")

    def _mark(self) -> None:
        self.ctx.mark_event()

    def _explorer_and_console(self) -> None:
        self.ctx.session.raw_command("ATI")
        self.ctx.session.raw_command("ATRV")
        modules = self.page("Modules")
        vehicle = self.ctx.vehicle
        target, scan_range = (
            ("EPS", ("00", "3F")) if vehicle.has_steering_page else (vehicle.obd_module, ("F400", "F43F"))
        )
        if modules.select_module(target):
            explorer = modules.explorer
            modules.tabs.setCurrentWidget(explorer)
            explorer.start_edit.setText(scan_range[0])
            explorer.end_edit.setText(scan_range[1])
            explorer.start_scan()
            QTimer.singleShot(1500, explorer.start_watch)

    def _finish(self) -> None:
        self._check_milestones()
        if self.ctx.recorder.is_recording:
            self.ctx.stop_recording()
        recordings = self.page("Recordings")
        recordings.refresh()
        saved = list_recordings(recordings.folder)
        if saved:
            recordings.open_recording(saved[0])
        produced = [path for path in saved if path not in self._recordings_before]
        if self.ctx.vehicle.has_steering_page and not produced:  # the steering watch records
            self.failures.append("missing milestone: recording")
        if self.screenshot_dir:
            self.screenshot_dir.mkdir(parents=True, exist_ok=True)
            QTimer.singleShot(300, self._screenshots)
        else:
            QTimer.singleShot(300, self._quit)

    def _screenshots(self) -> None:
        titles = list(self.window.pages)
        self._shoot_next(titles)

    def _shoot_next(self, remaining: list[str]) -> None:
        if not remaining:
            self._quit()
            return
        title = remaining[0]
        self.window.show_page(title)

        def grab() -> None:
            path = self.screenshot_dir / f"{_slug(title)}.png"
            if not self.window.grab().save(str(path)):
                self.failures.append(f"could not save {path}")
            log.info("smoke screenshot %s", path)
            self._shoot_next(remaining[1:])

        QTimer.singleShot(700, grab)

    def _quit(self) -> None:
        status = "ok" if not self.failures else "failures: " + "; ".join(self.failures)
        print(f"smoke run finished after {time.monotonic() - self.started:.1f}s: {status}", flush=True)
        self.window.close()
        QApplication.instance().exit(1 if self.failures else 0)


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
