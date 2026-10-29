"""MechanIQ settings: auth mode, API key and model, stored as JSON.

The file holds an API key, so it is written with mode 0600 (on Windows the
mode is mostly ignored and the file keeps the ACL of the user profile). It
lives under the home directory, outside the project tree that MechanIQ edits
and commits.

In a development run, subscription mode is the default: the child Claude Code
process gets no API credentials from the environment and uses the login in
~/.claude instead. A packaged build (PyInstaller, `sys.frozen`) is API key
only, because apps built on the Agent SDK may not offer claude.ai login."""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path

SUBSCRIPTION = "subscription"
API_KEY = "api_key"
AUTH_MODES = (SUBSCRIPTION, API_KEY)
MODELS = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1")
DEFAULT_MODEL = MODELS[0]
AUTH_ENV_KEYS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
DEFAULT_PATH = Path.home() / ".config" / "q50diag" / "mechaniq.json"
API_KEY_PREFIX = "sk-ant-"
# From https://platform.claude.com/docs/en/get-api-key ("go to the API keys page").
API_KEY_PAGE = "https://platform.claude.com/settings/keys"

log = logging.getLogger(__name__)


class MissingApiKeyError(ValueError):
    """API key mode is in effect but no key is set."""


def is_packaged() -> bool:
    """True in a PyInstaller build."""
    return bool(getattr(sys, "frozen", False))


def default_auth_mode() -> str:
    return API_KEY if is_packaged() else SUBSCRIPTION


def effective_auth_mode(auth_mode: object) -> str:
    """The mode that is used: a packaged build always uses API key mode, and an
    unknown value falls back to the default."""
    if is_packaged():
        return API_KEY
    return auth_mode if auth_mode in AUTH_MODES else SUBSCRIPTION


@dataclass(frozen=True)
class MechaniqSettings:
    auth_mode: str = field(default_factory=default_auth_mode)
    api_key: str = ""
    model: str = DEFAULT_MODEL


def needs_api_key(settings) -> bool:
    """True when API key mode is in effect and no key is set."""
    return effective_auth_mode(settings.auth_mode) == API_KEY and not settings.api_key.strip()


def clean_api_key(raw: str) -> str:
    """The key with surrounding whitespace removed. Raises ValueError when empty."""
    key = (raw or "").strip()
    if not key:
        raise ValueError("Enter an API key.")
    return key


def api_key_warning(key: str) -> str:
    """A warning for a key that does not look like an Anthropic API key, or ""."""
    if key.strip().startswith(API_KEY_PREFIX):
        return ""
    return (
        f"This does not look like an Anthropic API key. Keys start with {API_KEY_PREFIX}. "
        "Check that the whole key was copied."
    )


def load_settings(path: Path | None = None) -> MechaniqSettings:
    """Read settings from `path`; a missing or unreadable file gives defaults."""
    target = Path(path) if path else DEFAULT_PATH
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return MechaniqSettings()
    except (OSError, ValueError) as exc:
        log.warning("MechanIQ settings at %s unreadable, using defaults: %s", target, exc)
        return MechaniqSettings()
    if not isinstance(raw, dict):
        log.warning("MechanIQ settings at %s are not a JSON object, using defaults", target)
        return MechaniqSettings()
    return _from_dict(raw)


def _from_dict(raw: Mapping[str, object]) -> MechaniqSettings:
    defaults = MechaniqSettings()
    api_key = raw.get("api_key", defaults.api_key)
    model = raw.get("model", defaults.model)
    return MechaniqSettings(
        auth_mode=effective_auth_mode(raw.get("auth_mode", defaults.auth_mode)),
        api_key=api_key if isinstance(api_key, str) else defaults.api_key,
        model=model if isinstance(model, str) and model else defaults.model,
    )


def save_settings(settings: MechaniqSettings, path: Path | None = None) -> Path:
    """Write settings as JSON with mode 0600 and return the path written."""
    target = Path(path) if path else DEFAULT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    # Create with 0600 from the start so the key is never world-readable, even briefly.
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(asdict(settings), handle, indent=2)
    _restrict_to_owner(target)
    return target


def _restrict_to_owner(target: Path) -> None:
    # Tightens a file that already existed with a wider mode. On Windows chmod
    # only toggles the read-only flag; the file keeps the user profile ACL.
    try:
        os.chmod(target, 0o600)
    except OSError as exc:
        log.warning("Could not set mode 0600 on %s: %s", target, exc)


def child_env(settings: MechaniqSettings, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Environment for the Claude Code child process.

    Subscription mode removes every API credential so the CLI falls back to the
    login stored under HOME. API key mode sets ANTHROPIC_API_KEY from settings.
    A packaged build always uses API key mode."""
    source = dict(os.environ if base is None else base)
    env = {key: value for key, value in source.items() if key not in AUTH_ENV_KEYS}
    auth_mode = API_KEY if is_packaged() else settings.auth_mode
    if auth_mode == API_KEY:
        if not settings.api_key.strip():
            raise MissingApiKeyError("API key mode is selected but no API key is set; enter one in MechanIQ settings")
        env["ANTHROPIC_API_KEY"] = settings.api_key.strip()
    elif auth_mode != SUBSCRIPTION:
        raise ValueError(f"unknown auth mode {settings.auth_mode!r}")
    return env
