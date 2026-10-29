"""MechanIQ runtime: SDK message translation, session options, and the runner
thread driven by a fake client that yields real SDK message dataclasses."""

from __future__ import annotations

import asyncio
import re

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    HookMatcher,
    RateLimitEvent,
    RateLimitInfo,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TaskNotificationMessage,
    TaskStartedMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from q50diag.mechaniq import prompt
from q50diag.mechaniq.events import TOOL_RESULT_LIMIT, AgentEvent, translate
from q50diag.mechaniq.runner import MechaniqRunner, build_options
from q50diag.mechaniq.settings import API_KEY, MechaniqSettings
from tests.test_gui_fakes import app, wait_until  # noqa: F401 (app is a fixture)

MODEL = "claude-opus-5-5"
SESSION = "session-1"
AGENT_CALL = "toolu_agent_1"


def init_message() -> SystemMessage:
    data = {"type": "system", "subtype": "init", "model": MODEL, "session_id": SESSION, "apiKeySource": "none"}
    return SystemMessage(subtype="init", data=data)


def delta(text: str, parent: str | None = None) -> StreamEvent:
    event = {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}}
    return StreamEvent(uuid="u-delta", session_id=SESSION, event=event, parent_tool_use_id=parent)


def assistant(*blocks, parent: str | None = None, error=None) -> AssistantMessage:
    return AssistantMessage(content=list(blocks), model=MODEL, parent_tool_use_id=parent, error=error)


def tool_result(tool_use_id: str, content, is_error=False, parent: str | None = None) -> UserMessage:
    block = ToolResultBlock(tool_use_id=tool_use_id, content=content, is_error=is_error)
    return UserMessage(content=[block], parent_tool_use_id=parent)


def result(origin=None, subtype="success", is_error=False) -> ResultMessage:
    return ResultMessage(
        subtype=subtype,
        duration_ms=1200,
        duration_api_ms=900,
        is_error=is_error,
        num_turns=3,
        session_id=SESSION,
        total_cost_usd=0.042,
        result="done",
        origin=origin,
    )


AGENT_INPUT = {"description": "Check EPS voltage", "prompt": "Read EPS 21 01", "subagent_type": "data-analyst"}


def subagent_turn() -> list:
    return [
        init_message(),
        delta("Hel"),
        delta("lo"),
        assistant(TextBlock("Hello"), ToolUseBlock(AGENT_CALL, "Agent", AGENT_INPUT)),
        assistant(
            ToolUseBlock("toolu_sub_1", "mcp__q50__get_readings", {"keys": ["EPS.21.01.supply"]}), parent=AGENT_CALL
        ),
        tool_result("toolu_sub_1", [{"type": "text", "text": '{"value": 14.1}'}], parent=AGENT_CALL),
        tool_result(AGENT_CALL, "Supply 14.1 V"),
        result(),
    ]


# --- translate ---------------------------------------------------------------


def test_translate_init():
    assert translate(init_message()) == [
        AgentEvent("init", "lead", data={"model": MODEL, "session_id": SESSION, "api_key_source": "none"})
    ]


def test_translate_other_system_messages_are_ignored():
    assert translate(SystemMessage(subtype="status", data={})) == []


def test_translate_delta_lead_and_subagent():
    assert translate(delta("abc")) == [AgentEvent("delta", "lead", "abc")]
    assert translate(delta("x", parent=AGENT_CALL)) == [AgentEvent("delta", AGENT_CALL, "x")]


