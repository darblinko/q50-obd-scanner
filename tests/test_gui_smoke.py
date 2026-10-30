"""The hidden smoke run must fail when its diagnostic operations fail."""

from __future__ import annotations

import pytest

from q50diag.gui import smoke
from q50diag.gui.main_window import MainWindow
from tests.test_gui_fakes import FakeBackend, app, ctx, wait_until  # noqa: F401 (fixtures)


class BrokenBackend(FakeBackend):
    def read_parameter(self, t, module, param):
        raise RuntimeError("adapter fell over")


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
