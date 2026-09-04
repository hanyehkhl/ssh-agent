"""Session persistence: resume a conversation across process restarts.

Inspired by agno's storage layer (`Agent(session_id=..., storage=...)`), but
kept to one dependency-free JSON file per session under `.ssh_agent/sessions/`.
The agent stores the message history; the JSONL session log stays separate and
remains the audit trail of what was actually executed.
"""
from __future__ import annotations

import datetime
import json
import uuid
from pathlib import Path

DEFAULT_STORE_DIR = Path(".ssh_agent") / "sessions"


def new_session_id() -> str:
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:6]}"


class SessionStore:
    """Loads and saves agent message histories keyed by session id."""

    def __init__(self, directory: Path | str = DEFAULT_STORE_DIR):
        self.directory = Path(directory)

    def _path(self, session_id: str) -> Path:
        # Session ids come from `new_session_id` or the user's --session flag;
        # keep them to a single safe filename component either way.
        safe = "".join(c for c in session_id if c.isalnum() or c in "-_")
        return self.directory / f"{safe}.json"

    def load(self, session_id: str) -> list[dict]:
        path = self._path(session_id)
        if not path.exists():
            return []
        try:
            return json.loads(path.read_text(encoding="utf-8")).get("messages", [])
        except (json.JSONDecodeError, OSError):
            return []

    def save(self, session_id: str, messages: list[dict], meta: dict | None = None):
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "session_id": session_id,
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "meta": meta or {},
            "messages": messages,
        }
        self._path(session_id).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def list_sessions(self) -> list[dict]:
        """Most-recently-updated first, for `cli.py --list-sessions`."""
        sessions = []
        if not self.directory.exists():
            return sessions
        for path in self.directory.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            turns = [m for m in data.get("messages", []) if m.get("role") == "user"]
            sessions.append({
                "session_id": data.get("session_id", path.stem),
                "updated_at": data.get("updated_at", ""),
                "turns": len(turns),
                "first_message": turns[0]["content"] if turns else "",
                "meta": data.get("meta", {}),
            })
        return sorted(sessions, key=lambda s: s["updated_at"], reverse=True)
