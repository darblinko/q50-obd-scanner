"""MechanIQ settings: JSON round trip, file mode, the child process environment,
packaged (API key only) behavior and API key input validation."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
import sys

import pytest

from q50diag.gui.mechaniq import PROJECT_ROOT
from q50diag.mechaniq import settings as settings_module
from q50diag.mechaniq.guard import make_guard
from q50diag.mechaniq.settings import (
    API_KEY,
    API_KEY_PAGE,
    DEFAULT_MODEL,
    DEFAULT_PATH,
    MODELS,
    SUBSCRIPTION,
    MechaniqSettings,
    MissingApiKeyError,
    api_key_warning,
    child_env,
    clean_api_key,
    is_packaged,
    load_settings,
    needs_api_key,
    save_settings,
)

BASE_ENV = {
    "HOME": "/home/tester",
    "PATH": "/usr/bin",
    "ANTHROPIC_API_KEY": "sk-inherited",
    "ANTHROPIC_AUTH_TOKEN": "token-inherited",
}


@pytest.fixture
def packaged(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)


@pytest.fixture
def development(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)


def test_defaults():
    settings = MechaniqSettings()
    assert settings.auth_mode == "subscription"
    assert settings.api_key == ""
    assert settings.model == "claude-opus-5-5" == DEFAULT_MODEL
    assert MODELS == ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1")


# Windows keeps only a read-only bit; the user profile ACL protects the file there.
posix_modes_only = pytest.mark.skipif(sys.platform == "win32", reason="Windows has no POSIX file modes")


def test_save_load_round_trip(tmp_path):
    path = tmp_path / "nested" / "mechaniq.json"
    settings = MechaniqSettings(auth_mode=API_KEY, api_key="sk-test", model="claude-sonnet-5-5")
    written = save_settings(settings, path)
    assert written == path
    assert load_settings(path) == settings
    assert json.loads(path.read_text())["model"] == "claude-sonnet-5-5"


@posix_modes_only
def test_save_writes_mode_0600(tmp_path):
    path = save_settings(MechaniqSettings(api_key="sk-test"), tmp_path / "nested" / "mechaniq.json")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@posix_modes_only
def test_save_tightens_existing_file_mode(tmp_path):
    path = tmp_path / "mechaniq.json"
    path.write_text("{}")
    path.chmod(0o644)
    save_settings(MechaniqSettings(), path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_load_missing_file_gives_defaults(tmp_path):
    assert load_settings(tmp_path / "absent.json") == MechaniqSettings()


@pytest.mark.parametrize("content", ["not json", "[1, 2]", '{"auth_mode": "bogus", "model": 5, "api_key": 3}'])
def test_load_bad_content_gives_defaults(tmp_path, content):
    path = tmp_path / "mechaniq.json"
    path.write_text(content)
    assert load_settings(path) == MechaniqSettings()


def test_child_env_subscription_strips_credentials_and_keeps_home():
    env = child_env(MechaniqSettings(), BASE_ENV)
    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert env["HOME"] == "/home/tester"
    assert env["PATH"] == "/usr/bin"
    assert BASE_ENV["ANTHROPIC_API_KEY"] == "sk-inherited"


def test_child_env_api_key_mode_sets_key():
    env = child_env(MechaniqSettings(auth_mode=API_KEY, api_key=" sk-mine "), BASE_ENV)
    assert env["ANTHROPIC_API_KEY"] == "sk-mine"
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert env["HOME"] == "/home/tester"


def test_child_env_api_key_mode_without_key_raises():
    with pytest.raises(ValueError, match="API key"):
        child_env(MechaniqSettings(auth_mode=API_KEY, api_key="  "), BASE_ENV)


def test_child_env_defaults_to_process_environment(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-process")
    monkeypatch.setenv("MECHANIQ_TEST_MARKER", "1")
    env = child_env(MechaniqSettings())
    assert "ANTHROPIC_API_KEY" not in env
    assert env["MECHANIQ_TEST_MARKER"] == "1"


# Packaged build: API key only ------------------------------------------------------
def test_packaged_default_auth_mode_is_api_key(packaged, tmp_path):
    assert is_packaged()
    assert MechaniqSettings().auth_mode == API_KEY
    assert load_settings(tmp_path / "absent.json").auth_mode == API_KEY


def test_packaged_load_treats_saved_subscription_as_api_key(packaged, tmp_path):
    path = tmp_path / "mechaniq.json"
    path.write_text(json.dumps({"auth_mode": "subscription", "api_key": "sk-ant-kept", "model": "claude-sonnet-5-5"}))
    assert load_settings(path) == MechaniqSettings(API_KEY, "sk-ant-kept", "claude-sonnet-5-5")


def test_development_keeps_subscription_default_and_saved_value(development, tmp_path):
    path = tmp_path / "mechaniq.json"
    path.write_text(json.dumps({"auth_mode": "subscription"}))
    assert not is_packaged()
    assert MechaniqSettings().auth_mode == SUBSCRIPTION
    assert load_settings(path).auth_mode == SUBSCRIPTION


def test_packaged_child_env_uses_api_key_for_subscription_settings(packaged):
    env = child_env(MechaniqSettings(auth_mode=SUBSCRIPTION, api_key="sk-ant-mine"), BASE_ENV)
    assert env["ANTHROPIC_API_KEY"] == "sk-ant-mine" and "ANTHROPIC_AUTH_TOKEN" not in env
    with pytest.raises(MissingApiKeyError):
        child_env(MechaniqSettings(auth_mode=SUBSCRIPTION), BASE_ENV)


def test_needs_api_key_development(development):
    assert not needs_api_key(MechaniqSettings(auth_mode=SUBSCRIPTION))
    assert needs_api_key(MechaniqSettings(auth_mode=API_KEY))
    assert needs_api_key(MechaniqSettings(auth_mode=API_KEY, api_key="  \n"))
    assert not needs_api_key(MechaniqSettings(auth_mode=API_KEY, api_key="sk-ant-x"))


def test_needs_api_key_packaged_ignores_subscription(packaged):
    assert needs_api_key(MechaniqSettings(auth_mode=SUBSCRIPTION))
    assert not needs_api_key(MechaniqSettings(auth_mode=SUBSCRIPTION, api_key="sk-ant-x"))


def test_missing_key_error_is_a_value_error():
    assert issubclass(MissingApiKeyError, ValueError)


def test_api_key_page_is_the_console_keys_page():
    assert API_KEY_PAGE == "https://platform.claude.com/settings/keys"


# API key input -----------------------------------------------------------------------
def test_clean_api_key_trims_whitespace():
    assert clean_api_key("  sk-ant-api03-abc \n") == "sk-ant-api03-abc"


@pytest.mark.parametrize("raw", ["", "   ", "\t\n", None])
def test_clean_api_key_rejects_empty(raw):
    with pytest.raises(ValueError, match="Enter an API key"):
        clean_api_key(raw)


def test_api_key_warning_only_for_unexpected_prefix():
    assert api_key_warning("sk-ant-api03-abc") == ""
    assert api_key_warning("  sk-ant-api03-abc ") == ""
    assert "sk-ant-" in api_key_warning("sk-proj-abc")
    assert "sk-ant-" in api_key_warning("abc")


# Storage -------------------------------------------------------------------------
def test_save_succeeds_when_chmod_is_unsupported(tmp_path, monkeypatch, caplog):
    def refuse(*_args, **_kwargs):
        raise OSError("operation not supported")

    monkeypatch.setattr(settings_module.os, "chmod", refuse)
    path = tmp_path / "mechaniq.json"
    settings = MechaniqSettings(auth_mode=API_KEY, api_key="sk-ant-x")
    with caplog.at_level(logging.WARNING, logger=settings_module.__name__):
        assert save_settings(settings, path) == path
    assert load_settings(path) == settings
    assert "0600" in caplog.text


def test_save_with_noop_chmod_like_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_module.os, "chmod", lambda *_args, **_kwargs: None)
    path = tmp_path / "profile" / ".config" / "q50diag" / "mechaniq.json"
    settings = MechaniqSettings(auth_mode=API_KEY, api_key="sk-ant-x")
    save_settings(settings, path)
    save_settings(settings, path)
    assert load_settings(path) == settings


def test_default_path_is_under_home_and_outside_the_project():
    assert DEFAULT_PATH.is_relative_to(os.path.expanduser("~"))
    assert not DEFAULT_PATH.resolve().is_relative_to(PROJECT_ROOT.resolve())


@pytest.mark.parametrize(
    "tool_name, tool_input",
    [
        ("Read", {"file_path": str(DEFAULT_PATH)}),
        ("Grep", {"pattern": "api_key", "path": str(DEFAULT_PATH)}),
        ("Bash", {"command": f"cat {DEFAULT_PATH}"}),
        ("Bash", {"command": "cat ~/.config/q50diag/*"}),
        ("Bash", {"command": "cat ~/.config/q50diag/mech*.json"}),
        ("Grep", {"pattern": "api_key", "path": str(DEFAULT_PATH.parent.parent)}),
        ("Write", {"file_path": str(DEFAULT_PATH), "content": "{}"}),
    ],
)
def test_guard_blocks_the_agent_from_the_settings_file(tool_name, tool_input):
    hook = make_guard(PROJECT_ROOT, DEFAULT_PATH)
    data = {"hook_event_name": "PreToolUse", "cwd": str(PROJECT_ROOT), "tool_name": tool_name,
            "tool_input": tool_input}
    result = asyncio.run(hook(data, "toolu_1", {"signal": None}))
    decision = result["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert "settings file" in decision["permissionDecisionReason"]
