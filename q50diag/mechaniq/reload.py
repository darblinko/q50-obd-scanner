"""Hot reload of q50diag code that MechanIQ changed.

`classify()` sorts changed files into modules that reload in place (data,
protocol, catalog, widgets, pages), transport modules that reload and then
reconnect the same port, files that need an app restart, and files that need
nothing. A module that bound names from a changed module at import time
(`from x import y`) keeps the old objects, so the plan also reloads every
reloadable module that imports names from a changed one. Imports inside
function bodies (the backend) resolve at call time and need nothing.

`HotReloader.apply()` runs on the Qt main thread: it reloads in dependency
order, rebuilds the catalog, refreshes live polling subscriptions to the new
parameter definitions, rebuilds affected pages through
`MainWindow.rebuild_page(title)` and, for transport changes, reconnects the
same port while the recorder keeps writing."""

from __future__ import annotations

import ast
import importlib
import importlib.util
import logging
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget

from q50diag.mechaniq.mainthread import call_in_main_thread

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "q50diag"
TRANSPORT_PACKAGE = "q50diag.transport"
PAGES_PACKAGE = "q50diag.gui.pages"
MAIN_WINDOW = "q50diag.gui.main_window"
# Reload rank of each reloadable package; imports decide the order inside a rank.
RANKS = (
    ("q50diag.data", 0),
    ("q50diag.protocol", 1),
    (TRANSPORT_PACKAGE, 2),
    ("q50diag.gui.catalog", 3),
    ("q50diag.gui.widgets", 4),
    (PAGES_PACKAGE, 5),
)
CATALOG_INPUTS = ("q50diag.data", "q50diag.protocol", "q50diag.gui.catalog")
RECONNECT_MARKER = "Reconnected after code change"
RECONNECT_TIMEOUT_MS = 15000
DISCONNECT_WAIT_S = 3.0
APPLY_TIMEOUT_S = 60.0


def _within(name: str, package: str) -> bool:
    return name == package or name.startswith(package + ".")


def rank(module: str) -> int | None:
    """Reload rank of a module, or None when it cannot reload live."""
    for package, value in RANKS:
        if _within(module, package):
            return value
    return None


