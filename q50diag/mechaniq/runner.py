"""MechanIQ runner: one persistent Claude Code session on a background thread.

An asyncio event loop runs in its own thread and owns one ClaudeSDKClient.
The client stays connected across turns (streaming input mode), so stdin stays
open between turns and hook or in-process MCP tool callbacks never hit a
closed stream. Public methods are called from the Qt main thread; they only
post commands to the loop. Events come back through the `event` signal, which
is always emitted on the main thread."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions, ClaudeSDKClient, HookMatcher, ResultMessage
from PySide6.QtCore import QObject, Qt, Signal

from q50diag.mechaniq import prompt
from q50diag.mechaniq.events import AGENT_STARTED, BUSY, ERROR, IDLE, LEAD, AgentEvent, translate
from q50diag.mechaniq.settings import AUTH_ENV_KEYS, MechaniqSettings, child_env, load_settings

log = logging.getLogger(__name__)

STDERR_LINES = 20
SHUTDOWN_TIMEOUT_S = 10.0
SEND, RESET, SETTINGS, STOP = "send", "reset", "settings", "stop"


def build_options(
    settings: MechaniqSettings,
    project_root: Path,
    mcp_server: Any,
    guard: Callable[..., Any] | None,
    agents: dict[str, AgentDefinition] | None = None,
    stderr: Callable[[str], None] | None = None,
    base_env: dict[str, str] | None = None,
    system_append: str | None = None,
) -> ClaudeAgentOptions:
    """Options for the lead session. Pure: no I/O, no process spawned.
    system_append defaults to prompt.SYSTEM_APPEND (Q50 and vLinker FS)."""
    append = prompt.SYSTEM_APPEND if system_append is None else system_append
    return ClaudeAgentOptions(
        cwd=str(project_root),
        permission_mode="bypassPermissions",
        model=settings.model,
        env=_process_env(settings, base_env),
        system_prompt={"type": "preset", "preset": "claude_code", "append": append},
        include_partial_messages=True,
        forward_subagent_text=True,
        setting_sources=["project"],
        mcp_servers={"q50": mcp_server} if mcp_server is not None else {},
        agents=prompt.AGENTS if agents is None else agents,
        hooks={"PreToolUse": [HookMatcher(hooks=[guard])]} if guard is not None else None,
        stderr=stderr,
    )


def _process_env(settings: MechaniqSettings, base_env: dict[str, str] | None) -> dict[str, str]:
    # The SDK merges options.env over the parent environment, so dropping a key
    # is not enough to hide it; credentials the auth mode does not set become "".
    return {**{key: "" for key in AUTH_ENV_KEYS}, **child_env(settings, base_env)}


def _is_own_result(message: ResultMessage) -> bool:
    """True for the result of a prompt we sent, False for injected turns such
    as background task notifications."""
    origin = message.origin
    return origin is None or origin.get("kind") == "human"


class MechaniqRunner(QObject):
    event = Signal(object)
    _relay = Signal(object)

    def __init__(
        self,
        project_root: Path,
        mcp_server: Any,
        guard: Callable[..., Any] | None,
        settings: MechaniqSettings | None = None,
        client_factory: Callable[..., Any] = ClaudeSDKClient,
        parent: QObject | None = None,
        system_append: Callable[[], str] | None = None,
    ) -> None:
        super().__init__(parent)
        self._project_root = Path(project_root)
        self._mcp_server = mcp_server
        self._guard = guard
        self._settings = settings if settings is not None else load_settings()
        self._client_factory = client_factory
        # Called when each new lead session starts, so the prompt follows the
        # vehicle and adapter selected at that time.
        self._system_append = system_append
        self._busy = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._commands: asyncio.Queue[tuple[str, Any]] | None = None
        # Loop-thread state below; touched only by coroutines on the loop.
        self._client: Any = None
        self._turn_active = False
        self._agent_ids: frozenset[str] = frozenset()
        self._stderr_tail: deque[str] = deque(maxlen=STDERR_LINES)
        self._relay.connect(self._deliver, Qt.ConnectionType.QueuedConnection)

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def settings(self) -> MechaniqSettings:
        return self._settings

    def send(self, text: str) -> None:
        if text.strip():
            self._post(SEND, text)

    def interrupt(self) -> None:
        if self._loop is not None and self._thread is not None and self._thread.is_alive():
            asyncio.run_coroutine_threadsafe(self._interrupt(), self._loop)

    def reset(self) -> None:
        """Start a new conversation: stop the current turn and drop the session."""
        self.interrupt()
        self._post(RESET, None)

    def apply_settings(self, settings: MechaniqSettings) -> None:
        """Use new settings; the session reconnects on the next send."""
        self._settings = settings
        self._post(SETTINGS, settings)

    def shutdown(self) -> None:
        if self._thread is None:
            return
        self.interrupt()
        self._post(STOP, None)
        self._thread.join(SHUTDOWN_TIMEOUT_S)
        if self._thread.is_alive():
            log.warning("MechanIQ runner thread did not stop within %.0f s", SHUTDOWN_TIMEOUT_S)
        self._thread = None
        self._loop = None
        self._commands = None

    def _post(self, kind: str, value: Any) -> None:
        self._ensure_thread()
        assert self._loop is not None and self._commands is not None
        self._loop.call_soon_threadsafe(self._commands.put_nowait, (kind, value))

    def _ensure_thread(self) -> None:
        if self._thread is not None:
            return
        self._loop = asyncio.new_event_loop()
        self._commands = asyncio.Queue()
        self._thread = threading.Thread(target=self._run_loop, name="mechaniq", daemon=True)
        self._thread.start()

    def _run_loop(self) -> None:
        assert self._loop is not None and self._commands is not None
        loop = self._loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._worker(self._commands))
        finally:
            loop.close()

    def _deliver(self, item: AgentEvent) -> None:
        if item.kind in (BUSY, IDLE):
            self._busy = item.kind == BUSY
        self.event.emit(item)

    def _emit(self, item: AgentEvent) -> None:
        self._relay.emit(item)

    async def _worker(self, commands: asyncio.Queue[tuple[str, Any]]) -> None:
        while True:
            kind, value = await commands.get()
            if kind == SEND:
                await self._run_turn(value)
            elif kind == RESET:
                await self._disconnect()
                self._agent_ids = frozenset()
            elif kind == SETTINGS:
                await self._disconnect()
            elif kind == STOP:
                await self._disconnect()
                return

    async def _run_turn(self, text: str) -> None:
        self._turn_active = True
        self._emit(AgentEvent(BUSY, LEAD))
        try:
            client = await self._ensure_client()
            await client.query(text)
            finished = await self._read_until_result(client)
            if not finished:
                self._emit(self._error_event("Claude Code ended the session before the turn finished"))
                await self._disconnect()
        except Exception as exc:  # noqa: BLE001 (every failure is reported to the UI)
            log.exception("MechanIQ turn failed")
            self._emit(self._error_event(f"{type(exc).__name__}: {exc}"))
            await self._disconnect()
        finally:
            self._turn_active = False
            self._emit(AgentEvent(IDLE, LEAD))

    async def _read_until_result(self, client: Any) -> bool:
        async with contextlib.aclosing(client.receive_messages()) as stream:
            async for message in stream:
                self._dispatch(message)
                if isinstance(message, ResultMessage) and _is_own_result(message):
                    return True
        return False

    def _dispatch(self, message: object) -> None:
        for item in translate(message, self._agent_ids):
            if item.kind == AGENT_STARTED:
                self._agent_ids = self._agent_ids | {item.agent_id}
            self._emit(item)

    async def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        self._stderr_tail.clear()
        append = self._system_append() if self._system_append is not None else None
        options = build_options(
            self._settings, self._project_root, self._mcp_server, self._guard, stderr=self._stderr_tail.append,
            system_append=append,
        )
        client = self._client_factory(options=options)
        await client.connect()
        self._client = client
        return client

    async def _disconnect(self) -> None:
        client, self._client = self._client, None
        if client is None:
            return
        try:
            await client.disconnect()
        except Exception as exc:  # noqa: BLE001 (a failed disconnect must not stop the worker)
            log.warning("MechanIQ disconnect failed: %s", exc)

    async def _interrupt(self) -> None:
        if self._client is None or not self._turn_active:
            return
        try:
            await self._client.interrupt()
        except Exception as exc:  # noqa: BLE001 (reported to the UI)
            self._emit(self._error_event(f"Interrupt failed: {exc}"))

    def _error_event(self, text: str) -> AgentEvent:
        tail = list(self._stderr_tail)
        full = text + ("\n\nClaude Code stderr:\n" + "\n".join(tail) if tail else "")
        return AgentEvent(ERROR, LEAD, full, {"stderr": tail})
