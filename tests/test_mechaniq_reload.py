"""Tests for the MechanIQ hot reloader: file classification, live module
reload, page rebuilds and the transport reconnect with recording kept."""

from __future__ import annotations

import csv
import dataclasses
import importlib
import sys
import threading
from pathlib import Path

import pytest
from PySide6.QtWidgets import QWidget

import q50diag.data
import q50diag.gui.pages
import q50diag.protocol
from q50diag.gui.backend import SIMULATOR
from q50diag.mechaniq import reload as reload_mod
from q50diag.mechaniq.reload import RECONNECT_MARKER, HotReloader, ReloadPlan, classify, page_titles
from tests.test_gui_fakes import ECM, RPM, app, ctx, wait_until  # noqa: F401 (fixtures)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FakeWindow:
    """Stands in for MainWindow: a pages dict and rebuild_page(title)."""

    def __init__(self, titles: tuple[str, ...] = ()) -> None:
        self.pages = {title: object() for title in titles}
        self.rebuilt: list[str] = []

    def rebuild_page(self, title: str) -> None:
        self.rebuilt.append(title)


# classify -------------------------------------------------------------------
@pytest.mark.parametrize(
    ("files", "kind", "live_first", "reconnect", "restart", "ignored"),
    [
        (["q50diag/protocol/reader.py"], "live", "q50diag.protocol.reader", (), (), ()),
        (["q50diag/data/dtc_descriptions.py"], "live", "q50diag.data.dtc_descriptions", (), (), ()),
        (["q50diag/gui/catalog.py"], "live", "q50diag.gui.catalog", (), (), ()),
        (["q50diag/gui/pages/console.py"], "live", "q50diag.gui.pages.console", (), (), ()),
        (["q50diag/transport/ports.py"], "reconnect", None, ("q50diag.transport.ports",), (), ()),
        (["q50diag/session.py"], "restart", None, (), ("q50diag/session.py",), ()),
        (["q50diag/gui/context.py"], "restart", None, (), ("q50diag/gui/context.py",), ()),
        (["q50diag/gui/main_window.py"], "restart", None, (), ("q50diag/gui/main_window.py",), ()),
        (["q50diag/core/types.py"], "restart", None, (), ("q50diag/core/types.py",), ()),
        (["q50diag/mechaniq/changes.py"], "restart", None, (), ("q50diag/mechaniq/changes.py",), ()),
        (["q50diag/protocol/no_such_module.py"], "restart", None, (), ("q50diag/protocol/no_such_module.py",), ()),
        (
            ["docs/notes.md", "tests/test_dtc.py", "pyproject.toml"],
            "none",
            None,
            (),
            (),
            ("docs/notes.md", "pyproject.toml", "tests/test_dtc.py"),
        ),
        ([], "none", None, (), (), ()),
    ],
)
def test_classify_table(files, kind, live_first, reconnect, restart, ignored):
    plan = classify(files)
    assert plan.kind == kind
    assert (plan.live_modules[0] if plan.live_modules else None) == live_first
    assert plan.reconnect_modules[: len(reconnect)] == reconnect
    assert plan.restart_files == restart
    assert plan.ignored_files == ignored


def test_classify_accepts_absolute_paths():
    plan = classify([PROJECT_ROOT / "q50diag" / "protocol" / "reader.py"])
    assert plan.live_modules == ("q50diag.protocol.reader",)


def test_classify_reloads_name_importers_in_dependency_order():
    plan = classify(["q50diag/data/dtc_descriptions.py"])
    order = plan.live_modules
    # obd2 and dtc bind `describe`; nissan binds names from dtc; explorer from nissan.
    for module in ("q50diag.protocol.dtc", "q50diag.protocol.obd2", "q50diag.protocol.nissan"):
        assert module in order
    assert order.index("q50diag.protocol.dtc") < order.index("q50diag.protocol.nissan")
    # reader uses `from q50diag.protocol import nissan` (a module object), so it needs no reload.
    assert "q50diag.protocol.reader" not in order


def test_classify_orders_groups_and_notes_restart_importers():
    plan = classify(["q50diag/gui/widgets/common.py", "q50diag/transport/elm_parse.py", "q50diag/session.py"])
    assert plan.kind == "restart"
    assert plan.live_modules[0] == "q50diag.gui.widgets.common"
    assert "q50diag.gui.pages.steering" in plan.live_modules
    assert plan.reconnect_modules[0] == "q50diag.transport.elm_parse"
    assert "q50diag.transport.elm" in plan.reconnect_modules
    pages = [m for m in plan.live_modules if m.startswith("q50diag.gui.pages.")]
    assert all(plan.live_modules.index(p) > 0 for p in pages)
    top_bar = classify(["q50diag/gui/widgets/top_bar.py"])
    assert any("q50diag.gui.main_window" in note for note in top_bar.notes)


