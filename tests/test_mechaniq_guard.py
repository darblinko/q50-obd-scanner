"""MechanIQ PreToolUse guard: each deny rule and the allowed everyday calls."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from q50diag.mechaniq.guard import make_guard, shell_segments

ROOT = Path("/home/user/projects/obd-scanner")
SETTINGS = Path.home() / ".config/q50diag/mechaniq.json"


def decide(tool_name: str, tool_input: dict, cwd: Path = ROOT) -> dict:
    hook = make_guard(ROOT, SETTINGS)
    data = {
        "hook_event_name": "PreToolUse",
        "session_id": "s",
        "transcript_path": "/tmp/t.jsonl",
        "cwd": str(cwd),
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_use_id": "toolu_1",
    }
    return asyncio.run(hook(data, "toolu_1", {"signal": None}))


def denied(result: dict) -> str:
    output = result["hookSpecificOutput"]
    assert output["hookEventName"] == "PreToolUse"
    assert output["permissionDecision"] == "deny"
    return output["permissionDecisionReason"]


@pytest.mark.parametrize(
    "tool_name, tool_input",
    [
        ("Write", {"file_path": "/home/user/notes.txt", "content": "x"}),
        ("Edit", {"file_path": "/etc/hosts", "old_string": "a", "new_string": "b"}),
        ("MultiEdit", {"file_path": "../other/file.py", "edits": []}),
        ("NotebookEdit", {"notebook_path": "/tmp/n.ipynb", "new_source": ""}),
        ("Write", {"file_path": "/home/user/projects/obd-scanner-old/x.py", "content": ""}),
    ],
)
def test_denies_writes_outside_project(tool_name, tool_input):
    reason = denied(decide(tool_name, tool_input))
    assert str(ROOT) in reason


@pytest.mark.parametrize(
    "command",
    [
        "echo hi > /tmp/out.txt",
        "pytest -q 2>> /home/user/log.txt",
        "ls | tee /tmp/listing.txt",
        "cat q50diag/session.py > ../copy.py",
    ],
)
def test_denies_bash_writes_outside_project(command):
    assert "outside" in denied(decide("Bash", {"command": command}))


@pytest.mark.parametrize(
    "command",
    ["git push", "git push origin main", "git -C /x push --force", "cd q50diag && git push"],
)
def test_denies_git_push(command):
    assert "push" in denied(decide("Bash", {"command": command}))


@pytest.mark.parametrize(
    "command",
    ["git reset --hard", "git reset --hard HEAD~1", "git clean -fd", "git status; git clean -n"],
)
def test_denies_destructive_git(command):
    assert "undo_change" in denied(decide("Bash", {"command": command}))


@pytest.mark.parametrize(
    "command",
    [
        "screen /dev/ttyUSB0 115200",
        "python -c \"import serial; serial.Serial('/dev/ttyACM0')\"",
        "python -c \"import serial; s = serial.Serial('COM5', 38400)\"",
        "echo ATZ > COM3",
        "python -m serial.tools.miniterm",
        "python -c \"import serial; serial.serial_for_url('socket://x')\"",
    ],
)
def test_denies_serial_access(command):
    assert "q50 tools" in denied(decide("Bash", {"command": command}))


@pytest.mark.parametrize(
    "tool_name, tool_input",
    [
        ("Read", {"file_path": str(ROOT / ".env")}),
        ("Read", {"file_path": ".env.local"}),
        ("Read", {"file_path": str(SETTINGS)}),
        ("Read", {"file_path": "~/.config/q50diag/mechaniq.json"}),
        ("Glob", {"pattern": "**/.env"}),
        ("Grep", {"pattern": "KEY", "path": ".env"}),
        ("Bash", {"command": "cat .env"}),
        ("Bash", {"command": "source ./.env && pytest"}),
        ("Bash", {"command": "cat ~/.config/q50diag/mechaniq.json"}),
        ("Bash", {"command": f"jq . {SETTINGS}"}),
        # An unquoted Windows path; shlex alone would drop its backslashes.
        ("Bash", {"command": "cat " + str(SETTINGS).replace("/", "\\")}),
        ("Write", {"file_path": str(ROOT / ".env"), "content": "A=1"}),
    ],
)
def test_denies_secrets(tool_name, tool_input):
    assert "credentials" in denied(decide(tool_name, tool_input))


@pytest.mark.parametrize(
    "tool_name, tool_input",
    [
        ("Write", {"file_path": str(ROOT / "q50diag/protocol/nissan.py"), "content": ""}),
        ("Edit", {"file_path": "q50diag/gui/pages/steering.py", "old_string": "a", "new_string": "b"}),
        ("Edit", {"file_path": str(ROOT / "tests/test_x.py"), "old_string": "a", "new_string": "b"}),
        ("Read", {"file_path": "/home/user/.bashrc"}),
        ("Read", {"file_path": str(ROOT / "q50diag/session.py")}),
        ("Grep", {"pattern": "serial.Serial", "path": "q50diag"}),
        ("Glob", {"pattern": "q50diag/**/*.py"}),
        ("mcp__q50__raw_read", {"module": "EPS", "payload": "21 01"}),
        ("Task", {"prompt": "x", "subagent_type": "code-fixer"}),
    ],
)
def test_allows_project_file_tools(tool_name, tool_input):
    assert decide(tool_name, tool_input) == {}


@pytest.mark.parametrize(
    "command",
    [
        ".venv/bin/python -m pytest -q tests/test_dtc.py",
        "python -m pytest -q -x 2>&1 | tail -20",
        "git status --porcelain",
        "git diff -- q50diag/session.py",
        "git log --oneline -5",
        'git commit -m "fix(mechaniq): push EPS reads earlier; COM5 note; reset --hard docs"',
        "git add -- q50diag/protocol/nissan.py && git commit -m 'fix: clean up decode'",
        "git restore q50diag/gui/catalog.py",
        "ls -la .venv/bin",
        "grep -rn torque q50diag/protocol > /dev/null",
        "python -c 'import os; print(os.environ.get(\"HOME\"))'",
        "echo done > logs/scratch.txt",
        "git commit -F- <<'EOF'\nfix(mechaniq): decode\nEOF",
    ],
)
def test_allows_normal_bash(command):
    assert decide("Bash", {"command": command}) == {}


def test_other_hook_events_pass_through():
    hook = make_guard(ROOT, SETTINGS)
    result = asyncio.run(
        hook(
            {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": "git push"}},
            None,
            {"signal": None},
        )
    )
    assert result == {}


def test_relative_paths_resolve_against_cwd():
    assert decide("Write", {"file_path": "notes.txt", "content": ""}, cwd=ROOT / "q50diag") == {}
    denied(decide("Write", {"file_path": "notes.txt", "content": ""}, cwd=Path("/tmp")))


def test_unbalanced_quotes_fall_back_to_text_checks():
    assert "push" in denied(decide("Bash", {"command": "git push 'oops"}))
    assert decide("Bash", {"command": "echo 'unterminated"}) == {}


def test_shell_segments_split_on_separators():
    assert shell_segments("a b && c | d; e\nf") == [["a", "b"], ["c"], ["d"], ["e"], ["f"]]
    assert shell_segments("git commit -m 'x; git push'") == [["git", "commit", "-m", "x; git push"]]


def test_denies_powershell():
    reason = denied(decide("PowerShell", {"command": "Get-ChildItem"}))
    assert "PowerShell is not available" in reason


@pytest.mark.parametrize(
    "command",
    [
        "env git push",
        "env -i PATH=/usr/bin git push origin main",
        "GIT_TRACE=1 git push",
        "command git push",
        "nohup git push &",
        "timeout 30 git push",
        "sudo git clean -fd",
    ],
)
def test_denies_git_behind_wrappers(command):
    reason = denied(decide("Bash", {"command": command}))
    assert "push" in reason or "undo_change" in reason


@pytest.mark.parametrize(
    "command",
    [
        "cp README.md ../copied.txt",
        "cp -r q50diag /tmp/q50diag-copy",
        "mv notes.txt /home/user/notes.txt",
        "cp -t /tmp README.md",
        "rsync -a q50diag/ /tmp/backup/",
        "touch ../marker",
        "mkdir -p /tmp/out",
        "rm -rf ../other",
        "dd if=/dev/zero of=/tmp/blob bs=1 count=1",
        "env cp README.md /tmp/x",
    ],
)
def test_denies_file_commands_outside_project(command):
    assert "outside" in denied(decide("Bash", {"command": command}))


@pytest.mark.parametrize(
    "command",
    [
        "cd .. && echo text > copied.txt",
        "cd /tmp; touch x",
        "cd q50diag && cd ../.. && cp a b",
    ],
)
def test_tracks_cd_for_relative_write_targets(command):
    assert "outside" in denied(decide("Bash", {"command": command}))


def test_denies_relative_write_after_unresolvable_cd():
    assert "cd" in denied(decide("Bash", {"command": "cd $SOMEWHERE && echo x > out.txt"}))


@pytest.mark.parametrize(
    "command",
    [
        "cd q50diag && echo x > ../logs/out.txt",
        "cp README.md logs/readme-copy.md",
        "mkdir -p logs/run1",
        "rm -f logs/old.txt",
        "cd tests && python -m pytest -q test_dtc.py",
    ],
)
def test_allows_file_commands_inside_project(command):
    assert decide("Bash", {"command": command}) == {}


@pytest.mark.parametrize(
    "command",
    [
        "screen /dev/cu.usbserial-123 115200",
        "cat /dev/cu.usbmodem1101",
    ],
)
def test_denies_macos_serial_devices(command):
    assert "q50 tools" in denied(decide("Bash", {"command": command}))


@pytest.mark.parametrize(
    "command",
    [
        "printenv ANTHROPIC_API_KEY",
        "echo $ANTHROPIC_API_KEY",
        'echo "${ANTHROPIC_AUTH_TOKEN}"',
        "printenv",
        "env",
        "env | grep KEY",
        "set",
        "export -p",
        "declare -px",
        "cat /proc/self/environ",
        "python -c 'import os; print(os.environ[\"ANTHROPIC_API_KEY\"])'",
    ],
)
def test_denies_reading_credentials_from_environment(command):
    assert "credentials" in denied(decide("Bash", {"command": command}))


@pytest.mark.parametrize(
    "command",
    [
        "printenv HOME",
        "set -euo pipefail; python -m pytest -q",
        "export PYTHONPATH=.",
        "env PYTHONPATH=. python -m pytest -q tests/test_dtc.py",
        "timeout 60 python -m pytest -q 2>&1 | tail -5",
    ],
)
def test_allows_environment_use_without_listing(command):
    assert decide("Bash", {"command": command}) == {}
