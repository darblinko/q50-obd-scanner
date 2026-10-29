"""AgentEvent: the UI-facing form of Claude Agent SDK messages.

`translate()` maps one SDK message to zero or more events. `agent_id` is
"lead" for the top-level conversation and otherwise the tool_use id of the
Task/Agent call that spawned the subagent (the SDK's `parent_tool_use_id`).

`agent_finished` can arrive twice for one subagent (from its tool_result and
from a task_notification); consumers treat it as idempotent."""

from __future__ import annotations

from collections.abc import Set
from dataclasses import dataclass, field
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    RateLimitEvent,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TaskNotificationMessage,
    TaskStartedMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

LEAD = "lead"
AGENT_TOOLS = frozenset({"Task", "Agent"})
TOOL_RESULT_LIMIT = 4000

INIT = "init"
DELTA = "delta"
TEXT = "text"
TOOL_USE = "tool_use"
TOOL_RESULT = "tool_result"
AGENT_STARTED = "agent_started"
AGENT_FINISHED = "agent_finished"
RESULT = "result"
ERROR = "error"
BUSY = "busy"
IDLE = "idle"


@dataclass(frozen=True)
class AgentEvent:
    kind: str
    agent_id: str
    text: str = ""
    data: dict = field(default_factory=dict)


def translate(message: object, agent_ids: Set[str] = frozenset()) -> list[AgentEvent]:
    """Map one SDK message to events.

    `agent_ids` holds the tool_use ids of Task/Agent calls seen so far, so a
    matching tool_result also emits `agent_finished`. The caller adds the ids
    of `agent_started` events it receives."""
    if isinstance(message, StreamEvent):
        return _stream_events(message)
    if isinstance(message, AssistantMessage):
        return _assistant_events(message)
    if isinstance(message, UserMessage):
        return _user_events(message, agent_ids)
    if isinstance(message, ResultMessage):
        return [_result_event(message)]
    if isinstance(message, TaskStartedMessage):
        return _task_started_events(message, agent_ids)
    if isinstance(message, TaskNotificationMessage):
        return _task_notification_events(message)
    if isinstance(message, SystemMessage):
        return _system_events(message)
    if isinstance(message, RateLimitEvent) and message.rate_limit_info.status == "rejected":
        return [AgentEvent(ERROR, LEAD, "Usage limit reached; wait for the limit to reset or switch auth mode in settings")]
    return []


def _owner(parent_tool_use_id: str | None) -> str:
    return parent_tool_use_id or LEAD


def _stream_events(message: StreamEvent) -> list[AgentEvent]:
    event = message.event
    if event.get("type") != "content_block_delta":
        return []
    delta = event.get("delta") or {}
    if delta.get("type") != "text_delta" or not delta.get("text"):
        return []
    return [AgentEvent(DELTA, _owner(message.parent_tool_use_id), delta["text"])]


def _assistant_events(message: AssistantMessage) -> list[AgentEvent]:
    owner = _owner(message.parent_tool_use_id)
    events: list[AgentEvent] = []
    for block in message.content:
        if isinstance(block, TextBlock) and block.text:
            events.append(AgentEvent(TEXT, owner, block.text))
        elif isinstance(block, ToolUseBlock):
            events.extend(_tool_use_events(owner, block))
    if message.error:
        events.append(AgentEvent(ERROR, owner, _assistant_error_text(message.error)))
    return events



def _assistant_error_text(error: str) -> str:
    if error == "authentication_failed":
        return ("Anthropic rejected the API key. Open MechanIQ settings and paste a valid key "
                "from platform.claude.com/settings/keys.")
    return f"Assistant error: {error}"


def _tool_use_events(owner: str, block: ToolUseBlock) -> list[AgentEvent]:
    events = [AgentEvent(TOOL_USE, owner, data={"id": block.id, "name": block.name, "input": block.input})]
    if block.name in AGENT_TOOLS:
        title = str(block.input.get("description") or block.input.get("subagent_type") or "Subagent")
        subagent_type = str(block.input.get("subagent_type") or "general-purpose")
        events.append(AgentEvent(AGENT_STARTED, block.id, data={"title": title, "subagent_type": subagent_type}))
    return events


def _user_events(message: UserMessage, agent_ids: Set[str]) -> list[AgentEvent]:
    if isinstance(message.content, str):
        return []
    owner = _owner(message.parent_tool_use_id)
    events: list[AgentEvent] = []
    for block in message.content:
        if not isinstance(block, ToolResultBlock):
            continue
        is_error = bool(block.is_error)
        text = _clip(_result_text(block.content))
        events.append(AgentEvent(TOOL_RESULT, owner, text, {"tool_use_id": block.tool_use_id, "is_error": is_error}))
        if block.tool_use_id in agent_ids:
            status = "failed" if is_error else "completed"
            events.append(AgentEvent(AGENT_FINISHED, block.tool_use_id, data={"status": status, "is_error": is_error}))
    return events


def _result_text(content: str | list[dict[str, Any]] | None) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = []
    for item in content:
        if item.get("type") == "text":
            parts.append(str(item.get("text", "")))
        else:
            parts.append(f"[{item.get('type', 'content')}]")
    return "\n".join(parts)


def _clip(text: str) -> str:
    if len(text) <= TOOL_RESULT_LIMIT:
        return text
    return text[:TOOL_RESULT_LIMIT] + f"\n[clipped, {len(text) - TOOL_RESULT_LIMIT} more characters]"


def _result_event(message: ResultMessage) -> AgentEvent:
    data = {
        "cost_usd": message.total_cost_usd,
        "duration_ms": message.duration_ms,
        "num_turns": message.num_turns,
        "is_error": message.is_error,
        "subtype": message.subtype,
    }
    return AgentEvent(RESULT, LEAD, message.result or "", data)


def _task_started_events(message: TaskStartedMessage, agent_ids: Set[str]) -> list[AgentEvent]:
    # A foreground Task/Agent call already produced agent_started from its tool_use block.
    if not message.tool_use_id or message.tool_use_id in agent_ids:
        return []
    data = {"title": message.description or "Task", "subagent_type": message.task_type or "task"}
    return [AgentEvent(AGENT_STARTED, message.tool_use_id, data=data)]


def _task_notification_events(message: TaskNotificationMessage) -> list[AgentEvent]:
    if not message.tool_use_id:
        return []
    data = {"status": message.status, "is_error": message.status != "completed"}
    return [AgentEvent(AGENT_FINISHED, message.tool_use_id, message.summary, data)]


def _system_events(message: SystemMessage) -> list[AgentEvent]:
    if message.subtype != "init":
        return []
    data = {
        "model": message.data.get("model", ""),
        "session_id": message.data.get("session_id", ""),
        "api_key_source": message.data.get("apiKeySource", ""),
    }
    return [AgentEvent(INIT, LEAD, data=data)]