def _write_tree(root: Path, files: dict[str, str]) -> None:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def test_import_graph_rules(tmp_path):
    _write_tree(
        tmp_path,
        {
            "q50diag/__init__.py": "",
            "q50diag/data/__init__.py": "",
            "q50diag/data/table.py": "X = 1\n",
            "q50diag/protocol/__init__.py": "",
            "q50diag/protocol/names.py": "from q50diag.data.table import X\n",
            "q50diag/protocol/module_ref.py": "from q50diag.data import table\n",
            "q50diag/protocol/lazy.py": "def f():\n    from q50diag.data.table import X\n    return X\n",
            "q50diag/protocol/typing_only.py": (
                "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from q50diag.data.table import X\n"
            ),
            "q50diag/protocol/relative.py": "from ..data.table import X\n",
            "q50diag/protocol/second.py": "from q50diag.protocol.names import X\n",
            "q50diag/session.py": "from q50diag.protocol.names import X\n",
        },
    )
    plan = classify(["q50diag/data/table.py"], tmp_path)
    assert plan.live_modules == (
        "q50diag.data.table",
        "q50diag.protocol.names",
        "q50diag.protocol.relative",
        "q50diag.protocol.second",
    )
    assert plan.restart_files == ()
    assert any(note.startswith("q50diag.session imported names from q50diag.protocol.names") for note in plan.notes)


# Live reload ------------------------------------------------------------------
@pytest.fixture
def probe_dirs(tmp_path, monkeypatch):
    """Extra directories on the q50diag.data, .protocol and .gui.pages package
    paths, so probe modules load as real q50diag submodules without touching
    the source tree. Probe modules are dropped from sys.modules afterwards."""
    dirs = {}
    for package in (q50diag.data, q50diag.protocol, q50diag.gui.pages):
        folder = tmp_path / package.__name__.replace(".", "_")
        folder.mkdir()
        monkeypatch.setattr(package, "__path__", [*package.__path__, str(folder)])
        dirs[package.__name__] = folder
    importlib.invalidate_caches()
    yield dirs
    for name in [n for n in sys.modules if "_mechaniq_probe" in n]:
        sys.modules.pop(name, None)


def test_live_reload_takes_effect_and_refreshes_polling(ctx, probe_dirs):
    (probe_dirs["q50diag.data"] / "_mechaniq_probe.py").write_text("def answer():\n    return 1\n")
    (probe_dirs["q50diag.protocol"] / "_mechaniq_probe_user.py").write_text(
        "from q50diag.data._mechaniq_probe import answer\n"
    )
    user = importlib.import_module("q50diag.protocol._mechaniq_probe_user")
    assert user.answer() == 1
    stale_rpm = dataclasses.replace(RPM, name="stale definition")
    ctx.session.start_polling("probe", [(ECM, stale_rpm)])
    changes = []
    ctx.catalog_changed.connect(lambda: changes.append(1))

    (probe_dirs["q50diag.data"] / "_mechaniq_probe.py").write_text("def answer():\n    return 2  # edited\n")
    plan = ReloadPlan(live_modules=("q50diag.data._mechaniq_probe", "q50diag.protocol._mechaniq_probe_user"))
    report = HotReloader(FakeWindow(), ctx).apply(plan)

    assert report.ok, report.error
    assert report.kind == "live"
    assert report.reloaded == plan.live_modules
    assert user.answer() == 2
    assert changes, "catalog was not rebuilt"
    polled = ctx.session.polling_snapshot()["probe"][0][1]
    assert polled is ctx.entry(RPM.key).param
    ctx.session.stop_polling("probe")


def test_real_module_reload_rebinds_importers(ctx):
    from q50diag.data import dtc_descriptions
    from q50diag.protocol import dtc, obd2

    def patched(code: str) -> str:
        return "patched"

    dtc_descriptions.describe = patched  # reload re-executes the source and replaces this
    plan = classify(["q50diag/data/dtc_descriptions.py"])
    report = HotReloader(FakeWindow(), ctx).apply(plan)

    assert report.ok, report.error
    assert "q50diag.protocol.obd2" in report.reloaded
    assert dtc_descriptions.describe is not patched
    assert obd2.describe is dtc_descriptions.describe
    assert dtc.describe is dtc_descriptions.describe


def test_reload_failure_is_reported(ctx, probe_dirs):
    path = probe_dirs["q50diag.data"] / "_mechaniq_probe_bad.py"
    path.write_text("VALUE = 1\n")
    importlib.import_module("q50diag.data._mechaniq_probe_bad")
    path.write_text("VALUE = (\n")
    report = HotReloader(FakeWindow(), ctx).apply(ReloadPlan(live_modules=("q50diag.data._mechaniq_probe_bad",)))
    assert not report.ok
    assert "q50diag.data._mechaniq_probe_bad" in report.error
    assert "Reload failed" in report.text()


