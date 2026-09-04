"""PyQt5 UI: a live terminal pane on the left, an agent chat pane on the right.

Split-view successor to ai_terminal's single-pane "AI Mode" checkbox: instead
of one-shot command generation, every tool call the agent makes shows up as
its own card (command, why, safety level, output) as the agentic loop runs.
The agent loop runs on a QThread so the UI stays responsive.
"""
from __future__ import annotations

from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from agent.events import (
    AgentContent,
    RunCompleted,
    RunError,
    ToolCallCompleted,
    ToolCallStarted,
)
from agent.loop import Agent
from agent.session import SessionStore
from agent.tools import ToolContext
from core.logger import SessionLogger
from core.ssh_session import SSHSession

SAFETY_COLORS = {
    "SAFE": "#2e7d32",
    "CONFIRM": "#f9a825",
    "BLOCKED": "#c62828",
}


class ConfirmDialog(QDialog):
    """Modal confirmation dialog shown for CONFIRM-level commands.

    Must run on the GUI thread; the agent's confirm_fn blocks the worker
    thread until this returns, so it is invoked via a queued cross-thread
    call from AgentWorker.
    """

    def __init__(self, command: str, why: str, reason: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("تأیید اجرای دستور")
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("دستور:"))
        cmd_box = QTextEdit(command)
        cmd_box.setReadOnly(True)
        cmd_box.setMaximumHeight(60)
        layout.addWidget(cmd_box)

        layout.addWidget(QLabel(f"دلیل مدل: {why}"))
        layout.addWidget(QLabel(f"دلیل نیاز به تأیید: {reason}"))

        buttons = QDialogButtonBox(QDialogButtonBox.Yes | QDialogButtonBox.No)
        buttons.button(QDialogButtonBox.Yes).setText("اجرا شود")
        buttons.button(QDialogButtonBox.No).setText("رد کن")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class ToolCallCard(QFrame):
    """One collapsible-ish card summarizing a single tool call."""

    def __init__(self, command: str, why: str, parent=None):
        super().__init__(parent)
        self.command = command
        self.setFrameShape(QFrame.StyledPanel)
        layout = QVBoxLayout(self)

        self.header = QLabel(f"⏳  $ {command}")
        self.header.setWordWrap(True)
        layout.addWidget(self.header)
        self.set_safety("PENDING")

        why_label = QLabel(f"چرا: {why}")
        why_label.setWordWrap(True)
        layout.addWidget(why_label)

        self.output_box = QPlainTextEdit()
        self.output_box.setReadOnly(True)
        self.output_box.setMaximumHeight(120)
        layout.addWidget(self.output_box)

    def set_safety(self, safety_level: str):
        """Recolor the card once the policy verdict for the command is known."""
        color = SAFETY_COLORS.get(safety_level, "#888")
        self.setStyleSheet(
            f"QFrame {{ border: 1px solid {color}; border-radius: 6px; margin: 4px; }}"
        )
        label = "⏳" if safety_level == "PENDING" else f"[{safety_level}]"
        self.header.setText(f"{label}  $ {self.command}")
        self.header.setStyleSheet(f"font-weight: bold; color: {color};")

    def set_output(self, text: str):
        self.output_box.setPlainText(text)


