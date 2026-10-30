"""PreToolUse guard for the MechanIQ agent.

MechanIQ runs with permission prompts off, so this hook is the hard boundary.
It denies file writes outside the project, destructive or remote git
commands, Bash that opens a serial device (the app owns the adapter port),
any access to `.env` files or the MechanIQ settings file (it may hold an
API key), and Bash that prints the Anthropic credentials from the
environment. Each denial tells the agent what to do instead.

Bash commands are split into shell segments with shlex so quoted text such as
a commit message is not mistaken for a command. Leading VAR=value assignments
and wrappers such as env, nohup and timeout are skipped to find the command a
segment runs. Redirect and tee targets, file operands of cp, mv, rm and
similar commands, and dd of= are checked like file writes, relative to the
directory the preceding `cd` segments select and, for the command itself, the
directory env -C or sudo -D selects. A write target that contains `$` or a
backtick is denied because its expansion cannot be checked. The Bash checks
are a heuristic filter against mistakes and provide no OS sandbox: a script file can
still do anything its interpreter allows, and the credentials stay in the
environment of every command the agent runs."""

from __future__ import annotations

import re
import shlex
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from q50diag.mechaniq.settings import AUTH_ENV_KEYS

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
INPUT_REDIRECTS = {"<", "<<", "<<<"}
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# Commands that run their arguments as another command, with their options that take a value.
WRAPPERS = {
    "env": {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"},
    "command": set(),
    "exec": {"-a"},
    "nohup": set(),
    "time": {"-f", "-o"},
    "nice": {"-n", "--adjustment"},
    "sudo": {"-u", "-g", "-C", "-D", "-h", "-p", "-U"},
    "doas": {"-u", "-C"},
    "timeout": {"-s", "-k", "--signal", "--kill-after"},
    "stdbuf": {"-i", "-o", "-e"},
    "xargs": {"-I", "-i", "-n", "-L", "-l", "-P", "-d", "-s", "-E", "-e", "-a"},
}
# Short options of wrappers that run the command in another working directory
# (both also accept --chdir).
CHDIR_OPTIONS = {"env": "-C", "sudo": "-D"}
# File commands whose last operand (or -t directory) is the destination.
DESTINATION_COMMANDS = {"cp", "mv", "install", "ln", "rsync"}
# File commands that modify every operand.
OPERAND_COMMANDS = {"touch", "mkdir", "rm", "rmdir", "truncate"}
TARGET_DIRECTORY_OPTIONS = {"-t", "--target-directory"}
FILE_OPTIONS_WITH_VALUE = {
    "cp": {"-S", "--suffix"},
    "mv": {"-S", "--suffix"},
    "ln": {"-S", "--suffix"},
    "install": {"-m", "-o", "-g", "-S", "--mode", "--owner", "--group", "--suffix"},
    "rsync": {"-e", "--rsh", "--exclude", "--include", "--filter", "-f"},
    "touch": {"-d", "-t", "-r", "--date", "--reference"},
    "mkdir": {"-m", "--mode"},
    "truncate": {"-s", "-r", "--size", "--reference"},
}
# Builtins that list every exported variable when given only options.
ENV_LISTING_BUILTINS = {"export", "declare", "typeset", "compgen"}

SERIAL_PATTERNS = (
    re.compile(r"/dev/tty"),
    re.compile(r"/dev/serial"),
    re.compile(r"/dev/cu\."),
    re.compile(r"\bCOM\d+\b", re.IGNORECASE),
    re.compile(r"\bserial\.Serial\s*\("),
    re.compile(r"\bserial_for_url\b"),
    re.compile(r"\bminiterm\b"),
)
ENV_FILE = re.compile(r"(?:^|[\s/'\"=<>(:])\.env\b")
# Fallback when a command cannot be tokenized (unbalanced quotes).
ENV_SECRET = re.compile("|".join([*AUTH_ENV_KEYS, r"/proc/\S*/environ"]))
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
ENV_SECRET_MESSAGE = (
    "Reading ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN or the whole environment is blocked because it "
    "holds credentials. Use printenv NAME for one non-secret variable; to search code for these names, "
    "use the Grep tool."
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


def without_redirects(segment: list[str]) -> list[str]:
    """`segment` with redirect operators, their targets and their fd numbers removed."""
    kept: list[str] = []
    skip_next = False
    for token in segment:
        if skip_next:
            skip_next = False
            continue
        if token in WRITE_REDIRECTS or token in INPUT_REDIRECTS:
            if kept and kept[-1].isdigit():
                kept.pop()
            skip_next = True
            continue
        kept.append(token)
    return kept


def chdir_option(wrapper: str, args: list[str], index: int) -> tuple[str | None, int]:
    """(directory, tokens consumed) when args[index] is the working-directory
    option of `wrapper` (env -C, sudo -D, --chdir), else (None, 0)."""
    short = CHDIR_OPTIONS.get(wrapper)
    if short is None:
        return None, 0
    option = args[index]
    if option in (short, "--chdir"):
        return (args[index + 1], 2) if index + 1 < len(args) else ("", 1)
    if option.startswith("--chdir="):
        return option.removeprefix("--chdir="), 1
    if option.startswith(short):
        return option.removeprefix(short), 1
    return None, 0


def unwrap_with_directories(segment: list[str]) -> tuple[list[str], list[str]]:
    """The command a segment runs, after leading VAR=value assignments and
    wrapper commands such as env, nohup or timeout, plus the working
    directories those wrappers select for it, in order. The command is empty
    when only assignments and wrappers are present (a bare `env` lists the
    environment)."""
    index = 0
    directories: list[str] = []
    while index < len(segment):
        token = segment[index]
        if ASSIGNMENT.match(token):
            index += 1
            continue
        name = Path(token).name
        if name not in WRAPPERS:
            break
        index += 1
        while index < len(segment) and segment[index].startswith("-") and segment[index] != "-":
            directory, consumed = chdir_option(name, segment, index)
            if directory is not None:
                directories.append(directory)
                index += consumed
            else:
                index += 2 if segment[index] in WRAPPERS[name] else 1
        if name == "timeout":
            index += 1  # the duration
    return segment[index:], directories


def unwrap(segment: list[str]) -> list[str]:
    """The command a segment runs; see unwrap_with_directories."""
    return unwrap_with_directories(segment)[0]


def file_command_targets(command: list[str]) -> list[str]:
    """Paths a cp, mv, rm, dd or similar command writes, creates or removes."""
    name = Path(command[0]).name
    args = command[1:]
    if name == "dd":
        return [arg[3:] for arg in args if arg.startswith("of=")]
    if name not in DESTINATION_COMMANDS and name not in OPERAND_COMMANDS:
        return []
    with_value = FILE_OPTIONS_WITH_VALUE.get(name, set())
    operands: list[str] = []
    directories: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            operands += args[index + 1 :]
            break
        if name in DESTINATION_COMMANDS and arg in TARGET_DIRECTORY_OPTIONS and index + 1 < len(args):
            directories.append(args[index + 1])
            index += 2
        elif name in DESTINATION_COMMANDS and arg.startswith("--target-directory="):
            directories.append(arg.split("=", 1)[1])
            index += 1
        elif arg.startswith("-") and arg != "-":
            index += 2 if arg in with_value else 1
        else:
            operands.append(arg)
            index += 1
    if name in DESTINATION_COMMANDS:
        return directories or operands[-1:]
    return operands


def lists_environment(segment: list[str], command: list[str]) -> bool:
    """True for printenv, a bare env, set, export -p and similar listings."""
    if not command:
        return any(Path(token).name == "env" for token in segment)
    name = Path(command[0]).name
    args = command[1:]
    if name == "printenv":
        # Options such as -0 or --null still print every variable.
        return all(arg.startswith("-") for arg in args)
    if name == "set":
        return not args
    return name in ENV_LISTING_BUILTINS and all(arg.startswith("-") for arg in args)


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


def redirect_targets(segment: list[str]) -> list[str]:
    """Files a segment writes through output redirects."""
    targets: list[str] = []
    for index, token in enumerate(segment[:-1]):
        following = segment[index + 1]
        if token in WRITE_REDIRECTS and not (token == ">&" and following.isdigit()):
            targets.append(following)
    return targets


def tee_targets(command: list[str]) -> list[str]:
    """Files an unwrapped tee command writes."""
    if not command or Path(command[0]).name != "tee":
        return []
    return [token for token in command[1:] if not token.startswith("-")]


def is_dynamic(path: str) -> bool:
    """True when the shell expands the path at run time ($VAR, $(...), backticks)."""
    return "$" in path or "`" in path


def change_directory(target: str, cwd: Path | None) -> Path | None:
    """`cwd` after changing to `target`; None when it cannot be known statically."""
    if not target or target == "-" or is_dynamic(target):
        return None
    if cwd is None and not Path(target).expanduser().is_absolute():
        return None
    return resolve(target, cwd or Path.home())


def next_directory(command: list[str], cwd: Path | None) -> Path | None:
    """Working directory after `cd`/`pushd`; None when it cannot be known statically."""
    args = [arg for arg in command[1:] if not (arg.startswith("-") and arg != "-")]
    if not args:
        return Path.home()
    return change_directory(args[0], cwd)


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
        if ENV_SECRET.search(command):
            return ENV_SECRET_MESSAGE
        try:
            segments = shell_segments(command)
        except ValueError:
            return self._check_raw_bash(command)
        current: Path | None = cwd
        for segment in segments:
            program, directories = unwrap_with_directories(without_redirects(segment))
            if program and program[0] in ("cd", "pushd"):
                current = next_directory(program, current)
                continue
            program_cwd = current
            for directory in directories:
                program_cwd = change_directory(directory, program_cwd)
            message = self._check_segment(segment, program, current, program_cwd)
            if message:
                return message
        return None

    def _check_segment(
        self, segment: list[str], program: list[str], cwd: Path | None, program_cwd: Path | None
    ) -> str | None:
        """`cwd` is the shell's directory (redirects); `program_cwd` is the
        directory the command runs in after env -C or sudo -D."""
        if lists_environment(segment, program):
            return ENV_SECRET_MESSAGE
        text = " ".join(segment)
        git = git_subcommand(program)
        if git is not None:
            name, args = git
            if name == "push":
                return PUSH_MESSAGE
            if name == "clean" or (name == "reset" and "--hard" in args):
                return DISCARD_MESSAGE
        elif any(pattern.search(text) for pattern in SERIAL_PATTERNS):
            return SERIAL_MESSAGE
        program_targets = tee_targets(program) + (file_command_targets(program) if program else [])
        checks = [(target, cwd) for target in redirect_targets(segment)]
        checks += [(target, program_cwd) for target in program_targets]
        for target, directory in checks:
            message = self._check_write_target(target, directory)
            if message:
                return message
        return None

    def _check_write_target(self, target: str, cwd: Path | None) -> str | None:
        if target in HARMLESS_TARGETS:
            return None
        if is_dynamic(target):
            return (
                f"The write target {target} depends on a shell expansion the guard cannot check. "
                f"Write to a literal path under {self.root}."
            )
        if cwd is None and not Path(target).expanduser().is_absolute():
            return (
                f"The guard cannot tell which directory the earlier cd or chdir option selected, so the relative "
                f"path {target} cannot be checked. Use a literal directory or an absolute path under {self.root}."
            )
        return self._check_write(target, cwd or self.root)

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