def test_page_modules_are_rebuilt_through_the_window(ctx, probe_dirs):
    (probe_dirs["q50diag.gui.pages"] / "_mechaniq_probe_page.py").write_text(
        "from PySide6.QtWidgets import QWidget\n\n"
        "class ProbePage(QWidget):\n    title = 'Probe'\n\n"
        "class HiddenPage(QWidget):\n    title = 'Not in the window'\n"
    )
    importlib.import_module("q50diag.gui.pages._mechaniq_probe_page")
    assert page_titles(["q50diag.gui.pages._mechaniq_probe_page"]) == ["Probe", "Not in the window"]
    window = FakeWindow(("Probe", "Connect"))
    report = HotReloader(window, ctx).apply(ReloadPlan(live_modules=("q50diag.gui.pages._mechaniq_probe_page",)))
    assert window.rebuilt == ["Probe"]
    assert report.pages_rebuilt == ("Probe",)


def test_page_titles_skip_imported_classes():
    from q50diag.gui.pages import live_data, modules

    assert page_titles(["q50diag.gui.pages.live_data"]) == [live_data.LiveDataPage.title]
    assert page_titles(["q50diag.gui.pages.modules"]) == [modules.ModulesPage.title]
    assert issubclass(live_data.LiveDataPage, QWidget)


def test_apply_from_a_worker_thread_runs_on_the_main_thread(ctx, probe_dirs):
    (probe_dirs["q50diag.data"] / "_mechaniq_probe_thread.py").write_text("VALUE = 1\n")
    importlib.import_module("q50diag.data._mechaniq_probe_thread")
    reloader = HotReloader(FakeWindow(), ctx)
    seen = {}

    def record_thread(plan, label):
        seen["thread"] = threading.current_thread() is threading.main_thread()
        return HotReloader._apply(reloader, plan, label)

    reloader._apply = record_thread
    result = {}
    plan = ReloadPlan(live_modules=("q50diag.data._mechaniq_probe_thread",))
    worker = threading.Thread(target=lambda: result.setdefault("report", reloader.apply(plan)))
    worker.start()
    assert wait_until(lambda: "report" in result, 5.0)
    worker.join(1.0)
    assert seen["thread"] is True
    assert result["report"].ok


# Transport reload -------------------------------------------------------------
def _csv_rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_transport_reload_reconnects_same_port_and_keeps_recording(ctx, tmp_path):
    importlib.import_module("q50diag.transport.ports")
    session, backend = ctx.session, ctx.backend
    path = ctx.start_recording(tmp_path)
    session.start_polling("steering", [(ECM, RPM)])
    assert wait_until(lambda: ctx.recorder.rows_written > 2)
    plan = classify(["q50diag/transport/ports.py"])
    assert plan.kind == "reconnect"

    reloader = HotReloader(FakeWindow(), ctx)  # held: it owns the reconnect watch, as the app's does
    report = reloader.apply(plan, label="Code change abc: probe")

    assert report.ok, report.error
    assert report.reconnect_port == SIMULATOR
    assert "q50diag.transport.ports" in report.reloaded
    assert wait_until(lambda: any(m.label == RECONNECT_MARKER for m in ctx.markers), 5.0)
    assert len(backend.transports) == 2
    assert not backend.transports[0].is_open
    assert backend.transports[1].port == backend.transports[0].port
    assert session.connected and session.port == SIMULATOR
    assert ctx.recorder.is_recording and ctx.recorder.path == path
    assert "steering" in session.polling_owners()
    rows_at_marker = ctx.recorder.rows_written
    assert wait_until(lambda: ctx.recorder.rows_written > rows_at_marker + 2)
    ctx.stop_recording()

    rows = _csv_rows(path)
    events = [row["name"] for row in rows if row["type"] == "EVENT"]
    assert events == ["Code change abc: probe", RECONNECT_MARKER]
    marker_index = next(i for i, row in enumerate(rows) if row["name"] == RECONNECT_MARKER)
    assert any(row["key"] == RPM.key for row in rows[:marker_index])
    assert any(row["key"] == RPM.key for row in rows[marker_index + 1 :])


def test_transport_reload_when_disconnected_only_reloads(ctx):
    importlib.import_module("q50diag.transport.ports")
    ctx.session.disconnect_port()
    assert wait_until(lambda: not ctx.session.connected)
    report = HotReloader(FakeWindow(), ctx).apply(classify(["q50diag/transport/ports.py"]))
    assert report.reconnect_port is None
    assert "q50diag.transport.ports" in report.reloaded
    assert any("next connect" in note for note in report.notes)
    assert len(ctx.backend.transports) == 1


def test_report_text_lists_restart_files():
    report = reload_mod.ReloadReport(kind="restart", restart_files=("q50diag/session.py",))
    assert "Restart required for: q50diag/session.py" in report.text()
