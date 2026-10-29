"""MechanIQ subagent prompt hints for a source checkout and a packaged (frozen) build."""

from __future__ import annotations

import sys

import pytest

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


@pytest.mark.parametrize("name", ["data-analyst", "test-coach"])
@pytest.mark.parametrize("tool", ["Edit", "Write", "NotebookEdit", "mcp__q50__apply_change", "mcp__q50__undo_change"])
def test_read_only_subagents_cannot_edit_or_apply_changes(name, tool):
    assert tool in prompt.AGENTS[name].disallowedTools


def test_test_coach_has_no_bash():
    assert "Bash" in prompt.AGENTS["test-coach"].disallowedTools


def test_data_analyst_keeps_bash_for_statistics():
    assert "Bash" not in prompt.AGENTS["data-analyst"].disallowedTools
    assert "read-only Python in Bash" in prompt.DATA_ANALYST_PROMPT


def test_code_fixer_keeps_edit_tools_and_bash():
    assert prompt.AGENTS["code-fixer"].disallowedTools is None
