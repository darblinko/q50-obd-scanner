"""MechanIQ GUI: docked panel, full page, settings dialog and the wiring of
the runtime (q50diag.mechaniq) into the main window.

The runtime modules are imported inside `build_mechaniq` so the app still
starts when they are missing or broken; the panel then shows why."""

from __future__ import annotations

import logging
from pathlib import Path

import q50diag
from q50diag.gui.mechaniq.controller import MechaniqController
from q50diag.gui.mechaniq.model import MechaniqModel

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(q50diag.__file__).resolve().parent.parent
CHANGE_LOG_PATH = PROJECT_ROOT / "logs" / "mechaniq-changes.jsonl"
PAGE_TITLE = "MechanIQ"
TEST_TIMEOUT_S = 300.0  # the full suite takes about 240 s


def build_mechaniq(window, ctx) -> MechaniqController:
    """Create model, controller, panel and runtime, and attach the MechanIQ
    page. Returns the controller; its `panel` goes into the window layout."""
    from q50diag.gui.mechaniq.panel import MechaniqPanel

    model = MechaniqModel(PROJECT_ROOT)
    controller = MechaniqController(model, ctx=ctx, project_root=PROJECT_ROOT, parent=window)
    try:
        _wire_runtime(controller, window, ctx)
    except Exception as exc:  # noqa: BLE001; MechanIQ must never stop the diagnostic app
        log.exception("MechanIQ runtime unavailable")
        controller.mark_unavailable(f"{type(exc).__name__}: {exc}")
    attach_views(controller, MechaniqPanel(controller), window, ctx)
    return controller


def attach_views(controller: MechaniqController, panel, window, ctx) -> None:
    controller.panel = panel
    panel.open_page_requested.connect(lambda: ctx.navigate.emit(PAGE_TITLE))
    page = getattr(window, "pages", {}).get(PAGE_TITLE)
    if page is not None:
        page.attach(controller)


def _wire_runtime(controller: MechaniqController, window, ctx) -> None:
    from q50diag.mechaniq.changes import ChangeLog, related_tests
    from q50diag.mechaniq.guard import make_guard
    from q50diag.mechaniq.reload import HotReloader
    from q50diag.mechaniq.runner import MechaniqRunner
    from q50diag.mechaniq.settings import DEFAULT_PATH, load_settings
    from q50diag.mechaniq.tools import build_mcp_server

    settings = load_settings()
    controller.set_settings(settings, DEFAULT_PATH)
    change_log = ChangeLog(PROJECT_ROOT, CHANGE_LOG_PATH, test_timeout_s=TEST_TIMEOUT_S, test_selector=related_tests)
    controller.set_change_tools(change_log, HotReloader(window, ctx))
    server = build_mcp_server(ctx, lambda: window, controller.apply_change, controller.undo_change)
    guard = make_guard(PROJECT_ROOT, DEFAULT_PATH)
    controller.set_runner(MechaniqRunner(PROJECT_ROOT, server, guard, settings=settings,
                                         system_append=lambda: _system_append_for(ctx)))


def _system_append_for(ctx) -> str:
    from q50diag.mechaniq.prompt import build_system_append
    from q50diag.transport.adapters import adapter_by_key

    backend = ctx.backend
    return build_system_append(backend.vehicle, adapter_by_key(backend.adapter_key), backend.bus)
