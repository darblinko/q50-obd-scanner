"""MechanIQ view model: turns the runner's AgentEvents into per-agent
transcripts, an agent list, cost totals and the change log entries.

Events are duck-typed (`kind`, `agent_id`, `text`, `data`), matching
`q50diag.mechaniq.events.AgentEvent`, so this module does not import the
runtime. All methods run on the Qt main thread."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

from PySide6.QtCore import QObject, Signal

LEAD = "lead"
AGENT_TOOLS = ("Task", "Agent")
SUMMARY_KEYS = (
    "file_path", "path", "notebook_path", "command", "pattern", "query", "url", "description",
    "label", "summary", "keys", "module", "search", "title", "prompt",
)
SUMMARY_LIMIT = 90
MCP_PREFIX = "mcp__q50__"
FAILED_STATUSES = ("failed", "error", "killed", "stopped")


@dataclass(frozen=True)
class TranscriptEntry:
    """One transcript line. kind: user, assistant, task, tool, result, error, notice.
    status (tools only): running, done, failed."""

    kind: str
    text: str = ""
    tool_id: str = ""
    name: str = ""
    status: str = ""
    streaming: bool = False


@dataclass(frozen=True)
class AgentInfo:
    agent_id: str
    title: str
    subagent_type: str = ""
    state: str = "idle"  # idle, running, done, failed


def tool_display_name(name: str) -> str:
    return name[len(MCP_PREFIX):] if name.startswith(MCP_PREFIX) else name


def summarize_tool_input(tool_input: object, project_root: Path | None = None) -> str:
    """Short one-line description of a tool call's input."""
    if not isinstance(tool_input, dict) or not tool_input:
        return ""
    value = next((tool_input[key] for key in SUMMARY_KEYS if tool_input.get(key)), None)
    if value is None:
        value = next((v for v in tool_input.values() if isinstance(v, str) and v), None)
    if value is None:
        value = json.dumps(tool_input, separators=(",", ":"), default=str)
    text = value if isinstance(value, str) else json.dumps(value, separators=(",", ":"), default=str)
    if project_root is not None:
        text = text.replace(str(project_root) + "/", "")
    text = " ".join(text.split())
    return text if len(text) <= SUMMARY_LIMIT else text[: SUMMARY_LIMIT - 3] + "..."


def auth_label(api_key_source: str) -> str:
    """Map the CLI's apiKeySource to what the header shows."""
    if api_key_source in ("", "none", "/login managed key"):
        return "subscription"
    if api_key_source == "ANTHROPIC_API_KEY":
        return "API key"
    return api_key_source


def format_result(data: dict, turn_cost: float | None) -> str:
    seconds = (data.get("duration_ms") or 0) / 1000
    turns = data.get("num_turns") or 0
    cost = f", ${turn_cost:.4f}" if turn_cost is not None else ""
    if data.get("is_error"):
        reason = data.get("subtype") or "error"
        return f"Ended with error ({reason}) after {seconds:.1f} s, {turns} turns{cost}"
    return f"Done in {seconds:.1f} s, {turns} turns{cost}"


