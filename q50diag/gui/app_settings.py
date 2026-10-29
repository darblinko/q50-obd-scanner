"""Application settings: last vehicle, adapter and CAN bus, stored as JSON.

Written atomically (temporary file then rename) to ~/.config/q50diag/app.json.
A missing, unreadable or partly invalid file falls back to the defaults field
by field; unknown keys are validated by the caller against the known tables."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_PATH = Path.home() / ".config" / "q50diag" / "app.json"

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AppSettings:
    vehicle: str = "q50_2017"
    adapter: str = "vlinker_fs"
    bus: str = "hs"


def settings_path(path: Path | None = None) -> Path:
    return Path(path) if path else DEFAULT_PATH


def load_app_settings(path: Path | None = None) -> AppSettings:
    target = settings_path(path)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return AppSettings()
    except (OSError, ValueError) as exc:
        log.warning("App settings at %s unreadable, using defaults: %s", target, exc)
        return AppSettings()
    if not isinstance(raw, dict):
        log.warning("App settings at %s are not a JSON object, using defaults", target)
        return AppSettings()
    defaults = AppSettings()
    values = {name: raw.get(name) for name in asdict(defaults)}
    return AppSettings(
        **{
            name: value if isinstance(value, str) and value else getattr(defaults, name)
            for name, value in values.items()
        }
    )


def save_app_settings(settings: AppSettings, path: Path | None = None) -> Path:
    target = settings_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".app-", suffix=".json", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(asdict(settings), handle, indent=2)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return target