def module_name(path: Path, root: Path) -> str | None:
    """Dotted name of a .py file inside the q50diag package, else None."""
    try:
        relative = path.relative_to(root)
    except ValueError:
        return None
    if relative.suffix != ".py" or not relative.parts or relative.parts[0] != PACKAGE:
        return None
    parts = list(relative.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


# Import graph ---------------------------------------------------------------
def _is_type_checking(test: ast.expr) -> bool:
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


class _NameImports(ast.NodeVisitor):
    """Collects q50diag modules whose names are bound at import time."""

    def __init__(self, module: str, is_package: bool, known: set[str]) -> None:
        self.package = module if is_package else module.rpartition(".")[0]
        self.known = known
        self.found: set[str] = set()

    def visit_FunctionDef(self, node: ast.AST) -> None:  # noqa: N802; ast visitor
        """Imports in function bodies run at call time and see reloaded modules."""

    visit_AsyncFunctionDef = visit_FunctionDef  # noqa: N815

    def visit_If(self, node: ast.If) -> None:  # noqa: N802; ast visitor
        if _is_type_checking(node.test):
            return
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802; ast visitor
        base = self._resolve(node)
        if base is None or base not in self.known:
            return
        # `from pkg import submodule` binds the module object, which a reload updates in place.
        if any(f"{base}.{alias.name}" not in self.known for alias in node.names):
            self.found.add(base)

    def _resolve(self, node: ast.ImportFrom) -> str | None:
        if node.level == 0:
            return node.module
        parts = self.package.split(".")
        if node.level - 1 >= len(parts):
            return None
        base = parts[: len(parts) - (node.level - 1)]
        return ".".join(base + ([node.module] if node.module else []))


def module_files(root: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for path in sorted((root / PACKAGE).rglob("*.py")):
        name = module_name(path, root)
        if name and "__pycache__" not in path.parts:
            found[name] = path
    return found


def name_imports(root: Path) -> dict[str, set[str]]:
    """Module -> q50diag modules it binds names from at import time."""
    files = module_files(root)
    known = set(files)
    graph: dict[str, set[str]] = {}
    for name, path in files.items():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, ValueError) as exc:
            log.warning("import graph: cannot parse %s: %s", path, exc)
            graph[name] = set()
            continue
        collector = _NameImports(name, path.name == "__init__.py", known)
        collector.visit(tree)
        graph[name] = collector.found - {name}
    return graph


def _dependents(graph: dict[str, set[str]]) -> dict[str, set[str]]:
    reverse: dict[str, set[str]] = {}
    for module, imported in graph.items():
        for target in imported:
            reverse.setdefault(target, set()).add(module)
    return reverse


def _closure(changed: set[str], reverse: dict[str, set[str]]) -> set[str]:
    """Changed modules plus every reloadable module that transitively binds names from them."""
    result = set(changed)
    queue = list(changed)
    while queue:
        for dependent in reverse.get(queue.pop(), ()):
            if dependent not in result and rank(dependent) is not None:
                result.add(dependent)
                queue.append(dependent)
    return result


def _reload_order(modules: set[str], graph: dict[str, set[str]]) -> list[str]:
    """Topological order (imported before importer), ties broken by rank then name."""
    pending = {m: {d for d in graph.get(m, ()) if d in modules} for m in modules}
    order: list[str] = []
    while pending:
        ready = sorted((m for m, deps in pending.items() if not deps), key=lambda m: (rank(m), m))
        if not ready:  # import cycle: fall back to rank order for the rest
            ready = sorted(pending, key=lambda m: (rank(m), m))
        for module in ready:
            order.append(module)
            pending.pop(module)
        for deps in pending.values():
            deps.difference_update(ready)
    return order


def _restart_notes(modules: set[str], reverse: dict[str, set[str]]) -> list[str]:
    notes: list[str] = []
    for module in sorted(modules):
        for dependent in sorted(reverse.get(module, ())):
            if rank(dependent) is not None:
                continue
            if dependent == MAIN_WINDOW and _within(module, PAGES_PACKAGE):
                continue  # rebuild_page looks the page class up in the reloaded module
            notes.append(f"{dependent} imported names from {module} at startup; that use keeps the old code until restart")
    return notes


# Plan -------------------------------------------------------------------------
@dataclass(frozen=True)
class ReloadPlan:
    live_modules: tuple[str, ...] = ()  # reload order, dependents included
    reconnect_modules: tuple[str, ...] = ()  # transport modules, reload order
    restart_files: tuple[str, ...] = ()
    ignored_files: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def kind(self) -> str:
        """"restart", "reconnect", "live" or "none": the strongest effect needed."""
        if self.restart_files:
            return "restart"
        if self.reconnect_modules:
            return "reconnect"
        if self.live_modules:
            return "live"
        return "none"


def _relative(path: str | Path, root: Path) -> tuple[Path, str]:
    candidate = Path(path)
    absolute = candidate if candidate.is_absolute() else root / candidate
    absolute = Path(*absolute.parts)  # normalise separators without resolving symlinks
    try:
        text = absolute.relative_to(root).as_posix()
    except ValueError:
        text = str(candidate)
    return absolute, text


def classify(files: Iterable[str | Path], project_root: Path = PROJECT_ROOT) -> ReloadPlan:
    """Sort changed files (absolute or relative to project_root) into a ReloadPlan."""
    root = Path(project_root).resolve()
    changed: set[str] = set()
    restart: list[str] = []
    ignored: list[str] = []
    for item in files:
        path, text = _relative(item, root)
        name = module_name(path, root)
        if name is None:
            ignored.append(text)
        elif rank(name) is None or not path.exists():
            restart.append(text)
        else:
            changed.add(name)
    graph = name_imports(root)
    reverse = _dependents(graph)
    modules = _closure(changed, reverse)
    order = _reload_order(modules, graph)
    return ReloadPlan(
        live_modules=tuple(m for m in order if not _within(m, TRANSPORT_PACKAGE)),
        reconnect_modules=tuple(m for m in order if _within(m, TRANSPORT_PACKAGE)),
        restart_files=tuple(sorted(set(restart))),
        ignored_files=tuple(sorted(set(ignored))),
        notes=tuple(_restart_notes(modules, reverse)),
    )


# Report -----------------------------------------------------------------------
@dataclass(frozen=True)
class ReloadReport:
    kind: str
    reloaded: tuple[str, ...] = ()
    pages_rebuilt: tuple[str, ...] = ()
    reconnect_port: str | None = None
    restart_files: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    def text(self) -> str:
        lines = [f"Reload: {self.kind}"]
        if self.reloaded:
            lines.append("Reloaded modules: " + ", ".join(self.reloaded))
        if self.pages_rebuilt:
            lines.append("Rebuilt pages: " + ", ".join(self.pages_rebuilt))
        if self.reconnect_port:
            lines.append(f"Reconnecting to {self.reconnect_port}; recording continues with a marker")
        if self.restart_files:
            lines.append("Restart required for: " + ", ".join(self.restart_files))
        lines += [f"Note: {note}" for note in self.notes]
        if self.error:
            lines.append(f"Reload failed: {self.error}")
        return "\n".join(lines)


# Reloader ---------------------------------------------------------------------
def _drop_bytecode(module) -> None:
    """Remove cached bytecode so an edit within the same second as the last
    compile (pyc stores mtime in whole seconds) is not masked."""
    source = getattr(module, "__file__", None)
    if not source or not source.endswith(".py"):
        return
    try:
        Path(importlib.util.cache_from_source(source)).unlink(missing_ok=True)
    except (OSError, NotImplementedError, ValueError):
        pass  # no cache to drop; reload still compiles from source when mtimes differ


def reload_modules(names: Iterable[str]) -> tuple[list[str], str]:
    """importlib.reload each loaded module in order. Modules not loaded yet are
    skipped (their first import reads the new source). Stops at the first
    failure and returns (reloaded, error text)."""
    importlib.invalidate_caches()
    reloaded: list[str] = []
    for name in names:
        module = sys.modules.get(name)
        if module is None:
            continue
        _drop_bytecode(module)
        try:
            importlib.reload(module)
        except Exception:  # noqa: BLE001; reported to the agent with the traceback
            log.exception("reload of %s failed", name)
            return reloaded, f"{name}: {traceback.format_exc(limit=4)}"
        reloaded.append(name)
    return reloaded, ""


def page_titles(module_names: Iterable[str]) -> list[str]:
    """Titles of the Page classes defined in the given (reloaded) page modules."""
    titles: list[str] = []
    for name in module_names:
        module = sys.modules.get(name)
        if module is None or not _within(name, PAGES_PACKAGE):
            continue
        for obj in vars(module).values():
            if not (isinstance(obj, type) and issubclass(obj, QWidget) and obj.__module__ == name):
                continue
            title = getattr(obj, "title", "")
            if isinstance(title, str) and title and title not in titles:
                titles.append(title)
    return titles


class HotReloader:
    """Applies ReloadPlans to the running app. `window` exposes
    `rebuild_page(title)` and a `pages` dict; `ctx` is the AppContext."""

    def __init__(self, window, ctx, reconnect_timeout_ms: int = RECONNECT_TIMEOUT_MS) -> None:
        self.window = window
        self.ctx = ctx
        self.reconnect_timeout_ms = reconnect_timeout_ms
        self._watch: _ReconnectWatch | None = None

    def apply(self, plan: ReloadPlan, label: str = "") -> ReloadReport:
        """Apply the plan on the Qt main thread (safe to call from any thread).
        `label`, when given, is written as an event marker before any reconnect."""
        return call_in_main_thread(self._apply, plan, label, timeout_s=APPLY_TIMEOUT_S)

    def _apply(self, plan: ReloadPlan, label: str) -> ReloadReport:
        notes = list(plan.notes)
        reloaded, error = reload_modules(plan.live_modules)
        if any(_within(m, p) for m in reloaded for p in CATALOG_INPUTS):
            self.ctx.rebuild_catalog()
            self._refresh_subscriptions()
        pages = self._rebuild_pages(reloaded, notes)
        if label:
            self.ctx.mark_event(label)
        port = None
        if plan.reconnect_modules:
            port, transport_reloaded, transport_error = self._reload_transport(plan.reconnect_modules, notes)
            reloaded += transport_reloaded
            error = error or transport_error
        return ReloadReport(
            kind=plan.kind,
            reloaded=tuple(reloaded),
            pages_rebuilt=tuple(pages),
            reconnect_port=port,
            restart_files=plan.restart_files,
            notes=tuple(notes),
            error=error,
        )

    def _refresh_subscriptions(self) -> None:
        """Point active polling at the rebuilt catalog's parameter definitions,
        so a decoder fix applies to values already being polled."""
        session = self.ctx.session
        for owner, subscriptions in session.polling_snapshot().items():
            fresh = []
            for module, param in subscriptions:
                entry = self.ctx.entry(param.key)
                fresh.append((module, entry.param) if entry is not None else (module, param))
            session.start_polling(owner, fresh)

    def _rebuild_pages(self, reloaded: list[str], notes: list[str]) -> list[str]:
        titles = page_titles(reloaded)
        if not titles:
            return []
        if self.window is None or not hasattr(self.window, "rebuild_page"):
            notes.append("no main window to rebuild pages: " + ", ".join(titles))
            return []
        present = getattr(self.window, "pages", None)
        rebuilt: list[str] = []
        for title in titles:
            if present is not None and title not in present:
                continue
            try:
                self.window.rebuild_page(title)
            except Exception as exc:  # noqa: BLE001; the old page stays in place
                log.exception("rebuild of page %s failed", title)
                notes.append(f"page {title} was not rebuilt: {exc}")
                continue
            rebuilt.append(title)
        return rebuilt

    def _reload_transport(self, modules: tuple[str, ...], notes: list[str]) -> tuple[str | None, list[str], str]:
        """Disconnect, reload the transport modules, reconnect the same port and
        restore polling. The recorder is not touched, so recording continues."""
        session = self.ctx.session
        port = session.port if session.connected else None
        if port is None:
            reloaded, error = reload_modules(modules)
            notes.append("not connected: the new transport code is used at the next connect")
            return None, reloaded, error
        subscriptions = session.polling_snapshot()
        monitor_on, monitor_ids = session.bus_monitor_active, session.bus_monitor_ids
        session.disconnect_port()
        self._wait_disconnected(session)
        reloaded, error = reload_modules(modules)
        self._watch_reconnect(port)
        session.connect_port(port)
        for owner, subs in subscriptions.items():
            session.start_polling(owner, subs)
        if monitor_on:
            session.start_bus_monitor(monitor_ids)
        return port, reloaded, error

    def _wait_disconnected(self, session) -> None:
        """Let the worker finish the request in flight before its module code is replaced."""
        deadline = time.monotonic() + DISCONNECT_WAIT_S
        while session.connected and time.monotonic() < deadline:
            time.sleep(0.01)

    def _watch_reconnect(self, port: str) -> None:
        if self._watch is not None:
            self._watch.cancel()
        self._watch = _ReconnectWatch(self.ctx, port, self.reconnect_timeout_ms)


class _ReconnectWatch:
    """Writes the reconnect marker on the next successful connect, or reports a timeout."""

    def __init__(self, ctx, port: str, timeout_ms: int) -> None:
        self.ctx = ctx
        self.port = port
        self.started = time.monotonic()
        self.active = True
        self.timer = QTimer()
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._timed_out)
        ctx.session.connection_changed.connect(self._on_connection)
        self.timer.start(timeout_ms)

    def cancel(self) -> None:
        if not self.active:
            return
        self.active = False
        self.timer.stop()
        try:
            self.ctx.session.connection_changed.disconnect(self._on_connection)
        except (RuntimeError, TypeError):
            pass  # already disconnected

    def _on_connection(self, connected: bool, _info) -> None:
        if not connected or not self.active:
            return
        self.cancel()
        elapsed = time.monotonic() - self.started
        self.ctx.mark_event(RECONNECT_MARKER)
        self.ctx.notify(f"Reconnected to {self.port} after code change in {elapsed:.1f} s", "ok")

    def _timed_out(self) -> None:
        if not self.active:
            return
        self.cancel()
        self.ctx.notify(
            f"Reconnect to {self.port} after code change did not finish; reconnect from the Connect page",
            "error",
        )
