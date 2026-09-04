"""Tests for the agent loop's event stream, driven by a scripted fake model."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
from types import SimpleNamespace

import pytest

from agent.events import RunCompleted, RunError, RunStarted, ToolCallCompleted, ToolCallStarted
from agent.loop import Agent
from agent.session import SessionStore
from agent.tools import ToolContext


class FakeSession:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True

    def run(self, command, max_output_bytes=8000, timeout=20.0):
        return {"command": command, "exit_code": 0, "output": f"output of {command}",
                "truncated": False, "timed_out": False}


class FakeLogger:
    def __init__(self):
        self.entries = []

    def log_user_input(self, text):
        self.entries.append(("user", text))

    def log_tool_call(self, *args):
        self.entries.append(("tool", args))

    def log_agent_message(self, text):
        self.entries.append(("agent", text))


def _message(content=None, tool_calls=None):
    calls = [
        SimpleNamespace(id=f"call_{i}",
                        function=SimpleNamespace(name=name, arguments=json.dumps(args)))
        for i, (name, args) in enumerate(tool_calls or [])
    ] or None
    return SimpleNamespace(content=content, tool_calls=calls)


class FakeClient:
    """Replays a scripted list of assistant messages, one per model call."""

    def __init__(self, messages, usage=(10, 5)):
        self._messages = list(messages)
        self._usage = usage
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=self._messages.pop(0))],
            usage=SimpleNamespace(prompt_tokens=self._usage[0], completion_tokens=self._usage[1]),
        )


def make_agent(client, confirm=lambda *a: True, **kwargs):
    ctx = ToolContext(session=FakeSession(), logger=FakeLogger(), confirm_fn=confirm)
    return Agent(ctx, client=client, model="fake-model", **kwargs)


def test_safe_command_streams_start_tool_and_completion_events():
    client = FakeClient([
        _message(tool_calls=[("run_command", {"command": "ls -la", "why": "دیدن فایل‌ها"})]),
        _message(content="تمام شد"),
    ])
    events = list(make_agent(client).run("چه فایل‌هایی هست؟"))

    assert isinstance(events[0], RunStarted)
    started = next(e for e in events if isinstance(e, ToolCallStarted))
    assert started.command == "ls -la"
    completed = next(e for e in events if isinstance(e, ToolCallCompleted))
    assert completed.safety_level == "SAFE"
    assert "output of ls -la" in completed.result
    assert isinstance(events[-1], RunCompleted)
    assert events[-1].content == "تمام شد"


def test_the_model_is_given_the_generated_tool_schemas():
    client = FakeClient([_message(content="ok")])
    list(make_agent(client).run("q"))
    names = {t["function"]["name"] for t in client.calls[0]["tools"]}
    assert names == {"run_command", "read_file", "system_facts", "tail_log"}


def test_metrics_count_model_calls_tools_and_tokens():
    client = FakeClient([
        _message(tool_calls=[("run_command", {"command": "df -h", "why": "فضای دیسک"})]),
        _message(content="ok"),
    ])
    metrics = list(make_agent(client).run("q"))[-1].metrics
    assert (metrics.model_calls, metrics.tool_calls) == (2, 1)
    assert metrics.total_tokens == 30


def test_blocked_command_is_never_executed():
    client = FakeClient([
        _message(tool_calls=[("run_command", {"command": "rm -rf /", "why": "پاکسازی"})]),
        _message(content="نمی‌شود"),
    ])
    completed = next(e for e in make_agent(client).run("q")
                     if isinstance(e, ToolCallCompleted))
    assert completed.safety_level == "BLOCKED"
    assert completed.result.startswith("BLOCKED:")


def test_denied_confirm_tells_the_model_to_back_off():
    client = FakeClient([
        _message(tool_calls=[
            ("run_command", {"command": "systemctl restart nginx", "why": "ری‌استارت"}),
        ]),
        _message(content="باشد"),
    ])
    completed = next(e for e in make_agent(client, confirm=lambda *a: False).run("q")
                     if isinstance(e, ToolCallCompleted))
    assert completed.result.startswith("CONFIRM_DENIED:")


def test_model_failure_becomes_a_run_error_not_an_exception():
    class Boom(FakeClient):
        def _create(self, **kwargs):
            raise RuntimeError("401 unauthorized")

    events = list(make_agent(Boom([])).run("q"))
    assert isinstance(events[-1], RunError)
    assert "401 unauthorized" in events[-1].message


def test_iteration_cap_ends_the_run():
    client = FakeClient([
        _message(tool_calls=[("run_command", {"command": "ls", "why": "w"})])
        for _ in range(30)
    ])
    events = list(make_agent(client).run("q"))
    assert isinstance(events[-1], RunCompleted)
    assert "سقف" in events[-1].content


def test_history_is_persisted_and_resumed(tmp_path):
    store = SessionStore(tmp_path)
    first = make_agent(FakeClient([_message(content="سلام")]), session_id="s1", store=store)
    list(first.run("سلام"))

    resumed = make_agent(FakeClient([_message(content="دوباره")]), session_id="s1", store=store)
    assert [m["content"] for m in resumed.messages[1:]] == ["سلام", "سلام"]


def test_trim_keeps_the_system_prompt_and_drops_orphan_tool_replies():
    agent = make_agent(FakeClient([]), history_limit=4)
    agent.messages += [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "tool_call_id": "1", "content": "r"},
        {"role": "tool", "tool_call_id": "2", "content": "r2"},
        {"role": "assistant", "content": "a"},
        {"role": "user", "content": "q2"},
    ]
    trimmed = agent._trim()
    assert trimmed[0]["role"] == "system"
    assert trimmed[1]["role"] != "tool"
    assert trimmed[-1]["content"] == "q2"


@pytest.mark.parametrize("text,expected", [
    ("ديسك ۵۰ درصد", "دیسک 50 درصد"),
    ("  فاصله  ", "فاصله"),
])
def test_user_input_is_normalized_before_it_reaches_the_model(text, expected):
    events = list(make_agent(FakeClient([_message(content="ok")])).run(text))
    assert events[0].user_text == expected


def test_read_only_tools_also_announce_themselves():
    # Without this, the operator watches a blank screen while system_facts
    # runs its five commands.
    client = FakeClient([
        _message(tool_calls=[("system_facts", {})]),
        _message(content="ok"),
    ])
    events = list(make_agent(client).run("وضعیت سرور چطوره؟"))
    started = [e for e in events if isinstance(e, ToolCallStarted)]
    assert len(started) == 5
    assert {e.tool_name for e in started} == {"system_facts"}
    assert "uname -a" in [e.command for e in started]


def test_tail_log_reports_the_command_it_built():
    client = FakeClient([
        _message(tool_calls=[("tail_log", {"unit_or_path": "nginx", "lines": 20})]),
        _message(content="ok"),
    ])
    started = next(e for e in make_agent(client).run("q") if isinstance(e, ToolCallStarted))
    assert started.command == "journalctl -u nginx -n 20 --no-pager"
    assert started.tool_name == "tail_log"


def test_tool_call_count_matches_the_model_not_the_shell_commands():
    # system_facts is one tool call even though it shells out five times.
    client = FakeClient([_message(tool_calls=[("system_facts", {})]), _message(content="ok")])
    assert list(make_agent(client).run("q"))[-1].metrics.tool_calls == 1