class MechaniqModel(QObject):
    agents_changed = Signal()
    transcript_changed = Signal(str)  # agent_id
    busy_changed = Signal(bool)
    changes_changed = Signal()
    info_changed = Signal()  # model name, auth source, totals

    def __init__(self, project_root: Path | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.project_root = project_root
        self.busy = False
        self.model_name = ""
        self.auth_source = ""
        self.session_id = ""
        self.turns = 0
        self._cost_banked = 0.0
        self._cost_running = 0.0
        self._agents: dict[str, AgentInfo] = {LEAD: AgentInfo(LEAD, "Lead")}
        self._transcripts: dict[str, list[TranscriptEntry]] = {LEAD: []}
        self._tool_inputs: dict[str, dict] = {}
        self._changes: list = []

    # Read access ---------------------------------------------------------
    def agents(self) -> list[AgentInfo]:
        """Lead first, then helpers in start order."""
        return list(self._agents.values())

    def agent(self, agent_id: str) -> AgentInfo | None:
        return self._agents.get(agent_id)

    def transcript(self, agent_id: str = LEAD) -> list[TranscriptEntry]:
        return list(self._transcripts.get(agent_id, []))

    @property
    def total_cost(self) -> float:
        return self._cost_banked + self._cost_running

    def changes(self) -> list:
        """Change entries, newest first."""
        return list(reversed(self._changes))

    # Local input -----------------------------------------------------------
    def set_config(self, auth_mode: str, model_name: str) -> None:
        """Header values from settings, shown until the runtime reports its own."""
        self.auth_source = "API key" if auth_mode == "api_key" else "subscription"
        self.model_name = model_name
        self.info_changed.emit()

    def add_user_text(self, text: str) -> None:
        self._append(LEAD, TranscriptEntry("user", text))

    def add_notice(self, text: str, agent_id: str = LEAD, error: bool = False) -> None:
        self._append(agent_id, TranscriptEntry("error" if error else "notice", text))

    def set_changes(self, entries: list) -> None:
        self._changes = list(entries)
        self.changes_changed.emit()

    def add_change(self, entry) -> None:
        self._changes = [*self._changes, entry]
        self.changes_changed.emit()
        reload_kind = getattr(entry, "reload_kind", "") or "no reload"
        commit = (getattr(entry, "commit", "") or "")[:7]
        detail = ", ".join(part for part in (getattr(entry, "status", ""), reload_kind, commit) if part)
        self.add_notice(f"Change: {getattr(entry, 'summary', '')} ({detail})",
                        error=getattr(entry, "status", "") == "tests_failed")

    def reset(self) -> None:
        """New conversation: clear transcripts and helpers, keep the cost total."""
        self._cost_banked += self._cost_running
        self._cost_running = 0.0
        self.turns = 0
        self._agents = {LEAD: AgentInfo(LEAD, "Lead")}
        self._transcripts = {LEAD: []}
        self._tool_inputs = {}
        self.agents_changed.emit()
        self.transcript_changed.emit(LEAD)
        self.info_changed.emit()

    # Runtime events ----------------------------------------------------------
    def handle_event(self, event) -> None:
        handler = getattr(self, f"_on_{event.kind}", None)
        if handler is not None:
            handler(event.agent_id or LEAD, event.text or "", dict(event.data or {}))

    def _on_init(self, _agent_id: str, _text: str, data: dict) -> None:
        self.model_name = data.get("model") or self.model_name
        self.session_id = data.get("session_id") or self.session_id
        if "api_key_source" in data:
            self.auth_source = auth_label(data.get("api_key_source") or "")
        self.info_changed.emit()

    def _on_delta(self, agent_id: str, text: str, _data: dict) -> None:
        entries = self._entries(agent_id)
        last = entries[-1] if entries else None
        if last is not None and last.kind == "assistant" and last.streaming:
            self._replace_last(agent_id, replace(last, text=last.text + text))
        else:
            self._append(agent_id, TranscriptEntry("assistant", text, streaming=True))

    def _on_text(self, agent_id: str, text: str, _data: dict) -> None:
        entries = self._entries(agent_id)
        last = entries[-1] if entries else None
        if last is not None and last.kind == "assistant" and last.streaming:
            self._replace_last(agent_id, replace(last, text=text or last.text, streaming=False))
        elif text:
            self._append(agent_id, TranscriptEntry("assistant", text))

    def _on_tool_use(self, agent_id: str, _text: str, data: dict) -> None:
        self._close_stream(agent_id)
        tool_id = str(data.get("id", ""))
        tool_input = data.get("input") if isinstance(data.get("input"), dict) else {}
        self._tool_inputs[tool_id] = tool_input
        name = str(data.get("name", "tool"))
        summary = summarize_tool_input(tool_input, self.project_root)
        self._append(agent_id, TranscriptEntry("tool", summary, tool_id, tool_display_name(name), "running"))

    def _on_tool_result(self, agent_id: str, _text: str, data: dict) -> None:
        tool_id = str(data.get("tool_use_id", ""))
        status = "failed" if data.get("is_error") else "done"
        for owner in (agent_id, *self._transcripts):
            entries = self._transcripts.get(owner, [])
            index = next((i for i, e in enumerate(entries) if e.kind == "tool" and e.tool_id == tool_id), None)
            if index is not None:
                updated = [*entries[:index], replace(entries[index], status=status), *entries[index + 1:]]
                self._set_entries(owner, updated)
                return

    def _on_agent_started(self, agent_id: str, _text: str, data: dict) -> None:
        subagent_type = str(data.get("subagent_type", ""))
        title = str(data.get("title") or subagent_type or "Helper")
        self._agents = {**self._agents, agent_id: AgentInfo(agent_id, title, subagent_type, "running")}
        self._transcripts.setdefault(agent_id, [])
        prompt = self._tool_inputs.get(agent_id, {}).get("prompt")
        if prompt:
            self._append(agent_id, TranscriptEntry("task", str(prompt)))
        self.agents_changed.emit()

    def _on_agent_finished(self, agent_id: str, _text: str, data: dict) -> None:
        info = self._agents.get(agent_id)
        if info is None:
            return
        self._close_stream(agent_id)
        failed = info.state == "failed" or data.get("is_error") or data.get("status") in FAILED_STATUSES
        state = "failed" if failed else "done"
        self._agents = {**self._agents, agent_id: replace(info, state=state)}
        self.agents_changed.emit()

    def _on_result(self, _agent_id: str, _text: str, data: dict) -> None:
        self._close_stream(LEAD)
        turn_cost = self._account_cost(data.get("cost_usd"))
        self.turns += int(data.get("num_turns") or 0)
        self._append(LEAD, TranscriptEntry("result", format_result(data, turn_cost)))
        self.info_changed.emit()

    def _on_error(self, agent_id: str, text: str, _data: dict) -> None:
        self._close_stream(agent_id)
        self._append(agent_id, TranscriptEntry("error", text or "Unknown error"))

    def _on_busy(self, _agent_id: str, _text: str, _data: dict) -> None:
        self._set_busy(True)

    def _on_idle(self, _agent_id: str, _text: str, _data: dict) -> None:
        for agent_id in list(self._transcripts):
            self._close_stream(agent_id)
        self._set_busy(False)

    # Helpers -------------------------------------------------------------------
    def _account_cost(self, cumulative) -> float | None:
        """`cost_usd` is the running total of the connection; return this turn's share."""
        if cumulative is None:
            return None
        cumulative = float(cumulative)
        if cumulative < self._cost_running:  # the runtime reconnected and its total restarted
            self._cost_banked += self._cost_running
            self._cost_running = 0.0
        turn_cost = cumulative - self._cost_running
        self._cost_running = cumulative
        return turn_cost

    def _set_busy(self, busy: bool) -> None:
        if busy != self.busy:
            self.busy = busy
            lead = self._agents[LEAD]
            self._agents = {**self._agents, LEAD: replace(lead, state="running" if busy else "idle")}
            self.agents_changed.emit()
            self.busy_changed.emit(busy)

    def _entries(self, agent_id: str) -> list[TranscriptEntry]:
        return self._transcripts.get(agent_id, [])

    def _set_entries(self, agent_id: str, entries: list[TranscriptEntry]) -> None:
        if agent_id not in self._agents:
            self._agents = {**self._agents, agent_id: AgentInfo(agent_id, "Helper", state="running")}
            self.agents_changed.emit()
        self._transcripts[agent_id] = entries
        self.transcript_changed.emit(agent_id)

    def _append(self, agent_id: str, entry: TranscriptEntry) -> None:
        self._set_entries(agent_id, [*self._entries(agent_id), entry])

    def _replace_last(self, agent_id: str, entry: TranscriptEntry) -> None:
        self._set_entries(agent_id, [*self._entries(agent_id)[:-1], entry])

    def _close_stream(self, agent_id: str) -> None:
        entries = self._entries(agent_id)
        if entries and entries[-1].streaming:
            self._replace_last(agent_id, replace(entries[-1], streaming=False))
