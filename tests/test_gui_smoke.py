"""The hidden smoke run must fail when its diagnostic operations fail."""

from __future__ import annotations

import pytest

from q50diag.core.types import Reading
from q50diag.gui import smoke
from q50diag.gui.main_window import MainWindow
from tests.test_gui_fakes import FakeBackend, app, ctx, wait_until  # noqa: F401 (fixtures)


class BrokenBackend(FakeBackend):
    def read_parameter(self, t, module, param):
        raise RuntimeError("adapter fell over")


class ErrorReadingBackend(FakeBackend):
    """Every polled read returns Reading.error and no broadcast frame decodes;
    only the adapter voltage succeeds."""

    def read_parameter(self, t, module, param):
        return Reading(param.key, None, param.unit, 0.0, error="NO DATA")

    def decode_frame(self, frame):
        return {}


class EmptyExplorerBackend(FakeBackend):
    def scan_identifiers(self, t, m, service, start, end, progress=None, stop=None):
        return {}


class SilentDtcBackend(FakeBackend):
    def read_dtcs_by_mode(self, t):
        from q50diag.core.transport import NoResponse

        raise NoResponse("NO DATA")

    def read_module_dtcs(self, t, m):
        from q50diag.core.transport import NoResponse

        raise NoResponse("NO DATA")


def _context(backend, tmp_path):
    from q50diag.gui.backend import SIMULATOR
    from q50diag.gui.context import AppContext
    from q50diag.session import Session

    session = Session(backend)
    context = AppContext(session, backend)
    context.recorder.folder = tmp_path
    session.connect_port(SIMULATOR)
    assert wait_until(lambda: session.connected, 10)
    return context


@pytest.fixture
def exit_codes(monkeypatch):
    codes: list[int] = []

    class Instance:
        def exit(self, code: int) -> None:
            codes.append(code)

    class StubApplication:
        @staticmethod
        def instance() -> Instance:
            return Instance()

    monkeypatch.setattr(smoke, "QApplication", StubApplication)
    return codes


def _run(context, exit_codes) -> smoke.SmokeRun:
    window = MainWindow(context, mechaniq_builder=None)
    window.show()
    run = smoke.SmokeRun(window, 4.0, None)
    assert wait_until(lambda: exit_codes, 15), "smoke run did not finish"
    return run


def test_smoke_fails_on_session_errors_and_missing_milestones(app, tmp_path, exit_codes):  # noqa: F811
    from q50diag.gui.backend import SIMULATOR
    from q50diag.gui.context import AppContext
    from q50diag.session import Session

    backend = BrokenBackend()
    session = Session(backend)
    context = AppContext(session, backend)
    context.recorder.folder = tmp_path
    session.connect_port(SIMULATOR)
    assert wait_until(lambda: session.connected, 10)

    run = _run(context, exit_codes)

    assert exit_codes == [1]
    assert run.failures.count("session error: failed: adapter fell over") == 1, run.failures


def test_smoke_fails_when_never_connected(ctx, exit_codes):  # noqa: F811
    ctx.session.disconnect_port()
    assert wait_until(lambda: not ctx.session.connected)

    run = _run(ctx, exit_codes)

    assert exit_codes == [1]
    assert "not connected at the end of the run" in run.failures
    assert "missing milestone: overview" in run.failures


@pytest.mark.parametrize(
    ("backend_type", "milestone"),
    [
        (ErrorReadingBackend, "live reading"),
        (EmptyExplorerBackend, "explorer scan"),
        (SilentDtcBackend, "trouble codes"),
    ],
)
def test_smoke_fails_on_unsuccessful_results(app, tmp_path, exit_codes, backend_type, milestone):  # noqa: F811
    context = _context(backend_type(), tmp_path)
    try:
        run = _run(context, exit_codes)
    finally:
        if context.recorder.is_recording:
            context.stop_recording()
        context.session.shutdown()
    assert exit_codes == [1]
    assert f"missing milestone: {milestone}" in run.failures, run.failures


def test_smoke_requires_a_recording_made_during_the_run(ctx, tmp_path, exit_codes, monkeypatch):  # noqa: F811
    (tmp_path / "old-recording.csv").write_text("time,type,key,name,value,unit,error\n")
    monkeypatch.setattr(ctx, "start_recording", lambda *args, **kwargs: None)

    run = _run(ctx, exit_codes)

    assert exit_codes == [1]
    assert "missing milestone: recording" in run.failures, run.failures
