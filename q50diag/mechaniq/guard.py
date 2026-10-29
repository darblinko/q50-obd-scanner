"""PreToolUse guard for the MechanIQ agent.

MechanIQ runs with permission prompts off, so this hook is the hard boundary.
It denies file writes outside the project, destructive or remote git
commands, Bash that opens a serial device (the app owns the adapter port),
and any access to `.env` files or the MechanIQ settings file (it may hold an
API key). Each denial tells the agent what to do instead.

Bash commands are split into shell segments with shlex so quoted text such as
a commit message is not mistaken for a command. Redirect and tee targets are
checked like file writes. The Bash checks are a guard against mistakes, not a
sandbox: a script file can still do anything its interpreter allows."""

from __future__ import annotations

import re
import shlex
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

WRITE_TOOLS = {"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}
READ_TOOLS = {"Read": "file_path", "Grep": "path", "Glob": "path"}
# Claude Code on Windows without Git for Windows runs commands through a
# PowerShell tool. The Bash checks below do not parse PowerShell, so it is denied.
POWERSHELL_DENIED = (
    "PowerShell is not available to MechanIQ. Use Read, Edit and Write for files and the q50 tools "
    "for the car; apply_change runs the tests."
)
HARMLESS_TARGETS = {"/dev/null", "/dev/stdout", "/dev/stderr"}
WRITE_REDIRECTS = {">", ">>", ">|", "&>", "&>>", ">&"}
SEPARATOR_CHARS = set(";|&()\n")
GIT_OPTIONS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}

SERIAL_PATTERNS = (
    re.compile(r"/dev/tty"),
    re.compile(r"/dev/serial"),
    re.compile(r"\bCOM\d+\b", re.IGNORECASE),
    re.compile(r"\bserial\.Serial\s*\("),
    re.compile(r"\bserial_for_url\b"),
    re.compile(r"\bminiterm\b"),
)
ENV_FILE = re.compile(r"(?:^|[\s/'\"=<>(:])\.env\b")
# Fallback when a command cannot be tokenized (unbalanced quotes).
RAW_GIT_PUSH = re.compile(r"\bgit\b[^;&|\n]*\bpush\b")
RAW_GIT_DISCARD = re.compile(r"\bgit\b[^;&|\n]*\b(?:clean\b|reset\b[^;&|\n]*--hard\b)")

SECRET_MESSAGE = (
    "Access to .env files and the MechanIQ settings file is blocked because they hold "
    "credentials. Ask the user for the specific non-secret value you need."
)
PUSH_MESSAGE = (
    "git push is blocked. Changes are committed locally by apply_change; "
    "tell the user the commits are ready and let them push."
)
DISCARD_MESSAGE = (
    "git reset --hard and git clean are blocked because they discard work. "
    "Use the undo_change tool to revert a MechanIQ change, or git restore <file> "
    "for one file you edited in this turn."
)
SERIAL_MESSAGE = (
    "Bash may not open a serial device or adapter port: the q50diag app owns the "
    "connection. Use the q50 tools (raw_read, read_module, get_readings) for vehicle "
    "access. To search code for this text, use the Grep tool."
)

Hook = Callable[[dict[str, Any], str | None, Any], Awaitable[dict[str, Any]]]


def deny(message: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": message,
        }
    }


def resolve(raw: str, cwd: Path) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = cwd / path
    return path.resolve(strict=False)


def is_env_file(path: Path) -> bool:
    return path.name == ".env" or path.name.startswith(".env.")


