"""Offscreen tests for the MechanIQ API key experience: the packaged (API key
only) settings dialog, key validation and masking, the first-run prompt in
the panel and the page, and where settings may be saved."""

from __future__ import annotations

import sys

import pytest
from PySide6.QtWidgets import QDialog, QLineEdit

from q50diag.gui.mechaniq.controller import MechaniqController
from q50diag.gui.mechaniq.model import MechaniqModel
from q50diag.gui.mechaniq.page import MechaniqPage
from q50diag.gui.mechaniq.panel import MechaniqPanel
from q50diag.gui.mechaniq.settings_dialog import AUTH_NOTE, PACKAGED_NOTE, MechaniqSettingsDialog
from q50diag.mechaniq.settings import API_KEY_PAGE
from tests.test_gui_fakes import app, ctx  # noqa: F401
from tests.test_gui_mechaniq import FakeRunner, Settings

MODELS = ("claude-opus-5-5", "claude-sonnet-5-5")
NO_KEY = Settings("api_key", "")
WITH_KEY = Settings("api_key", "sk-ant-api03-abc")


@pytest.fixture
def packaged(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)


@pytest.fixture
def development(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)


def controller_with(settings, project_root=None, settings_path=None):
    controller = MechaniqController(MechaniqModel(), project_root=project_root)
    runner = FakeRunner()
    controller.set_runner(runner)
    controller.set_settings(settings, settings_path)
    return controller, runner


def record_open_settings(controller) -> list:
    calls: list = []
    controller.open_settings = lambda parent=None: calls.append(parent)
    return calls


# Settings dialog -----------------------------------------------------------------
def test_packaged_dialog_is_api_key_only(app, packaged):
    dialog = MechaniqSettingsDialog(Settings(), MODELS)
    assert dialog.packaged
    assert not dialog.subscription_radio.isVisibleTo(dialog) and not dialog.api_radio.isVisibleTo(dialog)
    assert dialog.key_edit.isVisibleTo(dialog) and dialog.key_edit.isEnabled()
    assert dialog.note_label.text() == PACKAGED_NOTE and "~/.claude" not in dialog.note_label.text()
    assert API_KEY_PAGE in dialog.key_help_label.text()
    assert dialog.error_label.text() == "Enter an API key." and not dialog.save_button.isEnabled()
    dialog.key_edit.setText("  sk-ant-api03-abc \n")
    assert dialog.save_button.isEnabled() and dialog.error_label.text() == ""
    assert dialog.result_settings() == Settings("api_key", "sk-ant-api03-abc", "claude-opus-5-5")


def test_development_dialog_keeps_both_modes(app, development):
    dialog = MechaniqSettingsDialog(Settings(), MODELS)
    assert not dialog.packaged
    assert dialog.subscription_radio.isVisibleTo(dialog) and dialog.subscription_radio.isChecked()
    assert dialog.note_label.text() == AUTH_NOTE
    assert dialog.result_settings() == Settings()


def test_dialog_rejects_empty_key_and_does_not_close(app):
    dialog = MechaniqSettingsDialog(NO_KEY, MODELS, packaged=True)
    dialog.key_edit.setText("    ")
    assert not dialog.save_button.isEnabled() and dialog.error_label.text() == "Enter an API key."
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Rejected
    dialog.key_edit.setText("sk-ant-api03-abc")
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_dialog_warns_but_allows_unexpected_key_prefix(app):
    dialog = MechaniqSettingsDialog(NO_KEY, MODELS, packaged=False)
    dialog.key_edit.setText(" sk-proj-abc ")
    assert "sk-ant-" in dialog.warning_label.text()
    assert dialog.save_button.isEnabled() and dialog.error_label.text() == ""
    assert dialog.result_settings().api_key == "sk-proj-abc"
    dialog.key_edit.setText("sk-ant-api03-abc")
    assert dialog.warning_label.text() == ""
    dialog.subscription_radio.setChecked(True)
    dialog.key_edit.setText("")
    assert dialog.warning_label.text() == "" and dialog.save_button.isEnabled()


