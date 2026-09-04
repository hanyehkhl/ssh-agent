"""Offscreen tests for the PyQt UI's half of the event contract.

These exercise the widget code without a display (QT_QPA_PLATFORM=offscreen)
and without a model or an SSH server: the worker is fed a scripted event
stream, and we assert the window renders it. Skipped when PyQt5 is absent,
since it lives behind the optional "gui" extra.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# MainWindow builds a real Agent, which builds an OpenAI client; the client is
# constructed lazily and never called here, but it insists on seeing a key.
os.environ.setdefault("LLM_API_KEY", "test-key-not-used")

pytest.importorskip("PyQt5", reason="the GUI lives behind the optional 'gui' extra")

from PyQt5.QtWidgets import QApplication  # noqa: E402

from agent.events import RunCompleted, RunMetrics  # noqa: E402
from tests.test_loop import FakeLogger, FakeSession  # noqa: E402
from ui.main_window import MainWindow, ToolCallCard  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # keep the session store out of the repo
    win = MainWindow(FakeSession(), FakeLogger(), session_id="test-session")
    yield win
    win.close()


def test_card_starts_pending_then_takes_the_policy_color(app):
    card = ToolCallCard("systemctl restart nginx", "ری‌استارت سرویس")
    assert "⏳" in card.header.text()
    card.set_safety("CONFIRM")
    assert "[CONFIRM]" in card.header.text()
    assert "#f9a825" in card.styleSheet()


def test_blocked_card_is_red(app):
    card = ToolCallCard("rm -rf /", "پاکسازی")
    card.set_safety("BLOCKED")
    assert "#c62828" in card.styleSheet()


def test_window_title_shows_the_session_id(window):
    assert "test-session" in window.windowTitle()


def test_tool_events_create_and_then_fill_a_card(window):
    window._on_tool_started("df -h", "بررسی فضای دیسک")
    card = window._pending_cards["df -h"]
    assert "⏳" in card.header.text()

    window._on_tool_finished("df -h", "exit_code=0\n/dev/sda1 45%", "SAFE")
    assert window._pending_cards == {}  # the card was claimed, not leaked
    assert "45%" in card.output_box.toPlainText()
    assert "[SAFE]" in card.header.text()
    assert "df -h" in window.terminal_view.toPlainText()


def test_completion_re_enables_input_and_shows_metrics(window):
    window.send_button.setEnabled(False)
    metrics = RunMetrics(input_tokens=100, output_tokens=20, tool_calls=2,
                         model_calls=3, duration_s=4.2)
    window._on_finished("nginx حالا بالاست.", metrics.summary())

    assert window.send_button.isEnabled()
    assert "120 توکن" in window.statusBar().currentMessage()
    assert "nginx حالا بالاست." in window.terminal_view.toPlainText()


def test_a_run_completed_event_carries_a_metrics_summary():
    # The worker emits exactly this string; keep the two ends in step.
    event = RunCompleted(content="ok", metrics=RunMetrics(tool_calls=1, model_calls=2))
    assert "1 ابزار" in event.metrics.summary()
