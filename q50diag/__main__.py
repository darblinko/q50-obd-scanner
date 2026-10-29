"""Entry point: `python -m q50diag [--vehicle KEY] [--sim | --port COM5]`."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from q50diag.gui.app_settings import AppSettings
from q50diag.gui.backend import SIMULATOR
from q50diag.recorder import DEFAULT_LOG_DIR
from q50diag.transport.adapters import DEFAULT_ADAPTER_KEY, adapter_by_key, bus_by_key
from q50diag.vehicles import DEFAULT_VEHICLE_KEY, VEHICLES, VehicleProfile, vehicle_by_key

log = logging.getLogger("q50diag")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="q50diag", description="OBD-II and manufacturer diagnostic explorer")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--sim", action="store_true", help="start connected to the built-in simulator")
    source.add_argument("--port", help="connect directly to this serial port, e.g. COM5 or /dev/ttyUSB0")
    parser.add_argument("--vehicle", choices=[vehicle.key for vehicle in VEHICLES], default=None,
                        help="vehicle profile; overrides the one saved in the app settings")
    parser.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR, help="folder for q50diag.log and recordings")
    parser.add_argument("--debug", action="store_true", help="log every adapter command and reply")
    parser.add_argument("--smoke-seconds", type=float, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--screenshot-dir", type=Path, default=None, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def resolve_selection(saved: AppSettings, vehicle_key: str | None = None) -> tuple[VehicleProfile, str, str]:
    """Vehicle, adapter key and bus key to start with. A command line vehicle
    wins over the saved one; unknown saved keys fall back to the defaults."""
    try:
        vehicle = vehicle_by_key(vehicle_key or saved.vehicle)
    except KeyError:
        log.warning("Unknown vehicle %r in app settings, using %s", saved.vehicle, DEFAULT_VEHICLE_KEY)
        vehicle = vehicle_by_key(DEFAULT_VEHICLE_KEY)
    try:
        adapter = adapter_by_key(saved.adapter)
    except KeyError:
        log.warning("Unknown adapter %r in app settings, using %s", saved.adapter, DEFAULT_ADAPTER_KEY)
        adapter = adapter_by_key(DEFAULT_ADAPTER_KEY)
    try:
        bus_key = bus_by_key(adapter, saved.bus).key
    except KeyError:
        log.warning("Unknown bus %r in app settings, using %s", saved.bus, adapter.buses[0].key)
        bus_key = adapter.buses[0].key
    return vehicle, adapter.key, bus_key


def setup_logging(log_dir: Path, debug: bool = False) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_dir / "q50diag.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if debug:
        logging.getLogger("q50diag.elm").setLevel(logging.DEBUG)


def install_interrupt_handler(window):
    """Qt's event loop keeps Python from seeing Ctrl+C. A handler that closes the
    window (so MechanIQ shuts down) plus a short idle timer that hands control
    back to Python makes Ctrl+C in the terminal quit cleanly."""
    from PySide6.QtCore import QTimer

    signal.signal(signal.SIGINT, lambda *_: window.close())
    timer = QTimer(window)
    timer.timeout.connect(lambda: None)
    timer.start(200)
    return timer


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.log_dir, args.debug)

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv[:1])
    app.setApplicationName("q50diag")

    from q50diag.gui import app_settings
    from q50diag.gui.backend import Backend
    from q50diag.gui.context import AppContext
    from q50diag.gui.main_window import MainWindow
    from q50diag.gui.theme import apply_theme
    from q50diag.recorder import Recorder
    from q50diag.session import Session

    apply_theme(app)
    vehicle, adapter_key, bus_key = resolve_selection(app_settings.load_app_settings(), args.vehicle)
    session = Session(Backend(vehicle=vehicle, adapter_key=adapter_key, bus=bus_key))
    ctx = AppContext(session, recorder=Recorder(args.log_dir), settings_path=app_settings.DEFAULT_PATH)
    window = MainWindow(ctx)
    window.show()
    port = SIMULATOR if args.sim else args.port
    if port:
        session.connect_port(port)
        window.show_page("Overview")
    if args.smoke_seconds is not None:
        from q50diag.gui.smoke import SmokeRun

        window._smoke = SmokeRun(window, args.smoke_seconds, args.screenshot_dir)  # keep a reference
    window._interrupt_timer = install_interrupt_handler(window)  # keep a reference
    log.info("q50diag started (vehicle=%s, port=%s)", vehicle.key, port or "none")
    print(f"q50diag running (port={port or 'none'}). Close the window or press Ctrl+C to quit.", file=sys.stderr)
    code = app.exec()
    log.info("q50diag exited with %s", code)
    return code


if __name__ == "__main__":
    sys.exit(main())