def test_dialog_masks_key_with_show_hide_toggle(app):
    dialog = MechaniqSettingsDialog(WITH_KEY, MODELS, packaged=True)
    assert dialog.key_edit.echoMode() == QLineEdit.EchoMode.Password
    assert dialog.show_key_button.text() == "Show"
    dialog.show_key_button.click()
    assert dialog.key_edit.echoMode() == QLineEdit.EchoMode.Normal
    assert dialog.show_key_button.text() == "Hide"
    dialog.show_key_button.click()
    assert dialog.key_edit.echoMode() == QLineEdit.EchoMode.Password
    assert dialog.show_key_button.text() == "Show"


# First-run prompt ------------------------------------------------------------------
def test_panel_prompts_for_key_and_opens_settings(app):
    controller, _runner = controller_with(NO_KEY)
    calls = record_open_settings(controller)
    panel = MechaniqPanel(controller)
    assert panel.key_prompt.isVisibleTo(panel)
    assert not panel.input.isEnabled() and not panel.send_button.isEnabled()
    panel.key_prompt.add_key_button.click()
    assert len(calls) == 1
    controller.save_and_apply(WITH_KEY, lambda settings, path: None)
    assert not panel.key_prompt.isVisibleTo(panel)
    assert panel.input.isEnabled() and panel.send_button.isEnabled()


def test_page_prompts_for_key_and_opens_settings(ctx):
    controller, _runner = controller_with(NO_KEY)
    calls = record_open_settings(controller)
    page = MechaniqPage(ctx)
    page.attach(controller)
    assert page.key_prompt.isVisibleTo(page)
    assert not page.input.isEnabled() and not page.send_button.isEnabled()
    page.key_prompt.add_key_button.click()
    assert len(calls) == 1
    controller.save_and_apply(WITH_KEY, lambda settings, path: None)
    assert not page.key_prompt.isVisibleTo(page) and page.send_button.isEnabled()


def test_send_without_key_reports_instead_of_failing(app):
    controller, runner = controller_with(NO_KEY)
    controller.send("why is steering heavy")
    assert runner.sent == []
    last = controller.model.transcript()[-1]
    assert last.kind == "error" and "API key" in last.text and "ValueError" not in last.text


def test_packaged_subscription_settings_prompt_for_key(app, packaged):
    controller, runner = controller_with(Settings())
    panel = MechaniqPanel(controller)
    assert controller.needs_api_key and panel.key_prompt.isVisibleTo(panel)
    controller.send("hello")
    assert runner.sent == []


def test_development_subscription_has_no_prompt(app, development):
    controller, runner = controller_with(Settings())
    panel = MechaniqPanel(controller)
    assert not controller.needs_api_key and not panel.key_prompt.isVisibleTo(panel)
    controller.send("hello")
    assert runner.sent == ["hello"]


# Saving --------------------------------------------------------------------------
def test_save_refuses_settings_path_inside_the_project(app, tmp_path):
    controller, runner = controller_with(NO_KEY, project_root=tmp_path, settings_path=tmp_path / "mechaniq.json")
    saved: list = []
    controller.save_and_apply(WITH_KEY, lambda settings, path: saved.append(settings))
    assert saved == [] and runner.settings is None and controller.settings == NO_KEY
    assert "inside the project folder" in controller.model.transcript()[-1].text


def test_save_allows_settings_path_outside_the_project(app, tmp_path):
    settings_path = tmp_path / "config" / "mechaniq.json"
    controller, runner = controller_with(NO_KEY, project_root=tmp_path / "workspace", settings_path=settings_path)
    saved: list = []
    controller.save_and_apply(WITH_KEY, lambda settings, path: saved.append((settings, path)))
    assert saved == [(WITH_KEY, settings_path)] and runner.settings == WITH_KEY
