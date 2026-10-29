"""Shared pytest configuration."""

from __future__ import annotations

import sys

import pytest


@pytest.fixture(autouse=True)
def delete_top_level_widgets():
    """Delete the parentless widgets a test created once it finishes.

    Tests build pages, windows and dialogs without parents and never delete
    them. Left alive, their timers keep firing for the rest of the run, and at
    interpreter exit PySide destroys them in arbitrary order, which segfaults
    or aborts ("pure virtual method called") after pytest prints its summary.
    """
    yield
    qt_widgets = sys.modules.get("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() if qt_widgets is not None else None
    if app is None:
        return
    import shiboken6
    from PySide6.QtCore import QCoreApplication, QEvent

    # Only widgets Python created: Qt and pyqtgraph also make hidden parentless
    # helpers that their owners delete, and deleting those here would double free.
    for widget in app.topLevelWidgets():
        if widget.parent() is None and shiboken6.createdByPython(widget):
            widget.close()
            widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture(autouse=True)
def isolated_favorites(tmp_path, monkeypatch):
    """Point the default favorites file into tmp so pages built without an
    explicit path never read or write ~/.config/q50diag during tests."""
    from q50diag.gui import favorites

    monkeypatch.setattr(favorites, "DEFAULT_PATH", tmp_path / "default-favorites" / "favorites.json")


def pytest_configure(config):
    config.addinivalue_line("markers", "keep_frozen: keep sys.frozen as the packaged app set it")


@pytest.fixture(autouse=True)
def source_checkout_mode(request, monkeypatch):
    """Run every test as a source checkout, also inside the packaged app.

    MechanIQ runs this suite through `<exe> --run-pytest`, where sys.frozen is
    True. Tests that need the real value carry the keep_frozen marker."""
    if request.node.get_closest_marker("keep_frozen") is None:
        monkeypatch.delattr(sys, "frozen", raising=False)
