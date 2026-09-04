"""JSONL session logger, format-compatible with ai_terminal's session_log.jsonl."""
from __future__ import annotations

import datetime
import json
from pathlib import Path


class SessionLogger:
    def __init__(self, path: str = "session_log.jsonl"):
        self.path = Path(path)

    def _write(self, entry: dict):
        entry["timestamp"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def log_user_input(self, text: str):
        self._write({"type": "user_input", "text": text})

    def log_tool_call(self, command: str, why: str, safety_level: str,
                       exit_code: int | None, output: str, executed: bool):
        self._write({
            "type": "tool_call",
            "command": command,
            "why": why,
            "safety_level": safety_level,
            "exit_code": exit_code,
            "output": output,
            "executed": executed,
        })

    def log_agent_message(self, text: str):
        self._write({"type": "agent_message", "text": text})
