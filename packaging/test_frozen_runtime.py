"""Checks of a packaged build, run inside it from the launcher workspace:

    cd <workspace>/app && <exe> --run-pytest -q <bundle>/.../test_frozen_runtime.py

Skipped under a normal interpreter. Not under tests/, so the regular suite never collects it."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

pytestmark = pytest.mark.skipif(not getattr(sys, "frozen", False), reason="checks a PyInstaller build")


def test_q50diag_loads_from_editable_workspace_source():
    import q50diag.gui.mechaniq as mechaniq_gui
    import q50diag.recorder as recorder

    source = Path(recorder.__file__)
    assert source.suffix == ".py"
    assert Path(sys._MEIPASS) not in source.parents
    assert mechaniq_gui.PROJECT_ROOT == source.parent.parent


def test_sdk_finds_bundled_cli_and_it_runs():
    path = Path(SubprocessCLITransport(prompt="x", options=ClaudeAgentOptions())._find_cli())
    assert path.parent.parts[-2:] == ("claude_agent_sdk", "_bundled")
    result = subprocess.run([str(path), "--version"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert "Claude Code" in result.stdout


def test_sdk_client_completes_handshake_with_bundled_cli(tmp_path):
    """Spawns the CLI through ClaudeSDKClient; no prompt is sent, so no API call is made."""

    async def connect_and_close():
        client = ClaudeSDKClient(ClaudeAgentOptions(cwd=str(tmp_path), max_turns=1))
        await asyncio.wait_for(client.connect(), timeout=120)
        try:
            return await client.get_server_info()
        finally:
            await client.disconnect()

    assert asyncio.run(connect_and_close())