def test_translate_non_text_stream_events_are_ignored():
    start = StreamEvent(uuid="u", session_id=SESSION, event={"type": "message_start", "message": {}})
    thinking = StreamEvent(
        uuid="u",
        session_id=SESSION,
        event={"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "hm"}},
    )
    assert translate(start) == [] and translate(thinking) == []


def test_translate_text_and_tool_use():
    message = assistant(
        ThinkingBlock("hm", "sig"), TextBlock("Reading"), ToolUseBlock("toolu_1", "Read", {"file_path": "a.py"})
    )
    assert translate(message) == [
        AgentEvent("text", "lead", "Reading"),
        AgentEvent("tool_use", "lead", data={"id": "toolu_1", "name": "Read", "input": {"file_path": "a.py"}}),
    ]


@pytest.mark.parametrize("tool_name", ["Agent", "Task"])
def test_translate_agent_call_emits_agent_started(tool_name):
    events = translate(assistant(ToolUseBlock(AGENT_CALL, tool_name, AGENT_INPUT)))
    assert [e.kind for e in events] == ["tool_use", "agent_started"]
    assert events[0].agent_id == "lead"
    assert events[1] == AgentEvent(
        "agent_started", AGENT_CALL, data={"title": "Check EPS voltage", "subagent_type": "data-analyst"}
    )


def test_translate_subagent_messages_are_attributed_to_the_agent_call():
    events = translate(assistant(TextBlock("sub text"), ToolUseBlock("toolu_s", "Grep", {}), parent=AGENT_CALL))
    assert {e.agent_id for e in events} == {AGENT_CALL}
    results = translate(tool_result("toolu_s", "hit", parent=AGENT_CALL), frozenset({AGENT_CALL}))
    assert results == [AgentEvent("tool_result", AGENT_CALL, "hit", {"tool_use_id": "toolu_s", "is_error": False})]


def test_translate_tool_result_list_content_and_error():
    events = translate(tool_result("toolu_1", [{"type": "text", "text": "a"}, {"type": "image"}], is_error=True))
    assert events == [AgentEvent("tool_result", "lead", "a\n[image]", {"tool_use_id": "toolu_1", "is_error": True})]


def test_translate_tool_result_is_clipped():
    events = translate(tool_result("toolu_1", "x" * (TOOL_RESULT_LIMIT + 500)))
    assert events[0].text.startswith("x" * TOOL_RESULT_LIMIT)
    assert "500 more characters" in events[0].text
    assert len(events[0].text) < TOOL_RESULT_LIMIT + 100


def test_translate_agent_tool_result_emits_agent_finished():
    events = translate(tool_result(AGENT_CALL, "Supply 14.1 V"), frozenset({AGENT_CALL}))
    assert [e.kind for e in events] == ["tool_result", "agent_finished"]
    assert events[0].agent_id == "lead"
    assert events[1] == AgentEvent("agent_finished", AGENT_CALL, data={"status": "completed", "is_error": False})
    assert [e.kind for e in translate(tool_result(AGENT_CALL, "x"))] == ["tool_result"]


def test_translate_plain_user_text_is_ignored():
    assert translate(UserMessage(content="hello")) == []


def test_translate_result():
    assert translate(result()) == [
        AgentEvent(
            "result",
            "lead",
            "done",
            {"cost_usd": 0.042, "duration_ms": 1200, "num_turns": 3, "is_error": False, "subtype": "success"},
        )
    ]


def test_translate_assistant_error():
    events = translate(assistant(parent=None, error="authentication_failed"))
    assert events[0].kind == "error" and "MechanIQ settings" in events[0].text
    other = translate(assistant(parent=None, error="rate_limit"))
    assert other == [AgentEvent("error", "lead", "Assistant error: rate_limit")]


def test_translate_task_messages():
    started = TaskStartedMessage(
        subtype="task_started",
        data={},
        task_id="t1",
        description="Background scan",
        uuid="u",
        session_id=SESSION,
        tool_use_id="toolu_bg",
        task_type="local_agent",
    )
    assert translate(started) == [
        AgentEvent("agent_started", "toolu_bg", data={"title": "Background scan", "subagent_type": "local_agent"})
    ]
    assert translate(started, frozenset({"toolu_bg"})) == []
    done = TaskNotificationMessage(
        subtype="task_notification",
        data={},
        task_id="t1",
        status="failed",
        output_file="/tmp/x",
        summary="Scan failed",
        uuid="u",
        session_id=SESSION,
        tool_use_id="toolu_bg",
    )
    assert translate(done) == [
        AgentEvent("agent_finished", "toolu_bg", "Scan failed", {"status": "failed", "is_error": True})
    ]


def test_translate_rate_limit_rejected_is_an_error():
    rejected = RateLimitEvent(rate_limit_info=RateLimitInfo(status="rejected"), uuid="u", session_id=SESSION)
    allowed = RateLimitEvent(rate_limit_info=RateLimitInfo(status="allowed"), uuid="u", session_id=SESSION)
    assert [e.kind for e in translate(rejected)] == ["error"]
    assert translate(allowed) == []


# --- build_options -----------------------------------------------------------


async def fake_guard(input_data, tool_use_id, context):
    return {}


MCP_SERVER = {"type": "sdk", "name": "q50", "instance": object()}
ENV = {"HOME": "/home/tester", "ANTHROPIC_API_KEY": "sk-inherited", "ANTHROPIC_AUTH_TOKEN": "tok"}
VIN_PATTERN = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")


def test_build_options_subscription(tmp_path):
    options = build_options(MechaniqSettings(model="claude-sonnet-5-5"), tmp_path, MCP_SERVER, fake_guard, base_env=ENV)
    assert options.cwd == str(tmp_path)
    assert options.permission_mode == "bypassPermissions"
    assert options.model == "claude-sonnet-5-5"
    assert options.system_prompt == {"type": "preset", "preset": "claude_code", "append": prompt.SYSTEM_APPEND}
    assert options.include_partial_messages is True
    assert options.setting_sources == ["project"]
    assert options.mcp_servers == {"q50": MCP_SERVER}
    assert options.agents is prompt.AGENTS
    assert set(options.agents) == {"code-fixer", "data-analyst", "test-coach"}
    matchers = options.hooks["PreToolUse"]
    assert len(matchers) == 1 and isinstance(matchers[0], HookMatcher)
    assert matchers[0].hooks == [fake_guard] and matchers[0].matcher is None
    # The SDK merges env over the parent environment, so credentials are blanked, not only dropped.
    assert options.env["ANTHROPIC_API_KEY"] == "" and options.env["ANTHROPIC_AUTH_TOKEN"] == ""
    assert options.env["HOME"] == "/home/tester"


def test_build_options_api_key_mode(tmp_path):
    settings = MechaniqSettings(auth_mode=API_KEY, api_key="sk-mine")
    options = build_options(settings, tmp_path, MCP_SERVER, fake_guard, base_env=ENV)
    assert options.env["ANTHROPIC_API_KEY"] == "sk-mine"
    # An inherited ANTHROPIC_AUTH_TOKEN would take precedence over the key.
    assert options.env["ANTHROPIC_AUTH_TOKEN"] == ""


def test_build_options_stderr_and_custom_agents(tmp_path):
    lines: list[str] = []
    options = build_options(
        MechaniqSettings(), tmp_path, MCP_SERVER, fake_guard, agents={}, stderr=lines.append, base_env=ENV
    )
    assert options.agents == {}
    options.stderr("boom")
    assert lines == ["boom"]


def test_prompt_has_no_vin():
    texts = [prompt.SYSTEM_APPEND] + [agent.prompt for agent in prompt.AGENTS.values()]
    assert not any(VIN_PATTERN.search(text) for text in texts)
    assert "apply_change" in prompt.SYSTEM_APPEND and "km/h" in prompt.SYSTEM_APPEND


# --- runner ------------------------------------------------------------------

WAIT_FOR_INTERRUPT = object()


class FakeClient:
    """Stands in for ClaudeSDKClient. Each query() consumes the next scripted
    turn; WAIT_FOR_INTERRUPT in a turn blocks until interrupt() is called."""

    def __init__(self, options, turns: list[list], stderr_lines: list[str]) -> None:
        self.options = options
        self.turns = turns
        self.stderr_lines = stderr_lines
        self.queries: list[str] = []
        self.connected = False
        self.disconnected = False
        self.interrupted = False
        self._current: list = []
        self._interrupt_event: asyncio.Event | None = None

    async def connect(self, prompt=None) -> None:
        self.connected = True
        self._interrupt_event = asyncio.Event()
        for line in self.stderr_lines:
            self.options.stderr(line)

    async def query(self, prompt: str, session_id: str = "default") -> None:
        self.queries.append(prompt)
        self._current = self.turns.pop(0)

    async def receive_messages(self):
        for message in self._current:
            if message is WAIT_FOR_INTERRUPT:
                await self._interrupt_event.wait()
                continue
            await asyncio.sleep(0)
            yield message

    async def interrupt(self) -> None:
        self.interrupted = True
        self._interrupt_event.set()

    async def disconnect(self) -> None:
        self.disconnected = True


class ClientFactory:
    def __init__(self, *turns: list) -> None:
        self.turns = list(turns)
        self.clients: list[FakeClient] = []
        self.stderr_lines: list[str] = []

    def __call__(self, options=None) -> FakeClient:
        client = FakeClient(options, self.turns, self.stderr_lines)
        self.clients.append(client)
        return client


@pytest.fixture
def make_runner(app, tmp_path):  # noqa: F811 (app is the imported fixture)
    runners: list[MechaniqRunner] = []

    def build(factory: ClientFactory, settings: MechaniqSettings | None = None):
        runner = MechaniqRunner(
            tmp_path, MCP_SERVER, fake_guard, settings or MechaniqSettings(), client_factory=factory
        )
        events: list[AgentEvent] = []
        runner.event.connect(events.append)
        runners.append(runner)
        return runner, events

    yield build
    for runner in runners:
        runner.shutdown()


def idle_count(events) -> int:
    return sum(1 for e in events if e.kind == "idle")


def test_runner_send_emits_events_in_order_then_idle(make_runner):
    factory = ClientFactory(subagent_turn())
    runner, events = make_runner(factory)
    runner.send("check the steering")
    assert wait_until(lambda: idle_count(events) == 1)
    assert [(e.kind, e.agent_id) for e in events] == [
        ("busy", "lead"),
        ("init", "lead"),
        ("delta", "lead"),
        ("delta", "lead"),
        ("text", "lead"),
        ("tool_use", "lead"),
        ("agent_started", AGENT_CALL),
        ("tool_use", AGENT_CALL),
        ("tool_result", AGENT_CALL),
        ("tool_result", "lead"),
        ("agent_finished", AGENT_CALL),
        ("result", "lead"),
        ("idle", "lead"),
    ]
    assert runner.busy is False
    client = factory.clients[0]
    assert client.connected and client.queries == ["check the steering"]
    assert client.options.permission_mode == "bypassPermissions"


def test_runner_keeps_one_client_across_turns(make_runner):
    factory = ClientFactory([result()], [result()])
    runner, events = make_runner(factory)
    runner.send("one")
    runner.send("two")
    assert wait_until(lambda: idle_count(events) == 2)
    assert len(factory.clients) == 1
    assert factory.clients[0].queries == ["one", "two"]
    assert not factory.clients[0].disconnected


def test_runner_ignores_blank_send(make_runner):
    factory = ClientFactory()
    runner, events = make_runner(factory)
    runner.send("   ")
    assert not wait_until(lambda: events, timeout_s=0.2)
    assert factory.clients == []


def test_runner_injected_result_does_not_end_the_turn(make_runner):
    turn = [result(origin={"kind": "task-notification"}), assistant(TextBlock("after")), result()]
    factory = ClientFactory(turn)
    runner, events = make_runner(factory)
    runner.send("go")
    assert wait_until(lambda: idle_count(events) == 1)
    kinds = [e.kind for e in events]
    assert kinds == ["busy", "result", "text", "result", "idle"]


def test_runner_interrupt(make_runner):
    factory = ClientFactory(
        [assistant(TextBlock("working")), WAIT_FOR_INTERRUPT, result(subtype="error_during_execution")]
    )
    runner, events = make_runner(factory)
    runner.send("long task")
    assert wait_until(lambda: any(e.kind == "text" for e in events))
    assert runner.busy is True
    runner.interrupt()
    assert wait_until(lambda: idle_count(events) == 1)
    assert factory.clients[0].interrupted
    assert runner.busy is False


def test_runner_reset_starts_a_new_session(make_runner):
    factory = ClientFactory([result()], [result()])
    runner, events = make_runner(factory)
    runner.send("first")
    assert wait_until(lambda: idle_count(events) == 1)
    runner.reset()
    assert wait_until(lambda: factory.clients[0].disconnected)
    runner.send("second")
    assert wait_until(lambda: idle_count(events) == 2)
    assert len(factory.clients) == 2
    assert factory.clients[1].queries == ["second"]


def test_runner_reset_interrupts_a_running_turn(make_runner):
    factory = ClientFactory([WAIT_FOR_INTERRUPT, result()])
    runner, events = make_runner(factory)
    runner.send("long")
    assert wait_until(lambda: runner.busy)
    runner.reset()
    assert wait_until(lambda: factory.clients[0].disconnected)
    assert factory.clients[0].interrupted


def test_runner_apply_settings_reconnects_with_new_model(make_runner):
    factory = ClientFactory([result()], [result()])
    runner, events = make_runner(factory)
    runner.send("first")
    assert wait_until(lambda: idle_count(events) == 1)
    runner.apply_settings(MechaniqSettings(model="claude-haiku-5-5"))
    assert runner.settings.model == "claude-haiku-5-5"
    runner.send("second")
    assert wait_until(lambda: idle_count(events) == 2)
    assert factory.clients[0].disconnected
    assert factory.clients[1].options.model == "claude-haiku-5-5"


def test_runner_shutdown_disconnects_and_stops_thread(make_runner):
    factory = ClientFactory([result()])
    runner, events = make_runner(factory)
    runner.send("hi")
    assert wait_until(lambda: idle_count(events) == 1)
    thread = runner._thread
    runner.shutdown()
    assert factory.clients[0].disconnected
    assert thread is not None and not thread.is_alive()
    runner.shutdown()


def test_runner_reports_missing_api_key(make_runner):
    factory = ClientFactory()
    runner, events = make_runner(factory, MechaniqSettings(auth_mode=API_KEY, api_key=""))
    runner.send("hi")
    assert wait_until(lambda: idle_count(events) == 1)
    assert [e.kind for e in events] == ["busy", "error", "idle"]
    assert "API key" in events[1].text
    assert factory.clients == []


def test_runner_reports_session_end_without_result_with_stderr(make_runner):
    factory = ClientFactory([assistant(TextBlock("partial"))], [result()])
    factory.stderr_lines.append("fatal: something broke")
    runner, events = make_runner(factory)
    runner.send("hi")
    assert wait_until(lambda: idle_count(events) == 1)
    error = next(e for e in events if e.kind == "error")
    assert "ended the session" in error.text
    assert error.data["stderr"] == ["fatal: something broke"]
    assert factory.clients[0].disconnected
    runner.send("again")
    assert wait_until(lambda: idle_count(events) == 2)
    assert len(factory.clients) == 2
