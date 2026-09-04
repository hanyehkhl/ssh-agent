"""The agentic loop: multi-step tool-calling against an OpenAI-compatible API.

Works with any OpenAI-compatible chat completions endpoint, configured via
base_url + api_key from the environment, so each user supplies their own key
and chooses their own provider.

The loop is written as a generator of typed events (see `agent.events`),
following agno's streaming `RunResponse` model: the CLI, the PyQt UI and any
future frontend consume the same ordered stream instead of reaching into the
agent's internals.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator

from openai import OpenAI

from agent.events import (
    AgentContent,
    RunCompleted,
    RunError,
    RunEvent,
    RunMetrics,
    RunStarted,
)
from agent.prompts import SYSTEM_PROMPT, normalize_fa
from agent.session import SessionStore, new_session_id
from agent.tools import SSHToolkit, ToolContext

DEFAULT_MODEL = "gpt-4o"
MAX_TOOL_ITERATIONS = 15  # safety cap so a runaway loop can't call tools forever
DEFAULT_HISTORY_LIMIT = 40  # messages kept after the system prompt


def build_client() -> OpenAI:
    api_key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    base_url = os.environ.get("LLM_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
    if not api_key:
        raise RuntimeError(
            "LLM_API_KEY (or OPENAI_API_KEY) is not set — put your own API key in .env"
        )
    kwargs = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)


def _assistant_entry(message) -> dict:
    """Turn the model's reply into a message to append to the history.

    The reply is echoed back verbatim rather than rebuilt field by field.
    Some providers attach their own data to a tool call and reject the next
    request if it does not come back — Gemini's OpenAI-compatible endpoint
    returns a `thought_signature` inside each tool call and answers HTTP 400
    ("Function call is missing a thought_signature") when a hand-rebuilt
    message drops it.
    """
    if hasattr(message, "model_dump"):
        entry = message.model_dump(exclude_none=True)
        entry["role"] = "assistant"
        if not entry.get("tool_calls") and entry.get("content") is None:
            entry["content"] = ""
        return entry

    # Fallback for test doubles and any client that is not a pydantic model.
    entry = {"role": "assistant", "content": message.content or ""}
    if message.tool_calls:
        entry["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in message.tool_calls
        ]
    return entry


class Agent:
    def __init__(
        self,
        ctx: ToolContext,
        client: OpenAI | None = None,
        model: str | None = None,
        session_id: str | None = None,
        store: SessionStore | None = None,
        history_limit: int = DEFAULT_HISTORY_LIMIT,
    ):
        self.ctx = ctx
        self.client = client or build_client()
        self.model = model or os.environ.get("LLM_MODEL", DEFAULT_MODEL)
        self.history_limit = history_limit

        # Events raised inside a tool are buffered here and yielded by run()
        # once the (synchronous) tool call returns.
        self._pending_events: list[RunEvent] = []
        self.toolkit = SSHToolkit(ctx, on_event=self._pending_events.append)

        self.store = store
        self.session_id = session_id or new_session_id()
        history = store.load(self.session_id) if store else []
        self.messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            *history,
        ]

    # -- history -----------------------------------------------------------

    def _trim(self) -> list[dict]:
        """Cap the context window, never orphaning a tool result.

        A `tool` message is only valid directly after the `assistant` message
        whose tool_calls it answers, so the trim point is pushed forward past
        any leading tool replies.
        """
        system, rest = self.messages[0], self.messages[1:]
        if len(rest) <= self.history_limit:
            return self.messages
        kept = rest[-self.history_limit :]
        while kept and kept[0].get("role") == "tool":
            kept.pop(0)
        return [system, *kept]

    def _persist(self):
        if self.store:
            self.store.save(
                self.session_id, self.messages[1:], meta={"model": self.model}
            )

    # -- the loop ----------------------------------------------------------

    def run(self, user_text: str) -> Iterator[RunEvent]:
        """Stream one user turn through the agent loop as typed events."""
        started_at = time.monotonic()
        metrics = RunMetrics()
        user_text = normalize_fa(user_text)
        self.ctx.logger.log_user_input(user_text)
        self.messages.append({"role": "user", "content": user_text})
        yield RunStarted(user_text=user_text, session_id=self.session_id)

        for _ in range(MAX_TOOL_ITERATIONS):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=self._trim(),
                    tools=self.toolkit.schemas,
                    tool_choice="auto",
                )
            except Exception as exc:  # noqa: BLE001 - network/auth/quota failures
                message = f"خطا در تماس با مدل: {type(exc).__name__}: {exc}"
                self.ctx.logger.log_agent_message(message)
                yield RunError(message=message)
                return

            metrics.model_calls += 1
            usage = getattr(response, "usage", None)
            if usage:
                metrics.input_tokens += getattr(usage, "prompt_tokens", 0) or 0
                metrics.output_tokens += getattr(usage, "completion_tokens", 0) or 0

            message = response.choices[0].message
            self.messages.append(_assistant_entry(message))

            if not message.tool_calls:
                final_text = (message.content or "").strip()
                self.ctx.logger.log_agent_message(final_text)
                metrics.duration_s = time.monotonic() - started_at
                self._persist()
                yield RunCompleted(content=final_text, metrics=metrics)
                return

            if message.content and message.content.strip():
                yield AgentContent(text=message.content.strip())

            for tool_call in message.tool_calls:
                metrics.tool_calls += 1
                result = self.toolkit.call(
                    tool_call.function.name, tool_call.function.arguments
                )
                while self._pending_events:
                    yield self._pending_events.pop(0)
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result,
                    }
                )

        timeout_text = (
            "تعداد دفعات فراخوانی ابزار در این پاسخ به سقف رسید؛ "
            "لطفاً سؤال را دقیق‌تر یا کوچک‌تر مطرح کن."
        )
        self.ctx.logger.log_agent_message(timeout_text)
        metrics.duration_s = time.monotonic() - started_at
        self._persist()
        yield RunCompleted(content=timeout_text, metrics=metrics)

    def ask(self, user_text: str, on_event=None) -> str:
        """Blocking convenience wrapper around `run()` returning the final text.

        Args:
            user_text: The user's turn.
            on_event: Optional callback invoked with every event as it arrives.
        """
        final = ""
        for event in self.run(user_text):
            if on_event:
                on_event(event)
            if isinstance(event, RunCompleted):
                final = event.content
            elif isinstance(event, RunError):
                final = event.message
        return final
