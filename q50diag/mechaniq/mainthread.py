"""Run a callable on the Qt main thread from any worker thread.

MechanIQ tool handlers run on the Agent SDK's asyncio thread, but the
session, context and widgets belong to the Qt main thread. A hidden QObject
with main-thread affinity receives each job through a queued signal; the
calling thread waits on a Future. The main thread never waits on a worker, so
this cannot deadlock it; a worker that waits too long gets TimeoutError."""

from __future__ import annotations

import threading
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any, Callable

from PySide6.QtCore import QCoreApplication, QObject, Qt, QThread, Signal

_invoker: "_Invoker | None" = None
_invoker_lock = threading.Lock()


class _Invoker(QObject):
    job = Signal(object)  # zero-argument callable

    def __init__(self) -> None:
        super().__init__()
        self.job.connect(self._run, Qt.ConnectionType.QueuedConnection)

    def _run(self, job: Callable[[], None]) -> None:
        job()


def _main_thread() -> QThread:
    app = QCoreApplication.instance()
    if app is None:
        raise RuntimeError("no Qt application is running")
    return app.thread()


def _get_invoker(main: QThread) -> _Invoker:
    global _invoker
    with _invoker_lock:
        if _invoker is None:
            invoker = _Invoker()
            if invoker.thread() != main:
                invoker.moveToThread(main)
            _invoker = invoker
        return _invoker


def call_in_main_thread(fn: Callable[..., Any], *args: Any, timeout_s: float = 10.0) -> Any:
    """Run fn(*args) on the Qt main thread and return its result, re-raising
    its exception in the caller. Raises TimeoutError when the main thread does
    not run the job within timeout_s; the job is then skipped if not started."""
    main = _main_thread()
    if QThread.currentThread() == main:
        return fn(*args)
    future: Future = Future()

    def job() -> None:
        if not future.set_running_or_notify_cancel():
            return
        try:
            future.set_result(fn(*args))
        except BaseException as exc:  # noqa: BLE001; handed back to the waiting thread
            future.set_exception(exc)

    _get_invoker(main).job.emit(job)
    try:
        return future.result(timeout=timeout_s)
    except FutureTimeout as exc:
        future.cancel()
        name = getattr(fn, "__name__", repr(fn))
        raise TimeoutError(f"the app did not run {name} within {timeout_s:.0f} s") from exc
