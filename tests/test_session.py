"""Tests for session persistence."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.session import SessionStore, new_session_id


def test_round_trip(tmp_path):
    store = SessionStore(tmp_path)
    messages = [
        {"role": "user", "content": "چرا nginx بالا نمیاد؟"},
        {"role": "assistant", "content": "بررسی می‌کنم"},
    ]
    store.save("s1", messages, meta={"model": "gpt-4o"})
    assert store.load("s1") == messages


def test_missing_or_corrupt_session_loads_empty(tmp_path):
    store = SessionStore(tmp_path)
    assert store.load("nope") == []
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    assert store.load("broken") == []


def test_session_ids_cannot_escape_the_store_directory(tmp_path):
    store = SessionStore(tmp_path / "store")
    store.save("../../evil", [{"role": "user", "content": "x"}])
    assert not (tmp_path.parent / "evil.json").exists()
    assert list((tmp_path / "store").glob("*.json")) == [tmp_path / "store" / "evil.json"]


def test_list_sessions_reports_turns_and_first_message(tmp_path):
    store = SessionStore(tmp_path)
    store.save("old", [{"role": "user", "content": "first"}])
    store.save("new", [{"role": "user", "content": "second"},
                       {"role": "user", "content": "third"}])
    listed = store.list_sessions()
    assert {s["session_id"] for s in listed} == {"old", "new"}
    by_id = {s["session_id"]: s for s in listed}
    assert by_id["old"]["first_message"] == "first"
    assert by_id["new"]["turns"] == 2


def test_new_session_ids_are_unique():
    assert new_session_id() != new_session_id()
