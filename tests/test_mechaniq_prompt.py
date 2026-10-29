"""MechanIQ subagent prompt hints for a source checkout and a packaged (frozen) build."""

from __future__ import annotations

import sys

from q50diag.mechaniq import prompt


def test_source_checkout_hints_use_the_venv_python(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert prompt._pytest_hint() == ".venv/bin/python -m pytest -q"
    assert "read-only Python in Bash" in prompt._statistics_hint()


def test_frozen_hints_do_not_assume_python(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert prompt._pytest_hint() == f'"{sys.executable}" --run-pytest -q'
    hint = prompt._statistics_hint()
    assert "do not run python from Bash" in hint
    assert "read_recording" in hint and "get_readings" in hint