def shell_segments(command: str) -> list[list[str]]:
    """Split a shell command into simple-command token lists. Raises
    ValueError when the quoting is unbalanced."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars="();<>|&\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    segments: list[list[str]] = [[]]
    for token in lexer:
        if set(token) <= SEPARATOR_CHARS:
            segments.append([])
        else:
            segments[-1].append(token)
    return [segment for segment in segments if segment]


def git_subcommand(segment: list[str]) -> tuple[str, list[str]] | None:
    """("push", [args]) for a segment that runs git, else None."""
    if not segment or Path(segment[0]).name != "git":
        return None
    index = 1
    while index < len(segment) and segment[index].startswith("-"):
        index += 2 if segment[index] in GIT_OPTIONS_WITH_VALUE else 1
    if index >= len(segment):
        return None
    return segment[index], segment[index + 1 :]


def write_targets(segment: list[str]) -> list[str]:
    """Files a segment writes through redirects or tee."""
    targets: list[str] = []
    for index, token in enumerate(segment[:-1]):
        following = segment[index + 1]
        if token in WRITE_REDIRECTS and not (token == ">&" and following.isdigit()):
            targets.append(following)
    if segment[0] == "tee":
        targets += [token for token in segment[1:] if not token.startswith("-")]
    return targets


class Guard:
    """Decides one tool call. `check` returns a denial message or None."""

    def __init__(self, project_root: Path, settings_path: Path) -> None:
        self.root = Path(project_root).expanduser().resolve(strict=False)
        self.settings_path = Path(settings_path).expanduser().resolve(strict=False)
        folder = self.settings_path.parent
        # The folder itself is a marker so a wildcard such as folder/* is caught too.
        self._settings_markers = (
            folder.as_posix(),
            f"{folder.parent.name}/{folder.name}",
            f"{folder.name}/{self.settings_path.name}",
        )

    def check(self, tool_name: str, tool_input: dict[str, Any], cwd: Path) -> str | None:
        if tool_name in WRITE_TOOLS:
            return self._check_write(tool_input.get(WRITE_TOOLS[tool_name]), cwd)
        if tool_name in READ_TOOLS:
            return self._check_read(tool_name, tool_input, cwd)
        if tool_name == "Bash":
            return self._check_bash(str(tool_input.get("command") or ""), cwd)
        if tool_name == "PowerShell":
            return POWERSHELL_DENIED
        return None

    # File tools ----------------------------------------------------------
    def _is_secret(self, path: Path) -> bool:
        return is_env_file(path) or path == self.settings_path

    def _check_write(self, raw: Any, cwd: Path) -> str | None:
        if not raw:
            return None
        path = resolve(str(raw), cwd)
        if self._is_secret(path):
            return SECRET_MESSAGE
        if not path.is_relative_to(self.root):
            return (
                f"Writes are limited to the project at {self.root}; {path} is outside it. "
                f"Put the file under {self.root} (scratch output can go in {self.root / 'logs'})."
            )
        return None

    def _check_read(self, tool_name: str, tool_input: dict[str, Any], cwd: Path) -> str | None:
        raw = tool_input.get(READ_TOOLS[tool_name])
        path = resolve(str(raw), cwd) if raw else None
        if path is not None and self._is_secret(path):
            return SECRET_MESSAGE
        if tool_name == "Grep" and path is not None and self.settings_path.is_relative_to(path):
            return SECRET_MESSAGE
        pattern = str(tool_input.get("pattern") or "") if tool_name == "Glob" else ""
        pattern += " " + str(tool_input.get("glob") or "")
        if ENV_FILE.search(pattern):
            return SECRET_MESSAGE
        return None

    # Bash ----------------------------------------------------------------
    def _mentions_secret(self, text: str) -> bool:
        return bool(ENV_FILE.search(text)) or any(marker in text for marker in self._settings_markers)

    def _check_bash(self, command: str, cwd: Path) -> str | None:
        # Checked before shlex, which drops the backslashes of an unquoted Windows path.
        if self._mentions_secret(command.replace("\\", "/")):
            return SECRET_MESSAGE
        try:
            segments = shell_segments(command)
        except ValueError:
            return self._check_raw_bash(command)
        for segment in segments:
            message = self._check_segment(segment, cwd)
            if message:
                return message
        return None

    def _check_segment(self, segment: list[str], cwd: Path) -> str | None:
        text = " ".join(segment)
        git = git_subcommand(segment)
        if git is not None:
            name, args = git
            if name == "push":
                return PUSH_MESSAGE
            if name == "clean" or (name == "reset" and "--hard" in args):
                return DISCARD_MESSAGE
        elif any(pattern.search(text) for pattern in SERIAL_PATTERNS):
            return SERIAL_MESSAGE
        for target in write_targets(segment):
            if target in HARMLESS_TARGETS or target.startswith("$"):
                continue
            message = self._check_write(target, cwd)
            if message:
                return message
        return None

    def _check_raw_bash(self, command: str) -> str | None:
        if RAW_GIT_PUSH.search(command):
            return PUSH_MESSAGE
        if RAW_GIT_DISCARD.search(command):
            return DISCARD_MESSAGE
        if any(pattern.search(command) for pattern in SERIAL_PATTERNS):
            return SERIAL_MESSAGE
        return None


def make_guard(project_root: Path, settings_path: Path) -> Hook:
    """Return the async PreToolUse hook for ClaudeAgentOptions.hooks."""
    guard = Guard(project_root, settings_path)

    async def pre_tool_use(input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
        if input_data.get("hook_event_name", "PreToolUse") != "PreToolUse":
            return {}
        cwd = Path(input_data.get("cwd") or guard.root)
        message = guard.check(str(input_data.get("tool_name") or ""), input_data.get("tool_input") or {}, cwd)
        return deny(message) if message else {}

    return pre_tool_use
