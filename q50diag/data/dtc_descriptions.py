"""DTC description lookup backed by a user-supplied file.

This repository ships no fault-code descriptions. Descriptions are read from a
text file that the user provides, in this order:

1. the path in the environment variable Q50DIAG_DTC_DESCRIPTIONS;
2. ~/.q50diag/dtc_descriptions.txt.

File format: one `CODE|description` entry per line. Blank lines and lines that
start with `#` are ignored. A line `[generic]`, `[nissan]` or `[ford]` starts a
section; entries before any header belong to `[generic]`. Make-specific
sections override the generic section for that make only, so a manufacturer
code is never labeled with another make's meaning.

Codes without an entry return an empty description. A small original example
is in examples/dtc_descriptions.sample.txt.
"""

import logging
import os
from pathlib import Path

_LOG = logging.getLogger(__name__)
_ENV_VAR = "Q50DIAG_DTC_DESCRIPTIONS"
_DEFAULT_PATH = Path.home() / ".q50diag" / "dtc_descriptions.txt"
_SECTIONS = ("generic", "nissan", "ford")


def parse_descriptions(text: str) -> dict[str, dict[str, str]]:
    """Parse the file format into {section: {CODE: description}}."""
    sections: dict[str, dict[str, str]] = {name: {} for name in _SECTIONS}
    current: str | None = "generic"
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1].strip().lower()
            if name not in sections:
                _LOG.warning("DTC descriptions line %d: unknown section %r, entries skipped", number, line)
                current = None
                continue
            current = name
            continue
        if current is None:
            continue
        code, separator, description = line.partition("|")
        if not separator or not code.strip() or not description.strip():
            _LOG.warning("DTC descriptions line %d: expected CODE|description", number)
            continue
        sections[current][code.strip().upper()] = description.strip()
    return sections


def load_descriptions(path: Path | str | None = None) -> dict[str, dict[str, str]]:
    """Read a descriptions file; a missing default file yields empty tables."""
    explicit = path or os.environ.get(_ENV_VAR)
    target = Path(explicit) if explicit else _DEFAULT_PATH
    try:
        return parse_descriptions(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if explicit:
            _LOG.warning("DTC descriptions file not found: %s", target)
    except (OSError, UnicodeDecodeError) as error:
        _LOG.warning("DTC descriptions file unreadable: %s (%s)", target, error)
    return parse_descriptions("")


_LOADED = load_descriptions()
_DESCRIPTIONS = _LOADED["generic"]
# Manufacturer codes. These meanings hold for one make only; other makes reuse the numbers.
NISSAN_DESCRIPTIONS = _LOADED["nissan"]
FORD_DESCRIPTIONS = _LOADED["ford"]


def _base(code: str) -> str:
    return code.strip().upper().split("-", 1)[0]


def describe_generic(code: str) -> str:
    """Generic meaning only; manufacturer codes give an empty string."""
    return _DESCRIPTIONS.get(_base(code), "")


def describe_with(code: str, overlay: dict[str, str]) -> str:
    """Make-specific meaning from `overlay`, else the generic meaning."""
    return overlay.get(_base(code)) or describe_generic(code)


def describe(code: str) -> str:
    """Q50 default: generic meanings plus the Nissan section."""
    return describe_with(code, NISSAN_DESCRIPTIONS)