class AgentWorker(QThread):
    """Runs one agent turn off the GUI thread, relaying its event stream.

    The agent yields typed events (agent.events), so the UI no longer has to
    wrap or patch the tool executor to learn when a command starts or ends.
    """

    tool_started = pyqtSignal(str, str)  # command, why
    tool_finished = pyqtSignal(str, str, str)  # command, output, safety_level
    agent_text = pyqtSignal(str)
    finished_ok = pyqtSignal(str, str)  # answer, metrics summary
    failed = pyqtSignal(str)

    def __init__(self, agent: Agent, user_text: str, parent=None):
        super().__init__(parent)
        self.agent = agent
        self.user_text = user_text

    def run(self):
        try:
            for event in self.agent.run(self.user_text):
                if isinstance(event, ToolCallStarted):
                    self.tool_started.emit(event.command, event.why)
                elif isinstance(event, ToolCallCompleted):
                    self.tool_finished.emit(event.command, event.result, event.safety_level)
                elif isinstance(event, AgentContent):
                    self.agent_text.emit(event.text)
                elif isinstance(event, RunCompleted):
                    self.finished_ok.emit(event.content, event.metrics.summary())
                elif isinstance(event, RunError):
                    self.failed.emit(event.message)
        except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self, session: SSHSession, logger: SessionLogger,
                 session_id: str | None = None):
        super().__init__()
        self.setWindowTitle("ssh-agent")
        self.resize(1100, 700)

        self.session = session
        self.logger = logger
        self.ctx = ToolContext(session=session, logger=logger, confirm_fn=self._confirm)
        self.agent = Agent(self.ctx, session_id=session_id, store=SessionStore())
        self.setWindowTitle(f"ssh-agent — {self.agent.session_id}")
        self.worker: AgentWorker | None = None

        splitter = QSplitter()

        # Left pane: raw terminal-style transcript.
        self.terminal_view = QPlainTextEdit()
        self.terminal_view.setReadOnly(True)
        self.terminal_view.setStyleSheet(
            "background-color: #111; color: #d4d4d4; font-family: Consolas, monospace;"
        )
        splitter.addWidget(self.terminal_view)

        # Right pane: chat + tool call cards.
        right = QWidget()
        right_layout = QVBoxLayout(right)

        self.chat_scroll = QScrollArea()
        self.chat_scroll.setWidgetResizable(True)
        self.chat_container = QWidget()
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.addStretch(1)
        self.chat_scroll.setWidget(self.chat_container)
        right_layout.addWidget(self.chat_scroll)

        input_row = QHBoxLayout()
        self.input_line = QLineEdit()
        self.input_line.setPlaceholderText("سؤال یا هدف خود را بنویس... (مثلاً: چرا nginx بالا نمیاد؟)")
        self.input_line.returnPressed.connect(self._on_submit)
        self.send_button = QPushButton("ارسال")
        self.send_button.clicked.connect(self._on_submit)
        input_row.addWidget(self.input_line)
        input_row.addWidget(self.send_button)
        right_layout.addLayout(input_row)

        splitter.addWidget(right)
        splitter.setSizes([500, 600])
        self.setCentralWidget(splitter)

        self._pending_cards: dict[str, ToolCallCard] = {}

    def _log_terminal(self, text: str):
        self.terminal_view.appendPlainText(text)

    def _add_chat_bubble(self, text: str, who: str):
        label = QLabel(f"<b>{who}:</b> {text}")
        label.setWordWrap(True)
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, label)

    def _confirm(self, command: str, why: str, reason: str) -> bool:
        # confirm_fn is invoked synchronously from AgentWorker.run() (a
        # background QThread). Qt dialogs must be created and exec'd on the
        # GUI thread, so we schedule the dialog there via QTimer.singleShot
        # and block this thread on a local QEventLoop until it closes.
        from PyQt5.QtCore import QEventLoop, QTimer

        result = {}
        loop = QEventLoop()

        def show_dialog():
            dialog = ConfirmDialog(command, why, reason, parent=self)
            result["approved"] = dialog.exec_() == QDialog.Accepted
            loop.quit()

        QTimer.singleShot(0, show_dialog)
        loop.exec_()
        return result.get("approved", False)

    def _on_submit(self):
        text = self.input_line.text().strip()
        if not text:
            return
        self.input_line.clear()
        self._add_chat_bubble(text, "شما")
        self._log_terminal(f"شما> {text}")
        self.send_button.setEnabled(False)

        self.worker = AgentWorker(self.agent, text)
        self.worker.tool_started.connect(self._on_tool_started)
        self.worker.tool_finished.connect(self._on_tool_finished)
        self.worker.agent_text.connect(lambda t: self._add_chat_bubble(t, "ایجنت"))
        self.worker.finished_ok.connect(self._on_finished)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def _on_tool_started(self, command: str, why: str):
        self._log_terminal(f"$ {command}")
        card = ToolCallCard(command, why)
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, card)
        self._pending_cards[command] = card

    def _on_tool_finished(self, command: str, output: str, safety_level: str):
        self._log_terminal(output)
        card = self._pending_cards.pop(command, None)
        if card:
            card.set_safety(safety_level)
            card.set_output(output)

    def _on_finished(self, answer: str, metrics: str):
        self.send_button.setEnabled(True)
        self._add_chat_bubble(answer, "ایجنت")
        self.statusBar().showMessage(metrics)
        self._log_terminal(f"ایجنت> {answer}")

    def _on_failed(self, error: str):
        self.send_button.setEnabled(True)
        QMessageBox.critical(self, "خطا", error)

    def closeEvent(self, event):
        # Closing a session whose socket is already gone raises; letting that
        # propagate out of closeEvent takes the whole application down on the
        # way out, which is a poor way to end a session that merely timed out.
        try:
            self.session.close()
        except Exception as exc:  # noqa: BLE001 - nothing left to recover
            self._log_terminal(f"[هشدار] بستن اتصال SSH ناموفق بود: {exc}")
        finally:
            super().closeEvent(event)


def run_app(session: SSHSession, logger: SessionLogger, session_id: str | None = None):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(session, logger, session_id=session_id)
    window.show()
    app.exec_()
