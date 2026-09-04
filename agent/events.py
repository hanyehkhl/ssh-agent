"""Typed run events emitted by the agent loop.

Inspired by agno's streaming `RunResponse` events: instead of every frontend
reaching into the agent's internals (the PyQt UI used to monkey-patch the
tool executor to learn when a command started), `Agent.run()` yields a single
ordered stream of events that the CLI, the GUI and the session log all read.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class EventType(str, Enum):
    RUN_STARTED = "RunStarted"
    AGENT_CONTENT = "AgentContent"
    TOOL_CALL_STARTED = "ToolCallStarted"
    TOOL_CALL_COMPLETED = "ToolCallCompleted"
    RUN_COMPLETED = "RunCompleted"
    RUN_ERROR = "RunError"


class RunEvent:
    """Base class for every event. Each subclass sets its own ``type``."""


@dataclass
class RunStarted(RunEvent):
    user_text: str
    session_id: str
    type: EventType = EventType.RUN_STARTED


@dataclass
class AgentContent(RunEvent):
    """Intermediate prose the model produced alongside its tool calls."""

    text: str
    type: EventType = EventType.AGENT_CONTENT


@dataclass
class ToolCallStarted(RunEvent):
    tool_name: str
    command: str
    why: str
    type: EventType = EventType.TOOL_CALL_STARTED


@dataclass
class ToolCallCompleted(RunEvent):
    tool_name: str
    command: str
    result: str
    safety_level: str = "SAFE"
    type: EventType = EventType.TOOL_CALL_COMPLETED


@dataclass
class RunMetrics:
    """Per-run accounting, in the spirit of agno's `RunResponse.metrics`."""

    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: int = 0
    model_calls: int = 0
    duration_s: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def summary(self) -> str:
        return (
            f"{self.tool_calls} ابزار · {self.model_calls} فراخوانی مدل · "
            f"{self.total_tokens} توکن · {self.duration_s:.1f}s"
        )


@dataclass
class RunCompleted(RunEvent):
    content: str
    metrics: RunMetrics = field(default_factory=RunMetrics)
    type: EventType = EventType.RUN_COMPLETED


@dataclass
class RunError(RunEvent):
    message: str
    type: EventType = EventType.RUN_ERROR
